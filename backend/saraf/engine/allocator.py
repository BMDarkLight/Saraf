"""The USDT core: the part of the strategy the evidence supports most.

USDT/TMN has risen with the toman's depreciation (+78% over 180 days; 72h windows positive 65% of
the time over the last 60 days), and every rule that tried to time USDT vs TMN lost to simply
holding it. So the core:

  accumulate  converts free TMN to USDT (keeping a small TMN reserve)
  hold        tracks the USDT/TMN peak; a fall of ENGINE_CORE_GUARD_PCT from it moves everything to
              TMN (the guard). It re-enters after the pause once the rate is back above the guard exit.
  exit        from ENGINE_CORE_EXIT_HOURS before the challenge end, sells USDT for TMN in slices. The
              AI picks the moment for each slice from USDT/TMN momentum; a fixed schedule forces each
              slice by its deadline, and the engine sells the rest at the final flatten.

The satellite (dip setups on X/USDT markets) spends the same USDT, so idle satellite cash is the core.
"""

from __future__ import annotations

import time

from .features import compute

SYMBOL = "USDTTMN"


class CoreAllocator:
    def __init__(self, engine):
        self.e = engine
        self.s = engine.s

    @property
    def st(self) -> dict:
        return self.e.state.setdefault("core", {
            "status": "idle", "entry_rate": None, "peak_rate": None, "guard_until": 0.0, "exit_rate": None,
            "slices_done": 0, "usdt_cost_tmn": 0.0, "usdt_qty": 0.0})

    async def rate(self) -> float:
        return (await self.e.feed.quote(SYMBOL))["bid"]

    async def _usdt(self) -> float:
        return await self.e.broker.holding("USDT")

    async def _min_tmn(self) -> float:
        return await self.e.feed.min_notional(SYMBOL)

    def _log(self, now: float, action: str, reason: str, **data) -> None:
        self.e.journal.log("core", now, action=action, reason=reason, status=self.st["status"], **data)

    # ------------------------------------------------------------- schedule
    def exit_opens(self) -> float | None:
        return self.e.end_ts - self.s.engine_core_exit_hours * 3600 if self.e.end_ts else None

    def slice_deadline(self, k: int) -> float:
        """Deadline of slice k (1-based): evenly spaced from the window's start to the final flatten."""
        last = self.e.end_ts - self.s.engine_flatten_before_end_min * 60
        n = self.s.engine_core_exit_slices
        return last - (n - k) * (last - self.exit_opens()) / n

    def blocks_entries(self) -> str | None:
        if self.st["status"] == "guarded":
            return "USDT core guard tripped: capital is parked in TMN"
        if self.st["status"] in ("exiting", "done"):
            return "core is converting back to TMN for the challenge end"
        return None

    # ----------------------------------------------------------------- tick
    async def tick(self, now: float) -> None:
        st = self.st
        if st["status"] == "done":
            return
        if self.e.state["halted"]:
            await self._sell(now, None, "drawdown halt: the whole account goes to TMN")
            st["status"] = "done"
            return
        opens = self.exit_opens()
        if opens and now >= opens:
            if st["status"] != "exiting":
                st["status"] = "exiting"
                self._log(now, "PHASE", "exit window opened: USDT goes back to TMN in "
                                        f"{self.s.engine_core_exit_slices} slices before the challenge end")
            bar = int(now // (self.e.tf * 60))   # one exit decision per signal bar
            if bar != st.get("exit_bar"):
                st["exit_bar"] = bar
                await self._exit_step(now)
            return
        rate = await self.rate()
        if not rate:
            return
        if st["status"] == "holding":
            st["peak_rate"] = max(st["peak_rate"] or rate, rate)
            if rate <= st["peak_rate"] * (1 - self.s.engine_core_guard_pct):
                await self.e.flatten(now, "core_guard")
                await self._sell(now, None, f"guard: USDT/TMN {rate:,.0f} is {self.s.engine_core_guard_pct:.0%} "
                                            f"below its peak {st['peak_rate']:,.0f}")
                st.update(status="guarded", guard_until=now + self.s.engine_pause_hours * 3600, exit_rate=rate)
                self.e.save()
                return
        if st["status"] == "guarded":
            if now < st["guard_until"] or rate <= st["exit_rate"]:
                return
            self._log(now, "PHASE", f"guard lifted: USDT/TMN {rate:,.0f} is back above the exit {st['exit_rate']:,.0f}")
            st["peak_rate"] = rate
        await self._accumulate(now, rate)

    async def _accumulate(self, now: float, rate: float) -> None:
        st = self.st
        if self.e.paused():   # switched off: hold what there is, buy no more USDT
            return
        tmn = await self.e.broker.holding("TMN")
        amount = tmn - self.s.engine_core_tmn_reserve
        if amount < await self._min_tmn() * 1.2:
            if st["status"] == "idle" and await self._usdt() * rate >= await self._min_tmn():
                st.update(status="holding", entry_rate=st["entry_rate"] or rate, peak_rate=rate)   # restart / pre-held
            return
        try:
            fill = await self.e.broker.buy(SYMBOL, amount, "USDT")
        except Exception as e:
            self.e.journal.log("error", now, symbol=SYMBOL, error=f"core buy failed: {e}")
            return
        st["usdt_cost_tmn"] += fill.tmn
        st["usdt_qty"] += fill.qty
        st.update(status="holding", entry_rate=st["usdt_cost_tmn"] / st["usdt_qty"],
                  peak_rate=max(st["peak_rate"] or 0, rate))
        self._log(now, "BUY_USDT", "core: hold USDT; the toman's depreciation is the measured edge "
                                   "(USDT/TMN +78% in 180d, 72h windows positive 65% of the time)",
                  qty=fill.qty, price=fill.price, tmn=fill.tmn)
        self.e.save()

    async def add_usdt(self, qty: float, rate: float) -> None:
        """USDT that joined the core from outside its own buys (a consolidated coin sold on its USDT market): it
        enters the cost basis at today's rate, so the core's gain and its exit slices count it."""
        st = self.st
        if not st["usdt_qty"] and st["entry_rate"]:   # pre-held USDT with no recorded cost: seed it at its entry rate
            held = max(0.0, await self._usdt() - qty)
            st["usdt_cost_tmn"], st["usdt_qty"] = held * st["entry_rate"], held
        st["usdt_cost_tmn"] += qty * rate
        st["usdt_qty"] += qty
        st.update(status="holding" if st["status"] == "idle" else st["status"],
                  entry_rate=st["usdt_cost_tmn"] / st["usdt_qty"], peak_rate=max(st["peak_rate"] or 0, rate))
        self.e.save()

    # ----------------------------------------------------------------- exit
    async def _exit_step(self, now: float) -> None:
        st = self.st
        n = self.s.engine_core_exit_slices
        if st["slices_done"] >= n:
            return
        usdt, rate = await self._usdt(), await self.rate()
        if usdt * rate < await self._min_tmn():
            st["slices_done"] = n
            return
        k = st["slices_done"] + 1
        deadline = self.slice_deadline(k)
        if now >= deadline:
            await self._sell(now, usdt / (n - k + 1), f"slice {k}/{n}: scheduled deadline reached")
            st["slices_done"] = k
            return
        d = await self.e.decider.time_core_exit(await self._exit_context(now, k, n, deadline, usdt, rate))
        self.e.journal.log("core_decision", now, symbol=SYMBOL, action=d.payload["action"], source=d.source,
                           prompt_hash=d.prompt_hash, error=d.error, reason=d.payload["reasoning"], slice=k)
        if d.payload["action"] == "SELL_SLICE":
            await self._sell(now, usdt / (n - k + 1), f"slice {k}/{n}: {d.payload['reasoning']}")
            st["slices_done"] = k

    async def _exit_context(self, now, k, n, deadline, usdt, rate) -> dict:
        bars, htf = await self.e.feed.bars(SYMBOL)
        f = compute(bars, htf) or {}
        keep = ("close", "rsi", "ret_1", "ret_4", "ret_16", "ema20", "ema50", "htf_trend", "htf_rsi", "htf_ret_24")
        from .core import r
        return {
            "time_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(now)),
            "hours_to_challenge_end": round((self.e.end_ts - now) / 3600, 2),
            "slice": k, "slices_total": n, "minutes_until_this_slice_is_forced": round((deadline - now) / 60),
            "usdt_held": round(usdt, 2), "usdt_tmn_bid": rate,
            "core_entry_rate": round(self.st["entry_rate"] or 0), "core_gain_pct": round(
                100 * (rate / self.st["entry_rate"] - 1), 2) if self.st["entry_rate"] else None,
            "usdt_tmn_features_15m_and_1h": r({x: f[x] for x in keep if x in f}),
            "order_book": await self.e.feed.book(SYMBOL),
        }

    async def final_exit(self, now: float) -> bool:
        """At the final flatten: sell all remaining USDT. True once nothing sellable is left."""
        if self.st["status"] == "done":
            return True
        usdt, rate = await self._usdt(), await self.rate()
        if usdt * rate >= await self._min_tmn():
            await self._sell(now, None, "challenge end: everything back to TMN")
            usdt = await self._usdt()
        if usdt * rate < await self._min_tmn():
            self.st["status"] = "done"
            return True
        return False

    async def _sell(self, now: float, qty: float | None, reason: str) -> None:
        st = self.st
        held = await self._usdt()
        qty = held if qty is None else min(qty, held)
        if qty * (await self.rate()) < await self._min_tmn():
            return
        try:
            fill = await self.e.broker.sell(SYMBOL, qty, "USDT")
        except Exception as e:
            self.e.journal.log("error", now, symbol=SYMBOL, error=f"core sell failed: {e}")
            return
        entry = st["entry_rate"] or fill.price
        cost = entry * fill.qty
        pnl = fill.tmn - cost
        if st["usdt_qty"]:   # the cost basis shrinks with what was sold
            share = min(1.0, fill.qty / st["usdt_qty"])
            st["usdt_cost_tmn"] *= 1 - share
            st["usdt_qty"] *= 1 - share
        self._log(now, "SELL_USDT", reason, qty=fill.qty, price=fill.price, tmn=fill.tmn, pnl_tmn=pnl)
        self.e.state["seq"] += 1
        self.e.journal.trade({
            "trade_id": f"C{self.e.state['seq']:04d}", "symbol": SYMBOL, "setup": "usdt_core",
            "entry_time": "", "entry_price": round(entry, 2), "qty": fill.qty, "cost_tmn": round(cost),
            "exit_time": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)), "exit_price": fill.price,
            "proceeds_tmn": round(fill.tmn), "pnl_tmn": round(pnl), "pnl_pct": round(100 * pnl / cost, 3) if cost else 0,
            "exit_reason": "core_" + ("guard" if reason.startswith("guard") else "exit"), "ai_confidence": "",
            "entry_reasoning": reason,
        })
        self.e.save()

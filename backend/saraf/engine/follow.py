"""Copying a real trader inside the engine (copy-trading «کپی زنده»): ENGINE_FOLLOW_ADDRESS names a Hyperliquid
account whose public positions the engine mirrors on Wallex USDT markets, with at most ENGINE_FOLLOW_PCT of equity.

Every minute the trader's open positions are read; their longs in coins Wallex lists become target weights of the
copy budget (their leverage dropped, at most 100%), and the engine buys or sells toward them from its USDT (the core
holds the rest). Shorts cannot be copied on spot: those coins are held at 0. A change of under 2% of the budget is
left alone (fees). New buys stop with the on/off switch, a drawdown pause, a halt and the no-entry window before the
challenge end; sells always run. When the copy is stopped (address cleared or changed) the copy's coins are sold back
to USDT; at the challenge end the final sweep sells everything to TMN as for any coin.
"""

from __future__ import annotations

import time

from ..traders import Hyperliquid, describe_changes, target_weights, venue_base

POLL_SECONDS = 60
BAND = 0.02
MIN_ORDER_USDT = 1.2   # Wallex's USDT-market minimum is 1 USDT


def _usd(v: float) -> str:
    return f"${v / 1e6:,.1f}M" if v >= 1e6 else f"${v / 1e3:,.0f}K"


ACTION_FA = {"opened": "باز کرد", "closed": "بست", "added": "افزایش داد", "reduced": "کاهش داد", "flipped": "جهت را عوض کرد"}


class FollowCopy:
    def __init__(self, engine, hl: Hyperliquid | None = None):
        self.e = engine
        self.s = engine.s
        self.address = (self.s.engine_follow_address or "").lower()
        self.pct = self.s.engine_follow_pct
        self.hl = hl
        self._last = 0.0
        self._last_error = 0.0

    @property
    def st(self) -> dict:
        return self.e.state.setdefault("follow", {"address": "", "holdings": {}, "cost": {}, "positions": None, "n": 0})

    @property
    def active(self) -> bool:
        return bool(self.address)

    def entries_allowed(self, now: float) -> bool:
        e = self.e
        if e.paused() or e.state["halted"] or e.state["flattened"] or now < e.state["pause_until"]:
            return False
        return not (e.end_ts and now >= e.end_ts - self.s.engine_no_entry_before_end_min * 60)

    async def tick(self, now: float) -> None:
        st = self.st
        if st["address"] != self.address:   # copy stopped or switched: the old copy's coins go back to USDT first
            if st["holdings"]:
                await self.liquidate(now, "the copy was stopped" if not self.address else "switched to another trader")
                if st["holdings"]:
                    return
            st.update(address=self.address, positions=None)
            self.e.save()
        if not self.active or now - self._last < POLL_SECONDS:
            return
        self._last = now
        if self.hl is None:
            self.hl = Hyperliquid()
        try:
            pos = await self.hl.positions(self.address)
        except Exception as e:
            if now - self._last_error > 600:   # Hyperliquid unreachable: hold what there is, say so every 10 min
                self._last_error = now
                self.e.journal.log("error", now, error=f"follow {self.address}: {type(e).__name__}: {e}")
            return
        await self.step(pos, now)

    async def _tradable(self, coins: list[str]) -> dict[str, dict]:
        out = {}
        for coin in coins:
            base = venue_base(coin)
            if not base:
                continue
            sym = f"{base}USDT"
            try:
                q = await self.e.feed.quote(sym)
            except Exception:   # not listed on Wallex
                continue
            if q.get("bid") and q.get("ask"):
                out[sym] = q
        return out

    async def step(self, pos: dict, now: float) -> None:
        st, e = self.st, self.e
        if st["positions"] is not None:
            for ch in describe_changes(st["positions"], pos["positions"]):
                side = "لانگ" if ch["side"] == "long" else "شورت"
                copyable = ch["side"] == "long" and venue_base(ch["coin"])
                e.journal.log("trader", now, symbol=ch["coin"], action=ch["action"],
                              reason=f"تریدر {ch['coin']} را {ACTION_FA.get(ch['action'], ch['action'])} ({side}، "
                                     f"{_usd(ch['usd'])})" + ("" if copyable else "؛ شورت در اسپات کپی نمی‌شود"))
        st["positions"] = pos["positions"]
        quotes = await self._tradable(sorted({p["coin"] for p in pos["positions"]}
                                             | {s[:-4] for s in st["holdings"]}))
        weights, _ = target_weights(pos, set(quotes))
        rate = await e.rate()
        equity_tmn, _, _ = await e.equity()
        budget = self.pct * equity_tmn / rate                    # USDT the copy may hold in coins
        buys_ok = self.entries_allowed(now)
        for sym in sorted(set(st["holdings"]) | set(weights)):
            q = quotes.get(sym)
            if not q:
                continue
            have = st["holdings"].get(sym, 0.0) * q["bid"]
            want = weights.get(sym, 0.0) * budget
            diff = want - have
            if want == 0 and have > 0:
                await self._sell(sym, st["holdings"][sym], q, now, "تریدر این پوزیشن را بست یا شورت کرد")
            elif diff < -max(BAND * budget, MIN_ORDER_USDT):
                await self._sell(sym, -diff / q["bid"], q, now, "تریدر پوزیشن را کوچک کرد")
            elif diff > max(BAND * budget, MIN_ORDER_USDT) and buys_ok:
                free = await e.broker.holding("USDT")
                spend = min(diff, free * 0.995)
                if spend >= MIN_ORDER_USDT:
                    await self._buy(sym, spend, q, now, weights[sym])
        e.save()

    async def _buy(self, sym: str, usdt: float, q: dict, now: float, weight: float) -> None:
        st, e = self.st, self.e
        try:
            fill = await e.broker.buy(sym, usdt, sym[:-4], price_hint=q["ask"])
        except Exception as ex:
            e.journal.log("error", now, symbol=sym, error=f"copy buy failed: {ex}")
            return
        st["holdings"][sym] = st["holdings"].get(sym, 0.0) + fill.qty
        st["cost"][sym] = st["cost"].get(sym, 0.0) + fill.tmn
        e.journal.log("entry", now, symbol=sym, setup="copy", qty=fill.qty, price=fill.price, usdt=fill.tmn,
                      reason=f"کپی تریدر: {100 * weight:.0f}٪ از بودجه کپی در {sym[:-4]}")

    async def _sell(self, sym: str, qty: float, q: dict, now: float, why: str) -> None:
        st, e = self.st, self.e
        held = st["holdings"].get(sym, 0.0)
        qty = min(qty, held, await e.broker.holding(sym[:-4]))
        if qty * q["bid"] < 1.0:   # dust Wallex won't take: forget it rather than retry forever
            if held * q["bid"] < 1.0:
                st["holdings"].pop(sym, None)
                st["cost"].pop(sym, None)
            return
        try:
            fill = await e.broker.sell(sym, qty, sym[:-4], price_hint=q["bid"])
        except Exception as ex:
            e.journal.log("error", now, symbol=sym, error=f"copy sell failed: {ex}")
            return
        cost = st["cost"].get(sym, 0.0) * min(1.0, fill.qty / held) if held else 0.0
        st["holdings"][sym] = held - fill.qty
        st["cost"][sym] = st["cost"].get(sym, 0.0) - cost
        if st["holdings"][sym] * q["bid"] < 1.0:
            st["holdings"].pop(sym, None)
            st["cost"].pop(sym, None)
        st["n"] += 1
        pnl = fill.tmn - cost
        rate = await e.rate()
        e.journal.trade({"trade_id": f"F{st['n']:04d}", "symbol": sym, "setup": "copy", "qty": fill.qty,
                         "entry_price": round(cost / fill.qty, 8) if fill.qty else "", "cost_tmn": round(cost * rate),
                         "exit_price": fill.price, "proceeds_tmn": round(fill.tmn * rate), "pnl_tmn": round(pnl * rate),
                         "pnl_pct": round(100 * pnl / cost, 3) if cost else 0, "exit_reason": "copy",
                         "exit_time": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now))})
        e.journal.log("exit", now, symbol=sym, qty=fill.qty, price=fill.price,
                      pnl_pct=round(100 * pnl / cost, 3) if cost else 0, reason=f"کپی تریدر: {why}")

    async def liquidate(self, now: float, why: str) -> None:
        for sym in list(self.st["holdings"]):
            try:
                q = await self.e.feed.quote(sym)
            except Exception:
                continue
            await self._sell(sym, self.st["holdings"][sym], q, now, why)
        self.e.save()

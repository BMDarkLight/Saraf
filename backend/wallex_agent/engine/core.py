"""The trading engine. One class, three modes: the same code runs in backtest (SimFeed +
SimExec), shadow (LiveFeed + paper broker) and live (LiveFeed + real orders).

Cycle:
  on_bar  (each closed 15m bar)  regime check -> position reviews -> signals -> AI decision
                                 -> risk sizing -> buy
  monitor (every few seconds)    drawdown guard, end-of-challenge flatten, stop / target /
                                 trailing stop exits
Every step is written to the decision journal with its reasons.
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime
from pathlib import Path

from ..config import Settings
from . import strategy
from .features import compute
from .journal import Journal
from .risk import RiskManager


def r(x, digits: int = 4):
    """Round to significant digits (stable, compact prompts and logs)."""
    if isinstance(x, float):
        if x == 0 or not math.isfinite(x):
            return x
        return round(x, max(0, digits - 1 - int(math.floor(math.log10(abs(x))))))
    if isinstance(x, dict):
        return {k: r(v, digits) for k, v in x.items()}
    if isinstance(x, list):
        return [r(v, digits) for v in x]
    return x


def parse_end(text: str) -> float | None:
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


class Engine:
    REGIME_EVERY_S = 4 * 3600
    REVIEW_EVERY_BARS = 4

    def __init__(self, settings: Settings, feed, broker, decider, journal: Journal, state_path: Path | None = None):
        self.s, self.feed, self.broker, self.decider, self.journal = settings, feed, broker, decider, journal
        self.risk = RiskManager(settings)
        self.tf = settings.engine_timeframe_min
        self.setups = tuple(x.strip() for x in settings.engine_setups.split(",") if x.strip())
        self.end_ts = parse_end(settings.engine_end_time)
        self.state_path = state_path
        self.state = {"positions": {}, "peak": settings.engine_capital_tmn, "pause_until": 0.0, "halted": False,
                      "cooldown": {}, "regime": "neutral", "regime_reason": "", "regime_at": 0.0,
                      "seq": 0, "bars": 0, "flattened": False, "closed": [], "pending": {}}
        if state_path and state_path.exists():
            self.state.update(json.loads(state_path.read_text()))
        self.maker = settings.engine_execution == "maker" and hasattr(broker, "place_limit")
        self._last_mark = 0.0

    # ---------------------------------------------------------------- utils
    def save(self) -> None:
        if self.state_path:
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1, default=str))
            tmp.replace(self.state_path)

    @property
    def positions(self) -> dict:
        return self.state["positions"]

    @property
    def pending(self) -> dict:
        return self.state["pending"]

    async def equity(self) -> tuple[float, float, float]:
        """(equity, free cash, exposure), valued from what the exchange actually holds: TMN (free +
        reserved by open buys) plus every managed coin incl. coins locked in sells or just bought by
        an entry order that filled moments ago (the engine may not have recorded it yet)."""
        cash = await self.broker.cash()
        total = await self.broker.total_cash() if hasattr(self.broker, "total_cash") else cash
        exposure = total - cash
        symbols = {p["base"]: p["symbol"] for p in [*self.positions.values(), *self.pending.values()]}
        for base, symbol in symbols.items():
            if hasattr(self.broker, "holding_total"):
                qty = await self.broker.holding_total(base)
            else:
                qty = sum(p["qty"] for p in self.positions.values() if p["base"] == base)
            exposure += qty * (await self.feed.quote(symbol))["bid"]
        # Coins the engine doesn't manage (e.g. bought through the chat assistant on the same account)
        # still count: otherwise converting TMN by hand looks like a crash and trips the drawdown stop.
        if hasattr(self.broker, "all_holdings"):
            for asset, qty in (await self.broker.all_holdings()).items():
                if asset in symbols or qty <= 0:
                    continue
                try:
                    exposure += qty * (await self.feed.quote(f"{asset}TMN"))["bid"]
                except Exception:   # no TMN market for it: can't value it, leave it out
                    pass
        return cash + exposure, cash, exposure

    async def reconcile(self) -> None:
        """After a restart, trust the exchange: drop positions whose coins are gone."""
        now = self.feed.now()
        for pid, p in list(self.positions.items()):
            if p.get("tp_order"):
                st = await self.broker.order_status(p["tp_order"])
                if st.status == "filled":
                    await self._closed(pid, st.tmn, st.price, "take_profit", now)
                    continue
                if st.status == "open":
                    continue        # the coins are locked in the resting sell, not missing
                p["tp_order"] = None  # cancelled outside the engine; monitor re-places it
            held = await self.broker.holding(p["base"])
            if held < p["qty"] * 0.5:
                self.journal.log("reconcile", now, symbol=p["symbol"], position_id=pid,
                                 reason=f"holding {held} < recorded {p['qty']}; dropped")
                del self.positions[pid]
            elif held < p["qty"]:
                p["qty"] = held
        self.save()

    # --------------------------------------------------------------- context
    async def _portfolio_ctx(self) -> dict:
        eq, cash, exp = await self.equity()
        recent = self.state["closed"][-5:]
        return {"equity_tmn": round(eq), "cash_tmn": round(cash), "exposure_pct": round(100 * exp / eq, 1) if eq else 0,
                "return_pct": round(100 * (eq / self.s.engine_capital_tmn - 1), 2),
                "drawdown_from_peak_pct": round(100 * self.risk.drawdown(eq, self.state["peak"]), 2),
                "open_positions": [p["symbol"] for p in self.positions.values()],
                "recent_trades_pnl_pct": [c["pnl_pct"] for c in recent]}

    async def _context(self, symbol: str, f: dict) -> dict:
        now = self.feed.now()
        keep = ("close", "ema20", "ema50", "ema20_slope_pct", "rsi", "atr_pct", "bb_upper", "bb_mid", "bb_lower",
                "bb_width_pct", "donchian_high", "volume_z", "adx", "ret_1", "ret_4", "ret_16",
                "htf_trend", "htf_ema20", "htf_ema50", "htf_rsi", "htf_ret_24")
        return {
            "time_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(now)),
            "hours_left_in_challenge": round((self.end_ts - now) / 3600, 1) if self.end_ts else None,
            "features_15m_and_1h": r({k: f[k] for k in keep}),
            "order_book": await self.feed.book(symbol),
            "regime": {"label": self.state["regime"], "why": self.state["regime_reason"]},
            "market": await self.feed.market_context(),
            "news_headlines": await self.feed.news(symbol),
            "portfolio": await self._portfolio_ctx(),
        }

    # --------------------------------------------------------------- on bar
    async def on_bar(self) -> None:
        now = self.feed.now()
        self.state["bars"] += 1
        if self.state["halted"] or self.state["flattened"]:
            return
        if now - self.state["regime_at"] >= self.REGIME_EVERY_S:
            await self._update_regime(now)
        for p in list(self.positions.values()):
            p["bars_held"] = p.get("bars_held", 0) + 1
            await self._time_stop(p, now)
        if self.positions and self.s.engine_ai_reviews != "off" and self.state["bars"] % self.REVIEW_EVERY_BARS == 0:
            await self._review_positions(now)

        cands = []
        for sym in await self.feed.universe():
            try:
                bars, htf = await self.feed.bars(sym)
            except Exception as e:
                self.journal.log("error", now, symbol=sym, error=f"data: {e}")
                continue
            f = compute(bars, htf)
            for c in strategy.candidates(sym, f, self.s.engine_min_atr_pct, self.s.engine_max_atr_pct, self.setups):
                busy = any(p["symbol"] == sym for p in [*self.positions.values(), *self.pending.values()])
                if c.score >= self.s.engine_min_score and not busy:
                    cands.append((c, f))
        cands.sort(key=lambda x: -x[0].score)
        for cand, f in cands[: self.s.engine_max_ai_calls_per_bar]:
            await self._consider(cand, f, now)
        self.save()

    async def _update_regime(self, now: float) -> None:
        ctx = {"time_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(now)), **(await self.feed.market_context())}
        btc = "BTCTMN" in await self.feed.universe()
        if btc:
            bars, htf = await self.feed.bars("BTCTMN")
            f = compute(bars, htf)
            if f:
                ctx["btc"] = r({k: f[k] for k in ("htf_trend", "htf_ret_24", "htf_rsi", "ret_16", "atr_pct")})
        ctx["portfolio"] = await self._portfolio_ctx()
        d = await self.decider.assess_regime(ctx)
        regime, reason = d.payload["regime"], d.payload["reasoning"]
        breadth = ctx.get("breadth_pct_up_24h")
        if breadth is not None and breadth < self.s.engine_breadth_gate_pct and regime != "risk_off":
            # hard rule: the decider may be stricter than the breadth gate, never looser
            regime_fa = {"risk_on": "ریسک‌پذیر", "neutral": "خنثی", "risk_off": "ریسک‌گریز"}
            regime, reason = "risk_off", (f"قانون فراگیری بازار: فقط {breadth}٪ بازارها در ۲۴ ساعت مثبت‌اند "
                                          f"(کمتر از {self.s.engine_breadth_gate_pct:g}٪)، پس خرید جدید ممنوع است؛ "
                                          f"نظر هوش مصنوعی: {regime_fa.get(d.payload['regime'], d.payload['regime'])}")
        self.state.update(regime=regime, regime_reason=reason, regime_at=now)
        self.journal.log("regime", now, regime=regime, decider_regime=d.payload["regime"], reason=reason,
                         source=d.source, prompt_hash=d.prompt_hash, error=d.error, context=ctx)

    async def _consider(self, cand: strategy.Candidate, f: dict, now: float) -> None:
        eq, cash, exposure = await self.equity()
        block = self.risk.entry_block(
            now=now, equity=eq, peak=self.state["peak"], n_positions=len(self.positions) + len(self.pending),
            regime=self.state["regime"], pause_until=self.state["pause_until"], halted=self.state["halted"],
            end_ts=self.end_ts, cooldown_until=self.state["cooldown"].get(cand.symbol, 0))
        if block:
            self.journal.log("risk_block", now, symbol=cand.symbol, setup=cand.setup, reason=block, candidate=cand.dict())
            return
        ctx = await self._context(cand.symbol, f)
        d = await self.decider.decide_entry(cand, ctx)
        dp = d.payload
        self.journal.log("decision", now, symbol=cand.symbol, setup=cand.setup, action=dp["action"], source=d.source,
                         prompt_hash=d.prompt_hash, error=d.error, candidate=cand.dict(), decision=dp,
                         context=ctx)
        if dp["action"] != "TAKE":
            return
        quote = await self.feed.quote(cand.symbol)
        price = quote["ask"]
        stop, target = self.risk.clamp_stop_target(price, cand.atr, dp["stop_atr"], dp["target_atr"])
        self.risk.min_notional = await self.feed.min_notional(cand.symbol)
        sz = self.risk.size(equity=eq, cash=cash, exposure=exposure, price=price, stop=stop,
                            size_fraction=dp["size_fraction"], regime=self.state["regime"])
        if not sz.quote_amount:
            self.journal.log("risk_block", now, symbol=cand.symbol, setup=cand.setup, reason=sz.reason)
            return
        base = cand.symbol.removesuffix("TMN")
        plan = {"symbol": cand.symbol, "base": base, "setup": cand.setup, "atr": cand.atr, "rules": cand.rules,
                "stop_atr": dp["stop_atr"], "target_atr": dp["target_atr"], "confidence": dp["confidence"],
                "reasoning": dp["reasoning"], "sizing": sz.reason}
        if self.maker:   # rest a limit at the bid instead of paying the spread + taker fee
            limit = quote["bid"]
            try:
                oid = await self.broker.place_limit(cand.symbol, "BUY", sz.quote_amount / limit, limit, base)
            except Exception as e:
                self.journal.log("error", now, symbol=cand.symbol, error=f"limit buy failed: {e}")
                return
            self.pending[oid] = {**plan, "order_id": oid, "limit": limit, "ask_at_order": quote["ask"], "placed_at": now,
                                 "amount_tmn": sz.quote_amount,
                                 "expires_at": now + self.s.engine_entry_timeout_bars * self.tf * 60}
            self.journal.log("entry_order", now, symbol=cand.symbol, setup=cand.setup, order_id=oid, limit=limit,
                             amount_tmn=sz.quote_amount, expires_in_bars=self.s.engine_entry_timeout_bars,
                             sizing=sz.reason, reason=dp["reasoning"])
            self.save()
            return
        try:
            fill = await self.broker.buy(cand.symbol, sz.quote_amount, base)
        except Exception as e:
            self.journal.log("error", now, symbol=cand.symbol, error=f"buy failed: {e}")
            return
        await self._open_position(plan, fill.qty, fill.price, fill.tmn, now)

    async def _open_position(self, plan: dict, qty: float, price: float, cost: float, now: float) -> None:
        self.state["seq"] += 1
        pid = f"T{self.state['seq']:04d}"
        stop, target = self.risk.clamp_stop_target(price, plan["atr"], plan["stop_atr"], plan["target_atr"])
        p = {"id": pid, "symbol": plan["symbol"], "base": plan["base"], "setup": plan["setup"], "qty": qty,
             "entry_price": price, "cost_tmn": cost, "entry_time": now, "stop": stop, "initial_stop": stop,
             "target": target, "atr": plan["atr"], "highest": price, "bars_held": 0,
             "ai_confidence": plan["confidence"], "reasoning": plan["reasoning"], "rules": plan["rules"],
             "tp_order": None}
        self.positions[pid] = p
        if self.maker:   # the take-profit rests on the book as a maker sell
            try:
                p["tp_order"] = await self.broker.place_limit(p["symbol"], "SELL", qty, target, p["base"])
            except Exception as e:
                self.journal.log("error", now, symbol=p["symbol"], position_id=pid, error=f"take-profit order failed: {e}")
        self.journal.log("entry", now, symbol=p["symbol"], setup=p["setup"], position_id=pid, qty=qty, price=price,
                         cost_tmn=cost, stop=stop, target=target, take_profit_order=p["tp_order"],
                         execution="maker" if self.maker else "taker", sizing=plan["sizing"], reason=plan["reasoning"])
        self.save()

    async def _process_pending(self, now: float) -> None:
        """Entry limits: open the position on fill; cancel when stale or when price ran away."""
        for oid, o in list(self.pending.items()):
            st = await self.broker.order_status(oid)
            if st.status == "open":
                ask = (await self.feed.quote(o["symbol"]))["ask"]
                # measured from the ask at order time: the spread alone must not count as running away
                ran_away = ask > o.get("ask_at_order", o["limit"]) + self.s.engine_entry_chase_atr * o["atr"]
                if now < o["expires_at"] and not ran_away:
                    continue
                st = await self.broker.cancel(oid)
                if st.filled_qty <= 0:
                    del self.pending[oid]
                    self.journal.log("entry_cancelled", now, symbol=o["symbol"], setup=o["setup"], order_id=oid,
                                     reason="price ran away from the limit" if ran_away else "not filled in time")
                    self.save()
                    continue
            del self.pending[oid]
            if st.filled_qty > 0:
                await self._open_position(o, st.filled_qty, st.price, st.tmn, now)

    async def _review_positions(self, now: float) -> None:
        for pid, p in list(self.positions.items()):
            bars, htf = await self.feed.bars(p["symbol"])
            f = compute(bars, htf)
            if not f:
                continue
            bid = (await self.feed.quote(p["symbol"]))["bid"]
            view = {k: p[k] for k in ("symbol", "setup", "entry_price", "stop", "target", "bars_held", "reasoning")}
            view.update(current_bid=bid, unrealized_pct=round(100 * (bid * p["qty"] / p["cost_tmn"] - 1), 2))
            d = await self.decider.review_position(r(view), await self._context(p["symbol"], f))
            act = d.payload["action"]
            self.journal.log("review", now, symbol=p["symbol"], position_id=pid, action=act, source=d.source,
                             prompt_hash=d.prompt_hash, error=d.error, reason=d.payload["reasoning"])
            if act == "EXIT" and self.s.engine_ai_reviews == "full":
                await self._exit(pid, "ai_exit", now, note=d.payload["reasoning"])
            elif act == "TIGHTEN":
                ns = d.payload.get("new_stop")
                if ns and p["stop"] < ns < bid:
                    p["stop"] = ns

    async def _time_stop(self, p: dict, now: float) -> None:
        if p["bars_held"] >= self.s.engine_time_stop_bars:
            bid = (await self.feed.quote(p["symbol"]))["bid"]
            if bid * p["qty"] < p["cost_tmn"] * 1.005:   # not working after N bars: free the capital
                await self._exit(p["id"], "time_stop", now)

    # --------------------------------------------------------------- monitor
    async def monitor(self) -> None:
        now = self.feed.now()
        await self._process_pending(now)     # record fills before valuing the account
        eq, cash, exposure = await self.equity()
        if eq > self.state["peak"]:
            self.state["peak"] = eq
        dd = self.risk.drawdown(eq, self.state["peak"])
        if now - self._last_mark >= 300:
            self.journal.mark(now, eq, cash, exposure, dd)
            self._last_mark = now

        if not self.state["halted"] and dd >= self.s.engine_drawdown_stop_pct:
            self.journal.log("circuit_breaker", now, reason=f"drawdown {dd:.1%} >= {self.s.engine_drawdown_stop_pct:.0%}: "
                                                            "flatten and halt", equity=eq)
            self.state["halted"] = True
            await self.flatten(now, "drawdown_halt")
            return
        if dd >= self.s.engine_drawdown_pause_pct and now >= self.state["pause_until"]:
            self.state["pause_until"] = now + self.s.engine_pause_hours * 3600
            self.journal.log("circuit_breaker", now, reason=f"drawdown {dd:.1%}: no new entries for "
                                                            f"{self.s.engine_pause_hours:g}h", equity=eq)
        if self.end_ts and now >= self.end_ts - self.s.engine_flatten_before_end_min * 60 and not self.state["flattened"]:
            await self.flatten(now, "challenge_end")
            self.state["flattened"] = True
            self.save()
            return

        fee = self.s.engine_fee_rate
        for pid, p in list(self.positions.items()):
            if p.get("tp_order"):
                st = await self.broker.order_status(p["tp_order"])
                if st.status == "filled":
                    await self._closed(pid, st.tmn, st.price, "take_profit", now)
                    continue
                if st.status == "canceled":
                    p["tp_order"] = None
            if self.maker and not p.get("tp_order"):   # lost (restart, failed exit, manual cancel): re-place
                try:
                    p["tp_order"] = await self.broker.place_limit(p["symbol"], "SELL", p["qty"], p["target"], p["base"])
                except Exception as e:
                    self.journal.log("error", now, symbol=p["symbol"], position_id=pid, error=f"re-place take-profit: {e}")
            rng = await self.feed.bar_range(p["symbol"])
            if not rng["high"]:
                continue
            p["highest"] = max(p["highest"], rng["high"])
            gain = p["highest"] - p["entry_price"]
            if gain >= 1.5 * p["atr"]:                                  # lock in break-even + fees
                p["stop"] = max(p["stop"], p["entry_price"] * (1 + 2 * fee))
            if gain >= 2.5 * p["atr"]:                                  # then trail
                p["stop"] = max(p["stop"], p["highest"] - 1.5 * p["atr"])
            if rng["low"] <= p["stop"]:
                reason = "stop_loss" if p["stop"] < p["entry_price"] else "trailing_stop"
                await self._exit(pid, reason, now, hint=min(p["stop"], rng["open"]))
            elif not p.get("tp_order") and rng["high"] >= p["target"]:
                await self._exit(pid, "take_profit", now, hint=max(p["target"], rng["open"]))

    async def flatten(self, now: float, reason: str) -> None:
        for oid in list(self.pending):
            st = await self.broker.cancel(oid)
            o = self.pending.pop(oid)
            if st.filled_qty > 0:
                await self._open_position(o, st.filled_qty, st.price, st.tmn, now)
        for pid in list(self.positions):
            await self._exit(pid, reason, now)

    async def _exit(self, pid: str, reason: str, now: float, hint: float | None = None, note: str | None = None) -> None:
        """Market exit. A resting take-profit is cancelled first (it may have filled meanwhile)."""
        p = self.positions.get(pid)
        if not p:
            return
        if p.get("tp_order"):
            try:
                st = await self.broker.cancel(p["tp_order"])
            except Exception as e:
                self.journal.log("error", now, symbol=p["symbol"], position_id=pid, error=f"cancel take-profit: {e}")
                return
            if st.status == "filled":
                await self._closed(pid, st.tmn, st.price, "take_profit", now)
                return
            p["tp_order"] = None
            self.save()
        try:
            fill = await self.broker.sell(p["symbol"], p["qty"], p["base"], hint)
        except Exception as e:
            self.journal.log("error", now, symbol=p["symbol"], position_id=pid, error=f"sell failed ({reason}): {e}")
            return  # keep the position; the next monitor pass retries
        await self._closed(pid, fill.tmn, fill.price, reason, now, note)

    async def _closed(self, pid: str, proceeds: float, price: float, reason: str, now: float,
                      note: str | None = None) -> None:
        p = self.positions.pop(pid)
        pnl = proceeds - p["cost_tmn"]
        pnl_pct = round(100 * pnl / p["cost_tmn"], 3)
        self.state["cooldown"][p["symbol"]] = now + self.s.engine_cooldown_bars * self.tf * 60
        self.state["closed"].append({"id": pid, "symbol": p["symbol"], "pnl_pct": pnl_pct, "reason": reason})
        self.state["closed"] = self.state["closed"][-50:]
        self.journal.log("exit", now, symbol=p["symbol"], position_id=pid, setup=p["setup"], reason=reason,
                         note=note, price=price, proceeds_tmn=proceeds, pnl_tmn=pnl, pnl_pct=pnl_pct)
        self.journal.trade({
            "trade_id": pid, "symbol": p["symbol"], "setup": p["setup"],
            "entry_time": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(p["entry_time"])),
            "entry_price": p["entry_price"], "qty": p["qty"], "cost_tmn": round(p["cost_tmn"]),
            "exit_time": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)), "exit_price": price,
            "proceeds_tmn": round(proceeds), "pnl_tmn": round(pnl), "pnl_pct": pnl_pct, "exit_reason": reason,
            "ai_confidence": p["ai_confidence"], "entry_reasoning": p["reasoning"],
        })
        self.save()

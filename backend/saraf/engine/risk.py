"""Deterministic risk management. The AI proposes, this module disposes: every size and stop
the AI returns is clamped here, and these rules can veto any entry."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings

REGIME_EXPOSURE = {"risk_on": 1.0, "neutral": 0.67, "risk_off": 0.0}


@dataclass
class SizeResult:
    quote_amount: float
    reason: str


class RiskManager:
    def __init__(self, s: Settings, min_notional: float = 50_000):
        self.s = s
        self.min_notional = min_notional

    def drawdown(self, equity: float, peak: float) -> float:
        return max(0.0, 1 - equity / peak) if peak > 0 else 0.0

    def entry_block(self, *, now: float, equity: float, peak: float, n_positions: int, regime: str,
                    pause_until: float, halted: bool, end_ts: float | None, cooldown_until: float) -> str | None:
        s = self.s
        if halted:
            return "trading halted: max drawdown hit"
        if now < pause_until:
            return f"paused after {s.engine_drawdown_pause_pct:.0%} drawdown until {pause_until:.0f}"
        if end_ts and now >= end_ts - s.engine_no_entry_before_end_min * 60:
            return "too close to the challenge end for new entries"
        if regime == "risk_off":
            return "regime risk_off: no new longs"
        if n_positions >= s.engine_max_positions:
            return f"max {s.engine_max_positions} concurrent positions"
        if now < cooldown_until:
            return "symbol cooling down after a recent exit"
        return None

    def size(self, *, equity: float, cash: float, exposure: float, price: float, stop: float,
             size_fraction: float, regime: str) -> SizeResult:
        """Position value in TMN = min(risk-based size, per-position cap, exposure room, cash) x AI fraction."""
        s = self.s
        if stop >= price or price <= 0:
            return SizeResult(0, "invalid stop")
        stop_dist = (price - stop) / price + 2 * s.engine_fee_rate        # loss if stopped, incl. fees
        by_risk = equity * s.engine_risk_per_trade_pct / stop_dist
        cap = equity * s.engine_max_position_pct
        room = equity * s.engine_max_exposure_pct * REGIME_EXPOSURE.get(regime, 0.67) - exposure
        frac = max(0.0, min(1.0, size_fraction))
        amount = min(by_risk, cap, room, cash * 0.98) * frac
        parts = (f"risk {by_risk:,.0f} | cap {cap:,.0f} | exposure room {room:,.0f} | cash {cash:,.0f} "
                 f"x AI fraction {frac:.2f}")
        floor = self.min_notional * 1.2
        if amount < floor and frac > 0 and floor <= min(by_risk, room, cash * 0.98):
            # A small account on USDT markets: 5 USDT x 1.2 is ~23% of 5M TMN, above the per-position cap, so
            # nothing could ever be bought. The exchange minimum is the smallest trade there is; take it when its
            # loss at the stop stays within the per-trade risk budget and it fits the exposure room and the cash.
            return SizeResult(floor, f"exchange minimum order {floor:,.0f} (within the risk budget; {parts})")
        if amount < floor:
            return SizeResult(0, f"size {amount:,.0f} TMN below the minimum order ({parts})")
        return SizeResult(amount, parts)

    def clamp_stop_target(self, price: float, atr: float, stop_atr: float, target_atr: float) -> tuple[float, float]:
        stop_atr = min(max(stop_atr, 0.8), 3.0)
        # the target must clear round-trip fees with margin
        min_target = (4 * self.s.engine_fee_rate * price) / atr if atr else 1.2
        target_atr = min(max(target_atr, 1.2, min_target), 6.0)
        return price - stop_atr * atr, price + target_atr * atr

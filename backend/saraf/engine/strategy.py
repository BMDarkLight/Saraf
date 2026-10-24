"""Signal layer: deterministic setups that PROPOSE trades. The AI decider (decider.py) then
approves, sizes or rejects each proposal; the risk manager has the final veto.

All setups are long-only spot trades in Toman markets (the challenge settles in realized
Toman, and spot cannot be shorted). Every candidate carries a human-readable trace of the
rules that fired, which goes into the decision log.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Candidate:
    symbol: str
    setup: str            # trend_pullback | breakout | mean_reversion
    score: float          # 0..1 rule-based conviction
    price: float          # reference price at signal time
    atr: float
    stop_atr: float       # default stop distance in ATRs (the AI may adjust within bounds)
    target_atr: float     # default take-profit distance in ATRs
    rules: list[str] = field(default_factory=list)

    def dict(self) -> dict:
        return asdict(self)


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# Research (scripts/research.py, 28 days, 10 TMN markets, ~0.75% round-trip cost): breakouts and
# trend pullbacks had no gross edge and lost after costs both in- and out-of-sample, so they are
# off by default. Oversold mean reversion and dips inside an up-trend showed a gross edge of
# roughly +0.4..0.6% per trade with wide (about 3 ATR) stops/targets.
DEFAULT_SETUPS = ("oversold_reversion", "dip_in_uptrend")
ALL_SETUPS = DEFAULT_SETUPS + ("trend_pullback", "breakout", "mean_reversion")


def candidates(symbol: str, f: dict, min_atr_pct: float, max_atr_pct: float,
               setups: tuple[str, ...] = DEFAULT_SETUPS, dip_atr: float = 0.0) -> list[Candidate]:
    """Evaluate the enabled setups on one feature snapshot. `dip_atr` > 0 sizes the dip_in_uptrend pullback in ATRs
    (a 1-hour fall of more than dip_atr x the 15m ATR) instead of a fixed -1.5%, which calm USDT majors (ATR ~0.1-0.2%)
    almost never reach."""
    out: list[Candidate] = []
    if not f or not (min_atr_pct <= f["atr_pct"] <= max_atr_pct):
        return out  # too quiet to beat fees, or too wild to size safely
    c, a = f["close"], f["atr"]
    rsi_v, rsi_p = f["rsi"] or 50, f["rsi_prev"] or 50
    adx_v = f["adx"] or 0

    # A) Oversold reversion: stretched below the lower Bollinger band with RSI < 30 while the 1h
    #    trend is not down, or a capitulation-level RSI < 25 in any trend.
    if "oversold_reversion" in setups and c < f["bb_lower"] and (
            (rsi_v < 30 and f["htf_trend"] != "down") or rsi_v < 25):
        stretch = (f["bb_lower"] - c) / a if a else 0
        score = 0.45 + 0.25 * _clip((30 - rsi_v) / 12) + 0.15 * _clip(stretch) + 0.15 * (f["htf_trend"] == "up")
        out.append(Candidate(symbol, "oversold_reversion", round(score, 3), c, a, 2.5, 3.5, [
            f"close {c:.6g} below lower Bollinger band {f['bb_lower']:.6g} ({stretch:.2f} ATR stretch)",
            f"RSI {rsi_v:.1f} {'< 25 (capitulation)' if rsi_v < 25 else '< 30'}",
            f"1h trend {f['htf_trend']}",
        ]))

    # B) Dip in an up-trend: the 1h trend is up but price fell >1.5% over the last 4 bars.
    dip = -(dip_atr * f["atr_pct"]) if dip_atr > 0 else -1.5
    if "dip_in_uptrend" in setups and f["htf_trend"] == "up" and f["ret_4"] < dip and rsi_v < 40:
        score = 0.45 + 0.25 * _clip(f["ret_4"] / (4 * dip / 1.5)) + 0.15 * _clip((40 - rsi_v) / 15) + 0.15 * _clip(f["htf_ret_24"] / 5)
        out.append(Candidate(symbol, "dip_in_uptrend", round(score, 3), c, a, 2.5, 3.5, [
            f"1h trend up (EMA20 {f['htf_ema20']:.6g} > EMA50 {f['htf_ema50']:.6g}, 24h {f['htf_ret_24']:+.2f}%)",
            f"pulled back {f['ret_4']:+.2f}% over the last 4 bars (threshold {dip:.2f}%)",
            f"RSI {rsi_v:.1f} < 40",
        ]))

    # --- disabled by default (no edge after costs in research) ---
    # 1) Trend pullback: higher timeframe up-trend, short-term dip that is turning back up.
    if "trend_pullback" in setups and f["htf_trend"] == "up" and c > f["ema50"] and rsi_p < 42 and rsi_v > rsi_p and c >= f["ema20"] * 0.995:
        score = 0.45 + 0.2 * _clip((42 - rsi_p) / 15) + 0.2 * _clip(adx_v / 35) + 0.15 * _clip(f["htf_ret_24"] / 4)
        out.append(Candidate(symbol, "trend_pullback", round(score, 3), c, a, 1.5, 3.0, [
            f"1h trend up (EMA20 {f['htf_ema20']:.6g} > EMA50 {f['htf_ema50']:.6g})",
            f"price {c:.6g} above 15m EMA50 {f['ema50']:.6g}",
            f"RSI dipped to {rsi_p:.1f} and turned up to {rsi_v:.1f}",
        ]))

    # 2) Breakout: close above the 20-bar high on expanding volume, trend not down.
    if "breakout" in setups and c > f["donchian_high"] and f["volume_z"] > 1.0 and f["htf_trend"] != "down" and rsi_v < 78:
        score = 0.4 + 0.25 * _clip(f["volume_z"] / 3) + 0.2 * _clip(adx_v / 35) + 0.15 * (f["htf_trend"] == "up")
        out.append(Candidate(symbol, "breakout", round(score, 3), c, a, 1.3, 2.6, [
            f"close {c:.6g} broke 20-bar high {f['donchian_high']:.6g}",
            f"volume z-score {f['volume_z']:.2f} (>1)",
            f"1h trend {f['htf_trend']}, RSI {rsi_v:.1f} not exhausted",
        ]))

    # 3) Mean reversion: ranging market (low ADX), stretched below the lower band, RSI oversold.
    if "mean_reversion" in setups and adx_v < 22 and c < f["bb_lower"] and rsi_v < 30 and f["htf_trend"] != "down":
        stretch = (f["bb_lower"] - c) / a if a else 0
        score = 0.4 + 0.25 * _clip((30 - rsi_v) / 12) + 0.2 * _clip(stretch) + 0.15 * _clip((22 - adx_v) / 10)
        target = max((f["bb_mid"] - c) / a, 1.5) if a else 2.0
        out.append(Candidate(symbol, "mean_reversion", round(score, 3), c, a, 1.2, round(min(target, 3.0), 2), [
            f"range regime: ADX {adx_v:.1f} < 22",
            f"close {c:.6g} below lower Bollinger band {f['bb_lower']:.6g}",
            f"RSI {rsi_v:.1f} oversold; target the band middle {f['bb_mid']:.6g}",
        ]))
    return out

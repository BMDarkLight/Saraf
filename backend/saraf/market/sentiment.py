"""Fear & Greed / risk indices.

- Global crypto Fear & Greed from alternative.me.
- "Market Pulse": a local fear/greed index computed from a venue's own market breadth.
- Per-market risk score from volatility, liquidity and spread.
"""

from __future__ import annotations

import time

import httpx

from ..venues import Venue
from .analytics import market_overview, order_book_analysis, technical_analysis

_fng_cache: tuple[float, dict] | None = None


def classify(v: float) -> str:
    if v < 25:
        return "Extreme Fear"
    if v < 45:
        return "Fear"
    if v <= 55:
        return "Neutral"
    if v <= 75:
        return "Greed"
    return "Extreme Greed"


async def global_fear_greed(days: int = 7) -> dict:
    global _fng_cache
    if _fng_cache and _fng_cache[0] > time.monotonic():
        return _fng_cache[1]
    async with httpx.AsyncClient(timeout=10) as http:
        r = await http.get("https://api.alternative.me/fng/", params={"limit": max(days, 1)})
        data = r.json()["data"]
    out = {
        "source": "alternative.me (global crypto market)",
        "value": int(data[0]["value"]), "classification": data[0]["value_classification"],
        "history": [{"date": time.strftime("%Y-%m-%d", time.gmtime(int(d["timestamp"]))), "value": int(d["value"])}
                    for d in data],
    }
    _fng_cache = (time.monotonic() + 600, out)
    return out


async def market_pulse(venue: Venue, quote: str = "USDT") -> dict:
    """0-100 fear/greed index from the venue's own markets (top 50 by volume)."""
    rows = await market_overview(venue, quote=quote, sort_by="volume", limit=50)
    rows = [r for r in rows if r["last"]]
    if not rows:
        return {"error": "no market data"}
    total_vol = sum(r["quote_volume_24h"] for r in rows) or 1
    breadth = sum(1 for r in rows if r["change_24h"] > 0) / len(rows)  # 0..1
    vw_change = sum(r["change_24h"] * r["quote_volume_24h"] for r in rows) / total_vol
    momentum = min(max((vw_change + 10) / 20, 0), 1)  # -10%..+10% -> 0..1
    pressures = [r["buy_pressure_pct"] for r in rows if r["buy_pressure_pct"] is not None]
    buy_pressure = (sum(pressures) / len(pressures) / 100) if pressures else 0.5
    wk = sum(r["change_7d"] * r["quote_volume_24h"] for r in rows) / total_vol
    trend = min(max((wk + 20) / 40, 0), 1)
    score = round(100 * (0.35 * breadth + 0.30 * momentum + 0.20 * buy_pressure + 0.15 * trend))
    return {
        "source": f"Market Pulse ({quote} markets, computed locally)", "value": score,
        "classification": classify(score),
        "components": {
            "breadth_pct_markets_up_24h": round(100 * breadth, 1),
            "volume_weighted_change_24h_pct": round(vw_change, 2),
            "avg_buy_order_share_pct": round(100 * buy_pressure, 1),
            "volume_weighted_change_7d_pct": round(wk, 2),
        },
        "weights": {"breadth": 0.35, "momentum": 0.30, "buy_pressure": 0.20, "weekly_trend": 0.15},
    }


async def market_risk(venue: Venue, symbol: str) -> dict:
    """0 (low) - 100 (high) risk score for a single market."""
    ta = await technical_analysis(venue, symbol, "60", 200)
    ob = await order_book_analysis(venue, symbol)
    if "error" in ta or "error" in ob:
        return {"symbol": symbol.upper(), "error": ta.get("error") or ob.get("error")}
    atr_pct = ta["atr_pct"] or 0
    vol_score = min(atr_pct / 3, 1)                     # 3%/hour ATR -> max
    spread_score = min(ob["spread_pct"] / 1.0, 1)        # 1% spread -> max
    depth_1pct = ob["bands"]["1%"]["bid_value"] + ob["bands"]["1%"]["ask_value"]
    m = (await venue.markets()).get(symbol.upper(), {})
    quote = m.get("quoteAsset", "USDT")
    ref_depth = 50_000 if quote == "USDT" else 5_000_000_000
    liq_score = 1 - min(depth_1pct / ref_depth, 1)
    rsi_v = ta["rsi14"] or 50
    extreme_score = min(abs(rsi_v - 50) / 30, 1)
    score = round(100 * (0.4 * vol_score + 0.25 * liq_score + 0.2 * spread_score + 0.15 * extreme_score))
    level = "low" if score < 30 else "medium" if score < 55 else "high" if score < 75 else "very high"
    return {
        "symbol": symbol.upper(), "risk_score": score, "level": level,
        "factors": {"hourly_atr_pct": atr_pct, "spread_pct": ob["spread_pct"],
                    "liquidity_within_1pct": depth_1pct, "rsi14": rsi_v, "trend": ta["trend"]},
        "suggested_max_leverage": 1 if score >= 75 else 2 if score >= 55 else 5 if score >= 30 else 10,
        "suggested_stop_distance_pct": round(max(1.5 * atr_pct, ob["spread_pct"] * 3, 0.3), 2),
    }

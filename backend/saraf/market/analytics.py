"""Market analytics on any venue's data: movers, order book depth, technicals."""

from __future__ import annotations

import asyncio
import math
import time

from ..venues import Venue


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


async def market_overview(venue: Venue, quote: str | None = None, sort_by: str = "volume",
                          limit: int = 20, search: str | None = None) -> list[dict]:
    """Snapshot of spot markets from /v1/markets, sortable by volume/change."""
    rows = []
    for m in (await venue.markets()).values():
        if quote and m["quoteAsset"] != quote.upper():
            continue
        if search:
            s = search.lower()
            if s not in m["symbol"].lower() and s not in (m.get("faBaseAsset") or "") and s not in (m.get("enBaseAsset") or "").lower():
                continue
        st = m.get("stats") or {}
        rows.append({
            "symbol": m["symbol"], "name": m.get("enBaseAsset") or m["baseAsset"], "fa_name": m.get("faBaseAsset"),
            "quote": m["quoteAsset"], "last": _f(st.get("lastPrice")), "bid": _f(st.get("bidPrice")),
            "ask": _f(st.get("askPrice")), "change_24h": _f(st.get("24h_ch")), "change_7d": _f(st.get("7d_ch")),
            "quote_volume_24h": _f(st.get("24h_quoteVolume")), "high_24h": _f(st.get("24h_highPrice")),
            "low_24h": _f(st.get("24h_lowPrice")),
            "buy_pressure_pct": (st.get("direction") or {}).get("BUY"),
        })
    key = {"volume": lambda r: r["quote_volume_24h"], "change": lambda r: r["change_24h"],
           "change_7d": lambda r: r["change_7d"]}.get(sort_by, lambda r: r["quote_volume_24h"])
    reverse = not sort_by.startswith("-")
    if sort_by.startswith("-"):
        key = {"-change": lambda r: r["change_24h"], "-change_7d": lambda r: r["change_7d"]}.get(sort_by, key)
    rows.sort(key=key, reverse=reverse)
    return rows[:limit]


_movers_cache: dict[tuple, tuple[float, list]] = {}


async def top_movers(venue: Venue, hours: int = 4, quote: str = "USDT", limit: int = 10,
                     direction: str = "gainers", universe: int = 60) -> list[dict]:
    """Best/worst performing markets over the last `hours` (1-72), computed from hourly candles.

    To stay fast, only the `universe` most liquid markets (by 24h quote volume) are scanned.
    """
    hours = max(1, min(int(hours), 72))
    ck = (hours, quote.upper(), universe)
    hit = _movers_cache.get(ck)
    if hit and hit[0] > time.monotonic():
        rows = hit[1]
    else:
        candidates = await market_overview(venue, quote=quote, sort_by="volume", limit=universe)
        now = int(time.time())
        start = now - hours * 3600 - 3600
        sem = asyncio.Semaphore(12)

        async def scan(m: dict):
            async with sem:
                try:
                    cs = await venue.candles(m["symbol"], "60", start, now)
                except Exception:
                    return None
            cutoff = now - hours * 3600
            window = [c for c in cs if c["t"] >= cutoff - 3599]
            if len(window) < 1 or not window[0]["o"]:
                return None
            open_, close = window[0]["o"], window[-1]["c"]
            hi, lo = max(c["h"] for c in window), min(c["l"] for c in window)
            vol = sum(c["v"] * c["c"] for c in window)
            return {"symbol": m["symbol"], "name": m["name"], "fa_name": m["fa_name"],
                    "change_pct": round(100 * (close / open_ - 1), 3), "open": open_, "last": close,
                    "high": hi, "low": lo, "range_pct": round(100 * (hi / lo - 1), 3) if lo else None,
                    "quote_volume_window": vol, "change_24h": m["change_24h"]}

        rows = [r for r in await asyncio.gather(*(scan(m) for m in candidates)) if r]
        _movers_cache[ck] = (time.monotonic() + 60, rows)
    rows = sorted(rows, key=lambda r: r["change_pct"], reverse=direction != "losers")
    return rows[:limit]


async def order_book_analysis(venue: Venue, symbol: str, levels: int = 10) -> dict:
    """Order book depth: spread, liquidity bands, imbalance and walls."""
    book = await venue.depth(symbol)
    asks = [(_f(l["price"]), _f(l["quantity"])) for l in book.get("ask", [])]
    bids = [(_f(l["price"]), _f(l["quantity"])) for l in book.get("bid", [])]
    asks.sort(key=lambda x: x[0])
    bids.sort(key=lambda x: -x[0])
    if not asks or not bids:
        return {"symbol": symbol.upper(), "error": "empty order book"}
    best_ask, best_bid = asks[0][0], bids[0][0]
    mid = (best_ask + best_bid) / 2

    def band(pct: float) -> dict:
        b = sum(p * q for p, q in bids if p >= mid * (1 - pct / 100))
        a = sum(p * q for p, q in asks if p <= mid * (1 + pct / 100))
        return {"bid_value": b, "ask_value": a,
                "imbalance": round((b - a) / (b + a), 3) if a + b else 0.0}

    def walls(side):
        if not side:
            return []
        values = [p * q for p, q in side]
        avg = sum(values) / len(values)
        return [{"price": p, "quantity": q, "value": p * q, "x_avg": round(p * q / avg, 1)}
                for (p, q), v in zip(side, values) if v > 3 * avg][:3]

    total_b = sum(p * q for p, q in bids)
    total_a = sum(p * q for p, q in asks)
    return {
        "symbol": symbol.upper(), "best_bid": best_bid, "best_ask": best_ask, "mid": mid,
        "spread": best_ask - best_bid, "spread_pct": round(100 * (best_ask - best_bid) / mid, 4),
        "bands": {"0.5%": band(0.5), "1%": band(1), "2%": band(2), "5%": band(5)},
        "total_bid_value": total_b, "total_ask_value": total_a,
        "book_imbalance": round((total_b - total_a) / (total_b + total_a), 3) if total_a + total_b else 0,
        "bid_walls": walls(bids), "ask_walls": walls(asks),
        "top_bids": [{"price": p, "quantity": q} for p, q in bids[:levels]],
        "top_asks": [{"price": p, "quantity": q} for p, q in asks[:levels]],
        "interpretation_hint": "imbalance > 0 means more resting buy liquidity than sell (bid-heavy)",
    }


# ---------------------------------------------------------------- technicals
def ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) <= period:
        return None
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(d, 0)) / period
        al = (al * (period - 1) + max(-d, 0)) / period
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def atr(candles: list[dict], period: int = 14) -> float | None:
    if len(candles) <= period:
        return None
    trs = [max(c["h"] - c["l"], abs(c["h"] - p["c"]), abs(c["l"] - p["c"])) for p, c in zip(candles, candles[1:])]
    a = sum(trs[:period]) / period
    for tr in trs[period:]:
        a = (a * (period - 1) + tr) / period
    return a


RES_SECONDS = {"1": 60, "60": 3600, "180": 10800, "360": 21600, "720": 43200, "1D": 86400}


async def technical_analysis(venue: Venue, symbol: str, resolution: str = "60", bars: int = 200) -> dict:
    now = int(time.time())
    cs = await venue.candles(symbol, resolution, now - RES_SECONDS[resolution] * bars, now)
    if len(cs) < 30:
        return {"symbol": symbol.upper(), "error": f"not enough candles ({len(cs)}) for analysis"}
    closes = [c["c"] for c in cs]
    e20, e50 = ema(closes, 20), ema(closes, 50)
    e12, e26 = ema(closes, 12), ema(closes, 26)
    macd = [a - b for a, b in zip(e12, e26)]
    signal = ema(macd, 9)
    last = closes[-1]
    r = rsi(closes)
    a = atr(cs)
    window = closes[-20:]
    mean = sum(window) / len(window)
    sd = math.sqrt(sum((x - mean) ** 2 for x in window) / len(window))
    recent = cs[-50:]
    trend = "up" if e20[-1] > e50[-1] and last > e20[-1] else "down" if e20[-1] < e50[-1] and last < e20[-1] else "sideways"
    return {
        "symbol": symbol.upper(), "resolution": resolution, "bars": len(cs), "last": last,
        "change_over_window_pct": round(100 * (last / closes[0] - 1), 2),
        "ema20": e20[-1], "ema50": e50[-1], "trend": trend,
        "rsi14": round(r, 2) if r is not None else None,
        "rsi_state": None if r is None else "overbought" if r > 70 else "oversold" if r < 30 else "neutral",
        "macd": macd[-1], "macd_signal": signal[-1], "macd_histogram": macd[-1] - signal[-1],
        "atr14": a, "atr_pct": round(100 * a / last, 3) if a else None,
        "bollinger": {"upper": mean + 2 * sd, "middle": mean, "lower": mean - 2 * sd},
        "support": min(c["l"] for c in recent), "resistance": max(c["h"] for c in recent),
    }

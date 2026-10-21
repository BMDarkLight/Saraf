"""Bars and indicators. Pure functions on closed candles only, so backtest, shadow and live
compute exactly the same numbers from the same data (no look-ahead)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..market.analytics import atr, ema, rsi


@dataclass(frozen=True)
class Bar:
    t: int      # open time (unix seconds)
    o: float
    h: float
    l: float
    c: float
    v: float    # base volume


def to_bars(raw: list[dict]) -> list[Bar]:
    return [Bar(int(c["t"]), float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"]), float(c["v"])) for c in raw]


def resample(bars: list[Bar], minutes: int) -> list[Bar]:
    """Aggregate (1m) bars into `minutes` buckets aligned to the epoch. Incomplete trailing
    buckets are kept; callers drop bars that are not closed yet."""
    step = minutes * 60
    out: list[Bar] = []
    for b in bars:
        start = b.t - b.t % step
        if out and out[-1].t == start:
            p = out[-1]
            out[-1] = Bar(p.t, p.o, max(p.h, b.h), min(p.l, b.l), b.c, p.v + b.v)
        else:
            out.append(Bar(start, b.o, b.h, b.l, b.c, b.v))
    return out


def closed(bars: list[Bar], minutes: int, now: float) -> list[Bar]:
    return [b for b in bars if b.t + minutes * 60 <= now]


def adx(bars: list[Bar], period: int = 14) -> float | None:
    if len(bars) < 2 * period + 1:
        return None
    tr, pdm, ndm = [], [], []
    for p, c in zip(bars, bars[1:]):
        up, down = c.h - p.h, p.l - c.l
        pdm.append(up if up > down and up > 0 else 0.0)
        ndm.append(down if down > up and down > 0 else 0.0)
        tr.append(max(c.h - c.l, abs(c.h - p.c), abs(c.l - p.c)))

    def wilder(xs):
        s = sum(xs[:period])
        out = [s]
        for x in xs[period:]:
            s = s - s / period + x
            out.append(s)
        return out

    atr_s, p_s, n_s = wilder(tr), wilder(pdm), wilder(ndm)
    dx = []
    for a, p, n in zip(atr_s, p_s, n_s):
        if a == 0:
            dx.append(0.0)
            continue
        pdi, ndi = 100 * p / a, 100 * n / a
        dx.append(100 * abs(pdi - ndi) / (pdi + ndi) if pdi + ndi else 0.0)
    val = sum(dx[:period]) / period
    for d in dx[period:]:
        val = (val * (period - 1) + d) / period
    return val


def compute(bars: list[Bar], htf: list[Bar]) -> dict | None:
    """Feature snapshot from closed signal-timeframe bars (`bars`) and higher-timeframe bars (`htf`)."""
    if len(bars) < 60 or len(htf) < 55:
        return None
    closes = [b.c for b in bars]
    last = bars[-1]
    e20, e50 = ema(closes, 20), ema(closes, 50)
    a = atr([{"h": b.h, "l": b.l, "c": b.c} for b in bars], 14) or 0.0
    window = closes[-20:]
    mid = sum(window) / 20
    sd = math.sqrt(sum((x - mid) ** 2 for x in window) / 20)
    vols = [b.v for b in bars[-21:-1]]
    vmean = sum(vols) / len(vols) if vols else 0.0
    vstd = math.sqrt(sum((v - vmean) ** 2 for v in vols) / len(vols)) if vols else 0.0
    prior = bars[-21:-1]
    hcl = [b.c for b in htf]
    h20, h50 = ema(hcl, 20), ema(hcl, 50)
    r_now, r_prev = rsi(closes), rsi(closes[:-1])
    return {
        "time": last.t, "close": last.c,
        "ema20": e20[-1], "ema50": e50[-1], "ema20_slope_pct": 100 * (e20[-1] / e20[-5] - 1),
        "rsi": r_now, "rsi_prev": r_prev,
        "atr": a, "atr_pct": 100 * a / last.c if last.c else 0.0,
        "bb_upper": mid + 2 * sd, "bb_mid": mid, "bb_lower": mid - 2 * sd,
        "bb_width_pct": 100 * 4 * sd / mid if mid else 0.0,
        "donchian_high": max(b.h for b in prior), "donchian_low": min(b.l for b in prior),
        "volume_z": (last.v - vmean) / vstd if vstd else 0.0,
        "adx": adx(bars[-60:]),
        "ret_1": 100 * (last.c / bars[-2].c - 1),
        "ret_4": 100 * (last.c / bars[-5].c - 1),
        "ret_16": 100 * (last.c / bars[-17].c - 1),
        "htf_close": hcl[-1], "htf_ema20": h20[-1], "htf_ema50": h50[-1],
        "htf_trend": "up" if h20[-1] > h50[-1] and hcl[-1] > h50[-1] else
                     "down" if h20[-1] < h50[-1] and hcl[-1] < h50[-1] else "flat",
        "htf_rsi": rsi(hcl),
        "htf_ret_24": 100 * (hcl[-1] / hcl[-25] - 1) if len(hcl) > 25 else 0.0,
    }

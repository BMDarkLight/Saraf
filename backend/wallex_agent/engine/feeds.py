"""Market data feeds. LiveFeed reads Wallex in real time; SimFeed replays recorded history
with a moving clock. The engine only sees this interface, so its logic is identical in
backtest, shadow and live runs."""

from __future__ import annotations

import asyncio
import bisect
import json
import time
from pathlib import Path

from ..brokers.base import safe_float
from ..market.analytics import order_book_analysis
from ..market.sentiment import global_fear_greed
from ..news.web import crypto_news
from ..wallex import WallexClient
from .features import Bar, closed, resample, to_bars

DEFAULT_EXCLUDE = {"USDTTMN", "USDCTMN", "DAITMN", "XAUTTMN", "PAXGTMN", "USOONTMN", "COPXONTMN", "SLVONTMN"}


def _symbols(csv_text: str) -> list[str]:
    return [s.strip().upper() for s in csv_text.split(",") if s.strip()]


class LiveFeed:
    def __init__(self, client: WallexClient, settings):
        self.client, self.s = client, settings
        self.tf = settings.engine_timeframe_min
        self._news: dict[str, tuple[float, list[str]]] = {}
        self._ctx: tuple[float, dict] | None = None

    def now(self) -> float:
        return time.time()

    async def universe(self) -> list[str]:
        if self.s.engine_universe != "auto":
            syms = _symbols(self.s.engine_universe)
        else:
            rows = []
            for sym, m in (await self.client.markets()).items():
                if m["quoteAsset"] != "TMN" or sym in DEFAULT_EXCLUDE:
                    continue
                st = m.get("stats") or {}
                bid, ask = safe_float(st.get("bidPrice")), safe_float(st.get("askPrice"))
                if not bid or not ask or 100 * (ask - bid) / ((ask + bid) / 2) > self.s.engine_max_spread_pct:
                    continue
                rows.append((safe_float(st.get("24h_quoteVolume")), sym))
            syms = [s for _, s in sorted(rows, reverse=True)[: self.s.engine_universe_size]]
        allowed = set(_symbols(self.s.engine_allowed_markets))
        return [s for s in syms if not allowed or s in allowed]

    async def bars(self, symbol: str) -> tuple[list[Bar], list[Bar]]:
        now = int(self.now())
        m1 = to_bars(await self.client.candles(symbol, "1", now - 86_340, now))
        h1 = to_bars(await self.client.candles(symbol, "60", now - 6 * 86_400, now))
        return closed(resample(m1, self.tf), self.tf, now), closed(h1, 60, now)

    async def quote(self, symbol: str) -> dict:
        st = (await self.client.markets())[symbol]["stats"]
        bid, ask, last = (safe_float(st.get(k)) for k in ("bidPrice", "askPrice", "lastPrice"))
        return {"bid": bid or last, "ask": ask or last, "last": last}

    async def bar_range(self, symbol: str) -> dict:
        q = await self.quote(symbol)
        return {"low": q["bid"], "high": q["bid"], "open": q["bid"]}

    async def book(self, symbol: str) -> dict:
        ob = await order_book_analysis(self.client, symbol, 5)
        if "error" in ob:
            return {"error": ob["error"]}
        b1 = ob["bands"]["1%"]
        return {"spread_pct": ob["spread_pct"], "imbalance_1pct": b1["imbalance"],
                "bid_depth_1pct_tmn": round(b1["bid_value"]), "ask_depth_1pct_tmn": round(b1["ask_value"])}

    async def market_context(self) -> dict:
        if self._ctx and self._ctx[0] > time.monotonic():
            return self._ctx[1]
        rows = [m for s, m in (await self.client.markets()).items() if m["quoteAsset"] == "TMN" and s not in DEFAULT_EXCLUDE]
        changes = [(safe_float(m["stats"].get("24h_ch")), safe_float(m["stats"].get("24h_quoteVolume"))) for m in rows]
        changes = [c for c in changes if c[1] > 0]
        vol = sum(v for _, v in changes) or 1
        ctx = {"breadth_pct_up_24h": round(100 * sum(1 for c, _ in changes if c > 0) / max(len(changes), 1), 1),
               "volume_weighted_change_24h_pct": round(sum(c * v for c, v in changes) / vol, 2)}
        try:
            fg = await global_fear_greed(3)
            ctx["fear_greed"] = {"value": fg["value"], "label": fg["classification"]}
        except Exception:
            pass
        self._ctx = (time.monotonic() + 300, ctx)
        return ctx

    async def news(self, symbol: str) -> list[str]:
        base = symbol.removesuffix("TMN")
        hit = self._news.get(base)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        names = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "xrp", "DOGE": "dogecoin",
                 "BNB": "bnb", "TRX": "tron", "PEPE": "pepe", "ZEC": "zcash", "TON": "toncoin"}
        try:
            items = await asyncio.wait_for(crypto_news(names.get(base, base.lower()), 5, 24), 20)
            heads = [f"{i['source']}: {i['title']}" for i in items]
        except Exception:
            heads = []
        self._news[base] = (time.monotonic() + 1800, heads)
        return heads

    async def min_notional(self, symbol: str) -> float:
        return safe_float((await self.client.markets())[symbol].get("minNotional")) or 50_000


class SimFeed:
    """Replays 1-minute history. `t` is the current simulated time; only bars that closed at or
    before `t` are visible (no look-ahead)."""

    def __init__(self, m1: dict[str, list[Bar]], h1: dict[str, list[Bar]], tf: int,
                 spreads: dict[str, float], fng: dict[str, int] | None = None):
        self.m1, self.h1, self.tf = m1, h1, tf
        self.tfb = {s: resample(b, tf) for s, b in m1.items()}
        self._t1 = {s: [b.t for b in bars] for s, bars in m1.items()}
        self._ttf = {s: [b.t for b in bars] for s, bars in self.tfb.items()}
        self._th = {s: [b.t for b in bars] for s, bars in h1.items()}
        self.spreads, self.fng = spreads, fng or {}
        self.t = 0.0

    def now(self) -> float:
        return self.t

    async def universe(self) -> list[str]:
        return list(self.m1)

    def _last_closed(self, times: list[int], width: int) -> int:
        return bisect.bisect_right(times, self.t - width) - 1   # index of the last bar with t + width <= now

    async def bars(self, symbol: str) -> tuple[list[Bar], list[Bar]]:
        i = self._last_closed(self._ttf[symbol], self.tf * 60)
        j = self._last_closed(self._th.get(symbol, []), 3600)
        return self.tfb[symbol][max(0, i - 199): i + 1], self.h1.get(symbol, [])[max(0, j - 149): j + 1]

    def _bar(self, symbol: str) -> Bar | None:
        i = self._last_closed(self._t1[symbol], 60)
        return self.m1[symbol][i] if i >= 0 else None

    async def quote(self, symbol: str) -> dict:
        b = self._bar(symbol)
        c = b.c if b else 0.0
        half = self.spreads.get(symbol, 0.002) / 2
        return {"bid": c * (1 - half), "ask": c * (1 + half), "last": c}

    async def bar_range(self, symbol: str) -> dict:
        b = self._bar(symbol)
        half = self.spreads.get(symbol, 0.002) / 2
        if not b:
            return {"low": 0.0, "high": 0.0, "open": 0.0}
        return {"low": b.l * (1 - half), "high": b.h * (1 - half), "open": b.o * (1 - half)}

    async def book(self, symbol: str) -> dict:
        return {"spread_pct": round(100 * self.spreads.get(symbol, 0.002), 3),
                "note": "historical order book unavailable in backtest"}

    async def market_context(self) -> dict:
        ups, n = 0, 0
        for s in self.h1:
            j = self._last_closed(self._th[s], 3600)
            if j >= 24:
                n += 1
                ups += self.h1[s][j].c > self.h1[s][j - 24].c
        ctx = {"breadth_pct_up_24h": round(100 * ups / n, 1) if n else 50.0}
        day = time.strftime("%Y-%m-%d", time.gmtime(self.t))
        if day in self.fng:
            ctx["fear_greed"] = {"value": self.fng[day]}
        return ctx

    async def news(self, symbol: str) -> list[str]:
        return []   # historical headlines are not reproducible; documented limitation

    async def min_notional(self, symbol: str) -> float:
        return 50_000

    def future_1m(self, symbol: str, start: float, end: float) -> list[Bar]:
        times = self._t1[symbol]
        i, k = bisect.bisect_left(times, start), bisect.bisect_left(times, end)
        return self.m1[symbol][i:k]


async def load_history(client: WallexClient, symbols: list[str], start: int, end: int, cache_dir: Path,
                       warmup_hours: int = 150, tf_minutes: int = 15) -> tuple[dict, dict]:
    """1m bars in day chunks (Wallex returns at most 1440 per call) and 1h bars with warm-up,
    cached on disk so reruns are offline and identical."""
    cache_dir.mkdir(parents=True, exist_ok=True)

    async def cached(name: str, fetch):
        p = cache_dir / f"{name}.json"
        if p.exists():
            return json.loads(p.read_text())
        data = await fetch()
        if not name.endswith("_partial"):   # the current day keeps changing; never cache it
            p.write_text(json.dumps(data))
        return data

    m1: dict[str, list[Bar]] = {}
    h1: dict[str, list[Bar]] = {}
    sem = asyncio.Semaphore(6)
    warm_days = -(-60 * tf_minutes // 1440) + 1   # >= 60 signal bars before `start` so indicators are warm
    day0 = start - start % 86_400 - warm_days * 86_400

    async def one(sym: str):
        rows: list[dict] = []
        d = day0
        while d < end:
            complete = d + 86_400 <= time.time() - 120
            key = f"{sym}_1m_{d}" if complete else f"{sym}_1m_{d}_partial"
            async with sem:
                rows += await cached(key, lambda d=d: client.candles(sym, "1", d, d + 86_340))
            d += 86_400
        hrows: list[dict] = []
        h = start - warmup_hours * 3600
        while h < end:
            hend = min(h + 25 * 86_400, end)
            complete = hend <= time.time() - 3600
            key = f"{sym}_1h_{h}_{hend}" if complete else f"{sym}_1h_{h}_partial"
            async with sem:
                hrows += await cached(key, lambda h=h, hend=hend: client.candles(sym, "60", h, hend))
            h = hend
        m1[sym] = sorted({b.t: b for b in to_bars(rows) if b.t < end}.values(), key=lambda b: b.t)
        h1[sym] = sorted({b.t: b for b in to_bars(hrows) if b.t < end}.values(), key=lambda b: b.t)

    await asyncio.gather(*(one(s) for s in symbols))
    return m1, h1

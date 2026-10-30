"""Binance public market data (data-api.binance.vision, the market-data-only mirror), USDT markets only.

A global reference price: comparing it with an Iranian venue's X/USDT market shows the local premium or discount.
No account or trading."""

from __future__ import annotations

import time
from typing import Any

import httpx

from ..base import Capabilities, VenueError
from ..http import HttpVenue, decimals_of

BASE_URL = "https://data-api.binance.vision/api/v3"
INTERVALS = {"1": "1m", "5": "5m", "15": "15m", "30": "30m", "60": "1h", "240": "4h", "360": "6h", "720": "12h",
             "1D": "1d"}


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


class BinanceClient(HttpVenue):
    name = "binance"
    title = "Binance"
    title_fa = "بایننس"
    capabilities = Capabilities(asset_class="crypto", candles=tuple(INTERVALS))

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(BASE_URL, transport=transport)

    async def _get(self, path: str, params: dict | None = None) -> Any:
        r = await self._send("GET", path, params=params)
        data = self._json(r, path)
        if r.status_code >= 400:
            msg = data.get("msg") if isinstance(data, dict) else None
            raise VenueError(f"Binance: {msg or f'HTTP {r.status_code} from {path}'}", r.status_code, data)
        return data

    async def _info(self) -> dict[str, dict]:
        async def load():
            data = await self._get("/exchangeInfo", {"permissions": "SPOT", "symbolStatus": "TRADING"})
            return {s["symbol"]: s for s in data.get("symbols", []) if s.get("quoteAsset") == "USDT"}
        return await self._cached("info", 3600, load)

    async def markets(self) -> dict[str, dict]:
        return await self._cached("markets", 5, self._load_markets)

    async def _load_markets(self) -> dict[str, dict]:
        info = await self._info()
        out = {}
        for t in await self._get("/ticker/24hr"):
            m = info.get(t.get("symbol"))
            if not m:
                continue
            flt = {f["filterType"]: f for f in m.get("filters", [])}
            lot = flt.get("LOT_SIZE", {})
            out[m["symbol"]] = {
                "symbol": m["symbol"], "baseAsset": m["baseAsset"], "quoteAsset": "USDT", "enBaseAsset": m["baseAsset"],
                "stepSize": decimals_of(lot.get("stepSize", "0.00000001")),
                "tickSize": decimals_of(flt.get("PRICE_FILTER", {}).get("tickSize", "0.01")),
                "minQty": _f(lot.get("minQty")), "minNotional": _f(flt.get("NOTIONAL", {}).get("minNotional")),
                "stats": {"bidPrice": _f(t.get("bidPrice")), "askPrice": _f(t.get("askPrice")),
                          "lastPrice": _f(t.get("lastPrice")), "24h_ch": _f(t.get("priceChangePercent")),
                          "24h_highPrice": _f(t.get("highPrice")), "24h_lowPrice": _f(t.get("lowPrice")),
                          "24h_volume": _f(t.get("volume")), "24h_quoteVolume": _f(t.get("quoteVolume"))},
            }
        return out

    async def depth(self, symbol: str) -> dict:
        data = await self._get("/depth", {"symbol": symbol.upper(), "limit": 50})
        return {side: [{"price": _f(p), "quantity": _f(q)} for p, q in data.get(key) or []]
                for side, key in (("bid", "bids"), ("ask", "asks"))}

    async def trades(self, symbol: str) -> list[dict]:
        rows = await self._get("/trades", {"symbol": symbol.upper(), "limit": 50})
        return [{"symbol": symbol.upper(), "price": _f(t["price"]), "quantity": _f(t["qty"]),
                 "sum": _f(t.get("quoteQty")), "isBuyOrder": not t.get("isBuyerMaker"),
                 "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(_f(t.get("time")) / 1000))}
                for t in reversed(rows)]

    async def candles(self, symbol: str, resolution: str, start: int, end: int | None = None) -> list[dict]:
        if resolution not in INTERVALS:
            raise VenueError(f"Binance: resolution must be one of {list(INTERVALS)}")
        params = {"symbol": symbol.upper(), "interval": INTERVALS[resolution], "startTime": start * 1000, "limit": 1000}
        if end:
            params["endTime"] = end * 1000
        rows = await self._get("/klines", params)
        return [{"t": int(r[0]) // 1000, "o": _f(r[1]), "h": _f(r[2]), "l": _f(r[3]), "c": _f(r[4]), "v": _f(r[5])}
                for r in rows]

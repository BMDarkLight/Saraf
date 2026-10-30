"""Tabdeal (tabdeal.org), read-only. Its public API is Binance-style but partial: exchangeInfo, depth and trades
answer on api1.tabdeal.org, there is no ticker, so prices and 24h stats come from the web app's market list
(api-web.tabdeal.org) and candles from its chart endpoint. IRT markets are already in toman.

Trading needs a key nobody has tested with yet, so it is left out for now."""

from __future__ import annotations

import time
from typing import Any

import httpx

from ..base import Capabilities, VenueError
from ..http import HttpVenue, decimals_of

API = "https://api1.tabdeal.org/r/api/v1"
WEB = "https://api-web.tabdeal.org/r"
RESOLUTIONS = ("1", "5", "15", "30", "60", "240", "1D")


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _filter(m: dict, kind: str) -> dict:
    return next((f for f in m.get("filters", []) if f.get("filterType") == kind), {})


class TabdealClient(HttpVenue):
    name = "tabdeal"
    title = "Tabdeal"
    title_fa = "تبدیل"
    capabilities = Capabilities(asset_class="crypto", candles=RESOLUTIONS)

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(API, transport=transport)

    async def _get(self, url: str, params: dict | None = None) -> Any:
        r = await self._send("GET", url, params=params)
        if r.status_code >= 400:
            raise VenueError(f"Tabdeal: HTTP {r.status_code} from {url}", r.status_code, r.text[:300])
        return self._json(r, url)

    async def _info(self) -> dict[str, dict]:
        """Saraf symbol -> exchangeInfo entry, for markets that trade."""
        async def load():
            out = {}
            for m in await self._get(f"{API}/exchangeInfo"):
                if m.get("status") != "TRADING" or m.get("quoteAsset") not in ("IRT", "USDT"):
                    continue
                out[m["baseAsset"] + ("TMN" if m["quoteAsset"] == "IRT" else "USDT")] = m
            return out
        return await self._cached("info", 600, load)

    async def _market(self, symbol: str) -> dict:
        m = (await self._info()).get(symbol.upper())
        if not m:
            raise VenueError(f"Tabdeal: unknown market {symbol!r}")
        return m

    async def markets(self) -> dict[str, dict]:
        return await self._cached("markets", 5, self._load_markets)

    async def _load_markets(self) -> dict[str, dict]:
        info = await self._info()
        prices = {p["symbol"]: p for p in await self._get(f"{WEB}/plots/market_information/")}
        out = {}
        for symbol, m in info.items():
            p = prices.get(m.get("tabdealSymbol"), {})
            last, opened = _f(p.get("price")), _f(p.get("open"))
            lot = _filter(m, "LOT_SIZE")
            out[symbol] = {
                "symbol": symbol, "baseAsset": m["baseAsset"], "quoteAsset": symbol[len(m["baseAsset"]):],
                "enBaseAsset": m["baseAsset"], "faBaseAsset": str(p.get("symbol_fa", "")).split("-")[0],
                "stepSize": decimals_of(lot.get("stepSize", "0.00000001")),
                "tickSize": decimals_of(_filter(m, "PRICE_FILTER").get("tickSize", "1")),
                "minQty": _f(lot.get("minQty")), "minNotional": _f(_filter(m, "MIN_NOTIONAL").get("minNotional")),
                "stats": {"bidPrice": last, "askPrice": last, "lastPrice": last,
                          "24h_ch": round(100 * (last / opened - 1), 2) if last and opened else 0.0,
                          "24h_highPrice": _f(p.get("high")), "24h_lowPrice": _f(p.get("low")),
                          "24h_volume": _f(p.get("volume"))},
            }
        return out

    async def depth(self, symbol: str) -> dict:
        m = await self._market(symbol)
        data = await self._get(f"{API}/depth", {"symbol": m["symbol"], "limit": 50})
        return {side: [{"price": _f(p), "quantity": _f(q)} for p, q in data.get(key) or []]
                for side, key in (("bid", "bids"), ("ask", "asks"))}

    async def trades(self, symbol: str) -> list[dict]:
        m = await self._market(symbol)
        rows = await self._get(f"{API}/trades", {"symbol": m["symbol"], "limit": 50})
        rows = sorted(rows, key=lambda t: t.get("time", 0), reverse=True)
        # isBuyerMaker: the resting order was the buy, so the taker sold
        return [{"symbol": symbol.upper(), "price": _f(t["price"]), "quantity": _f(t["qty"]),
                 "sum": _f(t.get("quoteQty")), "isBuyOrder": not t.get("isBuyerMaker"),
                 "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(_f(t.get("time")) / 1000))}
                for t in rows]

    async def candles(self, symbol: str, resolution: str, start: int, end: int | None = None) -> list[dict]:
        if resolution not in RESOLUTIONS:
            raise VenueError(f"Tabdeal: resolution must be one of {list(RESOLUTIONS)}")
        m = await self._market(symbol)
        body = await self._get(f"{WEB}/plots/history/", {"symbol": m["tabdealSymbol"], "resolution": resolution,
                                                          "from": start, "to": end or int(time.time())})
        return [{"t": int(c["time"]), "o": _f(c["open"]), "h": _f(c["high"]), "l": _f(c["low"]),
                 "c": _f(c["close"]), "v": _f(c["volume"])} for c in body.get("data") or []]

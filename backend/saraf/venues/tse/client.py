"""Tehran Stock Exchange (TSE / بورس تهران), read-only, from the public TSETMC data service (cdn.tsetmc.com).

Data only, by design. There is no official public trading API for Iranian stock brokers: Mofid's Easy Trader
(ایزی تریدر), like the other brokers' online trading platforms, is a closed web/mobile app with no documented API
for third parties, so order placement raises NotImplementedError instead of scripting a broker's private
endpoints. A licensed broker API (or a broker's FIX gateway) would plug in as `live_spot_broker` here.

TSETMC endpoints used (JSON, no key; field names as published by TSETMC):
  ClosingPrice/GetMarketWatch           every instrument with last/close/yesterday prices and best limits
  BestLimits/{insCode}                  the 5-level order book
  Trade/GetTrade/{insCode}              today's trades
  ClosingPrice/GetClosingPriceDailyList/{insCode}/{n}   daily bars
Prices are in Rial (quote asset IRR); symbols are TSE tickers (نماد), e.g. فولاد, خودرو, شپنا.
TSETMC is often unreachable from outside Iran; errors surface as VenueError like any venue's.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..base import Capabilities, Venue, VenueError

TSETMC = "https://cdn.tsetmc.com/api"
TEHRAN = timezone(timedelta(hours=3, minutes=30))
WATCH_PARAMS = {"market": 0, "paperTypes[0]": 1, "showTraded": "false", "withBestLimits": "true", "hEven": 0,
                "RefID": 0}


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _pct(now: float, before: float) -> float:
    return round(100 * (now / before - 1), 2) if now and before else 0.0


def day_start(d_even: int | str) -> int:
    """TSETMC's dEven (yyyymmdd, Tehran calendar day in Gregorian) -> unix seconds of that day's start."""
    d = datetime.strptime(str(d_even), "%Y%m%d").replace(tzinfo=TEHRAN)
    return int(d.timestamp())


class TseClient(Venue):
    name = "tse"
    title = "Tehran Stock Exchange"
    title_fa = "بورس تهران"
    capabilities = Capabilities(asset_class="equity", candles=("1D",), trading=False, futures=False, account=False)

    def __init__(self, base_url: str = TSETMC, timeout: float = 15.0, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport,
                                       headers={"User-Agent": "saraf/0.1", "Accept": "application/json"})
        self._markets: tuple[float, dict[str, dict]] | None = None
        self._ins: dict[str, str] = {}   # ticker -> insCode

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: dict | None = None) -> Any:
        try:
            r = await self._http.get(path, params=params)
        except httpx.HTTPError as e:
            raise VenueError(f"TSETMC: network error calling {path}: {e}") from e
        if r.status_code >= 400:
            raise VenueError(f"TSETMC: HTTP {r.status_code} from {path}", r.status_code, r.text[:300])
        try:
            return r.json()
        except ValueError:
            raise VenueError(f"TSETMC: non-JSON response from {path}", r.status_code, r.text[:300])

    async def _code(self, symbol: str) -> str:
        if symbol not in self._ins:
            await self.markets()
        try:
            return self._ins[symbol]
        except KeyError:
            raise VenueError(f"unknown TSE ticker {symbol!r}") from None

    # ------------------------------------------------------------------------------------------ market data
    async def markets(self) -> dict[str, dict]:
        if self._markets and self._markets[0] > time.monotonic():
            return self._markets[1]
        rows = (await self._get("/ClosingPrice/GetMarketWatch", WATCH_PARAMS)).get("marketwatch") or []
        out: dict[str, dict] = {}
        for r in rows:
            ticker = str(r.get("lva") or "").strip()
            if not ticker:
                continue
            last, close, prev = _f(r.get("pdv")), _f(r.get("pcl")), _f(r.get("py"))
            best = (r.get("blDs") or [{}])[0]
            bid, ask = _f(best.get("pmd")), _f(best.get("pmo"))
            bq, aq = _f(best.get("qmd")), _f(best.get("qmo"))
            self._ins[ticker] = str(r.get("insCode"))
            out[ticker] = {
                "symbol": ticker, "baseAsset": ticker, "quoteAsset": "IRR", "faBaseAsset": str(r.get("lvc") or ticker),
                "enBaseAsset": ticker, "stepSize": 0, "tickSize": 0, "minQty": 1, "minNotional": 0,
                "stats": {"bidPrice": bid or last, "askPrice": ask or last, "lastPrice": last or close,
                          "24h_ch": _pct(close, prev), "7d_ch": 0.0,
                          "24h_highPrice": _f(r.get("pmx")), "24h_lowPrice": _f(r.get("pmn")),
                          "24h_volume": _f(r.get("qtc")), "24h_quoteVolume": _f(r.get("qtj")),
                          "direction": ({"BUY": round(100 * bq / (bq + aq)), "SELL": round(100 * aq / (bq + aq))}
                                        if bq + aq else {})},
            }
        self._markets = (time.monotonic() + 30, out)
        return out

    async def depth(self, symbol: str) -> dict:
        rows = (await self._get(f"/BestLimits/{await self._code(symbol)}")).get("bestLimits") or []
        rows = sorted(rows, key=lambda r: r.get("number", 0))
        return {"bid": [{"price": _f(r.get("pMeDem")), "quantity": _f(r.get("qTitMeDem"))} for r in rows if _f(r.get("pMeDem"))],
                "ask": [{"price": _f(r.get("pMeOf")), "quantity": _f(r.get("qTitMeOf"))} for r in rows if _f(r.get("pMeOf"))]}

    async def trades(self, symbol: str) -> list[dict]:
        rows = (await self._get(f"/Trade/GetTrade/{await self._code(symbol)}")).get("trade") or []
        rows = sorted((r for r in rows if not r.get("canceled")), key=lambda r: r.get("nTran", 0), reverse=True)
        out = []
        for r in rows:
            p, q, h = _f(r.get("pTran")), _f(r.get("qTitTran")), int(r.get("hEven") or 0)
            out.append({"symbol": symbol, "price": p, "quantity": q, "sum": p * q,
                        "timestamp": f"{h // 10000:02d}:{h // 100 % 100:02d}:{h % 100:02d}"})   # side isn't public
        return out

    async def candles(self, symbol: str, resolution: str, start: int, end: int | None = None) -> list[dict]:
        if resolution != "1D":
            raise VenueError("TSETMC publishes daily bars only: use resolution 1D for TSE tickers")
        end = end or int(time.time())
        n = max(1, min(int((end - start) // 86_400) + 1, 1000))
        rows = (await self._get(f"/ClosingPrice/GetClosingPriceDailyList/{await self._code(symbol)}/{n}")
                ).get("closingPriceDaily") or []
        bars = [{"t": day_start(r["dEven"]), "o": _f(r.get("priceFirst")), "h": _f(r.get("priceMax")),
                 "l": _f(r.get("priceMin")), "c": _f(r.get("pClosing")), "v": _f(r.get("qTotTran5J"))}
                for r in rows if r.get("dEven")]
        return sorted((b for b in bars if start <= b["t"] <= end), key=lambda b: b["t"])

    # ---------------------------------------------------------------------------------------------- orders
    async def place_order(self, *a, **k) -> dict:
        raise NotImplementedError("TSE trading needs a broker API; Mofid's Easy Trader has no official public "
                                  "trading API, so Saraf reads TSE data only")

    def live_spot_broker(self):
        raise NotImplementedError("TSE is a data-only venue in Saraf (no official public broker trading API)")

"""Free-market rate sources: prices only, no order book, trades or candles. Prices are in toman.

  Tetherland  the USDT/toman price (api.tetherland.com/currencies), no key
  Navasan     dollar, euro, coins and gold on the free market (api.navasan.tech), needs a free API key
"""

from __future__ import annotations

from typing import Any

import httpx

from ..base import Capabilities, Credential, VenueError
from ..http import HttpVenue

RATES_ONLY = Capabilities(asset_class="fx", order_book=False, public_trades=False, candles=())

# Navasan item -> (symbol base, Persian name). Unknown items keep their own key, upper-cased.
NAVASAN_ITEMS = {
    "usd_sell": ("USD", "دلار"), "eur": ("EUR", "یورو"), "gbp": ("GBP", "پوند"), "aed_sell": ("AED", "درهم"),
    "try": ("TRY", "لیر"), "cny": ("CNY", "یوان"), "cad": ("CAD", "دلار کانادا"), "aud": ("AUD", "دلار استرالیا"),
    "sekkeh": ("SEKKEH", "سکه امامی"), "bahar": ("BAHAR", "سکه بهار آزادی"), "nim": ("NIM", "نیم سکه"),
    "rob": ("ROB", "ربع سکه"), "gerami": ("GERAMI", "سکه گرمی"), "18ayar": ("GOLD18", "طلای ۱۸ عیار"),
    "abshodeh": ("MESGHAL", "مثقال طلای آب‌شده"),
}


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _rate(symbol: str, base: str, fa: str, price: float, change_pct: float, **extra) -> dict:
    return {"symbol": symbol, "baseAsset": base, "quoteAsset": "TMN", "faBaseAsset": fa, "enBaseAsset": base,
            "stepSize": 0, "tickSize": 0, "minQty": 0.0, "minNotional": 0.0,
            "stats": {"bidPrice": extra.get("bid", price), "askPrice": extra.get("ask", price), "lastPrice": price,
                      "24h_ch": change_pct, **{k: v for k, v in extra.items() if k not in ("bid", "ask")}}}


class _RatesVenue(HttpVenue):
    capabilities = RATES_ONLY

    async def depth(self, symbol: str) -> dict:
        self._no("an order book")

    async def trades(self, symbol: str) -> list[dict]:
        self._no("public trades")

    async def candles(self, symbol: str, resolution: str, start: int, end: int | None = None) -> list[dict]:
        self._no("candles")

    async def _get(self, path: str, params: dict | None = None) -> Any:
        r = await self._send("GET", path, params=params)
        if r.status_code >= 400:
            raise VenueError(f"{self.title}: HTTP {r.status_code} from {path}: {r.text[:120]}", r.status_code)
        return self._json(r, path)


class TetherlandClient(_RatesVenue):
    name = "tetherland"
    title = "Tetherland"
    title_fa = "تترلند"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__("https://api.tetherland.com", transport=transport)

    async def markets(self) -> dict[str, dict]:
        async def load():
            usdt = (await self._get("/currencies"))["data"]["currencies"]["USDT"]
            return {"USDTTMN": _rate("USDTTMN", "USDT", "تتر", _f(usdt.get("price")), _f(usdt.get("diff24d")),
                                     bid=_f(usdt.get("sell_price")), ask=_f(usdt.get("buy_price")),
                                     **{"24h_highPrice": _f(usdt.get("last24hMax")),
                                        "24h_lowPrice": _f(usdt.get("last24hMin"))})}
        return await self._cached("markets", 15, load)


class NavasanClient(_RatesVenue):
    name = "navasan"
    title = "Navasan"
    title_fa = "نوسان"
    credentials = (Credential("navasan_api_key", "API key", "کلید API",
                              help="کلید رایگان از navasan.tech. طرح رایگان تعداد درخواست ماهانه محدودی دارد، برای "
                                   "همین نرخ‌ها ۵ دقیقه نگه داشته می‌شوند."),)

    def __init__(self, api_key: str = "", transport: httpx.AsyncBaseTransport | None = None):
        super().__init__("https://api.navasan.tech", transport=transport)
        self.api_key = api_key

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)

    async def markets(self) -> dict[str, dict]:
        if not self.api_key:
            raise VenueError("Navasan needs an API key; add one under Settings > Connections.")

        async def load():
            data = await self._get("/latest/", {"api_key": self.api_key})
            out = {}
            for item, v in data.items():
                if not isinstance(v, dict) or not _f(v.get("value")):
                    continue
                base, fa = NAVASAN_ITEMS.get(item, (item.upper().replace("_", ""), item))
                price, change = _f(v["value"]), _f(v.get("change"))
                prev = price - change
                out[f"{base}TMN"] = _rate(f"{base}TMN", base, fa, price, round(100 * change / prev, 2) if prev else 0.0)
            return out
        return await self._cached("markets", 300, load)

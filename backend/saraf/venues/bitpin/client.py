"""The Bitpin venue (bitpin.ir), from its public REST API (https://docs.bitpin.ir/v1).

Bitpin's IRT markets are already in toman, so only the symbols change (BTC_IRT -> BTCTMN, BTC_USDT -> BTCUSDT).
Its tickers have no bid, ask or volume: the market list carries the last price in all three, and the order book
gives the real top of book.

Auth: the API key and secret buy a JWT pair at /usr/authenticate/. The access token lasts 15 minutes and is
renewed with the refresh token (30 days); a 401 re-authenticates once.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from ..base import Capabilities, Credential, Order, VenueError
from ..http import HttpVenue, jwt_exp

BASE_URL = "https://api.bitpin.ir"
RESOLUTIONS = {"1": "1m", "5": "5m", "15": "15m", "30": "30m", "60": "1h", "180": "3h", "240": "4h", "360": "6h",
               "1D": "1d"}


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def to_saraf(symbol: str) -> str:
    base, _, quote = symbol.upper().partition("_")
    return base + ("TMN" if quote == "IRT" else quote)


def to_bitpin(symbol: str) -> str:
    s = symbol.upper()
    if "_" in s:
        return s
    for quote, theirs in (("TMN", "IRT"), ("IRT", "IRT"), ("USDT", "USDT")):
        if s.endswith(quote) and len(s) > len(quote):
            return f"{s[: -len(quote)]}_{theirs}"
    raise VenueError(f"Bitpin: unknown market {symbol!r}")


def _error_text(data: Any) -> str | None:
    if isinstance(data, dict):
        if data.get("detail"):
            return str(data["detail"])
        if data.get("message"):
            return str(data["message"])
        parts = [f"{k}: {v[0] if isinstance(v, list) and v else v}" for k, v in data.items()]
        return "; ".join(parts) or None
    if isinstance(data, list) and data:
        return str(data[0])
    return None


class BitpinClient(HttpVenue):
    name = "bitpin"
    title = "Bitpin"
    title_fa = "بیت‌پین"
    capabilities = Capabilities(asset_class="crypto", candles=tuple(RESOLUTIONS), trading=True, account=True)
    credentials = (
        Credential("bitpin_api_key", "API key", "کلید API", secret=False,
                   help="بیت‌پین ← پروفایل ← API. کلید به فهرست IP بسته می‌شود؛ IP همین سرور را اضافه کنید."),
        Credential("bitpin_secret_key", "Secret key", "کلید مخفی"),
    )

    def __init__(self, api_key: str = "", secret_key: str = "", base_url: str = BASE_URL,
                 transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(base_url, transport=transport)
        self.api_key, self.secret_key = api_key, secret_key
        self._access = self._refresh = ""
        self._access_exp = 0.0

    @property
    def has_key(self) -> bool:
        return bool(self.api_key and self.secret_key)

    # ------------------------------------------------------------------ core
    async def _raw(self, method: str, path: str, **kw) -> tuple[httpx.Response, Any]:
        r = await self._send(method, path, **kw)
        if r.status_code == 204 or not r.content:
            return r, None
        return r, self._json(r, path)

    async def _login(self) -> None:
        if not self.has_key:
            raise VenueError("Bitpin: no API key is set; add one under Settings > Connections.")
        if self._refresh and jwt_exp(self._refresh) > time.time() + 60:
            r, data = await self._raw("POST", "/api/v1/usr/refresh_token/", json={"refresh": self._refresh})
            if r.status_code < 400 and isinstance(data, dict) and data.get("access"):
                self._access = data["access"]
                self._access_exp = jwt_exp(self._access) or time.time() + 840
                return
        r, data = await self._raw("POST", "/api/v1/usr/authenticate/",
                                  json={"api_key": self.api_key, "secret_key": self.secret_key})
        if r.status_code >= 400 or not isinstance(data, dict) or not data.get("access"):
            raise VenueError(f"Bitpin: sign-in failed: {_error_text(data) or f'HTTP {r.status_code}'}",
                             r.status_code, data)
        self._access, self._refresh = data["access"], data.get("refresh", "")
        self._access_exp = jwt_exp(self._access) or time.time() + 840

    async def _call(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None,
                    auth: bool = False) -> Any:
        for attempt in (0, 1):
            headers = {}
            if auth:
                if not self._access or self._access_exp < time.time() + 30:
                    await self._login()
                headers["Authorization"] = f"Bearer {self._access}"
            r, data = await self._raw(method, path, params=params, json=body, headers=headers)
            if auth and r.status_code == 401 and attempt == 0:
                self._access = ""
                continue
            if r.status_code >= 400:
                raise VenueError(f"Bitpin: {_error_text(data) or f'HTTP {r.status_code} from {path}'}",
                                 r.status_code, data)
            return data
        raise AssertionError("unreachable")

    # ----------------------------------------------------------- market data
    async def markets(self) -> dict[str, dict]:
        return await self._cached("markets", 5, self._load_markets)

    async def _load_markets(self) -> dict[str, dict]:
        info = await self._cached("info", 600, lambda: self._call("GET", "/api/v1/mkt/markets/"))
        tickers = {t["symbol"]: t for t in await self._call("GET", "/api/v1/mkt/tickers/")}
        out = {}
        for m in info:
            if not m.get("tradable") or m.get("suspended") or m.get("quote") not in ("IRT", "USDT"):
                continue
            t = tickers.get(m["symbol"], {})
            last = _f(t.get("price"))
            symbol = to_saraf(m["symbol"])
            out[symbol] = {
                "symbol": symbol, "baseAsset": m["base"], "quoteAsset": "TMN" if m["quote"] == "IRT" else "USDT",
                "enBaseAsset": m["base"], "stepSize": int(m.get("base_amount_precision") or 0),
                "tickSize": int(m.get("price_precision") or 0), "minQty": 0.0, "minNotional": 0.0,
                "stats": {"bidPrice": last, "askPrice": last, "lastPrice": last,
                          "24h_ch": _f(t.get("daily_change_price")), "24h_highPrice": _f(t.get("high")),
                          "24h_lowPrice": _f(t.get("low"))},
            }
        return out

    async def depth(self, symbol: str) -> dict:
        data = await self._call("GET", f"/api/v1/mth/orderbook/{to_bitpin(symbol)}/")
        return {side: [{"price": _f(p), "quantity": _f(q)} for p, q in data.get(key) or []]
                for side, key in (("bid", "bids"), ("ask", "asks"))}

    async def trades(self, symbol: str) -> list[dict]:
        data = await self._call("GET", f"/api/v1/mth/matches/{to_bitpin(symbol)}/")
        return [{"symbol": symbol.upper(), "price": _f(t["price"]), "quantity": _f(t["base_amount"]),
                 "sum": _f(t.get("quote_amount")), "isBuyOrder": t.get("side") == "buy",
                 "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(_f(t.get("time"))))}
                for t in data]

    async def candles(self, symbol: str, resolution: str, start: int, end: int | None = None) -> list[dict]:
        if resolution not in RESOLUTIONS:
            raise VenueError(f"Bitpin: resolution must be one of {list(RESOLUTIONS)}")
        res = RESOLUTIONS[resolution]
        rows = await self._call("GET", "/api/v1/mkt/candles/", params={
            "symbol": to_bitpin(symbol), "res": res, "from": start, "to": end or int(time.time())})
        # Bitpin answers an unknown resolution with 30m candles instead of an error
        if rows and rows[0].get("resolution") not in (None, res):
            raise VenueError(f"Bitpin: asked for {res} candles, got resolution {rows[0].get('resolution')}")
        return [{"t": int(_f(c["ts"])), "o": _f(c["open"]), "h": _f(c["high"]), "l": _f(c["low"]),
                 "c": _f(c["close"]), "v": _f(c["volume"])} for c in rows]

    # -------------------------------------------------------------- account
    async def balances(self) -> dict[str, dict]:
        rows = await self._call("GET", "/api/v1/wlt/wallets/", params={"service": "main", "limit": 200}, auth=True)
        out = {}
        for w in rows or []:
            asset = str(w.get("asset", "")).upper()
            asset = "TMN" if asset in ("IRT", "RIAL", "TOMAN") else asset
            free, frozen = _f(w.get("balance")), _f(w.get("frozen"))
            out[asset] = {"value": free + frozen, "locked": frozen}
        return out

    def _order(self, o: dict) -> Order:
        filled, spent = _f(o.get("dealed_base_amount")), _f(o.get("dealed_quote_amount"))
        orig = _f(o.get("base_amount"))
        state = o.get("state")
        if state in ("initial", "active"):
            status = "PARTIALLY_FILLED" if filled else "NEW"
        elif orig and filled >= orig - 1e-12:
            status = "FILLED"
        else:
            status = "CANCELED"
        return {"clientOrderId": o.get("identifier") or str(o.get("id")), "symbol": to_saraf(o.get("symbol", "")),
                "side": str(o.get("side", "")).upper(), "type": str(o.get("type", "")).upper(),
                "price": _f(o.get("price")), "origQty": orig, "executedQty": filled, "executedSum": spent,
                "executedPrice": spent / filled if filled else 0.0, "status": status,
                "active": status in ("NEW", "PARTIALLY_FILLED"), "created": o.get("created_at", ""),
                "venueOrderId": o.get("id")}

    async def place_order(self, symbol: str, side: str, type: str, quantity: str, price: str | None = None,
                          client_id: str | None = None) -> dict:
        body: dict[str, Any] = {"symbol": to_bitpin(symbol), "type": type.lower(), "side": side.lower(),
                                "base_amount": quantity}
        if price is not None:
            body["price"] = price
        body["identifier"] = client_id or uuid.uuid4().hex
        return self._order(await self._call("POST", "/api/v1/odr/orders/", body=body, auth=True))

    async def get_order(self, order_id: str) -> dict:
        rows = await self._call("GET", "/api/v1/odr/orders/", params={"identifier": order_id}, auth=True)
        if not rows and str(order_id).isdigit():
            return self._order(await self._call("GET", f"/api/v1/odr/orders/{order_id}/", auth=True))
        if not rows:
            raise VenueError(f"Bitpin: no order {order_id!r}")
        return self._order(rows[0])

    async def cancel_order(self, order_id: str) -> Any:
        return await self._call("DELETE", f"/api/v1/odr/orders/identifier/{order_id}/", auth=True)

    async def open_orders(self, symbol: str | None = None) -> list[dict]:
        rows = await self._call("GET", "/api/v1/odr/orders/", params={
            "state": "initial,active", "symbol": to_bitpin(symbol) if symbol else None}, auth=True)
        return [self._order(o) for o in rows or []]

    async def my_trades(self, symbol: str | None = None, side: str | None = None) -> list[dict]:
        rows = await self._call("GET", "/api/v1/odr/fills/", params={
            "symbol": to_bitpin(symbol) if symbol else None, "side": side.lower() if side else None, "limit": 100},
            auth=True)
        return [{"symbol": to_saraf(t.get("symbol", "")), "price": _f(t.get("price")),
                 "quantity": _f(t.get("base_amount")), "sum": _f(t.get("quote_amount")),
                 "fee": _f(t.get("commission")), "isBuyOrder": t.get("side") == "buy",
                 "timestamp": t.get("created_at", "")} for t in rows or []]

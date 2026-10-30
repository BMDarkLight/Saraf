"""The Wallex venue: an async client for the public Wallex REST API (https://api-docs.wallex.ir).

Covers market data, account, spot orders and OTC (instant) trades. Every method
returns the unwrapped `result` payload of Wallex's `{success, message, result}`
envelope and raises `WallexError` on failure. Its market data is already in
Saraf's shared venue schema (venues/base.py).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import json as json_lib

import httpx

from ..base import Capabilities, Credential, Venue, VenueError

ORDER_ERROR_CODES = {
    1001: "price precision not accepted",
    1002: "quantity precision not accepted",
    1003: "MARKET orders cannot have a price",
    1004: "quantity below the minimum",
    1005: "quantity or total is required",
    1006: "insufficient balance",
    1007: "stop price is required",
    1008: "price is required",
    1009: "stop price precision not accepted",
    1010: "quantity and total must not both be set",
    1011: "total precision not accepted",
}

CANDLE_RESOLUTIONS = {"1", "60", "180", "360", "720", "1D"}


class WallexError(VenueError):
    pass


class _TTLCache:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        hit = self._data.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        return None

    def set(self, key: str, value: Any, ttl: float) -> None:
        self._data[key] = (time.monotonic() + ttl, value)


KEY_WARNING = ("کلید API فعلی مال حساب اصلی است؛ موجودی اعتبار معاملاتی را می‌خواند ولی سفارش‌ها با موجودی "
               "حساب اصلی سنجیده و رد می‌شوند. برای حساب «هکاتون» یک کلید API با دسترسی معامله بسازید.")


class WallexClient(Venue):
    name = "wallex"
    title = "Wallex"
    title_fa = "والکس"
    capabilities = Capabilities(asset_class="crypto", candles=tuple(sorted(CANDLE_RESOLUTIONS)), trading=True,
                                futures=True, account=True)
    credentials = (
        Credential("wallex_api_key", "API key", "کلید API",
                   help="والکس ← حساب کاربری ← API. فقط دسترسی معامله، هرگز برداشت؛ کلید را به IP همین سرور "
                        "محدود کنید."),
        Credential("wallex_sub_account_client_id", "Trade-credit sub-account", "زیرحساب اعتبار معاملاتی",
                   secret=False, required=False,
                   help="clientId حساب اعتبار معاملاتی (از GET /sub-accounts). خالی = حساب اصلی."),
    )

    def __init__(self, base_url: str = "https://api.wallex.ir", api_key: str = "", timeout: float = 15.0,
                 sub_account: str = ""):
        self.api_key = api_key
        # Trade-credit (prop) accounts have their own wallet; account endpoints target it via this header
        self.sub_account = sub_account
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={"User-Agent": "saraf/0.1", "Accept": "application/json"},
        )
        self._cache = _TTLCache()

    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)

    # ------------------------------------------------------------------ core
    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        auth: bool = False,
        raw: bool = False,
        retries: int = 2,
    ) -> Any:
        headers = {}
        if auth:
            if not self.api_key:
                raise WallexError("WALLEX_API_KEY is not set; this endpoint requires an API key.")
            headers["X-API-Key"] = self.api_key
            if self.sub_account:
                headers["sub-account-client-id"] = self.sub_account
        params = {k: v for k, v in (params or {}).items() if v is not None}
        # A POST/DELETE (place or cancel an order) may have reached the exchange even when the reply was lost
        # (read timeout, 502/504): repeating it could place the order twice. Retry those only when the request
        # surely never arrived (no connection) or was refused unprocessed (429).
        idempotent = method.upper() == "GET"

        for attempt in range(retries + 1):
            try:
                r = await self._http.request(method, path, params=params, json=json, headers=headers)
            except httpx.TransportError as e:
                unsent = isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout))
                if attempt < retries and (idempotent or unsent):
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                # httpx timeouts stringify as "": name the exception so the journal says what happened
                raise WallexError(f"network error calling {path} after {attempt + 1} tries: "
                                  f"{type(e).__name__}{f' ({e})' if str(e) else ''}") from e
            retry_codes = (429, 502, 503, 504) if idempotent else (429,)
            if r.status_code in retry_codes and attempt < retries:
                await asyncio.sleep(0.7 * (attempt + 1))
                continue
            break

        try:
            body = r.json()
        except ValueError:
            raise WallexError(f"non-JSON response from {path} (HTTP {r.status_code})", r.status_code, r.text[:300])

        if raw:
            if r.status_code >= 400:
                raise WallexError(f"HTTP {r.status_code} from {path}", r.status_code, body)
            return body

        if r.status_code >= 400 or (isinstance(body, dict) and body.get("success") is False):
            msg = body.get("message") if isinstance(body, dict) else None
            result = body.get("result") if isinstance(body, dict) else None
            code = result.get("error_code") if isinstance(result, dict) else None
            if isinstance(code, (list, tuple)):   # seen live: {"error_code": [1006]} - a list crashed the lookup
                code = code[0] if code else None
            if isinstance(code, str) and code.isdigit():
                code = int(code)
            if isinstance(code, int) and code in ORDER_ERROR_CODES:
                msg = f"{msg} (error_code {code}: {ORDER_ERROR_CODES[code]})"
            elif isinstance(result, dict) and result:   # validation details, e.g. {"quantity": ["..."]}
                msg = f"{msg or 'rejected'}: {json_lib.dumps(result, ensure_ascii=False)[:300]}"
            raise WallexError(msg or f"HTTP {r.status_code} from {path}", r.status_code, body)
        return body.get("result", body) if isinstance(body, dict) else body

    async def _cached(self, key: str, ttl: float, coro_fn) -> Any:
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        value = await coro_fn()
        self._cache.set(key, value, ttl)
        return value

    # ----------------------------------------------------------- market data
    async def markets(self) -> dict[str, dict]:
        """All spot markets with 24h/7d stats, keyed by symbol."""
        res = await self._cached("markets", 5, lambda: self._request("GET", "/v1/markets"))
        return res["symbols"]

    async def markets_detail(self) -> list[dict]:
        """Richer market list used by the Wallex web app (leverage, precision, flags)."""
        res = await self._cached("hector_markets", 30, lambda: self._request("GET", "/hector/web/v1/markets"))
        return res["markets"]

    async def futures_markets(self) -> list[dict]:
        return [{"symbol": m["symbol"], "max_leverage": float(m.get("max_leverage") or 1), "price": m.get("price")}
                for m in await self.markets_detail() if m.get("is_margin")]

    async def coin_stats(self, key: str) -> Any:
        return await self.currencies_stats(key)

    async def currencies_stats(self, key: str) -> Any:
        """Global stats for one coin (the endpoint now requires `key`, unlike the docs)."""
        return await self._cached(f"currency:{key}", 60,
                                  lambda: self._request("GET", "/v1/currencies/stats", params={"key": key.upper()}))

    async def depth(self, symbol: str) -> dict:
        return await self._request("GET", "/v1/depth", params={"symbol": symbol.upper()})

    async def depth_all(self) -> dict:
        return await self._request("GET", "/v2/depth/all")

    async def trades(self, symbol: str) -> list[dict]:
        res = await self._request("GET", "/v1/trades", params={"symbol": symbol.upper()})
        return res["latestTrades"]

    async def candles(self, symbol: str, resolution: str, start: int, end: int | None = None) -> list[dict]:
        """OHLCV candles. resolution: 1 (min), 60, 180, 360, 720 or 1D."""
        if resolution not in CANDLE_RESOLUTIONS:
            raise WallexError(f"resolution must be one of {sorted(CANDLE_RESOLUTIONS)}")
        end = end or int(time.time())
        body = await self._request(
            "GET",
            "/v1/udf/history",
            params={"symbol": symbol.upper(), "resolution": resolution, "from": start, "to": end},
            raw=True,
        )
        if body.get("s") != "ok":
            return []
        return [
            {"t": t, "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": float(v)}
            for t, o, h, l, c, v in zip(body["t"], body["o"], body["h"], body["l"], body["c"], body["v"])
        ]

    # -------------------------------------------------------------- account
    async def key_is_main_account(self) -> bool | None:
        """True when the API key belongs to the main account rather than the configured trade-credit
        sub-account. Such a key reads the sub-account through the header, but Wallex checks ORDERS against
        the key's own account (0 TMN -> error 1006). A key created on the credit account sees that
        balance even without the header; a main-account key sees nothing there."""
        if not self.sub_account:
            return False
        try:
            r = await self._http.get("/v1/account/balances", headers={"X-API-Key": self.api_key})
            own = (r.json().get("result") or {}).get("balances") or {}
        except (httpx.HTTPError, ValueError, AttributeError):
            return None
        if r.status_code != 200:
            return None
        return not any(float(b.get("value") or 0) for b in own.values())

    async def account_warning(self) -> str | None:
        return KEY_WARNING if self.has_key and await self.key_is_main_account() else None

    def explain_order_error(self, message: str, key_flagged: bool) -> str:
        # 1006 = insufficient balance: with a main-account key, orders are checked against the (empty) main account
        if "1006" in message and key_flagged:
            return ("کلید API روی سرور مال حساب اصلی والکس است، نه حساب اعتبار معاملاتی؛ والکس سفارش را با موجودی "
                    "حساب اصلی (صفر) می‌سنجد. در والکس حساب «هکاتون» را انتخاب کنید، برای همان حساب کلید API با "
                    f"دسترسی معامله بسازید و جایگزین کنید. ({message})")
        return message

    def live_spot_broker(self):
        from .brokers import WallexSpotBroker
        return WallexSpotBroker(self)

    def live_futures_broker(self, settings):
        from .brokers import WallexFuturesBroker
        return WallexFuturesBroker(self, settings.wallex_futures_base_url)

    async def profile(self) -> dict:
        return await self._request("GET", "/v1/account/profile", auth=True)

    async def fees(self) -> dict:
        return await self._request("GET", "/v1/account/fee", auth=True)

    async def balances(self) -> dict[str, dict]:
        res = await self._request("GET", "/v1/account/balances", auth=True)
        return res["balances"]

    async def crypto_deposits(self, page: int = 1, per_page: int = 10) -> Any:
        return await self._request("GET", "/v1/account/crypto-deposit", params={"page": page, "per_page": per_page}, auth=True)

    async def crypto_withdrawals(self, page: int = 1, per_page: int = 10) -> Any:
        return await self._request(
            "GET", "/v1/account/crypto-withdrawal", params={"page": page, "per_page": per_page}, auth=True
        )

    # ---------------------------------------------------------------- orders
    async def place_order(
        self,
        symbol: str,
        side: str,
        type: str,
        quantity: str,
        price: str | None = None,
        client_id: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {"symbol": symbol.upper(), "side": side.upper(), "type": type.upper(), "quantity": quantity}
        if price is not None:
            body["price"] = price
        if client_id:
            body["client_id"] = client_id
        return await self._request("POST", "/v1/account/orders", json=body, auth=True)

    async def get_order(self, client_order_id: str) -> dict:
        return await self._request("GET", f"/v1/account/orders/{client_order_id}", auth=True)

    async def cancel_order(self, client_order_id: str) -> Any:
        # the docs (spot-cancel-order): the client order id goes in the path, not the query string
        return await self._request("DELETE", f"/v1/account/orders/{client_order_id}", auth=True)

    async def open_orders(self, symbol: str | None = None) -> list[dict]:
        res = await self._request("GET", "/v1/account/openOrders", params={"symbol": symbol}, auth=True)
        return res.get("orders", [])

    async def my_trades(self, symbol: str | None = None, side: str | None = None) -> list[dict]:
        res = await self._request("GET", "/v1/account/trades", params={"symbol": symbol, "side": side}, auth=True)
        return res.get("AccountLatestTrades", [])

    # ------------------------------------------------------------------- OTC
    async def otc_markets(self) -> dict[str, dict]:
        res = await self._cached("otc_markets", 30, lambda: self._request("GET", "/v1/otc/markets"))
        return res["symbols"]

    async def otc_price(self, symbol: str, side: str) -> dict:
        return await self._request(
            "GET", "/v1/account/otc/price", params={"symbol": symbol.upper(), "side": side.upper()}, auth=True
        )

    async def otc_order(self, symbol: str, side: str, amount: float) -> dict:
        return await self._request(
            "POST",
            "/v1/account/otc/orders",
            json={"symbol": symbol.upper(), "side": side.upper(), "amount": amount},
            auth=True,
        )

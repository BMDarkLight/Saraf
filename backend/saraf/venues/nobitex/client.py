"""The Nobitex venue (nobitex.ir), from its public REST API (https://apidocs.nobitex.ir).

Nobitex quotes its IRT markets in rial (the symbol says IRT, dstCurrency is `rls`). Saraf shows them as toman
markets (BTCIRT -> BTCTMN, prices / 10) like every other venue, and multiplies order prices back by 10. Its UDF
candles are the exception: they already come in toman.

Auth is either an API key signed with Ed25519 (Nobitex-Key / Nobitex-Signature / Nobitex-Timestamp over
timestamp + METHOD + path + body), or the older `Authorization: Token ...` from the web panel.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from ..base import Capabilities, Credential, Order, VenueError
from ..http import HttpVenue, decimals_of, fmt_dec, rial_to_toman

BASE_URL = "https://apiv2.nobitex.ir"
RESOLUTIONS = {"1": "1", "5": "5", "15": "15", "30": "30", "60": "60", "180": "180", "240": "240", "360": "360",
               "720": "720", "1D": "D"}
STATUS = {"New": "NEW", "Active": "NEW", "Done": "FILLED", "Canceled": "CANCELED", "Inactive": "CANCELED"}


def sign(private_key_b64: str, timestamp: str, method: str, path_qs: str, body: str) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw = base64.urlsafe_b64decode(private_key_b64 + "=" * (-len(private_key_b64) % 4))
    key = Ed25519PrivateKey.from_private_bytes(raw)
    sig = key.sign(f"{timestamp}{method.upper()}{path_qs}{body}".encode())
    return base64.urlsafe_b64encode(sig).decode()


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _iso_ms(ms: Any) -> str:
    return datetime.fromtimestamp(_f(ms) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class NobitexClient(HttpVenue):
    name = "nobitex"
    title = "Nobitex"
    title_fa = "نوبیتکس"
    capabilities = Capabilities(asset_class="crypto", candles=tuple(RESOLUTIONS), trading=True, account=True)
    credentials = (
        Credential("nobitex_api_key", "API key", "کلید API", secret=False, required=False,
                   help="پنل نوبیتکس ← کلیدهای API. فقط دسترسی READ و TRADE بدهید، هرگز WITHDRAW، و کلید را به "
                        "IP همین سرور محدود کنید."),
        Credential("nobitex_private_key", "Private key", "کلید خصوصی", required=False,
                   help="فقط یک بار، هنگام ساختن کلید، نمایش داده می‌شود."),
        Credential("nobitex_token", "Login token (instead of an API key)", "توکن ورود (به جای کلید API)",
                   required=False, help="توکن قدیمی از تنظیمات پنل؛ ۳۰ روز اعتبار دارد."),
    )

    def __init__(self, api_key: str = "", private_key: str = "", token: str = "", base_url: str = BASE_URL,
                 transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(base_url, transport=transport)
        self._http.headers["User-Agent"] = "TraderBot/saraf-0.1"
        self.api_key, self.private_key, self.token = api_key, private_key, token

    @property
    def has_key(self) -> bool:
        return bool(self.token or (self.api_key and self.private_key))

    # ------------------------------------------------------------------ core
    def _auth_headers(self, method: str, path: str, body: str) -> dict:
        if self.api_key and self.private_key:
            ts = str(int(time.time()))
            return {"Nobitex-Key": self.api_key, "Nobitex-Timestamp": ts,
                    "Nobitex-Signature": sign(self.private_key, ts, method, path, body)}
        if self.token:
            return {"Authorization": f"Token {self.token}"}
        raise VenueError("Nobitex: no API key or token is set; add one under Settings > Connections.")

    async def _call(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None,
                    auth: bool = False) -> Any:
        content = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = {"Content-Type": "application/json"} if body is not None else {}
        if auth:
            qs = str(httpx.QueryParams({k: v for k, v in (params or {}).items() if v is not None}))
            headers |= self._auth_headers(method, path + (f"?{qs}" if qs else ""), content)
        r = await self._send(method, path, params=params, content=content.encode() if content else None,
                             headers=headers)
        data = self._json(r, path)
        if r.status_code >= 400 or (isinstance(data, dict) and data.get("status") == "failed"):
            msg = data.get("message") or data.get("detail") or data.get("code") if isinstance(data, dict) else None
            raise VenueError(f"Nobitex: {msg or f'HTTP {r.status_code} from {path}'}", r.status_code, data)
        return data

    @staticmethod
    def _split(symbol: str) -> tuple[str, str]:
        """BTCTMN -> ("btc", "rls"), ETHUSDT -> ("eth", "usdt")."""
        s = symbol.upper()
        for quote, dst in (("TMN", "rls"), ("IRT", "rls"), ("USDT", "usdt")):
            if s.endswith(quote) and len(s) > len(quote):
                return s[: -len(quote)].lower(), dst
        raise VenueError(f"Nobitex: unknown market {symbol!r}")

    def _path_symbol(self, symbol: str) -> str:
        src, dst = self._split(symbol)
        return f"{src}{'irt' if dst == 'rls' else dst}".upper()

    # ----------------------------------------------------------- market data
    async def markets(self) -> dict[str, dict]:
        return await self._cached("markets", 5, self._load_markets)

    async def _load_markets(self) -> dict[str, dict]:
        opts = (await self._cached("options", 600, lambda: self._call("GET", "/v2/options")))["nobitex"]
        stats = (await self._call("GET", "/market/stats"))["stats"]
        amount_p, price_p = opts.get("amountPrecisions", {}), opts.get("pricePrecisions", {})
        mins = opts.get("minOrders", {})
        out = {}
        for key, st in stats.items():
            src, _, dst = key.partition("-")
            if not isinstance(st, dict) or st.get("isClosed") or dst not in ("rls", "usdt"):
                continue
            rial = dst == "rls"
            quote = "TMN" if rial else "USDT"
            nx = f"{src.upper()}{'IRT' if rial else 'USDT'}"
            px = (lambda v: rial_to_toman(v)) if rial else _f
            tick = _f(price_p.get(nx) or 1) / (10 if rial else 1)
            symbol = f"{src.upper()}{quote}"
            out[symbol] = {
                "symbol": symbol, "baseAsset": src.upper(), "quoteAsset": quote, "enBaseAsset": src.upper(),
                "stepSize": decimals_of(amount_p.get(nx, "0.00000001")), "tickSize": decimals_of(tick),
                "minQty": 0.0, "minNotional": px(mins.get(dst)) if rial else _f(mins.get(dst)),
                "stats": {"bidPrice": px(st.get("bestBuy")), "askPrice": px(st.get("bestSell")),
                          "lastPrice": px(st.get("latest")), "24h_ch": _f(st.get("dayChange")),
                          "24h_highPrice": px(st.get("dayHigh")), "24h_lowPrice": px(st.get("dayLow")),
                          "24h_volume": _f(st.get("volumeSrc")), "24h_quoteVolume": px(st.get("volumeDst"))},
            }
        return out

    def _px(self, symbol: str):
        return rial_to_toman if self._split(symbol)[1] == "rls" else _f

    async def depth(self, symbol: str) -> dict:
        data = await self._call("GET", f"/v3/orderbook/{self._path_symbol(symbol)}")
        px = self._px(symbol)
        return {side: [{"price": px(p), "quantity": _f(q)} for p, q in data.get(key) or []]
                for side, key in (("bid", "bids"), ("ask", "asks"))}

    async def trades(self, symbol: str) -> list[dict]:
        data = await self._call("GET", f"/v2/trades/{self._path_symbol(symbol)}")
        px = self._px(symbol)
        return [{"symbol": symbol.upper(), "price": px(t["price"]), "quantity": _f(t["volume"]),
                 "sum": px(t["price"]) * _f(t["volume"]), "isBuyOrder": t.get("type") == "buy",
                 "timestamp": _iso_ms(t.get("time"))} for t in data.get("trades", [])]

    async def candles(self, symbol: str, resolution: str, start: int, end: int | None = None) -> list[dict]:
        if resolution not in RESOLUTIONS:
            raise VenueError(f"Nobitex: resolution must be one of {list(RESOLUTIONS)}")
        body = await self._call("GET", "/market/udf/history", params={
            "symbol": self._path_symbol(symbol), "resolution": RESOLUTIONS[resolution], "from": start,
            "to": end or int(time.time())})
        if body.get("s") != "ok":
            return []
        return [{"t": int(t), "o": _f(o), "h": _f(h), "l": _f(l), "c": _f(c), "v": _f(v)}
                for t, o, h, l, c, v in zip(body["t"], body["o"], body["h"], body["l"], body["c"], body["v"])]

    # -------------------------------------------------------------- account
    async def balances(self) -> dict[str, dict]:
        data = await self._call("GET", "/users/wallets/list", auth=True)
        out = {}
        for w in data.get("wallets", []):
            cur = str(w.get("currency", "")).upper()
            rial = cur in ("RLS", "IRR")
            conv = rial_to_toman if rial else _f
            out["TMN" if rial else cur] = {"value": conv(w.get("balance")), "locked": conv(w.get("blockedBalance"))}
        return out

    def _order(self, o: dict, symbol: str | None = None) -> Order:
        src, _, dst = str(o.get("market") or o.get("srcCurrency", "")).lower().partition("-")
        dst = dst or str(o.get("dstCurrency", "")).lower()
        rial = dst == "rls"
        px = rial_to_toman if rial else _f
        symbol = symbol or f"{src.upper()}{'TMN' if rial else 'USDT'}"
        filled = _f(o.get("matchedAmount"))
        status = STATUS.get(o.get("status"), "NEW")
        if status == "NEW" and filled:
            status = "PARTIALLY_FILLED"
        avg = px(o.get("averagePrice"))
        return {"clientOrderId": o.get("clientOrderId") or str(o.get("id")), "symbol": symbol,
                "side": str(o.get("type", "")).upper(), "type": str(o.get("execution", "limit")).upper(),
                "price": px(o.get("price")), "origQty": _f(o.get("amount")), "executedQty": filled,
                "executedPrice": avg, "executedSum": avg * filled, "status": status,
                "active": status in ("NEW", "PARTIALLY_FILLED"), "created": o.get("created_at", ""),
                "venueOrderId": o.get("id")}

    async def place_order(self, symbol: str, side: str, type: str, quantity: str, price: str | None = None,
                          client_id: str | None = None) -> dict:
        src, dst = self._split(symbol)
        body: dict[str, Any] = {"type": side.lower(), "execution": type.lower(), "srcCurrency": src,
                                "dstCurrency": dst, "amount": quantity}
        if price is not None:
            body["price"] = fmt_dec(float(price) * 10) if dst == "rls" else price
        body["clientOrderId"] = client_id or uuid.uuid4().hex[:32]
        data = await self._call("POST", "/market/orders/add", body=body, auth=True)
        return self._order(data["order"], symbol.upper())

    def _id_field(self, order_id: str) -> dict:
        return {"id": int(order_id)} if str(order_id).isdigit() else {"clientOrderId": order_id}

    async def get_order(self, order_id: str) -> dict:
        data = await self._call("POST", "/market/orders/status", body=self._id_field(order_id), auth=True)
        return self._order(data["order"])

    async def cancel_order(self, order_id: str) -> Any:
        ident = self._id_field(order_id)
        body = {"order": ident["id"]} if "id" in ident else ident
        return await self._call("POST", "/market/orders/update-status", body={**body, "status": "canceled"}, auth=True)

    async def open_orders(self, symbol: str | None = None) -> list[dict]:
        params: dict[str, Any] = {"status": "open"}
        if symbol:
            params["srcCurrency"], params["dstCurrency"] = self._split(symbol)
        data = await self._call("GET", "/market/orders/list", params=params, auth=True)
        return [self._order(o) for o in data.get("orders", [])]

    async def my_trades(self, symbol: str | None = None, side: str | None = None) -> list[dict]:
        params: dict[str, Any] = {}
        if symbol:
            params["srcCurrency"], params["dstCurrency"] = self._split(symbol)
        data = await self._call("GET", "/market/trades/list", params=params, auth=True)
        out = []
        for t in data.get("trades", []):
            if side and str(t.get("type", "")).upper() != side.upper():
                continue
            src, _, dst = str(t.get("market", "")).lower().partition("-")
            px = rial_to_toman if dst == "rls" else _f
            out.append({"symbol": f"{src.upper()}{'TMN' if dst == 'rls' else 'USDT'}", "price": px(t.get("price")),
                        "quantity": _f(t.get("amount")), "sum": px(t.get("total")), "fee": _f(t.get("fee")),
                        "isBuyOrder": t.get("type") == "buy", "timestamp": t.get("timestamp", "")})
        return out

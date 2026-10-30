"""The Ramzinex venue (ramzinex.com), from its public REST API (https://docs.ramzinex.ir).

Markets are numeric pair ids (2 = BTC/IRR); Saraf names them like every other venue (BTCTMN, ETHUSDT). Rial pairs
are shown in toman (prices / 10), and order prices go back as rial. A market buy is sized in rial by Ramzinex,
so the adapter converts the base quantity at the current ask.

Ramzinex has no client order ids: the adapter keeps Saraf's id -> Ramzinex order id in a small JSON file so an
order can still be found after a restart. Auth: api_key + secret buy a JWT; private calls send it as
`Authorization2: Bearer ...` together with `x-api-key`.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from ..base import Capabilities, Credential, Order, VenueError
from ..http import HttpVenue, jwt_exp, rial_to_toman

PUBLIC = "https://publicapi.ramzinex.ir/exchange/api"
PRIVATE = "https://api.ramzinex.ir/exchange/api"
V1 = "/v1.0/exchange"
RESOLUTIONS = ("1", "5", "15", "30", "60", "240", "720", "1D")
STATES = {1: "NEW", 2: "CANCELED", 3: "FILLED", 4: "PARTIALLY_FILLED"}


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _en(x: Any) -> str:
    return str(x.get("en", "") if isinstance(x, dict) else x or "")


class RamzinexClient(HttpVenue):
    name = "ramzinex"
    title = "Ramzinex"
    title_fa = "رمزینکس"
    capabilities = Capabilities(asset_class="crypto", candles=RESOLUTIONS, trading=True, account=True)
    credentials = (
        Credential("ramzinex_api_key", "API key", "کلید API", secret=False,
                   help="رمزینکس ← مدیریت API. فقط دسترسی خواندن و معامله بدهید، نه برداشت."),
        Credential("ramzinex_secret", "Secret", "کلید مخفی"),
    )

    def __init__(self, api_key: str = "", secret: str = "", data_dir: Path | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(PRIVATE, transport=transport)
        self.api_key, self.secret = api_key, secret
        self._token, self._token_exp = "", 0.0
        self._ids_path = Path(data_dir) / "venues" / "ramzinex-orders.json" if data_dir else None
        self._ids: dict[str, int] = self._load_ids()

    @property
    def has_key(self) -> bool:
        return bool(self.api_key and self.secret)

    # ------------------------------------------------------------------ core
    def _load_ids(self) -> dict[str, int]:
        if self._ids_path and self._ids_path.exists():
            try:
                return json.loads(self._ids_path.read_text())
            except ValueError:
                return {}
        return {}

    def _remember(self, client_id: str, order_id: int) -> None:
        self._ids[client_id] = order_id
        if len(self._ids) > 2000:   # old orders are not looked up again
            self._ids = dict(list(self._ids.items())[-1000:])
        if self._ids_path:
            self._ids_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._ids_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._ids))
            tmp.replace(self._ids_path)

    def _check(self, r: httpx.Response, data: Any, path: str) -> Any:
        if r.status_code >= 400 or (isinstance(data, dict) and data.get("status") not in (0, None)):
            msg = None
            if isinstance(data, dict):
                msg = _en(data.get("description")) or data.get("message") or data.get("error")
            raise VenueError(f"Ramzinex: {msg or f'HTTP {r.status_code} from {path}'}", r.status_code, data)
        return data

    async def _public(self, path: str, params: dict | None = None) -> Any:
        r = await self._send("GET", PUBLIC + path, params=params)
        return self._check(r, self._json(r, path), path)

    async def _login(self) -> None:
        if not self.has_key:
            raise VenueError("Ramzinex: no API key is set; add one under Settings > Connections.")
        path = f"{V1}/auth/api_key/getToken"
        r = await self._send("POST", path, json={"api_key": self.api_key, "secret": self.secret})
        data = self._check(r, self._json(r, path), path)
        token = (data.get("data") or {}).get("token") if isinstance(data, dict) else None
        if not token:
            raise VenueError("Ramzinex: sign-in returned no token", r.status_code, data)
        self._token = token
        self._token_exp = jwt_exp(token) or time.time() + 3000

    async def _private(self, method: str, path: str, body: dict | None = None) -> Any:
        for attempt in (0, 1):
            if not self._token or self._token_exp < time.time() + 30:
                await self._login()
            r = await self._send(method, path, json=body,
                                 headers={"Authorization2": f"Bearer {self._token}", "x-api-key": self.api_key})
            if r.status_code == 401 and attempt == 0:
                self._token = ""
                continue
            return self._check(r, self._json(r, path), path)
        raise AssertionError("unreachable")

    # ----------------------------------------------------------- market data
    async def _pairs(self) -> dict[str, dict]:
        """Symbol -> pair, with the live prices that come with it (cached 5 s)."""
        async def load():
            data = await self._public(f"{V1}/pairs")
            out = {}
            for p in data.get("data", []):
                quote = _en(p.get("quote_currency_symbol")).lower()
                if p.get("is_delist") or p.get("is_temporarily_suspended") or quote not in ("irr", "usdt"):
                    continue
                base = _en(p.get("base_currency_symbol")).upper()
                out[f"{base}{'TMN' if quote == 'irr' else 'USDT'}"] = p
            return out
        return await self._cached("pairs", 5, load)

    async def _pair(self, symbol: str) -> dict:
        p = (await self._pairs()).get(symbol.upper())
        if not p:
            raise VenueError(f"Ramzinex: unknown market {symbol!r}")
        return p

    @staticmethod
    def _rial(p: dict) -> bool:
        return _en(p.get("quote_currency_symbol")).lower() == "irr"

    async def markets(self) -> dict[str, dict]:
        out = {}
        for symbol, p in (await self._pairs()).items():
            rial = self._rial(p)
            px = rial_to_toman if rial else _f
            day = (p.get("financial") or {}).get("last24h") or {}
            out[symbol] = {
                "symbol": symbol, "baseAsset": _en(p["base_currency_symbol"]).upper(),
                "quoteAsset": "TMN" if rial else "USDT", "faBaseAsset": (p.get("base_currency_symbol") or {}).get("fa", ""),
                "enBaseAsset": _en(p["base_currency_symbol"]).upper(),
                "stepSize": int(p.get("base_precision") or 0),
                "tickSize": int(p.get("price_precision") or 0) + (1 if rial else 0),
                "minQty": 0.0, "minNotional": 0.0,
                "stats": {"bidPrice": px(p.get("buy")), "askPrice": px(p.get("sell")), "lastPrice": px(day.get("close")),
                          "24h_ch": _f(day.get("change_percent")), "24h_highPrice": px(day.get("highest")),
                          "24h_lowPrice": px(day.get("lowest")), "24h_volume": _f(day.get("base_volume")),
                          "24h_quoteVolume": px(day.get("quote_volume"))},
            }
        return out

    async def depth(self, symbol: str) -> dict:
        p = await self._pair(symbol)
        px = rial_to_toman if self._rial(p) else _f
        data = (await self._public(f"{V1}/orderbooks/{p['pair_id']}/buys_sells")).get("data") or {}
        return {side: [{"price": px(row[0]), "quantity": _f(row[1])} for row in data.get(key) or []]
                for side, key in (("bid", "buys"), ("ask", "sells"))}

    async def trades(self, symbol: str) -> list[dict]:
        p = await self._pair(symbol)
        px = rial_to_toman if self._rial(p) else _f
        rows = (await self._public(f"{V1}/orderbooks/{p['pair_id']}/trades")).get("data") or []
        return [{"symbol": symbol.upper(), "price": px(r[0]), "quantity": _f(r[1]), "sum": px(r[0]) * _f(r[1]),
                 "isBuyOrder": r[3] == "buy", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(_f(r[4])))}
                for r in rows]

    async def candles(self, symbol: str, resolution: str, start: int, end: int | None = None) -> list[dict]:
        if resolution not in RESOLUTIONS:
            raise VenueError(f"Ramzinex: resolution must be one of {list(RESOLUTIONS)}")
        p = await self._pair(symbol)
        px = rial_to_toman if self._rial(p) else _f
        tv = str((p.get("tv_symbol") or {}).get("ramzinex") or "").upper()
        body = await self._public(f"{V1}/chart/tv/history", params={
            "symbol": tv, "resolution": resolution, "from": start, "to": end or int(time.time())})
        if body.get("s") != "ok":
            return []
        return [{"t": int(t), "o": px(o), "h": px(h), "l": px(l), "c": px(c), "v": _f(v)}
                for t, o, h, l, c, v in zip(body["t"], body["o"], body["h"], body["l"], body["c"], body["v"])]

    # -------------------------------------------------------------- account
    async def balances(self) -> dict[str, dict]:
        data = (await self._private("GET", f"{V1}/users/me/funds/summaryDesktop")).get("data")
        items = data if isinstance(data, list) else list(data.values()) if isinstance(data, dict) else []
        out = {}
        for it in items:
            if not isinstance(it, dict):
                continue
            cur = it.get("currency") if isinstance(it.get("currency"), dict) else it
            sym = _en(cur.get("symbol") or cur.get("currency_symbol") or it.get("currency")).upper()
            if not sym:
                continue
            total = _f(it.get("total", it.get("balance", it.get("value"))))
            locked = _f(it.get("in_orders", it.get("in_order", it.get("blocked"))))
            if sym in ("IRR", "RIAL"):
                out["TMN"] = {"value": total / 10, "locked": locked / 10}
            else:
                out[sym] = {"value": total, "locked": locked}
        return out

    async def _symbol_of(self, pair_id: int) -> tuple[str, bool]:
        for symbol, p in (await self._pairs()).items():
            if p["pair_id"] == pair_id:
                return symbol, self._rial(p)
        return str(pair_id), False

    async def _order(self, o: dict, client_id: str | None = None) -> Order:
        symbol, rial = await self._symbol_of(int(o.get("pair_id") or 0))
        px = rial_to_toman if rial else _f
        filled, avg = _f(o.get("filled_nr")), px(o.get("average_price_nr"))
        status = STATES.get(int(o.get("status_id") or 1), "NEW")
        if status == "NEW" and filled:
            status = "PARTIALLY_FILLED"
        if client_id is None:
            client_id = next((c for c, i in self._ids.items() if i == o.get("id")), str(o.get("id")))
        return {"clientOrderId": client_id, "symbol": symbol, "side": str(o.get("type_en", "")).upper(),
                "type": "LIMIT", "price": px(o.get("order_price_nr")), "origQty": _f(o.get("amount_nr")),
                "executedQty": filled, "executedPrice": avg, "executedSum": avg * filled, "status": status,
                "active": status in ("NEW", "PARTIALLY_FILLED"), "created": str(o.get("created_at", "")),
                "venueOrderId": o.get("id")}

    def _venue_id(self, order_id: str) -> int:
        if order_id in self._ids:
            return self._ids[order_id]
        if str(order_id).isdigit():
            return int(order_id)
        raise VenueError(f"Ramzinex: no order {order_id!r}")

    async def place_order(self, symbol: str, side: str, type: str, quantity: str, price: str | None = None,
                          client_id: str | None = None) -> dict:
        p = await self._pair(symbol)
        rial, side, qty = self._rial(p), side.lower(), float(quantity)
        if type.upper() == "MARKET":
            amount = qty
            if side == "buy":   # Ramzinex sizes a market buy in the quote currency
                book = await self.depth(symbol)
                asks = [float(lv["price"]) for lv in book["ask"] if lv["price"]]
                if not asks:
                    raise VenueError(f"Ramzinex: {symbol} has no asks to price a market buy")
                amount = qty * min(asks) * (10 if rial else 1)
            path, body = "/v2.0/exchange/users/me/orders/market", {"pair_id": p["pair_id"], "amount": amount,
                                                                   "type": side}
        else:
            if price is None:
                raise VenueError("Ramzinex: LIMIT orders need a price")
            pr = float(price) * (10 if rial else 1)
            path = f"{V1}/users/me/orders/limit"
            body = {"pair_id": p["pair_id"], "amount": qty, "price": int(pr) if pr == int(pr) else pr, "type": side}
        data = (await self._private("POST", path, body)).get("data") or {}
        order_id = data.get("order_id")
        if order_id is None:
            raise VenueError("Ramzinex: the order was accepted but no order id came back", payload=data)
        client_id = client_id or uuid.uuid4().hex
        self._remember(client_id, int(order_id))
        return {"clientOrderId": client_id, "symbol": symbol.upper(), "side": side.upper(), "type": type.upper(),
                "price": float(price or 0), "origQty": qty, "executedQty": 0.0, "executedSum": 0.0,
                "executedPrice": 0.0, "status": "NEW", "active": True, "venueOrderId": order_id}

    async def get_order(self, order_id: str) -> dict:
        vid = self._venue_id(order_id)
        data = (await self._private("GET", f"{V1}/users/me/orders2/{vid}")).get("data") or {}
        return await self._order(data, order_id if order_id in self._ids else None)

    async def cancel_order(self, order_id: str) -> Any:
        return await self._private("POST", f"{V1}/users/me/orders/{self._venue_id(order_id)}/cancel")

    async def _list(self, states: list[int], symbol: str | None) -> list[dict]:
        body: dict[str, Any] = {"offset": 0, "limit": 50, "states": states}
        if symbol:
            body["pairs"] = [(await self._pair(symbol))["pair_id"]]
        data = (await self._private("POST", f"{V1}/users/me/orders3", body)).get("data")
        rows = data.get("orders", data.get("data", [])) if isinstance(data, dict) else data or []
        return [await self._order(o) for o in rows]

    async def open_orders(self, symbol: str | None = None) -> list[dict]:
        return await self._list([1, 4], symbol)

    async def my_trades(self, symbol: str | None = None, side: str | None = None) -> list[dict]:
        # no fills endpoint: finished orders stand in for trades
        done = await self._list([3], symbol)
        return [{"symbol": o["symbol"], "price": o["executedPrice"], "quantity": o["executedQty"],
                 "sum": o["executedSum"], "isBuyOrder": o["side"] == "BUY", "timestamp": o["created"]}
                for o in done if not side or o["side"] == side.upper()]

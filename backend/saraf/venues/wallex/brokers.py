"""Live Wallex brokers: real spot orders, and the futures adapter waiting for Wallex's futures endpoints.
Paper trading on Wallex uses the generic paper brokers (saraf/brokers) on this venue's market data."""

from __future__ import annotations

import math

from ...brokers.base import BrokerError, FuturesBroker, MarketInfo, SpotBroker, fmt_num, safe_float
from ...brokers.spot import _resolve_quantity
from .client import WallexClient, WallexError


def wrap_wallex_error(e: WallexError) -> BrokerError:
    return BrokerError(f"Wallex: {e}")


def market_orders_refused(msg: str) -> bool:
    """Wallex's answer when a market does not take market orders: «امکان ثبت سفارش قیمت بازار در … وجود ندارد»."""
    return "قیمت بازار" in msg or "market order" in msg.lower()


MAX_WALK = 0.02            # how far through the book a refused market order may go as a limit

LIVE_BUY_HEADROOM = 0.005   # 0.3% taker fee on the challenge account + 0.2% for the ask moving


class WallexSpotBroker(SpotBroker):
    """Real spot orders on Wallex."""

    mode = "live"

    def __init__(self, client: WallexClient):
        self.venue = client
        self.info = MarketInfo(client)

    async def balances(self) -> dict[str, dict]:
        try:
            raw = await self.venue.balances()
        except WallexError as e:
            raise wrap_wallex_error(e)
        out = {}
        for asset, b in raw.items():
            total, locked = float(b.get("value") or 0), float(b.get("locked") or 0)
            if total or locked:
                out[asset] = {"free": max(total - locked, 0.0), "locked": locked, "total": total}
        return out

    async def place_order(self, symbol, side, type, quantity=None, price=None, quote_amount=None) -> dict:
        side, type = side.upper(), type.upper()
        info = await self.info.quote(symbol)
        if side == "BUY" and type == "MARKET" and quantity is None and quote_amount is not None:
            # "Spend N TMN": Wallex reserves a MARKET buy against a protective price above the ask, so a
            # near-whole-balance market buy is rejected (error 1006, seen live with 4,975,067 of 5,000,000).
            # A LIMIT at the live best ask reserves exactly qty x price and fills at once like a market order.
            book = await self.venue.depth(symbol)
            asks = [safe_float(lv["price"]) for lv in book.get("ask", []) if safe_float(lv["price"])]
            if asks:   # round up to the tick: a buy limit a hair under the ask would rest instead of filling
                step = 10 ** info["price_decimals"]
                type, price = "LIMIT", math.ceil(min(asks) * step - 1e-9) / step
            quote_amount = quote_amount / (1 + LIVE_BUY_HEADROOM)
        qty = await _resolve_quantity(info, side, quantity, quote_amount, price)
        if type == "LIMIT" and price is None:
            raise BrokerError("LIMIT orders need a price")
        try:
            return await self.venue.place_order(
                symbol, side, type, fmt_num(qty, info["qty_decimals"]),
                price=fmt_num(price, info["price_decimals"]) if type == "LIMIT" else None,
            )
        except WallexError as e:
            if type != "MARKET" or not market_orders_refused(str(e)):
                raise wrap_wallex_error(e)
        # some markets refuse market orders (seen live on USOONUSDT): the same order as a LIMIT priced through the
        # book fills at once at the resting orders' prices; the caller cancels any part that did not fill
        price = await self._through_book(symbol, side, qty, info["price_decimals"])
        try:
            return await self.venue.place_order(symbol, side, "LIMIT", fmt_num(qty, info["qty_decimals"]),
                                                 price=fmt_num(price, info["price_decimals"]))
        except WallexError as e:
            raise wrap_wallex_error(e)

    async def _through_book(self, symbol: str, side: str, qty: float, decimals: int) -> float:
        """The price that fills `qty` against the order book now (the worst level needed), on the tick."""
        book = await self.venue.depth(symbol)
        levels = book.get("bid" if side == "SELL" else "ask", [])
        levels = sorted(((safe_float(lv["price"]), safe_float(lv.get("quantity"))) for lv in levels if safe_float(lv["price"])),
                        reverse=side == "SELL")
        if not levels:
            raise BrokerError(f"{symbol}: market orders are refused and the order book is empty")
        need, price = qty, levels[-1][0]
        for p, q in levels:
            price, need = p, need - q
            if need <= 0:
                break
        best = levels[0][0]   # a thin book: go no further than 2% from the best price; the rest is cancelled unfilled
        price = max(price, best * (1 - MAX_WALK)) if side == "SELL" else min(price, best * (1 + MAX_WALK))
        step = 10 ** decimals
        return (math.floor(price * step + 1e-9) if side == "SELL" else math.ceil(price * step - 1e-9)) / step

    async def cancel_order(self, order_id: str) -> dict:
        try:
            await self.venue.cancel_order(order_id)
            return {"order_id": order_id, "status": "CANCELED"}
        except WallexError as e:
            raise wrap_wallex_error(e)

    async def open_orders(self, symbol=None) -> list[dict]:
        try:
            return await self.venue.open_orders(symbol)
        except WallexError as e:
            raise wrap_wallex_error(e)

    async def get_order(self, order_id: str) -> dict:
        try:
            return await self.venue.get_order(order_id)
        except WallexError as e:
            raise wrap_wallex_error(e)

    async def trades(self, symbol=None) -> list[dict]:
        try:
            return await self.venue.my_trades(symbol)
        except WallexError as e:
            raise wrap_wallex_error(e)


class WallexFuturesBroker(FuturesBroker):
    """Live Wallex futures adapter — wire the real endpoints in here once available."""

    mode = "live"

    def __init__(self, client: WallexClient, base_url: str):
        self.venue = client
        self.base_url = base_url

    def _unavailable(self):
        raise BrokerError(
            "Live Wallex futures endpoints are not in the public API docs yet. "
            "Set FUTURES_MODE=paper, or implement WallexFuturesBroker with the real endpoints."
        )

    async def account(self):
        self._unavailable()

    async def positions(self):
        self._unavailable()

    async def open_position(self, *a, **k):
        self._unavailable()

    async def close_position(self, *a, **k):
        self._unavailable()

    async def set_tp_sl(self, *a, **k):
        self._unavailable()

    async def open_orders(self):
        self._unavailable()

    async def cancel_order(self, *a, **k):
        self._unavailable()

    async def history(self, limit: int = 20):
        self._unavailable()

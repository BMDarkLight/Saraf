"""The live spot broker for any venue that trades: rounds to the market's precision and turns venue errors into
broker errors. Wallex keeps its own broker (venues/wallex/brokers.py) for its market-order workarounds."""

from __future__ import annotations

from ..venues.base import Venue, VenueError
from .base import BrokerError, MarketInfo, SpotBroker, fmt_num, safe_float
from .spot import _resolve_quantity


class VenueSpotBroker(SpotBroker):
    mode = "live"

    def __init__(self, venue: Venue):
        self.venue = venue
        self.info = MarketInfo(venue)

    def _wrap(self, e: VenueError) -> BrokerError:
        msg = str(e)
        prefix = f"{self.venue.title}: "
        return BrokerError(msg if msg.startswith(prefix) else prefix + msg)

    async def balances(self) -> dict[str, dict]:
        try:
            raw = await self.venue.balances()
        except VenueError as e:
            raise self._wrap(e)
        out = {}
        for asset, b in raw.items():
            total, locked = safe_float(b.get("value")), safe_float(b.get("locked"))
            if total or locked:
                out[asset] = {"free": max(total - locked, 0.0), "locked": locked, "total": total}
        return out

    async def place_order(self, symbol, side, type, quantity=None, price=None, quote_amount=None) -> dict:
        side, type = side.upper(), type.upper()
        info = await self.info.quote(symbol)
        if type == "LIMIT" and price is None:
            raise BrokerError("LIMIT orders need a price")
        qty = await _resolve_quantity(info, side, quantity, quote_amount, price)
        try:
            return await self.venue.place_order(
                info["symbol"], side, type, fmt_num(qty, info["qty_decimals"]),
                price=fmt_num(price, info["price_decimals"]) if type == "LIMIT" else None)
        except VenueError as e:
            raise self._wrap(e)

    async def cancel_order(self, order_id: str) -> dict:
        try:
            await self.venue.cancel_order(order_id)
        except VenueError as e:
            raise self._wrap(e)
        return {"order_id": order_id, "status": "CANCELED"}

    async def open_orders(self, symbol=None) -> list[dict]:
        try:
            return await self.venue.open_orders(symbol)
        except VenueError as e:
            raise self._wrap(e)

    async def get_order(self, order_id: str) -> dict:
        try:
            return await self.venue.get_order(order_id)
        except VenueError as e:
            raise self._wrap(e)

    async def trades(self, symbol=None) -> list[dict]:
        try:
            return await self.venue.my_trades(symbol)
        except VenueError as e:
            raise self._wrap(e)

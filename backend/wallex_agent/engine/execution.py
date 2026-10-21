"""Order execution. SpotExec trades through a SpotBroker (paper = shadow test on the live
order book, live = real Wallex orders); SimExec fills against the replayed history."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..brokers import BrokerError, SpotBroker


@dataclass
class Fill:
    qty: float          # base asset received (buy) or sold (sell), net of fees in kind
    price: float        # average execution price
    tmn: float          # TMN spent (buy) or received net of fees (sell)


class SpotExec:
    def __init__(self, spot: SpotBroker):
        self.spot = spot

    async def cash(self) -> float:
        return (await self.spot.balances()).get("TMN", {}).get("free", 0.0)

    async def holding(self, asset: str) -> float:
        return (await self.spot.balances()).get(asset, {}).get("free", 0.0)

    async def _settled(self, order: dict) -> dict:
        """Live market orders can report NEW for a moment; wait briefly for the fill."""
        for _ in range(10):
            if order.get("status") in ("FILLED", "CANCELED") or not order.get("active", False):
                return order
            await asyncio.sleep(0.5)
            order = await self.spot.get_order(order["clientOrderId"])
        return order

    async def buy(self, symbol: str, quote_amount: float, base: str, price_hint: float | None = None) -> Fill:
        before_q, before_b = await self.cash(), await self.holding(base)
        order = await self._settled(await self.spot.place_order(symbol, "BUY", "MARKET", quote_amount=quote_amount))
        after_q, after_b = await self.cash(), await self.holding(base)
        qty, spent = after_b - before_b, before_q - after_q
        if qty <= 0:
            raise BrokerError(f"buy {symbol} did not fill: {order.get('status')} {order.get('note', '')}")
        return Fill(qty, spent / qty if qty else float(order.get("executedPrice") or 0), spent)

    async def sell(self, symbol: str, qty: float, base: str, price_hint: float | None = None) -> Fill:
        held = await self.holding(base)
        qty = min(qty, held)
        if qty <= 0:
            raise BrokerError(f"nothing to sell for {symbol}")
        before_q = await self.cash()
        order = await self._settled(await self.spot.place_order(symbol, "SELL", "MARKET", quantity=qty))
        received = await self.cash() - before_q
        sold = float(order.get("executedQty") or qty)
        if received <= 0:
            raise BrokerError(f"sell {symbol} did not fill: {order.get('status')}")
        return Fill(sold, received / sold if sold else 0.0, received)


class SimExec:
    def __init__(self, feed, cash: float, fee_rate: float, slippage: float = 0.0005):
        self.feed, self.fee, self.slip = feed, fee_rate, slippage
        self._cash = cash
        self.holdings: dict[str, float] = {}
        self.fees_paid = 0.0

    async def cash(self) -> float:
        return self._cash

    async def holding(self, asset: str) -> float:
        return self.holdings.get(asset, 0.0)

    async def buy(self, symbol: str, quote_amount: float, base: str, price_hint: float | None = None) -> Fill:
        if quote_amount > self._cash + 1e-6:
            raise BrokerError("insufficient TMN")
        price = (await self.feed.quote(symbol))["ask"] * (1 + self.slip)
        fee = quote_amount * self.fee
        qty = (quote_amount - fee) / price
        self._cash -= quote_amount
        self.holdings[base] = self.holdings.get(base, 0.0) + qty
        self.fees_paid += fee
        return Fill(qty, price, quote_amount)

    async def sell(self, symbol: str, qty: float, base: str, price_hint: float | None = None) -> Fill:
        qty = min(qty, self.holdings.get(base, 0.0))
        if qty <= 0:
            raise BrokerError(f"nothing to sell for {symbol}")
        price = (price_hint or (await self.feed.quote(symbol))["bid"]) * (1 - self.slip)
        gross = qty * price
        fee = gross * self.fee
        self.holdings[base] -= qty
        self._cash += gross - fee
        self.fees_paid += fee
        return Fill(qty, price, gross - fee)

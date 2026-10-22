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


@dataclass
class OrderState:
    """Snapshot of a resting limit order. qty/tmn follow Fill's conventions for what has filled so far."""
    order_id: str
    status: str         # "open" | "filled" | "canceled"
    filled_qty: float
    price: float
    tmn: float


class SpotExec:
    def __init__(self, spot: SpotBroker, fee_rate: float = 0.0025):
        self.spot = spot
        self.fee_rate = fee_rate
        self._sides: dict[str, str] = {}

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

    async def total_cash(self) -> float:
        return (await self.spot.balances()).get("TMN", {}).get("total", 0.0)

    async def holding_total(self, asset: str) -> float:
        return (await self.spot.balances()).get(asset, {}).get("total", 0.0)

    async def all_holdings(self) -> dict[str, float]:
        """Every non-TMN asset in the account (total), including ones the engine didn't buy."""
        return {a: b.get("total", 0.0) for a, b in (await self.spot.balances()).items() if a != "TMN"}

    # ---- limit (maker) orders
    async def place_limit(self, symbol: str, side: str, qty: float, price: float, base: str) -> str:
        order = await self.spot.place_order(symbol, side, "LIMIT", quantity=qty, price=price)
        self._sides[order["clientOrderId"]] = side
        return order["clientOrderId"]

    def _state(self, order: dict) -> OrderState:
        side = self._sides.get(order["clientOrderId"], order.get("side", "BUY"))
        qty = float(order.get("executedQty") or 0)
        notional = float(order.get("executedSum") or 0)
        price = notional / qty if qty else float(order.get("price") or 0)
        status = "filled" if order.get("status") == "FILLED" else "open" if order.get("active", True) else "canceled"
        if side == "BUY":   # Wallex charges the buyer's fee in the received coin
            return OrderState(order["clientOrderId"], status, qty * (1 - self.fee_rate), price, notional)
        return OrderState(order["clientOrderId"], status, qty, price, notional * (1 - self.fee_rate))

    async def order_status(self, order_id: str) -> OrderState:
        return self._state(await self.spot.get_order(order_id))

    async def cancel(self, order_id: str) -> OrderState:
        try:
            await self.spot.cancel_order(order_id)
        except BrokerError:
            pass   # already filled or gone; the status below tells which
        st = self._state(await self.spot.get_order(order_id))
        if st.status == "open":   # live cancel acknowledged but not reflected yet
            st.status = "canceled"
        return st

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
    """Backtest fills. Market orders: next price +- half spread + slippage, taker fee.
    Limit orders: fill only when a later 1m bar trades THROUGH the limit (low < buy price,
    high > sell price) - touching is not enough, a conservative stand-in for queue position -
    at the limit price with the maker fee."""

    def __init__(self, feed, cash: float, fee_rate: float, slippage: float = 0.0005, maker_fee_rate: float | None = None):
        self.feed, self.fee, self.slip = feed, fee_rate, slippage
        self.maker_fee = fee_rate if maker_fee_rate is None else maker_fee_rate
        self._cash = cash
        self.holdings: dict[str, float] = {}
        self.fees_paid = 0.0
        self.orders: dict[str, dict] = {}
        self._seq = 0

    async def cash(self) -> float:
        return self._cash

    async def total_cash(self) -> float:
        return self._cash + sum(o["qty"] * o["price"] for o in self.orders.values()
                                if o["status"] == "open" and o["side"] == "BUY")

    async def holding_total(self, asset: str) -> float:
        return self.holdings.get(asset, 0.0) + sum(o["qty"] for o in self.orders.values()
                                                   if o["status"] == "open" and o["side"] == "SELL" and o["base"] == asset)

    async def place_limit(self, symbol: str, side: str, qty: float, price: float, base: str) -> str:
        if side == "BUY":
            if qty * price > self._cash + 1e-6:
                raise BrokerError("insufficient TMN")
            self._cash -= qty * price
        else:
            qty = min(qty, self.holdings.get(base, 0.0))
            if qty <= 0:
                raise BrokerError(f"nothing to sell for {symbol}")
            self.holdings[base] -= qty
        self._seq += 1
        oid = f"SIM-{self._seq}"
        self.orders[oid] = {"symbol": symbol, "side": side, "qty": qty, "price": price, "base": base,
                            "placed": self.feed.now(), "status": "open"}
        return oid

    def _settle(self, oid: str) -> None:
        o = self.orders[oid]
        if o["status"] != "open":
            return
        for b in self.feed.future_1m(o["symbol"], o["placed"], self.feed.now() - 59):
            if (o["side"] == "BUY" and b.l < o["price"]) or (o["side"] == "SELL" and b.h > o["price"]):
                gross = o["qty"] * o["price"]
                fee = gross * self.maker_fee
                self.fees_paid += fee
                if o["side"] == "BUY":
                    self.holdings[o["base"]] = self.holdings.get(o["base"], 0.0) + o["qty"] * (1 - self.maker_fee)
                else:
                    self._cash += gross - fee
                o["status"] = "filled"
                o["filled_at"] = b.t + 60
                return

    def _state(self, oid: str) -> OrderState:
        o = self.orders[oid]
        if o["status"] != "filled":
            return OrderState(oid, o["status"], 0.0, o["price"], 0.0)
        gross = o["qty"] * o["price"]
        if o["side"] == "BUY":
            return OrderState(oid, "filled", o["qty"] * (1 - self.maker_fee), o["price"], gross)
        return OrderState(oid, "filled", o["qty"], o["price"], gross * (1 - self.maker_fee))

    async def order_status(self, order_id: str) -> OrderState:
        self._settle(order_id)
        return self._state(order_id)

    async def cancel(self, order_id: str) -> OrderState:
        self._settle(order_id)
        o = self.orders[order_id]
        if o["status"] == "open":
            o["status"] = "canceled"
            if o["side"] == "BUY":
                self._cash += o["qty"] * o["price"]
            else:
                self.holdings[o["base"]] = self.holdings.get(o["base"], 0.0) + o["qty"]
        return self._state(order_id)

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

"""Order execution. SpotExec trades through a SpotBroker (paper = shadow test on the live
order book, live = real Wallex orders); SimExec fills against the replayed history."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..brokers import BrokerError, SpotBroker
from .markets import CASH_ASSETS, split_symbol


@dataclass
class Fill:
    qty: float          # base asset received (buy) or sold (sell), net of fees in kind
    price: float        # average execution price
    tmn: float          # quote currency (TMN, or USDT on USDT markets) spent (buy) / received net of fees (sell)


@dataclass
class OrderState:
    """Snapshot of a resting limit order. qty/tmn follow Fill's conventions for what has filled so far."""
    order_id: str
    status: str         # "open" | "filled" | "canceled"
    filled_qty: float
    price: float
    tmn: float


class SpotExec:
    """`quote` is the currency the satellite trades in: its free balance is the engine's cash."""

    def __init__(self, spot: SpotBroker, fee_rate: float = 0.0025, quote: str = "TMN"):
        self.spot = spot
        self.fee_rate = fee_rate
        self.quote = quote
        self._sides: dict[str, str] = {}

    async def cash(self) -> float:
        return await self.holding(self.quote)

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
        return await self.holding_total(self.quote)

    async def holding_total(self, asset: str) -> float:
        return (await self.spot.balances()).get(asset, {}).get("total", 0.0)

    async def wallet(self) -> dict[str, tuple[float, float]]:
        """Every asset in the account as (free, total), including ones the engine didn't buy."""
        return {a: (b.get("free", 0.0), b.get("total", 0.0)) for a, b in (await self.spot.balances()).items()}

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
        quote = split_symbol(symbol)[1]
        before_q, before_b = await self.holding(quote), await self.holding(base)
        order = await self._settled(await self.spot.place_order(symbol, "BUY", "MARKET", quote_amount=quote_amount))
        after_q, after_b = await self.holding(quote), await self.holding(base)
        qty, spent = after_b - before_b, before_q - after_q
        if qty <= 0:
            raise BrokerError(f"buy {symbol} did not fill: {order.get('status')} {order.get('note', '')}")
        return Fill(qty, spent / qty if qty else float(order.get("executedPrice") or 0), spent)

    async def sell(self, symbol: str, qty: float, base: str, price_hint: float | None = None) -> Fill:
        held = await self.holding(base)
        qty = min(qty, held)
        if qty <= 0:
            raise BrokerError(f"nothing to sell for {symbol}")
        quote = split_symbol(symbol)[1]
        before_q = await self.holding(quote)
        order = await self._settled(await self.spot.place_order(symbol, "SELL", "MARKET", quantity=qty))
        received = await self.holding(quote) - before_q
        sold = float(order.get("executedQty") or qty)
        if received <= 0:
            raise BrokerError(f"sell {symbol} did not fill: {order.get('status')}")
        return Fill(sold, received / sold if sold else 0.0, received)


class SimExec:
    """Backtest fills. Market orders: next price +- half spread + slippage, taker fee.
    Limit orders: fill only when a later 1m bar trades THROUGH the limit (low < buy price,
    high > sell price) - touching is not enough, a conservative stand-in for queue position -
    at the limit price with the maker fee.

    Balances: `cash_by` holds the two quote currencies (TMN, USDT), `holdings` every other coin.
    The account starts in TMN like the challenge; `quote` is the satellite's trading currency."""

    def __init__(self, feed, cash: float, fee_rate: float, slippage: float = 0.0005, maker_fee_rate: float | None = None,
                 quote: str = "TMN", tmn_fee_rate: float | None = None):
        self.feed, self.fee, self.slip = feed, fee_rate, slippage
        # USDT markets and TMN markets have different fees: with a USDT satellite, `fee_rate` is the USDT-market
        # fee and `tmn_fee_rate` prices the core's USDT<->TMN conversions
        self.tmn_fee = fee_rate if tmn_fee_rate is None else tmn_fee_rate
        self.maker_fee = fee_rate if maker_fee_rate is None else maker_fee_rate
        self.quote = quote
        self.cash_by = {"TMN": cash, "USDT": 0.0}
        self.holdings: dict[str, float] = {}
        self.fees_paid = 0.0                        # in each trade's quote currency (see fees_by)
        self.fees_by: dict[str, float] = {}
        self.orders: dict[str, dict] = {}
        self._seq = 0

    def _taker(self, quote: str) -> float:
        return self.tmn_fee if quote == "TMN" and self.quote != "TMN" else self.fee

    def _bag(self, asset: str) -> dict[str, float]:
        return self.cash_by if asset in CASH_ASSETS else self.holdings

    def _fee(self, asset: str, amount: float) -> None:
        self.fees_paid += amount
        self.fees_by[asset] = self.fees_by.get(asset, 0.0) + amount

    async def cash(self) -> float:
        return self.cash_by[self.quote]

    async def total_cash(self) -> float:
        return self.cash_by[self.quote] + sum(o["qty"] * o["price"] for o in self.orders.values()
                                              if o["status"] == "open" and o["side"] == "BUY" and o["quote"] == self.quote)

    async def holding(self, asset: str) -> float:
        return self._bag(asset).get(asset, 0.0)

    async def holding_total(self, asset: str) -> float:
        return await self.holding(asset) + sum(o["qty"] for o in self.orders.values()
                                               if o["status"] == "open" and o["side"] == "SELL" and o["base"] == asset)

    async def wallet(self) -> dict[str, tuple[float, float]]:
        out = {a: (v, v) for a, v in {**self.cash_by, **self.holdings}.items()}
        for o in self.orders.values():
            if o["status"] != "open":
                continue
            asset, locked = (o["quote"], o["qty"] * o["price"]) if o["side"] == "BUY" else (o["base"], o["qty"])
            free, total = out.get(asset, (0.0, 0.0))
            out[asset] = (free, total + locked)
        return out

    async def place_limit(self, symbol: str, side: str, qty: float, price: float, base: str) -> str:
        quote = split_symbol(symbol)[1]
        if side == "BUY":
            if qty * price > self.cash_by[quote] + 1e-6:
                raise BrokerError(f"insufficient {quote}")
            self.cash_by[quote] -= qty * price
        else:
            bag = self._bag(base)
            qty = min(qty, bag.get(base, 0.0))
            if qty <= 0:
                raise BrokerError(f"nothing to sell for {symbol}")
            bag[base] -= qty
        self._seq += 1
        oid = f"SIM-{self._seq}"
        self.orders[oid] = {"symbol": symbol, "side": side, "qty": qty, "price": price, "base": base, "quote": quote,
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
                self._fee(o["quote"], fee)
                if o["side"] == "BUY":
                    bag = self._bag(o["base"])
                    bag[o["base"]] = bag.get(o["base"], 0.0) + o["qty"] * (1 - self.maker_fee)
                else:
                    self.cash_by[o["quote"]] += gross - fee
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
                self.cash_by[o["quote"]] += o["qty"] * o["price"]
            else:
                bag = self._bag(o["base"])
                bag[o["base"]] = bag.get(o["base"], 0.0) + o["qty"]
        return self._state(order_id)

    async def buy(self, symbol: str, quote_amount: float, base: str, price_hint: float | None = None) -> Fill:
        quote = split_symbol(symbol)[1]
        if quote_amount > self.cash_by[quote] + 1e-6:
            raise BrokerError(f"insufficient {quote}")
        price = (await self.feed.quote(symbol))["ask"] * (1 + self.slip)
        fee = quote_amount * self._taker(quote)
        qty = (quote_amount - fee) / price
        self.cash_by[quote] -= quote_amount
        bag = self._bag(base)
        bag[base] = bag.get(base, 0.0) + qty
        self._fee(quote, fee)
        return Fill(qty, price, quote_amount)

    async def sell(self, symbol: str, qty: float, base: str, price_hint: float | None = None) -> Fill:
        quote = split_symbol(symbol)[1]
        bag = self._bag(base)
        qty = min(qty, bag.get(base, 0.0))
        if qty <= 0:
            raise BrokerError(f"nothing to sell for {symbol}")
        price = (price_hint or (await self.feed.quote(symbol))["bid"]) * (1 - self.slip)
        gross = qty * price
        fee = gross * self._taker(quote)
        bag[base] -= qty
        self.cash_by[quote] += gross - fee
        self._fee(quote, fee)
        return Fill(qty, price, gross - fee)

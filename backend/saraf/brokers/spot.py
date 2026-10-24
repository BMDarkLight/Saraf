from __future__ import annotations

import math

import time
import uuid
from typing import Any

from ..config import Settings
from ..wallex import WallexClient, WallexError
from .base import BrokerError, JsonState, MarketInfo, SpotBroker, fmt_num, round_down, safe_float, wrap_wallex_error

PAPER_FEE = 0.002  # Wallex spot taker fee is ~0.2-0.25%


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


async def _resolve_quantity(info: dict, side: str, quantity: float | None, quote_amount: float | None,
                            price: float | None) -> float:
    if quantity is None and quote_amount is None:
        raise BrokerError("either quantity (base asset) or quote_amount must be given")
    if quantity is None:
        ref = price or (info["ask"] if side == "BUY" else info["bid"]) or info["last"]
        if not ref:
            raise BrokerError("no reference price available to convert quote_amount to quantity")
        quantity = quote_amount / ref
    quantity = round_down(quantity, info["qty_decimals"])
    if quantity <= 0 or quantity < info["min_qty"]:
        raise BrokerError(f"quantity {quantity} is below the market minimum {info['min_qty']}")
    return quantity


def market_orders_refused(msg: str) -> bool:
    """Wallex's answer when a market does not take market orders: «امکان ثبت سفارش قیمت بازار در … وجود ندارد»."""
    return "قیمت بازار" in msg or "market order" in msg.lower()


MAX_WALK = 0.02            # how far through the book a refused market order may go as a limit

LIVE_BUY_HEADROOM = 0.005   # 0.3% taker fee on the challenge account + 0.2% for the ask moving


class LiveSpotBroker(SpotBroker):
    mode = "live"

    def __init__(self, client: WallexClient):
        self.client = client
        self.info = MarketInfo(client)

    async def balances(self) -> dict[str, dict]:
        try:
            raw = await self.client.balances()
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
            book = await self.client.depth(symbol)
            asks = [safe_float(lv["price"]) for lv in book.get("ask", []) if safe_float(lv["price"])]
            if asks:   # round up to the tick: a buy limit a hair under the ask would rest instead of filling
                step = 10 ** info["price_decimals"]
                type, price = "LIMIT", math.ceil(min(asks) * step - 1e-9) / step
            quote_amount = quote_amount / (1 + LIVE_BUY_HEADROOM)
        qty = await _resolve_quantity(info, side, quantity, quote_amount, price)
        if type == "LIMIT" and price is None:
            raise BrokerError("LIMIT orders need a price")
        try:
            return await self.client.place_order(
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
            return await self.client.place_order(symbol, side, "LIMIT", fmt_num(qty, info["qty_decimals"]),
                                                 price=fmt_num(price, info["price_decimals"]))
        except WallexError as e:
            raise wrap_wallex_error(e)

    async def _through_book(self, symbol: str, side: str, qty: float, decimals: int) -> float:
        """The price that fills `qty` against the order book now (the worst level needed), on the tick."""
        book = await self.client.depth(symbol)
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
            await self.client.cancel_order(order_id)
            return {"order_id": order_id, "status": "CANCELED"}
        except WallexError as e:
            raise wrap_wallex_error(e)

    async def open_orders(self, symbol=None) -> list[dict]:
        try:
            return await self.client.open_orders(symbol)
        except WallexError as e:
            raise wrap_wallex_error(e)

    async def get_order(self, order_id: str) -> dict:
        try:
            return await self.client.get_order(order_id)
        except WallexError as e:
            raise wrap_wallex_error(e)

    async def trades(self, symbol=None) -> list[dict]:
        try:
            return await self.client.my_trades(symbol)
        except WallexError as e:
            raise wrap_wallex_error(e)


class PaperSpotBroker(SpotBroker):
    """Simulates the Wallex spot matching engine against the live order book."""

    mode = "paper"

    def __init__(self, client: WallexClient, settings: Settings):
        self.client = client
        self.info = MarketInfo(client)
        self.state = JsonState(
            settings.data_dir / "paper_spot.json",
            {"balances": {"USDT": settings.paper_usdt, "TMN": settings.paper_tmn}, "locked": {}, "orders": {}, "trades": []},
        )

    # -- helpers
    def _bal(self, asset: str) -> float:
        return float(self.state.data["balances"].get(asset, 0.0))

    def _add(self, asset: str, amount: float) -> None:
        b = self.state.data["balances"]
        b[asset] = b.get(asset, 0.0) + amount
        if abs(b[asset]) < 1e-12:
            b.pop(asset)

    def _lock(self, asset: str, amount: float) -> None:
        lk = self.state.data["locked"]
        lk[asset] = lk.get(asset, 0.0) + amount
        if abs(lk[asset]) < 1e-12:
            lk.pop(asset)

    def _free(self, asset: str) -> float:
        return self._bal(asset) - float(self.state.data["locked"].get(asset, 0.0))

    async def _walk_book(self, symbol: str, side: str, qty: float, limit: float | None,
                         budget: float | None = None) -> tuple[float, float]:
        """Fill `qty` against the live book, spending at most `budget` quote (BUY).
        Returns (filled_qty, avg_price)."""
        book = await self.client.depth(symbol)
        levels = sorted(book["ask"] if side == "BUY" else book["bid"],
                        key=lambda lv: float(lv["price"]), reverse=side == "SELL")
        filled, cost = 0.0, 0.0
        for lv in levels:
            p, q = float(lv["price"]), float(lv["quantity"])
            if limit is not None and ((side == "BUY" and p > limit) or (side == "SELL" and p < limit)):
                break
            take = min(q, qty - filled)
            if budget is not None:
                take = min(take, (budget - cost) / p)
                if take <= 1e-12:
                    break
            filled += take
            cost += take * p
            if filled >= qty - 1e-12:
                break
        return filled, (cost / filled if filled else 0.0)

    def _settle(self, order: dict, qty: float, price: float) -> None:
        base, quote = order["base"], order["quote"]
        notional = qty * price
        if order["side"] == "BUY":
            self._add(quote, -notional)
            self._add(base, qty * (1 - PAPER_FEE))
            fee, fee_asset = qty * PAPER_FEE, base
        else:
            self._add(base, -qty)
            self._add(quote, notional * (1 - PAPER_FEE))
            fee, fee_asset = notional * PAPER_FEE, quote
        prev_qty = float(order["executedQty"])
        new_qty = prev_qty + qty
        order["executedPrice"] = (float(order["executedPrice"]) * prev_qty + price * qty) / new_qty
        order["executedQty"] = new_qty
        order["executedSum"] = float(order["executedSum"]) + notional
        order["executedPercent"] = round(100 * new_qty / float(order["origQty"]), 2)
        if new_qty >= float(order["origQty"]) - 1e-12:
            order["status"], order["active"] = "FILLED", False
        self.state.data["trades"].insert(0, {
            "symbol": order["symbol"], "quantity": qty, "price": price, "sum": notional, "fee": fee,
            "feeAsset": fee_asset, "isBuyer": order["side"] == "BUY", "timestamp": _now(),
            "clientOrderId": order["clientOrderId"],
        })
        del self.state.data["trades"][200:]

    async def _top_of_book(self, symbol: str) -> dict:
        """Best bid/ask from the order book; /v1/markets stats lag it (found in shadow testing)."""
        try:
            book = await self.client.depth(symbol)
            return {"bid": max(float(l["price"]) for l in book["bid"]), "ask": min(float(l["price"]) for l in book["ask"])}
        except Exception:
            return await self.info.quote(symbol)

    async def _match_resting(self) -> None:
        changed = False
        for order in list(self.state.data["orders"].values()):
            if not order["active"]:
                continue
            remaining = float(order["origQty"]) - float(order["executedQty"])
            q = await self._top_of_book(order["symbol"])
            crossed = (order["side"] == "BUY" and q["ask"] and q["ask"] <= order["price"]) or (
                order["side"] == "SELL" and q["bid"] and q["bid"] >= order["price"])
            if crossed:
                self._release(order, remaining)
                self._settle(order, remaining, float(order["price"]))
                changed = True
        if changed:
            self.state.save()

    def _release(self, order: dict, qty: float) -> None:
        if order["side"] == "BUY":
            self._lock(order["quote"], -qty * float(order["price"]))
        else:
            self._lock(order["base"], -qty)

    # -- interface
    async def balances(self) -> dict[str, dict]:
        await self._match_resting()
        out = {}
        for asset, total in self.state.data["balances"].items():
            locked = float(self.state.data["locked"].get(asset, 0.0))
            out[asset] = {"free": total - locked, "locked": locked, "total": total}
        return out

    async def place_order(self, symbol, side, type, quantity=None, price=None, quote_amount=None) -> dict:
        side, type = side.upper(), type.upper()
        info = await self.info.quote(symbol)
        qty = await _resolve_quantity(info, side, quantity, quote_amount, price)
        if type == "LIMIT":
            if price is None:
                raise BrokerError("LIMIT orders need a price")
            price = round_down(price, info["price_decimals"]) if info["price_decimals"] else float(int(price))
        ref = price or (info["ask"] if side == "BUY" else info["bid"]) or info["last"]
        if qty * ref < info["min_notional"]:
            raise BrokerError(f"order value {qty * ref:.4f} {info['quote']} is below min notional {info['min_notional']}")

        if side == "BUY":
            # A market buy sized in quote ("spend all my TMN") spends at most quote_amount: the book walk
            # caps the cost and the fee comes out of the bought asset. Only quantity-sized buys need the
            # 1% slippage buffer; applying it here made "spend everything" always fail.
            sized_in_quote = type == "MARKET" and quote_amount is not None and quantity is None
            need_asset, need = info["quote"], (quote_amount if sized_in_quote else qty * ref * 1.01)
        else:
            need_asset, need = info["base"], qty
        if self._free(need_asset) < need - 1e-9:
            raise BrokerError(f"insufficient {need_asset}: need ~{need:.8g}, free {self._free(need_asset):.8g}")

        oid = f"{type}-{uuid.uuid4()}"
        order: dict[str, Any] = {
            "symbol": info["symbol"], "type": type, "side": side, "price": price, "origQty": qty,
            "origSum": qty * ref, "executedPrice": 0.0, "executedQty": 0.0, "executedSum": 0.0,
            "executedPercent": 0, "status": "NEW", "active": True, "clientOrderId": oid,
            "created_at": _now(), "base": info["base"], "quote": info["quote"], "paper": True,
        }
        self.state.data["orders"][oid] = order

        # BUY never spends more than the free quote balance (or quote_amount, when given)
        budget = None
        if side == "BUY":
            budget = self._free(info["quote"])
            if quote_amount is not None and quantity is None:
                budget = min(budget, quote_amount)
        filled, avg = await self._walk_book(symbol, side, qty, price if type == "LIMIT" else None, budget)
        filled = round_down(filled, info["qty_decimals"]) if filled else 0.0
        if filled:
            self._settle(order, filled, avg)
        remaining = qty - filled
        if remaining > 1e-12:
            if type == "MARKET":
                order["status"], order["active"] = ("FILLED" if filled else "CANCELED"), False
                order["note"] = "book liquidity exhausted; remainder not filled"
            else:
                order["status"] = "NEW"
                if side == "BUY":
                    self._lock(info["quote"], remaining * price)
                else:
                    self._lock(info["base"], remaining)
        self.state.save()
        return order

    async def cancel_order(self, order_id: str) -> dict:
        order = self.state.data["orders"].get(order_id)
        if not order:
            raise BrokerError(f"order {order_id} not found")
        if order["active"]:
            self._release(order, float(order["origQty"]) - float(order["executedQty"]))
            order["status"], order["active"] = "CANCELED", False
            self.state.save()
        return order

    async def open_orders(self, symbol=None) -> list[dict]:
        await self._match_resting()
        return [o for o in self.state.data["orders"].values()
                if o["active"] and (not symbol or o["symbol"] == symbol.upper())]

    async def get_order(self, order_id: str) -> dict:
        await self._match_resting()
        order = self.state.data["orders"].get(order_id)
        if not order:
            raise BrokerError(f"order {order_id} not found")
        return order

    async def trades(self, symbol=None) -> list[dict]:
        return [t for t in self.state.data["trades"] if not symbol or t["symbol"] == symbol.upper()][:50]

    def reset(self, usdt: float, tmn: float) -> None:
        self.state.data = {"balances": {"USDT": usdt, "TMN": tmn}, "locked": {}, "orders": {}, "trades": []}
        self.state.save()

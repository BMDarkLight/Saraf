"""Futures (leveraged) trading.

Wallex's public API docs don't cover futures yet, so there are two brokers:
- PaperFuturesBroker: a full isolated-margin perpetuals simulator priced off live
  Wallex order books, restricted to the markets Wallex flags as leverage-enabled.
- WallexFuturesBroker: the live adapter. Its endpoints are placeholders until the
  real futures API is published; every method raises with a clear message.
"""

from __future__ import annotations

import time
import uuid

from ..config import Settings
from ..wallex import WallexClient
from .base import BrokerError, FuturesBroker, JsonState, MarketInfo

TAKER_FEE = 0.0006
MAINT_MARGIN = 0.005


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def liquidation_price(side: str, entry: float, leverage: float) -> float:
    if side == "LONG":
        return entry * (1 - 1 / leverage + MAINT_MARGIN)
    return entry * (1 + 1 / leverage - MAINT_MARGIN)


class PaperFuturesBroker(FuturesBroker):
    mode = "paper"

    def __init__(self, client: WallexClient, settings: Settings):
        self.client = client
        self.info = MarketInfo(client)
        self.settings = settings
        self.state = JsonState(
            settings.data_dir / "paper_futures.json",
            {"wallets": {"USDT": settings.paper_usdt, "TMN": settings.paper_tmn},
             "positions": {}, "orders": {}, "history": []},
        )

    async def leverage_markets(self) -> dict[str, float]:
        return {m["symbol"]: float(m.get("max_leverage") or 1)
                for m in await self.client.markets_detail() if m.get("is_margin")}

    async def _validate(self, symbol: str, leverage: float) -> float:
        lev_markets = await self.leverage_markets()
        sym = symbol.upper()
        if sym not in lev_markets:
            raise BrokerError(f"{sym} is not a leverage-enabled market. Available: {', '.join(sorted(lev_markets))}")
        cap = min(lev_markets[sym], self.settings.max_leverage)
        if not 1 <= leverage <= cap:
            raise BrokerError(f"leverage must be between 1 and {cap:g} for {sym}")
        return cap

    def _position_view(self, p: dict, mark: float) -> dict:
        direction = 1 if p["side"] == "LONG" else -1
        upnl = (mark - p["entry_price"]) * p["quantity"] * direction
        return {**p, "mark_price": mark, "unrealized_pnl": upnl,
                "roe_percent": 100 * upnl / p["margin"] if p["margin"] else 0.0,
                "notional": mark * p["quantity"]}

    def _record(self, event: dict) -> dict:
        event["time"] = _now()
        self.state.data["history"].insert(0, event)
        del self.state.data["history"][300:]
        return event

    def _open_from(self, symbol: str, side: str, leverage: float, qty: float, price: float, quote: str,
                   tp: float | None, sl: float | None) -> dict:
        notional = qty * price
        margin = notional / leverage
        fee = notional * TAKER_FEE
        wallets = self.state.data["wallets"]
        if wallets.get(quote, 0) < margin + fee:
            raise BrokerError(f"insufficient {quote} margin: need {margin + fee:.6g}, have {wallets.get(quote, 0):.6g}")
        wallets[quote] -= margin + fee
        pid = f"POS-{uuid.uuid4().hex[:10]}"
        pos = {"id": pid, "symbol": symbol, "side": side, "leverage": leverage, "quantity": qty,
               "entry_price": price, "margin": margin, "quote": quote, "take_profit": tp, "stop_loss": sl,
               "liquidation_price": liquidation_price(side, price, leverage), "fees_paid": fee,
               "opened_at": _now(), "paper": True}
        self.state.data["positions"][pid] = pos
        self._record({"type": "OPEN", "position_id": pid, "symbol": symbol, "side": side, "quantity": qty,
                      "price": price, "leverage": leverage, "fee": fee})
        return pos

    def _close(self, pid: str, fraction: float, price: float, reason: str) -> dict:
        p = self.state.data["positions"][pid]
        direction = 1 if p["side"] == "LONG" else -1
        qty = p["quantity"] * fraction
        margin_part = p["margin"] * fraction
        pnl = (price - p["entry_price"]) * qty * direction
        fee = qty * price * TAKER_FEE
        if reason == "LIQUIDATION":
            pnl, fee = -margin_part, 0.0
        self.state.data["wallets"][p["quote"]] = self.state.data["wallets"].get(p["quote"], 0) + max(margin_part + pnl - fee, 0)
        if fraction >= 0.999999:
            del self.state.data["positions"][pid]
        else:
            p["quantity"] -= qty
            p["margin"] -= margin_part
        return self._record({"type": "CLOSE", "reason": reason, "position_id": pid, "symbol": p["symbol"],
                             "side": p["side"], "quantity": qty, "entry_price": p["entry_price"],
                             "exit_price": price, "realized_pnl": pnl - fee, "quote": p["quote"]})

    # ------------------------------------------------------------ interface
    async def tick(self) -> list[dict]:
        events: list[dict] = []
        for oid, o in list(self.state.data["orders"].items()):
            q = await self.info.quote(o["symbol"])
            px = q["ask"] if o["side"] == "LONG" else q["bid"]
            if px and ((o["side"] == "LONG" and px <= o["price"]) or (o["side"] == "SHORT" and px >= o["price"])):
                del self.state.data["orders"][oid]
                self.state.data["wallets"][o["quote"]] += o["reserved"]
                try:
                    pos = self._open_from(o["symbol"], o["side"], o["leverage"], o["quantity"], o["price"],
                                          o["quote"], o["take_profit"], o["stop_loss"])
                    events.append({"type": "LIMIT_FILLED", "order_id": oid, "position_id": pos["id"]})
                except BrokerError as e:
                    events.append({"type": "LIMIT_REJECTED", "order_id": oid, "reason": str(e)})
        for pid, p in list(self.state.data["positions"].items()):
            q = await self.info.quote(p["symbol"])
            exit_px = q["bid"] if p["side"] == "LONG" else q["ask"]
            mark = q["mark"]
            if not mark:
                continue
            long = p["side"] == "LONG"
            if (long and mark <= p["liquidation_price"]) or (not long and mark >= p["liquidation_price"]):
                events.append(self._close(pid, 1.0, p["liquidation_price"], "LIQUIDATION"))
            elif p.get("stop_loss") and ((long and mark <= p["stop_loss"]) or (not long and mark >= p["stop_loss"])):
                events.append(self._close(pid, 1.0, exit_px or mark, "STOP_LOSS"))
            elif p.get("take_profit") and ((long and mark >= p["take_profit"]) or (not long and mark <= p["take_profit"])):
                events.append(self._close(pid, 1.0, exit_px or mark, "TAKE_PROFIT"))
        if events:
            self.state.save()
        return events

    async def account(self) -> dict:
        await self.tick()
        positions = await self.positions()
        out = {}
        for quote, free in self.state.data["wallets"].items():
            mine = [p for p in positions if p["quote"] == quote]
            used = sum(p["margin"] for p in mine)
            reserved = sum(o["reserved"] for o in self.state.data["orders"].values() if o["quote"] == quote)
            upnl = sum(p["unrealized_pnl"] for p in mine)
            out[quote] = {"available": free, "position_margin": used, "order_margin": reserved,
                          "unrealized_pnl": upnl, "equity": free + used + reserved + upnl}
        return {"mode": self.mode, "wallets": out, "open_positions": len(positions)}

    async def positions(self) -> list[dict]:
        out = []
        for p in self.state.data["positions"].values():
            q = await self.info.quote(p["symbol"])
            out.append(self._position_view(p, q["mark"]))
        return out

    async def open_position(self, symbol, side, leverage, margin=None, quantity=None, order_type="MARKET",
                            price=None, take_profit=None, stop_loss=None) -> dict:
        side, order_type, symbol = side.upper(), order_type.upper(), symbol.upper()
        if side in ("BUY", "LONG"):
            side = "LONG"
        elif side in ("SELL", "SHORT"):
            side = "SHORT"
        else:
            raise BrokerError("side must be LONG or SHORT")
        await self._validate(symbol, leverage)
        if len(self.state.data["positions"]) >= self.settings.max_open_positions:
            raise BrokerError(f"max open positions ({self.settings.max_open_positions}) reached")
        q = await self.info.quote(symbol)
        entry = (q["ask"] if side == "LONG" else q["bid"]) if order_type == "MARKET" else price
        if not entry:
            raise BrokerError("no price available" if order_type == "MARKET" else "LIMIT orders need a price")
        if quantity is None:
            if margin is None:
                raise BrokerError("give either margin (collateral in quote asset) or quantity (base asset)")
            quantity = margin * leverage / entry
        if quantity <= 0:
            raise BrokerError("quantity must be positive")
        liq = liquidation_price(side, entry, leverage)
        if stop_loss and ((side == "LONG" and stop_loss <= liq) or (side == "SHORT" and stop_loss >= liq)):
            raise BrokerError(f"stop loss {stop_loss} is beyond the liquidation price {liq:.6g}")

        if order_type == "MARKET":
            pos = self._open_from(symbol, side, leverage, quantity, entry, q["quote"], take_profit, stop_loss)
            self.state.save()
            return self._position_view(pos, q["mark"])

        reserved = quantity * entry / leverage * (1 + TAKER_FEE * leverage)
        if self.state.data["wallets"].get(q["quote"], 0) < reserved:
            raise BrokerError(f"insufficient {q['quote']} to reserve {reserved:.6g} for this limit order")
        self.state.data["wallets"][q["quote"]] -= reserved
        oid = f"FORD-{uuid.uuid4().hex[:10]}"
        order = {"id": oid, "symbol": symbol, "side": side, "leverage": leverage, "quantity": quantity,
                 "price": entry, "quote": q["quote"], "reserved": reserved, "take_profit": take_profit,
                 "stop_loss": stop_loss, "created_at": _now(), "status": "NEW", "paper": True}
        self.state.data["orders"][oid] = order
        self.state.save()
        return order

    async def close_position(self, position_id: str, fraction: float = 1.0) -> dict:
        p = self.state.data["positions"].get(position_id)
        if not p:
            raise BrokerError(f"position {position_id} not found")
        if not 0 < fraction <= 1:
            raise BrokerError("fraction must be in (0, 1]")
        q = await self.info.quote(p["symbol"])
        price = (q["bid"] if p["side"] == "LONG" else q["ask"]) or q["mark"]
        ev = self._close(position_id, fraction, price, "MANUAL")
        self.state.save()
        return ev

    async def set_tp_sl(self, position_id: str, take_profit, stop_loss) -> dict:
        p = self.state.data["positions"].get(position_id)
        if not p:
            raise BrokerError(f"position {position_id} not found")
        if take_profit is not None:
            p["take_profit"] = take_profit or None
        if stop_loss is not None:
            p["stop_loss"] = stop_loss or None
        self.state.save()
        return p

    async def open_orders(self) -> list[dict]:
        await self.tick()
        return list(self.state.data["orders"].values())

    async def cancel_order(self, order_id: str) -> dict:
        o = self.state.data["orders"].pop(order_id, None)
        if not o:
            raise BrokerError(f"order {order_id} not found")
        self.state.data["wallets"][o["quote"]] += o["reserved"]
        self.state.save()
        return {**o, "status": "CANCELED"}

    async def history(self, limit: int = 20) -> list[dict]:
        return self.state.data["history"][:limit]

    def reset(self, usdt: float, tmn: float) -> None:
        self.state.data = {"wallets": {"USDT": usdt, "TMN": tmn}, "positions": {}, "orders": {}, "history": []}
        self.state.save()


class WallexFuturesBroker(FuturesBroker):
    """Live Wallex futures adapter — wire the real endpoints in here once available."""

    mode = "live"

    def __init__(self, client: WallexClient, base_url: str):
        self.client = client
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

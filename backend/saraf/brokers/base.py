"""Broker interfaces. The agent only talks to these, so paper brokers (which work on any venue's market data)
and a venue's live brokers (venues/<name>/brokers.py) are interchangeable."""

from __future__ import annotations

import json
import math
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ..venues.base import Venue


class BrokerError(Exception):
    pass


def safe_float(x) -> float:
    """Venues return "-" or nothing instead of a number for inactive markets."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def round_down(value: float, decimals: int) -> float:
    q = 10 ** max(decimals, 0)
    return math.floor(value * q + 1e-9) / q


def fmt_num(value: float, decimals: int) -> str:
    return f"{round_down(value, decimals):.{max(decimals, 0)}f}"


class MarketInfo:
    """Symbol metadata + live top-of-book, backed by the cached /v1/markets call."""

    def __init__(self, venue: Venue):
        self.venue = venue

    async def get(self, symbol: str) -> dict:
        markets = await self.venue.markets()
        m = markets.get(symbol.upper())
        if not m:
            raise BrokerError(f"unknown market {symbol!r}. Use list_markets/search to find valid symbols.")
        return m

    async def quote(self, symbol: str) -> dict:
        m = await self.get(symbol)
        st = m.get("stats") or {}
        bid, ask, last = (safe_float(st.get(k)) for k in ("bidPrice", "askPrice", "lastPrice"))
        mid = (bid + ask) / 2 if bid and ask else last
        return {"symbol": m["symbol"], "bid": bid, "ask": ask, "last": last, "mark": mid or last,
                "base": m["baseAsset"], "quote": m["quoteAsset"],
                "qty_decimals": int(m.get("stepSize") or 0), "price_decimals": int(m.get("tickSize") or 0),
                "min_qty": float(m.get("minQty") or 0), "min_notional": float(m.get("minNotional") or 0)}


class JsonState:
    def __init__(self, path: Path, default: dict):
        self.path = path
        self.data = json.loads(path.read_text()) if path.exists() else default

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2))
        tmp.replace(self.path)


class SpotBroker(ABC):
    mode: str

    @abstractmethod
    async def balances(self) -> dict[str, dict]: ...

    @abstractmethod
    async def place_order(self, symbol: str, side: str, type: str, quantity: float | None = None,
                          price: float | None = None, quote_amount: float | None = None) -> dict: ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> dict: ...

    @abstractmethod
    async def open_orders(self, symbol: str | None = None) -> list[dict]: ...

    @abstractmethod
    async def get_order(self, order_id: str) -> dict: ...

    @abstractmethod
    async def trades(self, symbol: str | None = None) -> list[dict]: ...


class FuturesBroker(ABC):
    mode: str

    @abstractmethod
    async def account(self) -> dict: ...

    @abstractmethod
    async def positions(self) -> list[dict]: ...

    @abstractmethod
    async def open_position(self, symbol: str, side: str, leverage: float, margin: float | None = None,
                            quantity: float | None = None, order_type: str = "MARKET", price: float | None = None,
                            take_profit: float | None = None, stop_loss: float | None = None) -> dict: ...

    @abstractmethod
    async def close_position(self, position_id: str, fraction: float = 1.0) -> dict: ...

    @abstractmethod
    async def set_tp_sl(self, position_id: str, take_profit: float | None, stop_loss: float | None) -> dict: ...

    @abstractmethod
    async def open_orders(self) -> list[dict]: ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> dict: ...

    @abstractmethod
    async def history(self, limit: int = 20) -> list[dict]: ...

    async def tick(self) -> list[dict]:
        """Advance simulated state (fills, TP/SL, liquidations). Returns triggered events."""
        return []


def state_path(settings, venue: Venue, kind: str) -> Path:
    """A paper broker's state file. The default venue keeps the plain name (existing data dirs resume as before)."""
    suffix = "" if venue.name == settings.default_venue else f"-{venue.name}"
    return settings.data_dir / f"{kind}{suffix}.json"


def to_jsonable(x: Any) -> Any:
    return json.loads(json.dumps(x, default=str))

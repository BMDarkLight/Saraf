"""The venue interface: one exchange, broker or rate source that Saraf can read (and maybe trade on).

Everything above this layer (agent tools, analytics, paper brokers, the engine, the server) talks to a `Venue`,
never to an exchange's own client, so a new venue is one adapter plus a `register()` call (see registry.py).

Market data uses one schema for every venue (the shapes below). It is the shape the first adapter
already returned, so its client needs no translation; other adapters normalise their API into it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal, TypedDict

if TYPE_CHECKING:
    from ..brokers.base import FuturesBroker, SpotBroker
    from ..config import Settings

AssetClass = Literal["crypto", "fx", "equity"]


class VenueError(Exception):
    """A venue's API failed or refused a request (network error, HTTP error, rejected order)."""

    def __init__(self, message: str, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


class VenueUnsupported(VenueError):
    """The venue has no such capability (e.g. trading on a data-only venue)."""


# ------------------------------------------------------------------------------------------- shared schema
class MarketStats(TypedDict, total=False):
    bidPrice: str | float
    askPrice: str | float
    lastPrice: str | float
    # percent changes and 24h figures; "-" or missing when the venue has no value
    #   "24h_ch", "7d_ch", "24h_highPrice", "24h_lowPrice", "24h_volume", "24h_quoteVolume"
    direction: dict           # {"BUY": pct, "SELL": pct} share of recent orders, when known


class Market(TypedDict, total=False):
    symbol: str               # the venue's symbol, e.g. BTCUSDT or a TSE ticker
    baseAsset: str
    quoteAsset: str           # USDT, TMN, IRR, ...
    faBaseAsset: str          # Persian name, when the venue has one
    enBaseAsset: str
    stepSize: int             # quantity decimals
    tickSize: int             # price decimals
    minQty: float
    minNotional: float
    stats: MarketStats


class BookLevel(TypedDict):
    price: float | str
    quantity: float | str


class Book(TypedDict):
    bid: list[BookLevel]      # any order; consumers sort
    ask: list[BookLevel]


class Candle(TypedDict):
    t: int                    # bar open, unix seconds
    o: float
    h: float
    l: float
    c: float
    v: float


class Trade(TypedDict, total=False):
    symbol: str
    price: float | str
    quantity: float | str
    sum: float | str          # price x quantity
    isBuyOrder: bool          # taker side
    timestamp: str


class FuturesMarket(TypedDict):
    symbol: str
    max_leverage: float
    price: float | str


@dataclass(frozen=True)
class Capabilities:
    asset_class: AssetClass
    market_data: bool = True          # markets, tickers
    order_book: bool = True           # depth()
    public_trades: bool = True        # trades()
    candles: tuple[str, ...] = ("1", "60", "180", "360", "720", "1D")   # resolutions candles() accepts
    trading: bool = False             # live spot orders (live_spot_broker)
    futures: bool = False             # leverage markets (futures_markets); paper futures need this
    account: bool = False             # balances / order history with an API key


class Venue(ABC):
    name: ClassVar[str]               # registry key, e.g. "wallex"
    title: ClassVar[str]              # display name
    title_fa: ClassVar[str] = ""
    capabilities: ClassVar[Capabilities]

    @property
    def has_key(self) -> bool:
        """Credentials for account/trading endpoints are configured."""
        return False

    def describe(self) -> dict:
        return {"name": self.name, "title": self.title, "title_fa": self.title_fa or self.title,
                "has_key": self.has_key, **asdict(self.capabilities)}

    async def aclose(self) -> None:
        pass

    # ------------------------------------------------------------------------------------------ market data
    @abstractmethod
    async def markets(self) -> dict[str, Market]:
        """Every market with live stats, keyed by symbol. Implementations should cache it for a few seconds."""

    @abstractmethod
    async def depth(self, symbol: str) -> Book: ...

    @abstractmethod
    async def trades(self, symbol: str) -> list[Trade]:
        """Latest public trades, newest first."""

    @abstractmethod
    async def candles(self, symbol: str, resolution: str, start: int, end: int | None = None) -> list[Candle]: ...

    async def futures_markets(self) -> list[FuturesMarket]:
        """Markets that take leverage. Empty for spot-only venues."""
        return []

    async def coin_stats(self, key: str) -> Any:
        raise VenueUnsupported(f"{self.title} has no global coin stats")

    # -------------------------------------------------------------------------------------- account / orders
    def _no(self, what: str):
        raise VenueUnsupported(f"{self.title} does not support {what}")

    async def balances(self) -> dict[str, dict]:
        """{asset: {"value": total, "locked": locked}} of the account behind the API key."""
        self._no("account balances")

    async def place_order(self, symbol: str, side: str, type: str, quantity: str, price: str | None = None,
                          client_id: str | None = None) -> dict:
        self._no("placing orders")

    async def get_order(self, order_id: str) -> dict:
        self._no("orders")

    async def cancel_order(self, order_id: str) -> Any:
        self._no("orders")

    async def open_orders(self, symbol: str | None = None) -> list[dict]:
        self._no("orders")

    async def my_trades(self, symbol: str | None = None, side: str | None = None) -> list[dict]:
        self._no("trade history")

    async def account_warning(self) -> str | None:
        """A problem with the configured credentials worth showing the user (e.g. a key for the wrong account)."""
        return None

    def explain_order_error(self, message: str, key_flagged: bool) -> str:
        """A live order's error as the user should read it. A venue may turn its own error codes into advice;
        key_flagged says account_warning() complained about the key."""
        return message

    # -------------------------------------------------------------------------------------------- brokers
    def live_spot_broker(self) -> "SpotBroker":
        """The broker that sends real spot orders here. Paper brokers work on any venue's market data."""
        self._no("live trading")

    def live_futures_broker(self, settings: "Settings") -> "FuturesBroker":
        self._no("live futures")

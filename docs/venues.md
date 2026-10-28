# Venues

Saraf talks to markets through **venues**: one adapter per crypto exchange, stock exchange or currency-rate source,
behind one interface (`backend/saraf/venues/base.py`). The agent, the analytics, the paper brokers and the engine
only ever see a `Venue`, so a new exchange is one new adapter.

## Supported venues

| key | asset class | market data | order book | candles | trading | status |
|---|---|---|---|---|---|---|
| `wallex` | crypto, TMN and USDT markets | ✓ | ✓ | 1m to 1D | spot, live and paper; futures paper only | full adapter |
| `tse` | Tehran Stock Exchange equities, IRR | ✓ (TSETMC) | 5 levels | daily | none | read-only |
| Nobitex, Bitpin, Ramzinex | crypto | | | | | planned |
| Free-market currency and gold rates | fx | | | | | planned |

TSE has no official public trading API, so the `tse` venue raises `NotImplementedError` for orders. It never drives
a broker's private endpoints.

## Choosing a venue

- `DEFAULT_VENUE` is where orders go and what the dashboard shows.
- Every market-data tool and endpoint takes a `venue`, for example `GET /api/markets?venue=tse` or "show me the
  top TSE movers" in chat.
- `list_venues` and `GET /api/venues` describe what each venue supports.
- The engine takes `--venue` on `backtest` and `run`.

## The interface

```python
class Venue(ABC):
    name: str
    capabilities: Capabilities

    # market data, in the shared schema
    async def markets(self) -> dict[str, Market]
    async def depth(self, symbol) -> Book
    async def trades(self, symbol) -> list[Trade]
    async def candles(self, symbol, resolution, start, end=None) -> list[Candle]
    async def futures_markets(self) -> list[FuturesMarket]          # optional
    async def coin_stats(self, key)                                 # optional

    # account and orders, for venues that trade
    async def balances(self) -> dict[str, dict]
    async def place_order(self, symbol, side, type, quantity, price=None, client_id=None) -> dict
    async def get_order(self, order_id) / cancel_order(order_id) / open_orders(symbol=None) / my_trades(...)
    def live_spot_broker(self) -> SpotBroker
    def live_futures_broker(self, settings) -> FuturesBroker
    async def account_warning(self) -> str | None                   # e.g. an API key that can't trade
    def explain_order_error(self, message, key_flagged) -> str      # turn the venue's error codes into advice
```

`Capabilities` says what the venue can do, and tools check it before calling:

| flag | meaning |
|---|---|
| `asset_class` | `crypto`, `equity` or `fx` |
| `market_data`, `order_book`, `public_trades` | read endpoints the venue has |
| `trading` | live spot orders through `live_spot_broker()` |
| `futures` | leverage markets; paper futures need this |
| `account` | balances and order history with an API key |

Unsupported calls raise `VenueUnsupported`, and API failures raise `VenueError`. Both reach the model and the UI as
readable errors.

## Paper trading on any venue

`brokers/` holds the `SpotBroker` and `FuturesBroker` interfaces and paper versions that work on any venue:

- **Paper spot** fills orders by walking the venue's live order book level by level and charges a typical spot
  fee. A limit order fills whatever the book offers at its price or better, and the rest stays open with its funds
  locked.
- **Paper futures** is an isolated-margin perpetuals simulator priced off the venue's live order books, on the
  markets the venue flags as leverage-enabled, with limit orders, TP/SL and liquidation.

Each venue's paper state is kept in its own file in `data/`. `POST /api/paper/reset` resets it.

## Writing an adapter

```python
# backend/saraf/venues/nobitex/__init__.py
from ...config import Settings
from ..registry import register
from .client import NobitexClient      # a Venue subclass

@register("nobitex")
def _make(settings: Settings) -> NobitexClient:
    return NobitexClient(settings.nobitex_api_key)
```

1. Subclass `Venue` and implement `markets`, `depth`, `trades` and `candles` in the shared schema. Declare
   `Capabilities`.
2. For live trading, implement the order methods and return the venue's own broker from `live_spot_broker()`.
3. Register a factory with `@register("<key>")` and import the package in `saraf/venues/__init__.py`.
4. Add the venue's settings to `config.py` with its own prefix (`NOBITEX_API_KEY`).
5. Add it to `tests/test_venues.py`. The fake venue in `tests/conftest.py` shows the expected shapes.

# Venues

Saraf talks to markets through **venues**: one adapter per crypto exchange, stock exchange or currency-rate source,
behind one interface (`backend/saraf/venues/base.py`). The agent, the analytics, the paper brokers and the engine
only ever see a `Venue`, so a new exchange is one new adapter.

## Supported venues

| key | asset class | market data | order book | candles | trading | keys |
|---|---|---|---|---|---|---|
| `wallex` | crypto, TMN and USDT markets | ✓ | ✓ | 1m to 1D | spot, live and paper; futures paper only | API key |
| `nobitex` | crypto, TMN and USDT markets | ✓ | ✓ | 1m to 1D | spot, live and paper | API key + Ed25519 private key, or a login token |
| `bitpin` | crypto, TMN and USDT markets | ✓ | ✓ (top 20) | 1m to 1D | spot, live and paper | API key + secret |
| `ramzinex` | crypto, TMN and USDT markets | ✓ | ✓ | 1m to 1D | spot, live and paper | API key + secret |
| `tabdeal` | crypto, TMN and USDT markets | ✓ | ✓ | 1m to 1D | paper only | none |
| `binance` | crypto, USDT markets (global reference) | ✓ | ✓ | 1m to 1D | paper only | none |
| `tetherland` | fx: USDT/toman | price only | | | none | none |
| `navasan` | fx: free-market dollar, euro, coins, gold | price only | | | none | free API key |
| `tse` | Tehran Stock Exchange equities, IRR | ✓ (TSETMC) | 5 levels | daily | none | none |

**Prices are in toman everywhere.** Nobitex and Ramzinex quote their rial markets in rial; their adapters show them
as toman markets (`BTCTMN`, prices ÷ 10) and convert order prices back. Nobitex's candles are the exception: its
chart endpoint already answers in toman.

Notes per venue:

- **Nobitex** signs each private request with the key's Ed25519 private key (`Nobitex-Key`, `Nobitex-Signature`,
  `Nobitex-Timestamp`). The older login token from the panel also works.
- **Bitpin** has no bid, ask or volume in its tickers, so the market list shows the last price; the order book has
  the real top of book. Its access token lasts 15 minutes and is renewed automatically.
- **Ramzinex** names markets by numeric pair id and has no client order ids: Saraf keeps its own id for each order
  in `data/venues/ramzinex-orders.json`. A market buy is sized in the quote currency, converted at the current ask.
- **Tabdeal**'s public API has no ticker, so prices and candles come from the endpoints its web app uses. Trading is
  not wired yet.
- **TSE** has no official public trading API, so the `tse` venue refuses orders. It never drives a broker's private
  endpoints. TSETMC is often unreachable from outside Iran.

None of the account or order endpoints of Nobitex, Bitpin and Ramzinex have been exercised against a real account
yet; their tests run on responses shaped like the documented ones. Start with a small order.

`uv run python scripts/probe_venues.py` checks that every venue's public data answers from this machine.

### Not supported

| | why |
|---|---|
| Excoino, Bit24, Exnovin, Ompfinex (OK-EX), Arzpaya, Aban Tether | no public API that could be verified |
| Bonbast | no API; only a scraped page |
| Mofid and the other stock brokers | no public trading API |

## Choosing a venue

- The default venue is where orders go and what the dashboard shows. Pick it under **تنظیمات ← اتصال‌ها** in the
  app, or with `DEFAULT_VENUE`.
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

    # account and orders, for venues that trade; orders come back in the shared Order shape
    credentials: tuple[Credential, ...]                             # the keys it needs, for the settings screen
    async def test_connection(self) -> dict                         # {"ok", "message"}
    async def balances(self) -> dict[str, dict]
    async def place_order(self, symbol, side, type, quantity, price=None, client_id=None) -> Order
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

An `Order` uses the field names the engine and the tools read: `clientOrderId`, `symbol`, `side`, `type`, `price`,
`origQty`, `executedQty`, `executedSum`, `executedPrice`, `status` (`NEW`, `PARTIALLY_FILLED`, `FILLED`,
`CANCELED`) and `active`.

Unsupported calls raise `VenueUnsupported`, and API failures raise `VenueError`. Both reach the model and the UI as
readable errors.

## Paper trading on any venue

`brokers/` holds the `SpotBroker` and `FuturesBroker` interfaces and paper versions that work on any venue:

- **Paper spot** fills orders by walking the venue's live order book level by level and charges a typical spot
  fee. A limit order fills whatever the book offers at its price or better, and the rest stays open with its funds
  locked.
- **Paper futures** is an isolated-margin perpetuals simulator priced off the venue's live order books, on the
  markets the venue flags as leverage-enabled, with limit orders, TP/SL and liquidation.

Each venue's paper state is kept in its own file in `data/`. The plain file name belongs to the venue that was the
default when the data directory was first used (`data/paper-venue.txt`), so changing the default later never mixes
two venues' paper balances. `POST /api/paper/reset` resets it.

## Writing an adapter

```python
# backend/saraf/venues/nobitex/__init__.py
from ...config import Settings
from ..registry import register
from .client import NobitexClient      # an HttpVenue subclass

@register("nobitex", NobitexClient)
def _make(settings: Settings) -> NobitexClient:
    return NobitexClient(settings.nobitex_api_key, settings.nobitex_private_key, settings.nobitex_token)
```

1. Subclass `HttpVenue` (`venues/http.py`: one httpx client, a short cache, the retry policy) and implement
   `markets`, `depth`, `trades` and `candles` in the shared schema. Declare `Capabilities`.
2. For live trading, implement the order methods and return `Order`s. The generic `VenueSpotBroker`
   (`brokers/live.py`) then trades on it; override `live_spot_broker()` only for venue-specific workarounds, as
   Wallex does.
3. Declare `credentials`: one `Credential` per value the venue needs, named after its `Settings` field. The settings
   screen builds its form from them.
4. Register a factory with `@register("<key>", Client)` and import the package in `saraf/venues/__init__.py`.
5. Add the venue's settings to `config.py` with its own prefix (`NOBITEX_API_KEY`).
6. Add tests with `httpx.MockTransport`, like `tests/test_nobitex.py`. The fake venue in `tests/conftest.py` shows
   the expected shapes.

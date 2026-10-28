# The agent

The chat assistant is an LLM tool-calling loop (`backend/saraf/agent/loop.py`) over 36 tools. It streams its
reasoning, tool calls and answer to the browser over SSE. A turn runs at most `AGENT_MAX_STEPS` LLM steps (default 8).

## Tools

Every market-data tool takes an optional `venue`. Without it the tool reads `DEFAULT_VENUE`.

### Market data

| tool | what it returns |
|---|---|
| `list_venues` | the venues Saraf can read and what each supports |
| `list_markets` | spot markets with price, 24h/7d change and volume |
| `get_ticker` | last, bid/ask, 24h high/low/change/volume for one market |
| `top_movers` | gainers or losers over the last N hours, computed from candles |
| `order_book` | spread, liquidity within 0.5/1/2/5% of mid, bid/ask imbalance, walls |
| `recent_trades` | latest public trades with a buy/sell flow summary |
| `get_candles` | OHLCV candles |
| `technical_analysis` | trend, EMA20/50, RSI14, MACD, ATR, Bollinger bands, support and resistance |
| `fear_greed_index` | the global crypto index (7-day history) and the venue's Market Pulse |
| `market_risk` | a 0-100 risk score from volatility, liquidity, spread and RSI extremes |
| `global_coin_stats` | rank, market cap, dominance, ATH, 1h-1y changes |

### News and the web

| tool | |
|---|---|
| `crypto_news` | headlines from major crypto outlets, including Persian ones |
| `web_search` | general or news search (Tavily when `TAVILY_API_KEY` is set, otherwise DuckDuckGo) |
| `read_webpage` | the main text of a page, for reading an article in full |

### Account

| tool | |
|---|---|
| `spot_balances` | free, locked and total per asset, with USDT and Toman value |
| `spot_open_orders`, `spot_trade_history` | active orders and executed trades |
| `futures_account` | wallets (available, margin, unrealized PnL, equity) and positions |
| `futures_history` | opens, closes, TP/SL hits and liquidations with realized PnL |
| `futures_markets` | markets that support leverage and their max leverage |

### Trading (confirmation required)

| tool | |
|---|---|
| `spot_place_order` | BUY or SELL, MARKET or LIMIT, sized in base quantity or quote amount |
| `spot_cancel_order` | cancel an active order |
| `spot_close_asset` | market-sell the whole free balance of an asset |
| `futures_open_position` | LONG or SHORT, isolated margin, optional TP/SL |
| `futures_close_position` | close all or part of a position |
| `futures_set_tp_sl` | set or change take-profit and stop-loss |
| `futures_cancel_order` | cancel a pending limit order |

### Alerts

`create_price_alert`, `list_price_alerts`, `cancel_price_alert`. A background task checks active alerts and pushes a
notification to the app when one fires.

### Generative UI

See [below](#generative-ui).

## Tool groups

All 36 schemas cost about 4.5k tokens on every LLM step, and most questions only need the read-only market tools.
`toolsets.py` holds back two groups until a conversation needs them:

| group | tools | joins when the user says (Persian or English) |
|---|---|---|
| `futures` | the futures tools and `show_position_planner` | futures, leverage, long, short, position, margin, liquidation… |
| `viz` | the chart, heatmap and dashboard tools | chart, heatmap, compare, report, dashboard, plot… |

Once a group has joined, it stays for the rest of the session, so follow-ups like "close it" or "now short it" keep
working. Spot order tools are always sent.

## The confirmation flow

1. The model calls a trading tool.
2. The [risk guard](#risk-guard) checks it. A violation goes straight back to the model as the tool result, so it
   can fix the order or explain why it can't be placed.
3. If the order passes, the server creates a pending action and streams a `confirm` event. The UI draws the
   confirmation card.
4. `POST /api/actions/{id}` with `{"approve": true}` executes the order on the paper or live broker. `false`
   cancels it. The result goes back to the model, which continues the turn.

`REQUIRE_CONFIRMATION=false` skips step 3 and executes right away. Only use it with paper brokers.

## Risk guard

`backend/saraf/agent/risk.py` runs before any trade reaches you. It rejects:

- symbols outside `SYMBOL_ALLOWLIST`, when one is set
- spot orders above `MAX_ORDER_NOTIONAL_USDT` / `MAX_ORDER_NOTIONAL_TMN`, depending on the quote asset
- futures positions whose notional (margin × leverage) is above the same limit
- leverage above `MAX_LEVERAGE`
- LIMIT orders without a price, and orders without a size
- a stop-loss on the wrong side of the entry, or beyond the liquidation price
- a take-profit on the wrong side of the entry

For futures it also works out the liquidation price and the loss if the stop is hit, and warns when no stop-loss is
set. These facts appear on the confirmation card.

## Generative UI

| tool | component |
|---|---|
| `show_market_heatmap` | market heatmap (size = volume, colour = 24h change). A tile hands the market to the agent |
| `show_comparison_chart` | % performance of several markets on one chart |
| `show_price_chart` | candles with the support, resistance, entry, TP and SL levels the agent discusses |
| `show_trade_ticket` | pre-filled order form → risk guard → confirmation card |
| `show_position_planner` | leverage, margin, TP and SL sliders with live liquidation price, PnL and R:R |
| `render_dashboard` | a free-form report from metric, table, progress and callout blocks |

The tool returns a `ui` payload that the browser renders. The model only gets a short summary of it, which keeps
small models' context lean. The trade ticket and the planner submit through `POST /api/ui/ticket`, which runs the
same risk guard and confirmation flow as a chat order.

## Market Pulse

A 0-100 fear/greed index computed from the venue's own top 50 markets by volume:

| component | weight |
|---|---|
| breadth: share of markets up over 24h | 35% |
| momentum: volume-weighted 24h change, −10%…+10% mapped to 0…1 | 30% |
| buy pressure: average share of resting orders that are bids | 20% |
| weekly trend: volume-weighted 7d change, −20%…+20% mapped to 0…1 | 15% |

## Sessions

Each conversation is a session saved under `data/`. Sessions can be listed, renamed, reset and deleted
([API](api.md#sessions)). Older history is trimmed to `AGENT_HISTORY_CHARS` before each LLM call.

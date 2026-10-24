# Saraf

**Saraf** (صراف, the money changer of the Iranian bazaar) is an agentic AI trading assistant for Iranian markets.
You ask in Persian or English. Saraf reads live prices, order books, candles, sentiment and the news, explains what
it sees, and places orders for you. Every order is shown to you as a confirmation card first and runs only after
you approve it.

It talks to markets through **venues**: one adapter per crypto exchange, stock exchange or currency-rate source,
behind a single interface. Nothing in the agent, the analytics, the paper brokers or the trading engine names a
venue, so adding an exchange means writing one adapter.

- **Chat assistant**: 36 tools over OpenAI-compatible tool calling, streamed over SSE, with a risk guard and a
  confirmation step on every trade.
- **Generative UI**: the agent draws heatmaps, comparison charts, annotated candles, trade tickets, position
  planners and dashboards right in the conversation.
- **Paper trading on any venue**: spot fills are simulated against the venue's live order book, and futures run
  on an isolated-margin perpetuals simulator with TP/SL and liquidation.
- **Autonomous engine**: a rules-plus-LLM trading engine with backtests, shadow and live modes, copy-trading
  personas and a strategy assistant. It was first built for Wallex's AI trading challenge
  ([docs/hackathon](docs/hackathon/)).

## Venues

| venue | asset class | market data | order book | candles | trading | status |
|---|---|---|---|---|---|---|
| **Wallex** (`wallex`) | crypto, TMN and USDT markets | ✓ | ✓ | 1m to 1D | spot, live and paper; futures paper only | full adapter |
| **Tehran Stock Exchange** (`tse`) | equities, IRR | ✓ (TSETMC) | 5 levels | daily | none | data-only stub |
| Nobitex, Bitpin, Ramzinex | crypto | | | | | planned |
| Free-market currency and gold rates | fx | | | | | planned |

TSE trading is out of reach by design. Mofid's Easy Trader and the other brokers' platforms have no official public
trading API, so the TSE venue raises `NotImplementedError` for orders instead of driving a broker's private endpoints.

`DEFAULT_VENUE` (default `wallex`) is where orders go and what the dashboard shows. Every market-data tool and
endpoint also takes a `venue`, for example `GET /api/markets?venue=tse`, and `list_venues` / `GET /api/venues`
describe what each venue supports.

### Adding a venue

```python
# backend/saraf/venues/nobitex/__init__.py
from ...config import Settings
from ..registry import register
from .client import NobitexClient      # a Venue subclass

@register("nobitex")
def _make(settings: Settings) -> NobitexClient:
    return NobitexClient(settings.nobitex_api_key)
```

`NobitexClient` implements `markets`, `depth`, `trades` and `candles` in the shared schema
(`backend/saraf/venues/base.py`) and declares its `Capabilities`. For live trading it also implements the order
methods and returns its own broker from `live_spot_broker()`. Then import the package in `saraf/venues/__init__.py`.
Credentials keep the venue's prefix (`NOBITEX_API_KEY`).

## Architecture

```
frontend/                React + Vite, RTL Persian UI: chat, markets, chart, order book, portfolio, strategy
backend/saraf/
  venues/
    base.py              Venue interface, shared market-data schema, Capabilities, VenueError
    registry.py          name -> factory; Venues: the open venues of one process
    wallex/              Wallex REST client (market, account, orders, OTC) + its live spot/futures brokers
    tse/                 Tehran Stock Exchange from TSETMC, read-only
  brokers/               SpotBroker / FuturesBroker interfaces; paper brokers that work on any venue
  market/analytics.py    top movers, order book analysis, indicators (RSI, EMA, MACD, ATR, Bollinger)
  market/sentiment.py    global Fear & Greed, Market Pulse (a venue's own breadth index), per-market risk
  news/web.py            crypto news feeds (incl. Persian), web and news search, page reader
  agent/
    tools.py             the tool registry (36 tools)
    genui.py             generative UI tools
    risk.py              pre-trade risk guard: notional, leverage, SL/TP, liquidation
    loop.py              agent loop, streaming, the trade-confirmation flow
    toolsets.py          sends only the tool groups a conversation needs
  engine/                autonomous engine: feeds, features, strategy, AI decider, risk, execution, backtests
  traders.py, personas.py, tuning.py, strategy_chat.py   copy-trading, personas, settings, strategy assistant
  alerts.py              price alerts and their background checker
  server.py              FastAPI: chat SSE, dashboard API, engine controls, serves the built UI
deploy/                  systemd units (saraf-web, saraf-engine, saraf-strategy, saraf-traders, saraf-persona@)
docs/hackathon/          the Wallex challenge write-up, strategy and recorded results
```

## Setup

```bash
cp .env.example .env          # LLM_BASE_URL / LLM_API_KEY / LLM_MODEL; venue keys only for live trading
cd frontend && npm install && npm run build && cd ..
cd backend && uv sync && uv run python -m saraf.server
```

Open http://127.0.0.1:8000. For UI work, `cd frontend && npm run dev` serves on port 5173 and proxies the
backend. To see the whole UI on realistic fake data, without any exchange or LLM, run
`uv run --project backend python backend/scripts/ui_demo.py`.

Any OpenAI-compatible endpoint works: OpenAI, OpenRouter, Together, vLLM, or Ollama with
`LLM_BASE_URL=http://localhost:11434/v1`. If your provider can't stream tool calls, set `LLM_STREAM=false`.

### Local models (LM Studio)

```bash
LLM_BASE_URL=http://localhost:1234/v1
LLM_MODEL=google/gemma-4-e4b
LLM_COMPAT_SCHEMAS=true
```

`LLM_COMPAT_SCHEMAS` works around strict chat templates such as Gemma's on LM Studio. It drops `enum` and `anyOf`
from tool schemas, sends tool history as text, and turns a tool call written as text back into a real call.

## Trading modes

| | paper (default) | live |
|---|---|---|
| Spot | fills simulated against the venue's live order book, virtual balance | a venue that trades, its API key and `SPOT_MODE=live` |
| Futures | isolated-margin perpetuals simulator on the venue's leverage markets | Wallex's futures endpoints aren't public; `WallexFuturesBroker` is a stub ready to wire up |

Paper state lives in `data/` (one file per venue), and `POST /api/paper/reset` resets it. Lock exchange API keys to
your server's IP and give them trading permission only, never withdrawal.

## Autonomous engine

```bash
cd backend
uv run python -m saraf.engine backtest --days 14 --decider both   # rules vs AI, with counterfactuals
uv run python -m saraf.engine run --mode shadow                   # live data, simulated fills, no orders
uv run python -m saraf.engine run --mode live --confirm-live      # real orders on the default venue
```

Both commands take `--venue`. The engine resumes from `data/engine/<mode>/state.json` after a restart and
reconciles its positions with the exchange balances. `deploy/` holds the systemd units and `deploy.sh`, which
builds the UI, syncs the code to `/opt/saraf` and restarts the services. The design, research and results from the
challenge are in [docs/hackathon](docs/hackathon/).

## Generative UI

| tool | component |
|---|---|
| `show_market_heatmap` | market heatmap (size = volume, colour = 24h change); a tile hands the market to the agent |
| `show_comparison_chart` | % performance of several markets on one chart |
| `show_price_chart` | candles with the support, resistance, entry, TP and SL levels the agent discusses |
| `show_trade_ticket` | pre-filled order form → risk guard → confirmation card |
| `show_position_planner` | leverage, margin, TP and SL sliders with live liquidation price, PnL and R:R |
| `render_dashboard` | a free-form report built from metrics, tables, progress bars and callouts |

The `ui` payload stays in the browser; the LLM gets a short summary, which keeps small models' context lean.

## Tests

```bash
cd backend
uv run pytest                    # venues, paper brokers, risk guard, agent loop, HTTP API, engine, copy-trading
uv run python scripts/smoke.py   # every read-only tool + a paper round trip against the live default venue
```

Tests use a fake venue and a scripted LLM, so no inference runs. The agent-loop tests read market data from
Wallex's live public API and still place trades in paper mode only.
`backend/scripts/mock_llm.py` serves an OpenAI-compatible mock on :1234 for driving the UI without a model.

## License

Licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**, with additional terms permitted under AGPLv3 Section 7. See [LICENSE](LICENSE).

This means: you're free to use, study, modify, and redistribute this code, including running it as a network service — but any modified version (or service built on one) must also be released as source under AGPL-3.0, must keep author attribution intact, and must be clearly marked as a different, unaffiliated project (see the Trademark Notice below and the additional terms in [LICENSE](LICENSE)).

### Trademark Notice

"Saraf," "صراف" as the name of this project, and its logo/branding are **not** licensed under AGPL-3.0 and are not covered by the code license above. They are trademarks/branding of the original author. The AGPL-3.0 license grants rights to the *source code* — it does not grant permission to use the "Saraf" name, logo, or branding for a fork, modified version, or derivative service.

If you fork or modify this project, please:
- Use a different name and logo that isn't confusingly similar to "Saraf"
- Clearly state that your version is unaffiliated with and not endorsed by the original project

Wallex, Mofid, Easy Trader, TSETMC and the other exchanges and services named here are trademarks of their owners.
Saraf is an independent project and is not affiliated with or endorsed by any of them.

## Author

Built with ❤️ by **Behdad**

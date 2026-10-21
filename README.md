# Wallex Agent

An agentic AI trading assistant on top of the [Wallex](https://wallex.ir) API: you tell it what you want in plain
Persian or English, and it handles market analysis, order book depth, Fear & Greed, internet news, and spot and futures trades.
Every trade waits for the user's confirmation in the UI before it runs.

```
frontend/  React + Vite UI (RTL, Wallex design tokens): chat, markets, chart, order book, portfolio
backend/   FastAPI + agent loop (OpenAI-compatible tool calling, SSE streaming)
  wallex_agent/
    wallex/client.py     async client for the full public Wallex API (market, account, orders, OTC)
    brokers/             SpotBroker / FuturesBroker interfaces, live + paper implementations
    market/analytics.py  top movers over N hours, order book analysis, indicators (RSI/EMA/MACD/ATR/BB)
    market/sentiment.py  global Fear&Greed + "Wallex Pulse" (local index) + per-market risk score
    news/web.py          RSS feeds from crypto news sites, web/news search, page reader
    agent/tools.py       35 agent tools (the tool registry; add new tools here)
    agent/genui.py       Generative UI tools: the agent builds interactive components in the chat
    alerts.py            price alerts + background checker
    agent/risk.py        pre-trade risk guard (notional, leverage, SL/TP, liquidation)
    agent/loop.py        agent loop, streaming, the pending-actions trade-confirmation flow
    server.py            API + serves the frontend build
```

## Setup

```bash
cp .env.example .env          # set LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
cd frontend && npm install && npm run build && cd ..
cd backend && uv sync && uv run python -m wallex_agent.server
```

Open http://127.0.0.1:8000. For UI development: `cd frontend && npm run dev` (port 5173, with a proxy to the backend).

Any OpenAI-compatible endpoint works (OpenAI, OpenRouter, Together, vLLM, Ollama: `LLM_BASE_URL=http://localhost:11434/v1`).
If the provider doesn't support streaming tool calls: `LLM_STREAM=false`.

## Autonomous trading engine (challenge)

The algorithmic engine for the Wallex challenge lives in `backend/wallex_agent/engine/`; the full write-up
(architecture, AI role, entry/exit rules, risk, research, results, reproduction) is in **[STRATEGY.md](STRATEGY.md)**.

```bash
cd backend
uv run python -m wallex_agent.engine backtest --days 14 --decider both   # rules vs AI, with counterfactuals
uv run python -m wallex_agent.engine run --mode shadow                   # live data, no real orders
uv run python -m wallex_agent.engine run --mode live --confirm-live      # challenge account
```

### Deploy (VPS)

```bash
git clone <repo> && cd wallex-agent && cp .env.example .env   # fill LLM_*, WALLEX_API_KEY, ENGINE_*
docker compose build --build-arg GIT_COMMIT=$(git rev-parse --short HEAD)
docker compose up -d && docker compose logs -f engine         # shadow mode by default
```

The engine resumes from `data/engine/<mode>/state.json` after a restart and reconciles positions with the
exchange balances. Lock the Wallex API key to the server's IP and give it trading permission only (no withdrawal).

## Generative UI

The agent builds UI components itself, inside the conversation (`frontend/src/components/GenUI.tsx`):

| tool | component |
|---|---|
| `show_market_heatmap` | market heatmap (size = volume, color = 24h change); clicking a tile hands it to the agent for analysis |
| `show_comparison_chart` | % performance comparison chart for several markets |
| `show_price_chart` | candlestick chart with support/resistance/entry/TP/SL levels the agent draws |
| `show_trade_ticket` | editable pre-filled order form → risk guard → confirmation card |
| `show_position_planner` | leverage/margin/TP/SL sliders with live liquidation price, PnL and R:R → submit for confirmation |
| `render_dashboard` | free-form report built from blocks (metric, table, progress, callout, text) |

Plus: price alerts with an in-app toast and a "review with agent" button, and a collapsible display of the model's thinking stream.
The `ui` payload is browser-only; the LLM gets just a short summary (keeps small-model context lean).

## Local models (LM Studio)

```bash
LLM_BASE_URL=http://localhost:1234/v1
LLM_MODEL=google/gemma-4-e4b
LLM_COMPAT_SCHEMAS=true
```

`LLM_COMPAT_SCHEMAS` fixes problems in Gemma's template on LM Studio: no `enum`/`anyOf` in tool schemas, tool history
sent as text (`role: tool` breaks it with `Unknown test: sequence`), and a tool call the model writes as text is
converted back into a real call.

## Testing without inference (mock LLM)

```bash
uv run --project backend python backend/scripts/mock_llm.py   # OpenAI-compatible on :1234, scripted tool calls
uv run --project backend python backend/scripts/chat.py "heatmap" "compare" --approve
```

The mock picks the tool from keywords in the message (heatmap/compare/planner/alert/ticket/report/chart/long/depth;
`slow` gives a long streamed reply for testing the Stop button);
it's for testing the UI and the loop without loading the CPU.

## Trading modes

| | paper (default) | live |
|---|---|---|
| Spot | real fills against the live Wallex order book, virtual balance | `WALLEX_API_KEY` + `SPOT_MODE=live` |
| Futures | isolated-margin perpetual simulator on Wallex's 36 leverage-enabled markets (TP/SL/liquidation every 10s) | the endpoints aren't in the public docs; `WallexFuturesBroker` is a stub ready to wire up |

Paper state lives in `data/`; reset it with `POST /api/paper/reset`.

## Notes found in the API

- The futures API isn't in the [public docs](https://api-docs.wallex.ir/). `/hector/web/v1/markets` exposes `is_margin` and `max_leverage`.
- `/v1/currencies/stats` now requires the `key` parameter (the docs don't say so).
- The `api-docs.wallex.ir` SSL certificate has a hostname mismatch; `api.wallex.ir` itself is fine.

## Tests

```bash
cd backend
uv run pytest            # 56 tests: paper brokers, risk guard, agent loop under failure, HTTP API,
                         # Gemma regressions, generative UI, alerts (fake market + scripted LLM, no inference)
uv run python scripts/smoke.py   # calls every tool against the live API
```

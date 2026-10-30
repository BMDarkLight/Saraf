# Saraf

**Saraf** (صراف, the money changer of the Iranian bazaar) is an agentic AI trading assistant for Iranian markets.
You ask in Persian or English. Saraf reads live prices, order books, candles, sentiment and the news, explains what
it sees, and places orders for you. Every order is shown to you as a confirmation card first and runs only after
you approve it.

![Saraf](docs/screenshots/chat-heatmap.png)

It talks to markets through **venues**: one adapter per crypto exchange, stock exchange or currency-rate source,
behind a single interface. Nothing in the agent, the analytics, the paper brokers or the trading engine names a
venue, so adding an exchange means writing one adapter.

## Features

- **Chat assistant**: 36 tools over OpenAI-compatible tool calling, streamed over SSE, with a risk guard and a
  confirmation step on every trade.
- **Generative UI**: the agent draws heatmaps, comparison charts, annotated candles, trade tickets, position
  planners and dashboards right in the conversation.
- **Paper trading on any venue**: spot fills are simulated against the venue's live order book, and futures run
  on an isolated-margin perpetuals simulator with TP/SL and liquidation.
- **Autonomous engine**: a rules-plus-LLM trading engine with backtests, shadow and live modes, strategy personas,
  copy-trading of real traders and a strategy assistant.
- **Any model**: OpenAI, OpenRouter, vLLM, Ollama, LM Studio. Small local models work too.
- **Persian first**: an RTL Persian UI with light and dark themes that works on phones.
- **Settings in the app**: exchange keys, the default venue and the chat model are set from the settings screen and
  apply without a restart.

## Screenshots

| | |
|---|---|
| ![Annotated chart](docs/screenshots/chat-chart.png) | ![Confirmation card](docs/screenshots/chat-confirm.png) |
| **Charts in the chat**: candles with the levels the agent is discussing | **Nothing runs unapproved**: every order passes the risk guard, then waits for you |
| ![Position planner](docs/screenshots/chat-planner.png) | ![Report](docs/screenshots/chat-report.png) |
| **Position planner**: leverage, margin, TP and SL with live liquidation price and R:R | **Reports**: metrics, tables and callouts the agent composes |
| ![Markets](docs/screenshots/markets.png) | ![Strategy](docs/screenshots/strategy-overview.png) |
| **Markets**: market list, chart, live order book, Fear & Greed and Market Pulse | **Autonomous engine**: return, equity curve, positions and every decision with its reason |
| ![Settings](docs/screenshots/strategy-tuning.png) | ![Copy-trading](docs/screenshots/strategy-personas.png) |
| **Engine settings**: sizing, risk and entry setups, applied with a restart | **Copy-trading**: real on-chain traders and strategy personas with track records |

<p align="center"><img src="docs/screenshots/mobile-chat.png" alt="Saraf on a phone" width="300"></p>

More in [Using Saraf](docs/using-saraf.md).

## Quick start

```bash
cp .env.example .env          # optional: the LLM and venue keys can also be set in the app's settings screen
cd frontend && npm install && npm run build && cd ..
cd backend && uv sync && uv run python -m saraf.server
```

Open http://127.0.0.1:8000. Everything starts in paper mode: market data is live, orders fill against a virtual
balance.

To see the whole app on fake data, without an exchange or an LLM:

```bash
cd backend
uv run python scripts/mock_llm.py &
LLM_BASE_URL=http://127.0.0.1:1234/v1 uv run python scripts/ui_demo.py
```

See [Getting started](docs/getting-started.md) for LLM providers, local models and Docker.

## Venues

| venue | asset class | market data | order book | candles | trading |
|---|---|---|---|---|---|
| `wallex` | crypto, TMN and USDT markets | ✓ | ✓ | 1m to 1D | spot, live and paper; futures paper only |
| `nobitex` | crypto, TMN and USDT markets | ✓ | ✓ | 1m to 1D | spot, live and paper |
| `bitpin` | crypto, TMN and USDT markets | ✓ | ✓ | 1m to 1D | spot, live and paper |
| `ramzinex` | crypto, TMN and USDT markets | ✓ | ✓ | 1m to 1D | spot, live and paper |
| `tabdeal` | crypto, TMN and USDT markets | ✓ | ✓ | 1m to 1D | paper only |
| `binance` | crypto, global USDT reference | ✓ | ✓ | 1m to 1D | paper only |
| `tetherland`, `navasan` | fx: USDT, dollar, euro, coins, gold | prices | | | none |
| `tse` (Tehran Stock Exchange) | equities, IRR | ✓ (TSETMC) | 5 levels | daily | none |

Connect venues and pick the default one in the app under **تنظیمات ← اتصال‌ها** (or in `.env`). The default venue
is where orders go and what the dashboard shows; every tool and market-data endpoint also takes a `venue`.
[Venues](docs/venues.md) covers each adapter, the interface and how to add an exchange.

## Documentation

| | |
|---|---|
| [Getting started](docs/getting-started.md) | install, demo mode, LLM providers, local models, Docker |
| [Using Saraf](docs/using-saraf.md) | a tour of every screen |
| [The agent](docs/agent.md) | tools, tool groups, confirmation flow, risk guard, generative UI |
| [Venues](docs/venues.md) | the venue interface, paper brokers, writing an adapter |
| [Autonomous engine](docs/engine.md) | setups, AI decider, risk, backtests, shadow and live runs, personas, copy-trading |
| [Configuration](docs/configuration.md) | every environment variable |
| [HTTP API](docs/api.md) | REST and SSE endpoints |
| [Development](docs/development.md) | tests, mocks, screenshots, deployment |

## Architecture

```
frontend/                React + Vite, RTL Persian UI: chat, markets, chart, order book, portfolio, strategy
backend/saraf/
  venues/                Venue interface, shared market-data schema, registry, one package per venue
  brokers/               SpotBroker / FuturesBroker interfaces; paper brokers that work on any venue
  market/                top movers, order book analysis, indicators, Fear & Greed, Market Pulse, risk scores
  news/                  crypto news feeds (incl. Persian), web and news search, page reader
  agent/                 tool registry, generative UI, risk guard, agent loop, tool groups, sessions
  engine/                autonomous engine: feeds, features, strategy, AI decider, risk, execution, backtests
  traders.py, personas.py, tuning.py, strategy_chat.py   copy-trading, personas, settings, strategy assistant
  alerts.py              price alerts and their background checker
  server.py              FastAPI: chat SSE, dashboard API, engine controls, serves the built UI
deploy/                  systemd units and the deploy script
docs/                    documentation and screenshots
```

## Safety

- Paper mode is the default for both spot and futures.
- Every chat order passes the risk guard (notional, leverage, stop and target sanity, liquidation) and then waits
  for your approval.
- Live engine actions need an explicit `LIVE` confirmation.
- Lock exchange API keys to your server's IP, and give them trading permission only, never withdrawal.
- The API has no authentication. Keep it on localhost or behind an authenticating proxy.

Saraf is software, not financial advice. Trading crypto and leveraged products can lose you money.

## License

Licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**, with additional terms permitted under AGPLv3 Section 7. See [LICENSE](LICENSE).

This means: you're free to use, study, modify, and redistribute this code, including running it as a network service — but any modified version (or service built on one) must also be released as source under AGPL-3.0, must keep author attribution intact, and must be clearly marked as a different, unaffiliated project (see the Trademark Notice below and the additional terms in [LICENSE](LICENSE)).

### Trademark Notice

"Saraf," "صراف" as the name of this project, and its logo/branding are **not** licensed under AGPL-3.0 and are not covered by the code license above. They are trademarks/branding of the original author. The AGPL-3.0 license grants rights to the *source code* — it does not grant permission to use the "Saraf" name, logo, or branding for a fork, modified version, or derivative service.

If you fork or modify this project, please:
- Use a different name and logo that isn't confusingly similar to "Saraf"
- Clearly state that your version is unaffiliated with and not endorsed by the original project

The exchanges, brokers and data services named here are trademarks of their owners. Saraf is an independent
project and is not affiliated with or endorsed by any of them.

## Author

Built with ❤️ by **Behdad**

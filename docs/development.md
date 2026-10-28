# Development

## Tests

```bash
cd backend
uv run pytest                    # venues, paper brokers, risk guard, agent loop, HTTP API, engine, copy-trading
uv run python scripts/smoke.py   # every read-only tool + a paper round trip against the live default venue
```

The tests use a fake venue (`tests/conftest.py`) and a scripted LLM, so no inference runs. The agent-loop tests
read market data from the default venue's live public API, and still trade in paper mode only.

## Working without an exchange or a model

| script | |
|---|---|
| `scripts/mock_llm.py` | an OpenAI-compatible mock on `:1234` that picks tool calls from keywords in the message |
| `scripts/ui_demo.py` | the web app on deterministic fake data: venue, account, sentiment, engine journals and a scripted strategy assistant |
| `scripts/chat.py` | send messages to the running server from the terminal and print the agent's trace (`--approve` / `--reject` trades) |
| `scripts/status.py` | a read-only status report of a deployed engine |

Run the demo against the mock LLM:

```bash
cd backend
uv run python scripts/mock_llm.py &
LLM_BASE_URL=http://127.0.0.1:1234/v1 uv run python scripts/ui_demo.py
```

`ui_demo.py` overwrites `data/engine`. Never run it on a server.

## Browser checks

```bash
uv run --with playwright python scripts/ui_e2e.py --chromium /path/to/chrome
```

`ui_e2e.py` drives the strategy controls against the demo like a person would, and checks both the page and the files
the server writes (`data/engine/control.json`, `tuning.env`, `copy.json`).

## Screenshots

The images in [docs/screenshots](screenshots/) come from `scripts/screenshots.py`. With the mock LLM and the demo
running as above:

```bash
uv run --with playwright python scripts/screenshots.py      # uses Google Chrome on macOS by default
uv run --with playwright python scripts/screenshots.py --chromium /path/to/chrome
```

It saves 2× dark-theme PNGs at 1440×900, plus one at phone size. Each chat shot starts from a fresh browser context.

## Research scripts

`scripts/research.py`, `edge_research.py`, `rotation_research.py`, `usdt_timing_research.py` and `compare_execution.py` are the experiments behind the engine's defaults. Each one documents its
question and method at the top of the file.

## Deployment

`deploy/` holds systemd units and a deploy script for a Linux server:

| unit | |
|---|---|
| `saraf-web.service` | the web app and API |
| `saraf-engine.service` | the main autonomous engine |
| `saraf-strategy.service` | the strategy's own shadow session |
| `saraf-traders.service` | simulated copy results for tracked traders |
| `saraf-persona@<id>.service` | one shadow session per persona |

`deploy.sh <checkout> <commit>` builds the UI, syncs the code to `/opt/saraf` (never touching `.env` or `data/`),
installs the units and restarts the services. `setup-runner.sh` prepares the server once: Node for the UI build and a self-hosted GitHub Actions runner that calls `deploy.sh`.

Put a reverse proxy with authentication in front of `saraf-web`: the API itself has none.

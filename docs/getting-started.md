# Getting started

## Requirements

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- Node.js 20.19+ and npm (to build the UI)
- An OpenAI-compatible LLM endpoint: a hosted API or a local model

## Install and run

```bash
git clone <repo> saraf && cd saraf
cp .env.example .env
cd frontend && npm install && npm run build && cd ..
cd backend && uv sync && uv run python -m saraf.server
```

Open http://127.0.0.1:8000. The server serves the built UI and the API from the same port.

Out of the box everything runs in **paper mode**: market data is live, but orders fill against a virtual balance.
No exchange API key is needed until you switch a market to live trading (see [Configuration](configuration.md)).

## Try it without an exchange or an LLM

The demo mode swaps the venue, the account and the sentiment feed for deterministic fakes, and the mock LLM answers
with scripted tool calls. It is the quickest way to see every screen:

```bash
cd backend
uv run python scripts/mock_llm.py &                                        # scripted LLM on :1234
LLM_BASE_URL=http://127.0.0.1:1234/v1 uv run python scripts/ui_demo.py     # app on :8000
```

The mock LLM picks its tool calls from keywords in your message: `heatmap`, `chart`, `compare`, `planner`,
`ticket`, `long`, `report`, `depth`, `alert` (or their Persian equivalents).

`ui_demo.py` writes sample engine journals to `data/engine`. Don't run it on a server whose engine data you want
to keep.

## Choosing an LLM

Any endpoint that speaks the OpenAI chat-completions API with tool calling works:

| provider | `LLM_BASE_URL` |
|---|---|
| OpenAI | `https://api.openai.com/v1` |
| OpenRouter | `https://openrouter.ai/api/v1` |
| Together | `https://api.together.xyz/v1` |
| vLLM | `http://<host>:8000/v1` |
| Ollama | `http://localhost:11434/v1` |
| LM Studio | `http://localhost:1234/v1` |

Set `LLM_API_KEY` and `LLM_MODEL` to match. If the provider can't stream tool calls, set `LLM_STREAM=false`.

### Local models

Small local models work. Saraf only sends the tool groups a conversation needs and keeps generative-UI payloads
out of the model's context ([details](agent.md#tool-groups)). For chat templates that reject `enum` and `anyOf`
in tool schemas, such as Gemma on LM Studio:

```bash
LLM_BASE_URL=http://localhost:1234/v1
LLM_MODEL=google/gemma-4-e4b
LLM_COMPAT_SCHEMAS=true
```

`LLM_COMPAT_SCHEMAS` strips those keywords, sends tool history as plain text, and turns a tool call the model writes
as text back into a real call.

## UI development

```bash
cd frontend && npm run dev     # http://localhost:5173, proxies /api to the backend on :8000
```

## Docker

The image runs the autonomous engine only (it has no built UI). `docker-compose.yml` starts it in shadow mode
with `./data` mounted, so a restart resumes where it left off:

```bash
docker compose up -d engine
```

## Next

- [Using Saraf](using-saraf.md): what each screen does
- [Configuration](configuration.md): risk limits, paper balances, live trading

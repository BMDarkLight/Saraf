# HTTP API

The FastAPI server (`backend/saraf/server.py`) serves the UI and this API on `http://127.0.0.1:8000`. Request and
response bodies are JSON. Streaming endpoints answer with Server-Sent Events. There is no authentication: keep the
server on localhost or behind your own proxy.

Errors from a venue or a broker come back as `502` with the venue's message.

## Chat

### `POST /api/chat`

```json
{ "message": "BTC order book?", "session_id": null }
```

Streams SSE events, one JSON object per `data:` line:

| `type` | |
|---|---|
| `session` | the session id, first event of every stream |
| `reasoning` | the model's reasoning, when the provider streams it |
| `text` | a chunk of the answer |
| `tool_call` | a tool the model called, with its arguments |
| `tool_result` | the tool's result, with a `ui` payload for generative-UI tools |
| `confirm` | a pending trade waiting for approval (see below) |
| `error` | something failed; the stream ends |
| `done` | end of the turn |

### `POST /api/actions/{action_id}`

```json
{ "approve": true }
```

Approves or rejects a pending trade from a `confirm` event. It streams an `action_update` with the result, then the
model's follow-up as in `/api/chat`. `GET /api/actions?session_id=` lists pending actions.

### `POST /api/ui/ticket`

Submits a trade ticket or position planner. It runs the risk guard and returns a pending action like a chat order.

```json
{ "session_id": "...", "market": "futures", "symbol": "ETHUSDT", "side": "LONG", "amount": 50,
  "order_type": "MARKET", "price": null, "leverage": 3, "take_profit": 4100, "stop_loss": 3800 }
```

### Sessions

| | |
|---|---|
| `GET /api/sessions` | list conversations |
| `GET /api/sessions/{id}` | one conversation |
| `PUT /api/sessions/{id}` | save the UI transcript: `{"transcript": [...]}` |
| `POST /api/sessions/{id}/reset` | clear its history |
| `DELETE /api/sessions/{id}` | delete it |

## Market data

Every endpoint here takes an optional `venue` query parameter (default `DEFAULT_VENUE`).

| | query | |
|---|---|---|
| `GET /api/venues` | | the venues and their capabilities |
| `GET /api/markets` | `quote`, `sort` (`volume`…), `limit=30`, `search` | market overview |
| `GET /api/movers` | `hours=4`, `quote=USDT`, `direction=gainers\|losers`, `limit=10` | top movers |
| `GET /api/depth/{symbol}` | `levels=12` | order book with analysis |
| `GET /api/trades/{symbol}` | `limit=30` | recent public trades |
| `GET /api/candles/{symbol}` | `resolution=60`, `bars=150` | OHLCV; resolutions `1`, `60`, `180`, `360`, `720`, `1D` |
| `GET /api/sentiment` | `quote=USDT` | global Fear & Greed and Market Pulse |
| `GET /api/news` | `query`, `limit=15` | crypto headlines |

## Account

| | |
|---|---|
| `GET /api/config` | modes, default venue, limits, model: what the UI needs to draw itself |
| `GET /api/portfolio` | the chat's paper (or live) spot and futures portfolio |
| `GET /api/account` | the real account the engine trades, with its value and allocation |
| `POST /api/paper/reset` | reset the paper balances of the default venue |
| `GET /api/alerts` | price alerts |
| `POST /api/alerts/{id}/ack` | dismiss a fired alert's notification |

## Engine and strategy

Requests that would trade real money need `"confirm": "LIVE"` in the body when the engine runs live. Endpoints
that switch modes, save settings or copy someone restart the engine's systemd service, so they only work on a server
set up with `deploy/`. The pause switch works anywhere.

| | body | |
|---|---|---|
| `GET /api/strategy` | | engine state, metrics, equity, positions, decisions |
| `POST /api/engine/switch` | `{"on": bool}` | pause or resume new entries; exits, stops and the final sell-off continue |
| `POST /api/engine/mode` | `{"mode": "live"\|"shadow"}` | switch the engine between real and simulated |
| `POST /api/strategy/live` | `{"on": bool}` | run the strategy's settings on the real account, or drop back to the current mode |
| `POST /api/strategy/schedule` | `{"at": "<ISO-8601>"\|null}` | schedule real trading to start; `null` cancels |
| `POST /api/engine/consolidate` | `{"assets": ["SOL", ...]}` | sell coins bought outside the engine into its quote asset on the next tick |
| `GET /api/strategy/tuning` | | tunable settings with their bounds and current values |
| `POST /api/strategy/tuning` | `{"values": {...}, "reset": false}` | save settings and restart the engine |

### Strategy assistant

| | body | |
|---|---|---|
| `POST /api/strategy/chat` | `{"message", "session_id", "allow_apply": true}` | ask; may propose or apply settings |
| `POST /api/strategy/chats/{id}/undo` | `{"index": n}` | undo an applied change |
| `GET /api/strategy/chats` | | list conversations |
| `GET`, `DELETE /api/strategy/chats/{id}` | | read or delete one |

### Copy-trading

| | body | |
|---|---|---|
| `GET /api/traders` | | tracked traders with their simulated copy results |
| `POST /api/traders` | `{"address", "name"}` | track a trader |
| `POST /api/traders/{address}/copy` | `{"pct": 0.5}` | copy them live with this share of equity |
| `DELETE /api/traders/{address}` | | stop tracking |
| `GET /api/personas` | | personas with their track records |
| `POST /api/personas/{id}/copy` | | apply a persona's settings to the engine |
| `POST /api/personas/stop-copy` | | go back to your own settings |

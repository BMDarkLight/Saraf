# Configuration

Settings come from environment variables, read from `.env` in the repository root or in `backend/`
(`backend/saraf/config.py`). Names are case-insensitive. `.env.example` is a commented starting point.

The venue keys, the default venue and the chat model (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`) can also be set in
the app under **تنظیمات** (settings), without a restart. Those are saved in `data/connections.json`, readable by the
owner only, and win over `.env`; removing one there falls back to the `.env` value. Nothing else can be set that
way.

## LLM

| variable | default | |
|---|---|---|
| `LLM_BASE_URL` | `https://api.openai.com/v1` | any OpenAI-compatible endpoint |
| `LLM_API_KEY` | | falls back to `OPENAI_API_KEY` when empty |
| `LLM_MODEL` | `gpt-4o` | |
| `LLM_TEMPERATURE` | `0.2` | |
| `LLM_STREAM` | `true` | `false` for providers that can't stream tool calls |
| `LLM_COMPAT_SCHEMAS` | `false` | for strict local chat templates: no `enum`/`anyOf` in tool schemas, tool history as text |
| `LLM_MAX_OUTPUT_TOKENS` | `1500` | cap on each reply; `0` = provider default |
| `AGENT_MAX_STEPS` | `8` | LLM steps per chat turn |
| `AGENT_HISTORY_CHARS` | `24000` | history sent to the LLM; older turns are dropped. The main cost lever on paid APIs |

## Venue

| variable | default | |
|---|---|---|
| `DEFAULT_VENUE` | `wallex` | any venue key (`wallex`, `nobitex`, `bitpin`, `ramzinex`, `tabdeal`, `binance`, `tse`); where orders go and what the dashboard shows |
| `WALLEX_BASE_URL` | `https://api.wallex.ir` | |
| `WALLEX_API_KEY` | | only for live trading and real balances |
| `WALLEX_SUB_ACCOUNT_CLIENT_ID` | | trade on a trade-credit sub-account; empty = the main account |
| `WALLEX_FUTURES_BASE_URL` | | futures endpoints aren't public; leave empty and use paper futures |
| `NOBITEX_API_KEY`, `NOBITEX_PRIVATE_KEY` | | a Nobitex API key and its Ed25519 private key (shown once when the key is made) |
| `NOBITEX_TOKEN` | | or the older login token from the panel, instead of an API key |
| `BITPIN_API_KEY`, `BITPIN_SECRET_KEY` | | |
| `RAMZINEX_API_KEY`, `RAMZINEX_SECRET` | | |
| `NAVASAN_API_KEY` | | free key from navasan.tech for free-market currency and gold rates |

`tabdeal`, `binance`, `tetherland` and `tse` are read-only and need no credentials. See [Venues](venues.md).

Lock exchange API keys to your server's IP, and give them trading permission only, never withdrawal.

## Trading modes and risk

| variable | default | |
|---|---|---|
| `SPOT_MODE` | `paper` | `paper` simulates fills on the live order book; `live` sends real orders |
| `FUTURES_MODE` | `paper` | |
| `REQUIRE_CONFIRMATION` | `true` | every chat order needs your approval. Keep it on for live trading |
| `MAX_ORDER_NOTIONAL_USDT` | `500` | per order or position, USDT markets |
| `MAX_ORDER_NOTIONAL_TMN` | `50000000` | per order or position, Toman markets |
| `MAX_LEVERAGE` | `10` | |
| `MAX_OPEN_POSITIONS` | `10` | |
| `SYMBOL_ALLOWLIST` | | comma-separated; empty = all markets |
| `PAPER_USDT` | `1000` | paper starting balance |
| `PAPER_TMN` | `100000000` | paper starting balance |

## News

| variable | default | |
|---|---|---|
| `TAVILY_API_KEY` | | better web search; falls back to DuckDuckGo |

## Autonomous engine

See [Autonomous engine](engine.md) for how these fit together. The strategy page's settings tab saves a bounded
subset to `data/engine/tuning.env`. On a server, `saraf-engine.service` loads that file after `.env` and the strategy profile
(`data/engine/profile.env`), so a saved setting replaces the profile's value on the next engine restart.

### Mode and model

| variable | default | |
|---|---|---|
| `ENGINE_MODE` | `shadow` | `backtest`, `shadow` or `live` (live also needs `--confirm-live`) |
| `ENGINE_DECIDER` | `ai` | `rules` = the baseline without the LLM |
| `ENGINE_LLM_MODEL` | | defaults to `LLM_MODEL` |
| `ENGINE_LLM_BASE_URL`, `ENGINE_LLM_API_KEY` | | a separate endpoint, so chat usage can't use up the engine's budget |
| `ENGINE_LLM_EXTRA_BODY` | | JSON merged into each request, e.g. `{"reasoning_effort": "none"}` for thinking models |
| `ENGINE_MAX_AI_CALLS_PER_BAR` | `4` | |
| `ENGINE_AI_REVIEWS` | `off` | AI position reviews: `off`, `tighten` (may only raise stops) or `full` (may also exit) |

### Universe and costs

| variable | default | |
|---|---|---|
| `ENGINE_CAPITAL_TMN` | `5000000` | base capital; returns are measured against it |
| `ENGINE_QUOTE` | `TMN` | `TMN` = Toman markets only; `USDT` = USDT core plus trades on USDT markets |
| `ENGINE_UNIVERSE` | `auto` | `auto` or a comma list of symbols |
| `ENGINE_UNIVERSE_SIZE` | `10` | markets in the auto universe |
| `ENGINE_ALLOWED_MARKETS` | | restrict trading to these; empty = no restriction |
| `ENGINE_BLOCKED_MARKETS` | `credit` | never traded: `credit` = markets disabled on trade-credit accounts, or a comma list |
| `ENGINE_MAX_SPREAD_PCT` | `0.4` | |
| `ENGINE_TIMEFRAME_MIN` | `15` | bar size |
| `ENGINE_FEE_RATE` | `0.0025` | taker fee per side |
| `ENGINE_MAKER_FEE_RATE` | `-1` | maker fee; below 0 = same as taker |
| `ENGINE_EXECUTION` | `maker` | `maker`: limit entries at the bid and resting take-profits; stops stay market orders. `taker`: market orders everywhere |
| `ENGINE_ENTRY_TIMEOUT_BARS` | `2` | cancel an unfilled entry after this many bars |
| `ENGINE_ENTRY_CHASE_ATR` | `0.1` | cancel the entry once the ask runs this many ATRs away |

### Signals

| variable | default | |
|---|---|---|
| `ENGINE_SETUPS` | `oversold_reversion,dip_in_uptrend` | also `trend_pullback`, `breakout`, `mean_reversion` |
| `ENGINE_MIN_SCORE` | `0.5` | rule score needed to reach the decider |
| `ENGINE_MIN_ATR_PCT`, `ENGINE_MAX_ATR_PCT` | `0.35`, `4.0` | skip markets too quiet to beat fees, or too wild |
| `ENGINE_DIP_ATR` | `0` | above 0: a dip needs a 1h fall of this many ATRs (0 = a fixed −1.5%) |
| `ENGINE_BREADTH_GATE_PCT` | `25` | no new entries while fewer than this % of markets are up over 24h |

### Risk

| variable | default | |
|---|---|---|
| `ENGINE_MAX_POSITIONS` | `3` | |
| `ENGINE_MAX_POSITION_PCT` | `0.25` | of equity, per position |
| `ENGINE_MAX_EXPOSURE_PCT` | `0.75` | of equity, all positions |
| `ENGINE_RISK_PER_TRADE_PCT` | `0.01` | equity lost if the stop is hit |
| `ENGINE_DRAWDOWN_PAUSE_PCT` | `0.06` | from peak: no new entries for `ENGINE_PAUSE_HOURS` |
| `ENGINE_PAUSE_HOURS` | `12` | |
| `ENGINE_DRAWDOWN_STOP_PCT` | `0.12` | from peak: flatten everything and stop |
| `ENGINE_TIME_STOP_BARS` | `32` | exit a position that hasn't worked after this many bars |
| `ENGINE_COOLDOWN_BARS` | `4` | wait before re-entering a market |
| `ENGINE_MONITOR_SECONDS` | `15` | how often stops, targets and guards are checked |

### Session end

| variable | default | |
|---|---|---|
| `ENGINE_END_TIME` | | ISO-8601 UTC; everything is sold to Toman before it |
| `ENGINE_NO_ENTRY_BEFORE_END_MIN` | `240` | |
| `ENGINE_FLATTEN_BEFORE_END_MIN` | `45` | |
| `ENGINE_FINAL_SWEEP` | `true` | also sell coins the engine doesn't manage (bought by hand or in chat) |

### USDT core

Used with `ENGINE_QUOTE=USDT`.

| variable | default | |
|---|---|---|
| `ENGINE_CORE_ENABLED` | `true` | hold free Toman as USDT |
| `ENGINE_CORE_TMN_RESERVE` | `30000` | Toman left unconverted |
| `ENGINE_CORE_GUARD_PCT` | `0.06` | USDT/TMN this far below its peak moves the core to Toman |
| `ENGINE_CORE_EXIT_HOURS` | `6` | exit window before the session end |
| `ENGINE_CORE_EXIT_SLICES` | `3` | USDT → Toman in this many AI-timed slices |
| `ENGINE_CORE_FEE_RATE` | `0.003` | USDT/TMN taker fee, for backtests |

### Copy-trading

| variable | default | |
|---|---|---|
| `ENGINE_FOLLOW_ADDRESS` | | a Hyperliquid account to copy; empty = off |
| `ENGINE_FOLLOW_PCT` | `0.5` | at most this share of equity follows the trader's longs |

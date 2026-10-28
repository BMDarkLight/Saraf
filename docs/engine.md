# Autonomous engine

`backend/saraf/engine/` is a rules-plus-LLM spot trading engine that runs without a person in the loop. The same
`Engine` class runs in three modes. Only the feed and the executor change:

| mode | data | fills |
|---|---|---|
| `backtest` | recorded venue history replayed with a moving clock (`SimFeed`) | simulated against the replay (`SimExec`) |
| `shadow` | live venue data (`LiveFeed`) | paper broker on the live order book, no real orders |
| `live` | live venue data | real orders on the venue (needs `--confirm-live`) |

Because the code is identical, a backtest and a shadow run of the same period make the same decisions.

## The cycle

```
on_bar   (each closed 15m bar)   regime check → position reviews → signals → AI decision → risk sizing → entry
monitor  (every few seconds)     drawdown guard, end-of-session flatten, stop / target / trailing-stop exits
```

1. **Features** (`features.py`): bars and indicators computed from closed candles only, so there is no look-ahead.
2. **Signals** (`strategy.py`): deterministic setups propose long-only spot trades, each with a score, a default
   stop and target in ATRs, and the rules that fired. `ENGINE_SETUPS` picks which ones run:

   | setup | default |
   |---|---|
   | `oversold_reversion` | on |
   | `dip_in_uptrend` | on |
   | `trend_pullback`, `breakout`, `mean_reversion` | off: no edge after costs in research |

3. **Decision** (`decider.py`): with `ENGINE_DECIDER=ai`, an LLM sees each candidate with its full context
   (multi-timeframe features, order book, market regime, sentiment, news, portfolio and drawdown) and returns
   TAKE or SKIP, conviction, a size fraction and its own stop and target. It also reviews open positions
   (HOLD / EXIT / TIGHTEN) and sets the market regime, which scales risk. `ENGINE_DECIDER=rules` is the baseline
   without the LLM.
4. **Risk** (`risk.py`): the AI proposes and this module decides. It clamps every size and stop the AI returns and
   can veto any entry: risk per trade, per-position and total exposure caps, max positions, a market-breadth gate,
   ATR and spread filters, cooldowns, a drawdown pause and a drawdown stop.
5. **Execution** (`execution.py`): maker-first by default. Entries are posted as limit orders and cancelled if
   still unfilled after `ENGINE_ENTRY_TIMEOUT_BARS`. `ENGINE_EXECUTION=taker` uses market orders.

### The USDT core

With `ENGINE_QUOTE=USDT` and `ENGINE_CORE_ENABLED=true`, `allocator.py` holds free Toman as USDT, keeping a small
Toman reserve, because USDT/TMN has tended to rise with the toman's depreciation. If USDT/TMN falls
`ENGINE_CORE_GUARD_PCT` from its peak, the core moves back to Toman. Before the session ends it sells back to Toman in
`ENGINE_CORE_EXIT_SLICES` AI-timed slices within `ENGINE_CORE_EXIT_HOURS`.

### Ending a session

`ENGINE_END_TIME` sets a hard end. New entries stop `ENGINE_NO_ENTRY_BEFORE_END_MIN` minutes before it, and
everything is sold to Toman `ENGINE_FLATTEN_BEFORE_END_MIN` minutes before it.

## Running it

```bash
cd backend
uv run python -m saraf.engine backtest --days 14 --decider both     # rules vs AI, with counterfactuals
uv run python -m saraf.engine run --mode shadow                     # live data, simulated fills
uv run python -m saraf.engine run --mode live --confirm-live        # real orders on the default venue
uv run python -m saraf.engine report data/backtests/<run>           # metrics for any journal directory
```

| command | options |
|---|---|
| `backtest` | `--days`, `--end` (ISO UTC), `--decider ai\|rules\|both`, `--symbols`, `--offline` (AI answers from cache only, an exact replay), `--persona`, `--venue` |
| `run` | `--mode shadow\|live`, `--confirm-live`, `--hours` (stop after N hours and close to Toman), `--name` (journal directory), `--persona`, `--venue` |
| `report` | a journal directory |

### Backtests

Market orders fill at the next 1-minute price ± half the spread, plus slippage and the configured fee. Stops and
targets are checked against each 1-minute bar's low and high, stop first when both are hit. Every candidate the
decider saw is also simulated with its default stop and target. The report then compares the trades the AI took
with the ones it skipped.

### Journals and restarts

Every signal, AI decision, risk check, order and exit is appended to `decisions.jsonl` with its reasons, next to
`trades.csv` and `equity.csv`. A shadow or live run resumes from `data/engine/<name>/state.json` after a restart and
reconciles its positions with the exchange balances.

## Controlling it from the UI

The [strategy page](using-saraf.md#strategy-page) reads the journal and writes control files the engine picks up:

- the on/off switch pauses new entries
- the settings tab writes `data/engine/tuning.env` and restarts the engine with it, keeping its positions
- the account switch moves the engine between shadow and live
- the strategy assistant (`strategy_chat.py`) proposes or applies setting changes, each one undoable

## Personas

A persona (`personas.py`) is a named preset of engine settings with its own shadow session and track record:

| persona | style |
|---|---|
| استراتژیست (`strategist`) | the main strategy: USDT core plus AI-approved dip buying |
| نگهبان تتر (`guardian`) | holds USDT only, no crypto trades |
| شکارچی افت (`dip_hunter`) | buys dips more aggressively: 3 positions, 1.5% risk |
| موج‌سوار (`trend_rider`) | experimental: breakouts and trend pullbacks |
| محتاط (`cautious`) | only the strongest signals: one position, rules decider, tight 3% guards |

Each one runs as `saraf-persona@<id>.service` on a server, or locally with `run --persona <id>`. **Copy** on a
persona card applies its settings to the main engine.

## Copy-trading real traders

`traders.py` tracks public on-chain traders (Hyperliquid accounts). Their positions are simulated on the venue's
USDT markets to build a track record. Shorts can't be copied on spot, so only longs count. With
`ENGINE_FOLLOW_ADDRESS` set (or **live copy** in the UI), `follow.py` reads the trader's open positions every minute
and buys or sells towards their longs with at most `ENGINE_FOLLOW_PCT` of equity. The rest stays in the core.

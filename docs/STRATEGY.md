# Strategy proposal: USDT core + gated dip satellite

The challenge scores **realized TMN return** over 3 days on 5M TMN of spot credit, with no leverage.
Every number below was measured on Wallex data with real credit-account fees. The scripts in
`backend/scripts/` and the engine backtester reproduce them.

## 1. What the evidence says

| Idea | Result | Verdict |
|---|---|---|
| Hold USDT (the toman's depreciation) | +78% over 180d; 72h windows: mean +0.93%, net of fees +0.33%; last 60d maxDD 6.6%, 72h win rate 65% | **Core** |
| Time USDT vs TMN (12 rules: EMA, 72h mean, momentum) | Every rule lost to holding, both in-sample and out-of-sample | Drop |
| Rotate USDT ↔ BTC/ETH on trend | −8% to −47% (whipsaw plus fees) | Drop |
| Triangular arbitrage X/TMN vs X/USDT·USDT/TMN | 0 of 192 live samples profitable; best −0.85% | Drop |
| Dip engine on **TMN** markets (21d, 1-minute replay, 0.30% taker fee) | −3.03%, 17 trades, win 29%, PF 0.34, 113k TMN of fees | Drop. This is what the engine runs today |
| Dip engine on **USDT** markets (same setups, 0.13% taker fee) | +0.23% in USDT terms, 5 trades, win 60%, PF 1.34, maxDD 0.66% | **Satellite**, kept small |
| Regime/breadth gate | Signals the gate blocked averaged −1.1% (TMN) and −1.7% (USDT) per trade | **Keep, hard rule** |

Two takeaways:
- The edge in Wallex's TMN markets is the toman's drift itself, not intraday patterns.
- The same dip setups that had a +0.4–0.6% gross edge lose on TMN markets because a round trip
  costs about 0.6%. On USDT markets it costs about 0.23%, and the edge survives.

## 2. The flow

```
5M TMN credit
   │
   ├─ Phase 0 (start): convert free TMN to USDT in one order (USDTTMN, 0.30% taker)
   │                      └─ keep 30k TMN unconverted
   │
   ├─ Core (≈70–100% of equity): hold USDT ── earns USDT/TMN drift (+0.3%/72h expected, net)
   │
   ├─ Satellite (≤30% of equity, funded in USDT, so it never leaves dollar terms)
   │     signal:  oversold_reversion / dip_in_uptrend on top-liquid X/USDT markets
   │     gate:    breadth ≥ 25% of markets up 24h AND AI regime ≠ risk_off  (hard rule)
   │     AI:      approves / sizes (0–1) / adjusts stop and target within risk bounds
   │     risk:    1% of equity at stop, ≤12.5% per position, ≤3 positions, maker entries and exits
   │
   └─ Phase 3 (final hours): AI-timed exit of everything to TMN
         window opens T−6h; AI picks the moment from USDT/TMN momentum + book depth
         TWAP in 3 maker slices; forced taker exit at T−45min; no new entries after T−4h
```

### Why this maximises the score
- **Return rank (90):** the core captures the one robust edge. Teams trading TMN markets pay
  0.6% per round trip into a market with no edge after fees.
- **Drawdown (30) and stability (15):** USDT/TMN 72h drawdowns are small (worst 6.6% in 60d, typically
  under 2%). The satellite is capped and stopped.
- **Activity (15):** the satellite plus the final TWAP give a steady flow of real, explained trades,
  not churn.
- **Strategy logic (40):** each component is backed by a measured test and each rejected idea
  has a recorded reason (table above).
- **Real AI usage (35):** the AI does what rules do badly: regime judgement from news, fear & greed
  and breadth; per-trade approval and sizing; choosing the exit moment. Rules keep hard limits
  the AI cannot loosen.
- **Risk management (30):** deterministic clamps on size, stop, exposure and positions; a 6% drawdown
  pause; a 12% drawdown flatten; the breadth gate; a time stop; a forced exit before the end.
- **Reproducibility (25):** fixed seeds, cached AI decisions (`--offline` replays bit-for-bit), and
  research scripts in the repo.
- **Backtest + shadow (20):** a 1-minute replay backtest for both market sets (above), plus a
  shadow run logged in the same journal format as live.

## 3. Risk rules (deterministic; the AI can only be stricter)

| Rule | Value |
|---|---|
| Satellite cap | 30% of equity; 0% when regime is risk_off |
| Risk per trade | 1% of equity at the stop, fees included |
| Per position / concurrent | 12.5% / 3 |
| Drawdown pause / halt | 6% from peak → 12h no entries; 12% → flatten satellite, halt |
| Core guard | USDT/TMN 6% below its peak since entry → satellite flattened, core to TMN; re-entry after 12h once back above the exit rate |
| End of challenge | No entries after T−4h; AI-timed exit from T−6h; forced exit at T−45min |

## 4. UX and informativity

- **Benchmark chart:** equity vs "hold TMN" (flat) vs "hold USDT", so the value added is visible.
- **Strategy panel:** current phase, core/satellite split, gate status with the reason, next
  scheduled action, countdown to the end.
- **Decision feed in Persian:** each entry, skip, block and exit shows the rules that fired, the
  AI's two-sentence reasoning and the risk numbers used.
- **Evidence page:** the table in section 1, linked from the UI.

## 5. Implementation (done)

| Piece | Where |
|---|---|
| Engine trades X/USDT markets; books, drawdown and risk in TMN via USDT/TMN | `engine/core.py` (`ENGINE_QUOTE=USDT`) |
| USDT core: accumulate, guard, AI-timed 3-slice exit, final sweep | `engine/allocator.py` |
| AI exit timing (`time_core_exit`), rule baseline = sell when the last hour turned down | `engine/decider.py` |
| Strategy session in shadow beside the main engine (own journal `data/engine/strategy`) | `deploy/wallex-strategy.service`, `deploy/strategy.env` |
| Strategy panel: phase, allocation, equity vs hold-USDT/hold-TMN, gate, exit schedule, evidence | `frontend/src/components/Strategy.tsx`, `GET /api/strategy` |
| Tests: core rides the rate and ends in TMN, guard blocks entries, slices follow AI then schedule | `tests/test_engine.py` |

First full-pipeline backtest (7 days to 2026-10-22 21:00 UTC, rules decider, 1-minute replay):
**+1.45% in TMN, max drawdown 2.89%.** USDT/TMN itself rose 2.04%; the gap is the two conversions
(about 0.6%). The satellite took no trades: the breadth gate blocked all 20 signals, which would have
averaged −0.42%. Reproduce:

```
set -a; . deploy/strategy.env; set +a
uv run python -m wallex_agent.engine backtest --days 7 --end 2026-10-22T21:00:00Z --decider rules \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,TRXUSDT,ADAUSDT,AVAXUSDT,LINKUSDT,USDTTMN
```

## 6. Audit (2026-10-23): why it barely traded

| Finding | Effect | Fix |
|---|---|---|
| Backtests used a 5 USDT minimum order for USDT markets; Wallex's `/v1/markets` says 1 USDT. At 5 USDT x 1.2 (~1.64M TMN, 33% of equity) no satellite order fit the 12.5% cap, so the backtests showed no satellite trades | Backtests understated the satellite (live was not affected) | SimFeed uses 1 USDT; `risk.size` also takes the exchange minimum when its loss at the stop fits the risk budget, the exposure room and the cash |
| Live, after the switch to the USDT profile (2026-10-23 08:30): no satellite candidate at all in hours, the gate closed overnight (6-10% of markets up), and ~3.95M TMN of USOON bought outside the engine filling the exposure cap | The satellite was idle | the exposure cap counts only the engine's own positions and entry orders (`Engine.satellite_exposure`); outside coins still count in equity and the drawdown guards; activity is otherwise a tuning choice (section 7) |
| Live breadth gate counted **TMN** markets, while the satellite trades USDT markets and the backtest measured breadth on the USDT universe | A strengthening toman read as a broad sell-off (risk_off: no entries) and a weakening one as strength; backtest and live applied different gates | breadth is measured on the markets of `ENGINE_QUOTE` |
| An LLM failure (timeout, spent budget, bad JSON) made every entry a fail-safe SKIP | A provider outage silently stopped all trading | entries fall back to the rule baseline (which beat the AI in every backtest above); regime and reviews keep their safe defaults |
| `deploy/strategy.env` reached the live engine only through a copy made when the switch was pressed | Strategy changes pushed later never reached the live engine | `deploy.sh` refreshes `data/engine/profile.env` when the strategy is live |
| `ENGINE_END_TIME` lived only in the server's `.env` | Moving the challenge window needed shell access | `deploy/challenge.env` (tracked, loaded last by all three services) |

| ~80% of the live account sat in USOON bought outside the engine; the end flatten sold only the engine's own positions | That part would never be realized in TMN | the final sweep sells every other coin with a market before the end (`ENGINE_FINAL_SWEEP`) |

## 7. Tuning, copy-trading and the strategy chat

The strategy is a page of its own in the UI (header switch «استراتژی»), with five tabs:

| Tab | What it does | Where |
|---|---|---|
| Overview | return vs capital and vs holding USDT, the curve, where the money is (core / satellite / coins bought outside the engine / TMN), the gate, positions, the end-of-challenge plan, the latest decisions | `strategy/Overview.tsx`, `GET /api/strategy` |
| Tuning | ~20 bounded engine settings (size and risk, entries, execution, AI, core and end). Saved to `data/engine/tuning.env`, which `wallex-engine.service` loads after the profile; the engine restarts. Live mode asks first | `wallex_agent/tuning.py`, `GET/POST /api/strategy/tuning` |
| Copy-trading | personas = named presets of those settings. Each runs as its own shadow session (`wallex-persona@<id>.service`, journal `data/engine/persona-<id>`), so a profile shows a real track record. Copying writes the preset to `tuning.env` on the strategy profile | `wallex_agent/personas.py`, `GET /api/personas`, `POST /api/personas/{id}/copy`, `/api/personas/stop-copy` |
| Assistant | a chat about the strategy; the model may propose changes (validated against the tuning bounds), shown as a diff the person applies | `wallex_agent/strategy_chat.py`, `POST /api/strategy/chat` |
| Evidence | the research table above | |

Personas other than the strategist use the rule decider, so several sessions don't spend the engine's LLM budget.

## 8. Remaining steps

1. The challenge ends **Monday 2026-10-26 23:59 Tehran** (`deploy/challenge.env`, `2026-10-26T20:29:00Z`), extended
   by two days. No new entries after T-4h, the AI-timed USDT exit opens at T-6h, everything is in TMN by T-45min.
2. Review the shadow session, then press «اجرای واقعی» on the strategy card: the main engine goes live with
   `deploy/strategy.env` (copied to `data/engine/profile.env`, loaded by `wallex-engine.service`).
3. Convert TMN↔USDT with maker limits (0.25% instead of 0.30%) to recover about 0.1%.

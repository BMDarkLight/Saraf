# The Wallex AI trading challenge

Saraf started as an entry to Wallex's agentic AI trading challenge (October 2026): an autonomous engine trading
5M TMN of trade credit for a few days, plus the chat assistant. The engine and its tooling are still in the code
(`backend/saraf/engine/`, personas, copy-trading, `deploy/`); this folder keeps the challenge's own record.

| file | what it is |
|---|---|
| [ENGINE.fa.md](ENGINE.fa.md) | the engine write-up for the judges (Persian): architecture, the AI's role, entry/exit rules, risk, research, results, how to reproduce |
| [STRATEGY.md](STRATEGY.md) | the later strategy proposal: a USDT core plus a gated dip-buying satellite |
| [results/](results/) | backtests (rules vs AI, 7 and 14 days), the maker/taker execution comparison, persona backtests, a shadow session, and the AI response cache that makes AI backtests replay exactly |

All numbers in these files were measured on Wallex market data with the challenge account's fees.

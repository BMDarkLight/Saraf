# Backtest rules - 7 days to 20261021-1300 UTC

| metric | value |
|---|---|
| initial_capital_tmn | 5000000.0 |
| final_equity_tmn | 5010480 |
| net_return_pct | 0.21 |
| max_drawdown_pct | 0.799 |
| sharpe_annualized | 1.08 |
| trades | 7 |
| win_rate_pct | 28.6 |
| avg_trade_pnl_pct | 0.123 |
| profit_factor | 1.2 |
| traded_volume_tmn | 17555189 |
| avg_trade_size_tmn | 1253193 |
| decisions | 15 |
| decisions_taken | 15 |
| risk_blocks | 31 |
| errors | 0 |
| circuit_breakers | 0 |
| fees_paid_tmn | 17564 |
| llm_calls | 0 |

**by_setup**

```json
{
 "dip_in_uptrend": {
  "trades": 5,
  "avg_pnl_pct": -0.295
 },
 "oversold_reversion": {
  "trades": 2,
  "avg_pnl_pct": 1.167
 }
}
```

**exit_reasons**

```json
{
 "trailing_stop": 3,
 "take_profit": 2,
 "stop_loss": 2
}
```

**decision_sources**

```json
{
 "rules": 15
}
```

**counterfactual_by_action**

```json
{
 "TAKE": {
  "n": 15,
  "avg_pnl_pct": 0.097,
  "win_rate_pct": 46.7
 },
 "SKIP": {
  "n": 0,
  "avg_pnl_pct": null,
  "win_rate_pct": null
 }
}
```

**buy_and_hold_pct**

```json
{
 "PUMPTMN": -2.03,
 "ARBTMN": 4.19,
 "XRPTMN": -0.59,
 "ETHTMN": -0.35,
 "PEPETMN": 11.61,
 "BTCTMN": 0.43,
 "DOGETMN": -1.79,
 "TRXTMN": -0.56,
 "ADATMN": -2.3,
 "BNBTMN": 1.43
}
```


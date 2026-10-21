# Backtest rules - 21 days to 20261021-1300 UTC

| metric | value |
|---|---|
| initial_capital_tmn | 5000000.0 |
| final_equity_tmn | 4838149 |
| net_return_pct | -3.237 |
| max_drawdown_pct | 4.183 |
| sharpe_annualized | -3.2 |
| trades | 51 |
| win_rate_pct | 33.3 |
| avg_trade_pnl_pct | -0.363 |
| profit_factor | 0.67 |
| traded_volume_tmn | 117548805 |
| avg_trade_size_tmn | 1154026 |
| decisions | 53 |
| decisions_taken | 53 |
| risk_blocks | 133 |
| errors | 0 |
| circuit_breakers | 0 |
| fees_paid_tmn | 294240 |
| llm_calls | 0 |

**by_setup**

```json
{
 "dip_in_uptrend": {
  "trades": 30,
  "avg_pnl_pct": -0.429
 },
 "oversold_reversion": {
  "trades": 21,
  "avg_pnl_pct": -0.267
 }
}
```

**exit_reasons**

```json
{
 "stop_loss": 17,
 "trailing_stop": 23,
 "time_stop": 2,
 "take_profit": 9
}
```

**decision_sources**

```json
{
 "rules": 53
}
```

**counterfactual_by_action**

```json
{
 "TAKE": {
  "n": 53,
  "avg_pnl_pct": -0.297,
  "win_rate_pct": 47.2
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
 "PEPETMN": 7.17,
 "ARBTMN": -4.73,
 "XRPTMN": -8.63,
 "PUMPTMN": -14.87,
 "ETHTMN": -6.6,
 "BTCTMN": 1.24,
 "BNBTMN": -0.18,
 "DOGETMN": -12.99,
 "TRXTMN": 1.27,
 "ADATMN": -6.09
}
```


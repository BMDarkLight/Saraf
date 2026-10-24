# Backtest ai - 14 days to 20261014-1300 UTC

| metric | value |
|---|---|
| initial_capital_tmn | 5000000.0 |
| final_equity_tmn | 5008874 |
| net_return_pct | 0.177 |
| max_drawdown_pct | 1.894 |
| sharpe_annualized | 0.49 |
| trades | 24 |
| win_rate_pct | 45.8 |
| avg_trade_pnl_pct | 0.041 |
| profit_factor | 1.05 |
| traded_volume_tmn | 35747294 |
| avg_trade_size_tmn | 744550 |
| decisions | 54 |
| decisions_taken | 54 |
| risk_blocks | 88 |
| errors | 0 |
| circuit_breakers | 0 |
| fees_paid_tmn | 35765 |
| llm_calls | 130 |

**by_setup**

```json
{
 "dip_in_uptrend": {
  "trades": 11,
  "avg_pnl_pct": 0.062
 },
 "oversold_reversion": {
  "trades": 13,
  "avg_pnl_pct": 0.024
 }
}
```

**exit_reasons**

```json
{
 "trailing_stop": 4,
 "stop_loss": 11,
 "take_profit": 8,
 "time_stop": 1
}
```

**decision_sources**

```json
{
 "cache": 5,
 "llm": 49
}
```

**counterfactual_by_action**

```json
{
 "TAKE": {
  "n": 54,
  "avg_pnl_pct": 0.098,
  "win_rate_pct": 50.0
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
 "PEPETMN": -3.98,
 "PUMPTMN": -13.1,
 "BTCTMN": 0.66,
 "ETHTMN": -6.27,
 "XRPTMN": -8.08,
 "ARBTMN": -8.56,
 "BNBTMN": -1.59,
 "DOGETMN": -11.41,
 "TRXTMN": 1.84,
 "ADATMN": -3.88
}
```


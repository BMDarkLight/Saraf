# Backtest rules - 14 days to 20261014-1300 UTC

| metric | value |
|---|---|
| initial_capital_tmn | 5000000.0 |
| final_equity_tmn | 5055403 |
| net_return_pct | 1.108 |
| max_drawdown_pct | 3.139 |
| sharpe_annualized | 1.8 |
| trades | 23 |
| win_rate_pct | 47.8 |
| avg_trade_pnl_pct | 0.211 |
| profit_factor | 1.21 |
| traded_volume_tmn | 58026190 |
| avg_trade_size_tmn | 1260235 |
| decisions | 59 |
| decisions_taken | 59 |
| risk_blocks | 90 |
| errors | 0 |
| circuit_breakers | 0 |
| fees_paid_tmn | 58055 |
| llm_calls | 0 |

**by_setup**

```json
{
 "dip_in_uptrend": {
  "trades": 11,
  "avg_pnl_pct": 0.062
 },
 "oversold_reversion": {
  "trades": 12,
  "avg_pnl_pct": 0.347
 }
}
```

**exit_reasons**

```json
{
 "trailing_stop": 4,
 "stop_loss": 10,
 "take_profit": 8,
 "time_stop": 1
}
```

**decision_sources**

```json
{
 "rules": 59
}
```

**counterfactual_by_action**

```json
{
 "TAKE": {
  "n": 59,
  "avg_pnl_pct": -0.024,
  "win_rate_pct": 47.5
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


# Backtest rules - 7 days to 20261021-1300 UTC

| metric | value |
|---|---|
| initial_capital_tmn | 5000000.0 |
| final_equity_tmn | 5007641 |
| net_return_pct | 0.153 |
| max_drawdown_pct | 1.332 |
| sharpe_annualized | 0.64 |
| trades | 10 |
| win_rate_pct | 40.0 |
| avg_trade_pnl_pct | 0.067 |
| profit_factor | 1.09 |
| traded_volume_tmn | 25085562 |
| avg_trade_size_tmn | 1253896 |
| decisions | 12 |
| decisions_taken | 12 |
| risk_blocks | 32 |
| errors | 0 |
| circuit_breakers | 0 |
| fees_paid_tmn | 62793 |
| llm_calls | 0 |

**by_setup**

```json
{
 "dip_in_uptrend": {
  "trades": 7,
  "avg_pnl_pct": -0.555
 },
 "oversold_reversion": {
  "trades": 3,
  "avg_pnl_pct": 1.519
 }
}
```

**exit_reasons**

```json
{
 "trailing_stop": 5,
 "take_profit": 2,
 "stop_loss": 3
}
```

**decision_sources**

```json
{
 "rules": 12
}
```

**counterfactual_by_action**

```json
{
 "TAKE": {
  "n": 12,
  "avg_pnl_pct": 0.291,
  "win_rate_pct": 58.3
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
 "PEPETMN": 11.61,
 "PUMPTMN": -2.03,
 "BTCTMN": 0.43,
 "ETHTMN": -0.35,
 "ARBTMN": 4.19,
 "XRPTMN": -0.59,
 "BNBTMN": 1.43,
 "TRXTMN": -0.56,
 "DOGETMN": -1.79,
 "ADATMN": -2.3
}
```


# Backtest ai - 7 days to 20261021-1300 UTC

| metric | value |
|---|---|
| initial_capital_tmn | 5000000.0 |
| final_equity_tmn | 5006356 |
| net_return_pct | 0.127 |
| max_drawdown_pct | 0.48 |
| sharpe_annualized | 1.07 |
| trades | 7 |
| win_rate_pct | 28.6 |
| avg_trade_pnl_pct | 0.123 |
| profit_factor | 1.2 |
| traded_volume_tmn | 10522500 |
| avg_trade_size_tmn | 751153 |
| decisions | 14 |
| decisions_taken | 14 |
| risk_blocks | 30 |
| errors | 0 |
| circuit_breakers | 0 |
| fees_paid_tmn | 10528 |
| llm_calls | 54 |

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
 "cache": 1,
 "llm": 13
}
```

**counterfactual_by_action**

```json
{
 "TAKE": {
  "n": 14,
  "avg_pnl_pct": -0.03,
  "win_rate_pct": 42.9
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


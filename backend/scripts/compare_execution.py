"""Backtest grid: taker vs maker execution at several maker-fee levels (rules decider, fixed window).

usage: uv run python scripts/compare_execution.py --end 2026-10-21T13:00:00Z --days 7,14,21
"""
import argparse
import asyncio
import json
from datetime import datetime

from saraf.config import ROOT, get_settings
from saraf.engine.backtest import run_backtest

CONFIGS = [("taker", -1.0), ("maker", -1.0), ("maker", 0.0015), ("maker", 0.001)]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", required=True)
    ap.add_argument("--days", default="7,14,21")
    ap.add_argument("--symbols", help="pin the universe (comma-separated) for comparable runs")
    a = ap.parse_args()
    end = datetime.fromisoformat(a.end.replace("Z", "+00:00")).timestamp()
    base = get_settings()
    rows = []
    for days in [float(d) for d in a.days.split(",")]:
        for execution, maker_fee in CONFIGS:
            s = base.model_copy(update={"engine_execution": execution, "engine_maker_fee_rate": maker_fee})
            tag = f"{execution}-{'same' if maker_fee < 0 else maker_fee}"
            out = ROOT / "data" / "backtests" / "execution" / tag
            syms = [x.strip().upper() for x in a.symbols.split(",")] if a.symbols else None
            m = (await run_backtest(s, days, end, ("rules",), syms, out_root=out))["rules"]
            ev = [json.loads(l)["event"] for l in open(m["dir"] + "/decisions.jsonl")]
            rows.append((days, tag, m, ev.count("entry"), ev.count("entry_order")))
    print(f"{'days':>4} {'execution':16} {'return%':>8} {'maxDD%':>7} {'trades':>6} {'win%':>5} {'PF':>5} {'fees':>8}  fills")
    for days, tag, m, fills, orders in rows:
        f = f"{fills}/{orders}" if orders else "-"
        print(f"{days:4g} {tag:16} {m['net_return_pct']:8.2f} {m['max_drawdown_pct']:7.2f} {m['trades']:6} "
              f"{m['win_rate_pct'] or 0:5.1f} {m['profit_factor'] or 0:5.2f} {m['fees_paid_tmn']:8}  {f}")


asyncio.run(main())

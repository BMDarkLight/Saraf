"""CLI.

  python -m wallex_agent.engine backtest --days 14 --decider both
  python -m wallex_agent.engine run --mode shadow
  python -m wallex_agent.engine run --mode live --confirm-live
  python -m wallex_agent.engine report data/engine/live
"""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from ..config import get_settings
from .metrics import compute_metrics, render_markdown


def main() -> None:
    p = argparse.ArgumentParser(prog="wallex_agent.engine")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backtest")
    b.add_argument("--days", type=float, default=14)
    b.add_argument("--end", help="ISO end time (UTC); default = last full hour")
    b.add_argument("--decider", choices=["ai", "rules", "both"], default="both")
    b.add_argument("--symbols", help="comma-separated; default = current auto universe")
    b.add_argument("--offline", action="store_true", help="AI decisions from cache only (exact replay)")
    b.add_argument("--persona", help="apply a copy-trading persona's preset (wallex_agent/personas.py)")
    r = sub.add_parser("run")
    r.add_argument("--mode", choices=["shadow", "live"], default=None)
    r.add_argument("--confirm-live", action="store_true")
    r.add_argument("--hours", type=float, help="stop after this many hours (shadow sessions); positions are closed to TMN")
    r.add_argument("--name", help="journal directory under data/engine (default: the mode), for a session that runs "
                                  "beside the main engine")
    r.add_argument("--persona", help="run a copy-trading persona's preset in shadow (journal: data/engine/persona-<id>)")
    rep = sub.add_parser("report")
    rep.add_argument("dir")
    a = p.parse_args()
    s = get_settings()
    if getattr(a, "persona", None):
        from ..personas import BY_ID, run_name
        from ..tuning import validate
        if a.persona not in BY_ID:
            raise SystemExit(f"unknown persona {a.persona!r}; known: {', '.join(BY_ID)}")
        s = s.model_copy(update=validate(BY_ID[a.persona]["preset"]))
        if a.cmd == "run":
            if (a.mode or s.engine_mode) == "live":
                raise SystemExit("personas run in shadow only; copy one to trade its preset for real")
            a.name = a.name or run_name(BY_ID[a.persona])

    if a.cmd == "backtest":
        from .backtest import run_backtest
        kinds = ("rules", "ai") if a.decider == "both" else (a.decider,)
        end = datetime.fromisoformat(a.end.replace("Z", "+00:00")).timestamp() if a.end else None
        syms = [x.strip().upper() for x in a.symbols.split(",")] if a.symbols else None
        res = asyncio.run(run_backtest(s, a.days, end, kinds, syms, a.offline))
        first = next(iter(res.values()))
        print("\nreproduce exactly (the auto universe changes with 24h volume):\n  python -m wallex_agent.engine "
              f"backtest --days {a.days:g} --end {first['window_end']} --symbols {','.join(first['symbols'])} "
              f"--decider {a.decider}")
        for kind, m in res.items():
            print(f"\n=== {kind} === {m['dir']}")
            for k in ("net_return_pct", "max_drawdown_pct", "trades", "win_rate_pct", "profit_factor",
                      "avg_trade_pnl_pct", "fees_paid_tmn", "llm_calls", "counterfactual_by_action"):
                print(f"  {k}: {m.get(k)}")
    elif a.cmd == "run":
        from .runner import run_engine
        asyncio.run(run_engine(s, a.mode or s.engine_mode, a.confirm_live, hours=a.hours, name=a.name))
    else:
        d = Path(a.dir)
        m = compute_metrics(d, s.engine_capital_tmn)
        (d / "report.md").write_text(render_markdown(f"Report {d.name}", m))
        print(json.dumps(m, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()

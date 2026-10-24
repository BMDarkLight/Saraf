"""Performance metrics from a journal directory (works for backtest, shadow and live runs)."""

from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open() as f:
        return list(csv.DictReader(f))


def compute_metrics(run_dir: Path, capital: float) -> dict:
    trades = _read_csv(run_dir / "trades.csv")
    equity = [float(r["equity_tmn"]) for r in _read_csv(run_dir / "equity.csv")]
    events = [json.loads(l) for l in (run_dir / "decisions.jsonl").read_text().splitlines() if l.strip()] \
        if (run_dir / "decisions.jsonl").exists() else []

    final = equity[-1] if equity else capital
    peak, max_dd = capital, 0.0
    for e in equity:
        peak = max(peak, e)
        max_dd = max(max_dd, 1 - e / peak)
    rets = [b / a - 1 for a, b in zip(equity, equity[1:]) if a]
    sharpe = None
    if len(rets) > 10:
        mu = sum(rets) / len(rets)
        sd = math.sqrt(sum((x - mu) ** 2 for x in rets) / (len(rets) - 1))
        sharpe = round(mu / sd * math.sqrt(365 * 288), 2) if sd else None   # 5-minute marks

    pnls = [float(t["pnl_tmn"]) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    by_setup: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        by_setup[t["setup"]].append(float(t["pnl_pct"]))
    decisions = [e for e in events if e["event"] == "decision"]
    return {
        "initial_capital_tmn": capital,
        "final_equity_tmn": round(final),
        "net_return_pct": round(100 * (final / capital - 1), 3),
        "max_drawdown_pct": round(100 * max_dd, 3),
        "sharpe_annualized": sharpe,
        "trades": len(trades),
        "win_rate_pct": round(100 * len(wins) / len(trades), 1) if trades else None,
        "avg_trade_pnl_pct": round(sum(float(t["pnl_pct"]) for t in trades) / len(trades), 3) if trades else None,
        "profit_factor": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
        "traded_volume_tmn": round(sum(float(t["cost_tmn"]) + float(t["proceeds_tmn"]) for t in trades)),
        "avg_trade_size_tmn": round(sum(float(t["cost_tmn"]) for t in trades) / len(trades)) if trades else None,
        "by_setup": {k: {"trades": len(v), "avg_pnl_pct": round(sum(v) / len(v), 3)} for k, v in by_setup.items()},
        "exit_reasons": dict(Counter(t["exit_reason"] for t in trades)),
        "decisions": len(decisions),
        "decisions_taken": sum(1 for d in decisions if d["action"] == "TAKE"),
        "decision_sources": dict(Counter(d["source"] for d in decisions)),
        "risk_blocks": sum(1 for e in events if e["event"] == "risk_block"),
        "errors": sum(1 for e in events if e["event"] == "error"),
        "circuit_breakers": sum(1 for e in events if e["event"] == "circuit_breaker"),
    }


def render_markdown(title: str, m: dict, extra: str = "") -> str:
    rows = "\n".join(f"| {k} | {v} |" for k, v in m.items() if not isinstance(v, dict))
    nested = "\n".join(f"\n**{k}**\n\n```json\n{json.dumps(v, indent=1, ensure_ascii=False)}\n```" for k, v in m.items()
                       if isinstance(v, dict) and v)
    return f"# {title}\n\n| metric | value |\n|---|---|\n{rows}\n{nested}\n{extra}\n"

"""Decision log (a challenge deliverable): every signal, AI decision, risk check, order and
exit is appended as one JSON line with its reasons, plus trades.csv and equity.csv."""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path


class Journal:
    TRADE_FIELDS = ["trade_id", "symbol", "setup", "entry_time", "entry_price", "qty", "cost_tmn",
                    "exit_time", "exit_price", "proceeds_tmn", "pnl_tmn", "pnl_pct", "exit_reason",
                    "ai_confidence", "entry_reasoning"]

    def __init__(self, directory: Path, echo: bool = False):
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self.decisions = self.dir / "decisions.jsonl"
        self.trades = self.dir / "trades.csv"
        self.equity = self.dir / "equity.csv"
        self.echo = echo
        if not self.trades.exists():
            with self.trades.open("w", newline="") as f:
                csv.writer(f).writerow(self.TRADE_FIELDS)
        if not self.equity.exists():
            self.equity.write_text("time,equity_tmn,cash_tmn,exposure_tmn,drawdown_pct\n")

    def log(self, event: str, now: float, **data) -> None:
        rec = {"ts": round(now, 3), "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
               "event": event, **data}
        with self.decisions.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        if self.echo:
            brief = {k: v for k, v in data.items() if k in ("symbol", "action", "setup", "reason", "pnl_pct",
                                                            "regime", "equity", "error", "source")}
            print(f"[{rec['time']}] {event} {json.dumps(brief, ensure_ascii=False, default=str)}", flush=True)

    def trade(self, row: dict) -> None:
        with self.trades.open("a", newline="") as f:
            csv.DictWriter(f, self.TRADE_FIELDS, extrasaction="ignore").writerow(row)

    def mark(self, now: float, equity: float, cash: float, exposure: float, dd: float) -> None:
        with self.equity.open("a") as f:
            f.write(f"{int(now)},{equity:.0f},{cash:.0f},{exposure:.0f},{100 * dd:.3f}\n")

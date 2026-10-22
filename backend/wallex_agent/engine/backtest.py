"""Backtesting: replay recorded Wallex history through the exact live Engine code.

Fills: market orders at the next 1-minute price +- half the spread (snapshot of current
spreads), plus slippage and the configured fee. Stops/targets are checked against each
1-minute bar's low/high (stop first when both are hit: conservative).

Counterfactual: every candidate the decider saw is also simulated with its default
stop/target, so the report shows how the trades the AI took compare with the ones it
rejected; this measures what the AI adds over the rules alone.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from ..brokers.base import safe_float
from ..config import ROOT, Settings
from ..market.sentiment import global_fear_greed
from ..wallex import WallexClient
from .core import Engine
from .decider import AIDecider, ResponseCache, RuleDecider
from .execution import SimExec
from .feeds import LiveFeed, SimFeed, load_history
from .journal import Journal
from .metrics import compute_metrics, render_markdown


def make_decider(s: Settings, kind: str, offline: bool = False):
    if kind == "rules":
        return RuleDecider(s.engine_min_score)
    from openai import AsyncOpenAI
    llm = AsyncOpenAI(base_url=s.engine_llm_base_url or s.llm_base_url,
                      api_key=s.engine_llm_api_key or s.llm_api_key or "missing")
    extra = json.loads(s.engine_llm_extra_body) if s.engine_llm_extra_body.strip() else None
    return AIDecider(llm, s.engine_llm_model or s.llm_model, ResponseCache(s.data_dir / "ai_cache.jsonl"),
                     s.engine_fee_rate, offline=offline, extra_body=extra, quote=s.engine_quote)


def counterfactual(feed: SimFeed, run_dir: Path, fee: float, horizon_s: int) -> dict:
    """Outcome of every candidate with its default stop/target, grouped by the decider's action."""
    groups: dict[str, list[float]] = {"TAKE": [], "SKIP": []}
    for line in (run_dir / "decisions.jsonl").read_text().splitlines():
        e = json.loads(line)
        if e["event"] != "decision":
            continue
        c = e["candidate"]
        half = feed.spreads.get(c["symbol"], 0.002) / 2
        entry = c["price"] * (1 + half)
        stop, target = entry - c["stop_atr"] * c["atr"], entry + c["target_atr"] * c["atr"]
        path = feed.future_1m(c["symbol"], e["ts"], e["ts"] + horizon_s)
        exit_px = path[-1].c if path else entry
        for b in path:
            if b.l <= stop:
                exit_px = stop
                break
            if b.h >= target:
                exit_px = target
                break
        groups[e["action"]].append(100 * (exit_px * (1 - half) / entry * (1 - fee) ** 2 - 1))

    def stats(v):
        return {"n": len(v), "avg_pnl_pct": round(sum(v) / len(v), 3) if v else None,
                "win_rate_pct": round(100 * sum(1 for x in v if x > 0) / len(v), 1) if v else None}
    return {k: stats(v) for k, v in groups.items()}


async def run_backtest(s: Settings, days: float, end: float | None = None, kinds: tuple[str, ...] = ("rules", "ai"),
                       symbols: list[str] | None = None, offline: bool = False, out_root: Path | None = None) -> dict:
    client = WallexClient(s.wallex_base_url, s.wallex_api_key, sub_account=s.wallex_sub_account_client_id)
    end = end or (time.time() // 3600) * 3600
    start = end - days * 86_400
    try:
        if not symbols:
            symbols = await LiveFeed(client, s).universe()
        if s.engine_quote == "USDT" and "USDTTMN" not in symbols:   # values the USDT core in TMN
            symbols = [*symbols, "USDTTMN"]
        # Historical spreads don't exist, so a snapshot stands in for them. It is saved per window
        # end and reused, otherwise reruns would silently use different spreads (and results).
        spread_file = ROOT / "data" / "history" / f"spreads_{int(end)}.json"
        spreads = json.loads(spread_file.read_text()) if spread_file.exists() else {}
        missing = [sym for sym in symbols if sym not in spreads]
        if missing:
            markets = await client.markets()
            for sym in missing:
                st = markets.get(sym, {}).get("stats", {})
                bid, ask = safe_float(st.get("bidPrice")), safe_float(st.get("askPrice"))
                spreads[sym] = max((ask - bid) / ((ask + bid) / 2), 0.0005) if bid and ask else 0.003
            spread_file.parent.mkdir(parents=True, exist_ok=True)
            spread_file.write_text(json.dumps(spreads, indent=1))
        spreads = {sym: spreads[sym] for sym in symbols}
        m1, h1 = await load_history(client, symbols, int(start), int(end), ROOT / "data" / "history",
                                    tf_minutes=s.engine_timeframe_min)
        try:
            fg = await global_fear_greed(int(days) + 10)
            fng = {h["date"]: h["value"] for h in fg["history"]}
        except Exception:
            fng = {}
    finally:
        await client.aclose()

    stamp = datetime.fromtimestamp(end, timezone.utc).strftime("%Y%m%d-%H%M")
    out_root = out_root or (ROOT / "data" / "backtests")
    results = {}
    for kind in kinds:
        run_dir = out_root / f"{stamp}-{int(days)}d-{kind}"
        if run_dir.exists():
            for f in run_dir.iterdir():
                f.unlink()
        feed = SimFeed({k: v for k, v in m1.items() if v}, h1, s.engine_timeframe_min, spreads, fng)
        broker = SimExec(feed, s.engine_capital_tmn, s.engine_fee_rate, maker_fee_rate=s.maker_fee,
                         quote=s.engine_quote, tmn_fee_rate=s.engine_core_fee_rate)
        settings = s.model_copy(update={"engine_end_time": datetime.fromtimestamp(end, timezone.utc).isoformat()})
        decider = make_decider(settings, kind, offline)
        journal = Journal(run_dir)
        engine = Engine(settings, feed, broker, decider, journal)
        journal.log("start", start, mode="backtest", decider=kind, symbols=symbols, days=days,
                    execution=s.engine_execution, taker_fee=s.engine_fee_rate, maker_fee=s.maker_fee,
                    window=[datetime.fromtimestamp(start, timezone.utc).isoformat(),
                            datetime.fromtimestamp(end, timezone.utc).isoformat()], spreads=spreads)
        t, step, bar = int(start), 60, s.engine_timeframe_min * 60
        while t <= end:
            feed.t = t
            await engine.monitor()
            if t % bar == 0:
                await engine.on_bar()
            t += step
        await engine.flatten(end, "backtest_end")
        eq, cash, exp = await engine.equity()
        journal.mark(end, eq, cash, exp, engine.risk.drawdown(eq, engine.state["peak"]))
        m = compute_metrics(run_dir, s.engine_capital_tmn)
        usdt_tmn = (await feed.quote("USDTTMN"))["bid"] if "USDTTMN" in feed.m1 else 0.0
        m["fees_paid_tmn"] = round(broker.fees_by.get("TMN", 0.0) + broker.fees_by.get("USDT", 0.0) * usdt_tmn)
        m["strategy"] = {"quote": s.engine_quote, "core": bool(engine.core), "core_state": engine.state.get("core")}
        m["llm_calls"] = getattr(decider, "calls", 0)
        cf = counterfactual(feed, run_dir, s.engine_fee_rate, s.engine_time_stop_bars * bar)
        m["counterfactual_by_action"] = cf
        bh = {}
        for sym, bars in feed.m1.items():
            if bars:
                first = next((b for b in bars if b.t >= start), bars[0])
                bh[sym] = round(100 * (bars[-1].c / first.o - 1), 2)
        m["buy_and_hold_pct"] = bh
        (run_dir / "metrics.json").write_text(json.dumps(m, indent=1, ensure_ascii=False))
        (run_dir / "report.md").write_text(render_markdown(f"Backtest {kind} - {days:g} days to {stamp} UTC", m))
        results[kind] = {"dir": str(run_dir), "symbols": symbols,
                         "window_end": datetime.fromtimestamp(end, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **m}
    return results

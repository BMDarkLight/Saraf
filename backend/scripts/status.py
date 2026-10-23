"""Read-only status report of the deployed engine, for the Status workflow (.github/workflows/status.yml).

Run on the VPS from /opt/wallex-agent/backend:  uv run --no-dev python <path>/status.py [--events 60]
Prints: which runs exist and how they are configured, state (positions, orders, core, regime), equity and return,
event counts since the last start, the newest events and trades, and the market facts sizing depends on (USDT/TMN,
USDT-market minimum order, the account's fees and balances). No secrets are printed and nothing is changed.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import time
from collections import Counter
from pathlib import Path

from wallex_agent.brokers import LiveSpotBroker
from wallex_agent.brokers.base import safe_float
from wallex_agent.config import ROOT, get_settings
from wallex_agent.wallex import WallexClient

SHOW = ("engine_quote", "engine_decider", "engine_end_time", "engine_fee_rate", "engine_maker_fee_rate",
        "engine_max_exposure_pct", "engine_max_position_pct", "engine_max_positions", "engine_risk_per_trade_pct",
        "engine_breadth_gate_pct", "engine_core_enabled", "engine_setups", "engine_universe", "engine_execution",
        "engine_ai_reviews", "llm_model", "engine_llm_model")


def tail_lines(path: Path, nbytes: int = 3_000_000) -> list[str]:
    with path.open("rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - nbytes))
        return f.read().decode(errors="ignore").splitlines()[1:]


def brief(e: dict) -> str:
    keep = {k: e[k] for k in ("symbol", "setup", "action", "source", "regime", "reason", "error", "pnl_pct", "price",
                              "amount_tmn", "limit", "qty", "status") if e.get(k) not in (None, "")}
    if e.get("decision"):
        keep["ai"] = {k: e["decision"].get(k) for k in ("confidence", "size_fraction", "reasoning")}
    return f"{e['time']} {e['event']:<15} {json.dumps(keep, ensure_ascii=False, default=str)[:400]}"


def report_run(d: Path, n_events: int) -> None:
    print(f"\n================ run: {d.name} ================")
    state = json.loads((d / "state.json").read_text()) if (d / "state.json").exists() else {}
    events = [json.loads(l) for l in tail_lines(d / "decisions.jsonl") if l.strip().startswith("{")] \
        if (d / "decisions.jsonl").exists() else []
    starts = [e for e in events if e["event"] == "start"]
    if starts:
        s = starts[-1]
        cfg = s.get("config", {})
        print(f"last start {s['time']} mode={s.get('mode')} commit={s.get('commit')}")
        print("config:", json.dumps({k.removeprefix("engine_"): cfg.get(k) for k in SHOW if k in cfg}, ensure_ascii=False))
        since = [e for e in events if e["ts"] >= s["ts"]]
    else:
        since = events
    print("events since last start:", dict(Counter(e["event"] for e in since)))
    blocks = Counter((e.get("reason") or "")[:90] for e in since if e["event"] == "risk_block")
    if blocks:
        print("risk blocks by reason:", json.dumps(blocks.most_common(8), ensure_ascii=False))
    errs = Counter((e.get("error") or "")[:120] for e in since if e["event"] == "error")
    if errs:
        print("errors:", json.dumps(errs.most_common(8), ensure_ascii=False))
    srcs = Counter(e.get("source") for e in since if e["event"] == "decision")
    if srcs:
        print("decision sources:", dict(srcs))
    print(f"state: regime={state.get('regime')} halted={state.get('halted')} flattened={state.get('flattened')} "
          f"pause_until={state.get('pause_until')} peak={state.get('peak')}")
    print("regime reason:", state.get("regime_reason"))
    print("core:", json.dumps(state.get("core"), ensure_ascii=False))
    for p in state.get("positions", {}).values():
        print("position:", json.dumps({k: p.get(k) for k in ("id", "symbol", "setup", "qty", "entry_price", "stop", "target",
                                                             "cost_tmn", "entry_time", "tp_order")}, default=str))
    for o in state.get("pending", {}).values():
        print("pending:", json.dumps({k: o.get(k) for k in ("symbol", "limit", "amount_tmn", "placed_at", "expires_at")}))
    if (d / "equity.csv").exists():
        rows = (d / "equity.csv").read_text().strip().splitlines()[1:]
        if rows:
            print("equity marks (time, equity, cash, exposure, dd%): first", rows[0], "| last", rows[-1],
                  f"| n={len(rows)}")
            hourly = rows[-1::-12][:12][::-1]
            print("last hours:", " ".join(r.split(",")[1] for r in hourly))
    if (d / "trades.csv").exists():
        with (d / "trades.csv").open() as f:
            trades = list(csv.DictReader(f))
        pnl = sum(float(t["pnl_tmn"] or 0) for t in trades)
        print(f"trades: {len(trades)} realized pnl {pnl:,.0f} TMN")
        for t in trades[-10:]:
            print("  ", {k: t[k] for k in ("trade_id", "symbol", "setup", "entry_time", "exit_time", "cost_tmn", "pnl_tmn",
                                          "pnl_pct", "exit_reason")})
    key = [e for e in events if e["event"] in ("start", "core", "entry", "entry_order", "entry_cancelled", "exit",
                                               "error", "circuit_breaker", "reconcile", "stop")]
    print(f"--- key events in the journal tail: {dict(Counter(e['event'] for e in key))}; newest 40 ---")
    for e in key[-40:]:
        print(brief(e))
    print(f"--- newest {n_events} events (oldest first) ---")
    for e in events[-n_events:]:
        print(brief(e))


async def market_facts() -> None:
    s = get_settings()
    c = WallexClient(s.wallex_base_url, s.wallex_api_key, sub_account=s.wallex_sub_account_client_id)
    try:
        m = await c.markets()
        u = m.get("USDTTMN", {})
        print("\n================ market ================")
        print("USDTTMN stats:", {k: u.get("stats", {}).get(k) for k in ("bidPrice", "askPrice", "lastPrice", "24h_ch")},
              "minNotional", u.get("minNotional"))
        usdt = [(safe_float(v.get("stats", {}).get("24h_quoteVolume")), k, v.get("minNotional"))
                for k, v in m.items() if v.get("quoteAsset") == "USDT"]
        print("top USDT markets (volume, symbol, minNotional):", sorted(usdt, reverse=True)[:8])
        ch = [safe_float(v.get("stats", {}).get("24h_ch")) for v in m.values() if v.get("quoteAsset") == "USDT"
              and safe_float(v.get("stats", {}).get("24h_quoteVolume")) > 0]
        if ch:
            print(f"USDT-market breadth: {100 * sum(x > 0 for x in ch) / len(ch):.1f}% of {len(ch)} up over 24h")
        if s.wallex_api_key:
            try:
                fees = await c.fees()
                print("account fees:", json.dumps(fees, ensure_ascii=False)[:800])
            except Exception as e:
                print("fees error:", e)
            try:
                bal = await LiveSpotBroker(c).balances()
                print("live balances (free, locked):", {a: (round(b["free"], 8), round(b["locked"], 8)) for a, b in bal.items()})
                print("open orders:", json.dumps([{k: o.get(k) for k in ("symbol", "side", "type", "price", "origQty",
                                                                        "executedQty", "status", "clientOrderId")}
                                                  for o in await c.open_orders()], default=str)[:2000])
            except Exception as e:
                print("balances error:", e)
            # why a market sell placed as a limit through the book can come back unfilled: the book as the API
            # returns it, and the account's latest fills on the markets the engine sold on
            for sym in ("BTCUSDT", "USOONUSDT"):
                try:
                    book = await c.depth(sym)
                    print(f"{sym} depth keys {sorted(book)[:6]} bid top3 {book.get('bid', [])[:3]} ask top3 {book.get('ask', [])[:3]}")
                except Exception as e:
                    print(f"{sym} depth error:", e)
            for sym in ("BTCUSDT", "BTCTMN", "USOONUSDT"):
                try:
                    fills = await c.my_trades(sym)
                    print(f"{sym} latest fills:", json.dumps(fills[:4], default=str, ensure_ascii=False)[:900])
                except Exception as e:
                    print(f"{sym} fills error:", e)
    finally:
        await c.aclose()


async def signal_scan() -> None:
    """Why the satellite does (not) buy: for each market it watches, the live 15m/1h features next to the setups'
    entry conditions (strategy.py), with the strategy profile's settings."""
    from wallex_agent.engine import strategy
    from wallex_agent.engine.features import compute
    from wallex_agent.engine.feeds import LiveFeed
    s = get_settings()
    prof = {}
    for line in (ROOT / "deploy" / "strategy.env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            prof[k.strip().lower()] = v.strip()
    s = s.model_copy(update={k: type(getattr(s, k))(v) if not isinstance(getattr(s, k), bool) else v == "true"
                             for k, v in prof.items() if hasattr(s, k)})
    c = WallexClient(s.wallex_base_url, s.wallex_api_key, sub_account=s.wallex_sub_account_client_id)
    feed = LiveFeed(c, s)
    print("\n================ SIGNAL SCAN (strategy profile) ================")
    print(f"filters: ATR% {s.engine_min_atr_pct}..{s.engine_max_atr_pct}, min score {s.engine_min_score}, setups {s.engine_setups}")
    try:
        for sym in await feed.universe():
            f = compute(*(await feed.bars(sym)))
            if not f:
                print(f"{sym:<10} no features (not enough bars)")
                continue
            fails = []
            if f["atr_pct"] < s.engine_min_atr_pct:
                fails.append(f"ATR {f['atr_pct']:.2f}% < {s.engine_min_atr_pct}")
            if not f["close"] < f["bb_lower"]:
                fails.append("not below lower BB")
            if (f["rsi"] or 50) >= 30:
                fails.append(f"RSI {f['rsi']:.0f} >= 30")
            if f["htf_trend"] != "up":
                fails.append(f"1h trend {f['htf_trend']}")
            if f["ret_4"] > -1.5:
                fails.append(f"1h move {f['ret_4']:+.2f}% (dip needs < -1.5%)")
            cands = strategy.candidates(sym, f, s.engine_min_atr_pct, s.engine_max_atr_pct,
                                        tuple(x for x in s.engine_setups.split(",") if x))
            hit = ", ".join(f"{x.setup}({x.score})" for x in cands) or "none"
            print(f"{sym:<10} close {f['close']:<12.6g} ATR {f['atr_pct']:.2f}% RSI {f['rsi']:.0f} 1h {f['htf_trend']:<5} "
                  f"4bar {f['ret_4']:+.2f}% | candidates: {hit} | blocks: {'; '.join(fails)}")
    finally:
        await c.aclose()


def report_trader(d: Path) -> None:
    """A followed real trader (wallex_agent/traders.py): their book and the simulated copy."""
    print(f"\n================ trader: {d.name} ================")
    prof = json.loads((d / "trader.json").read_text()) if (d / "trader.json").exists() else {}
    state = json.loads((d / "state.json").read_text()) if (d / "state.json").exists() else {}
    print("name:", prof.get("name"), "source:", prof.get("source"), "account $", round(prof.get("account_usd") or 0),
          "30d pnl $", round((prof.get("board") or {}).get("pnl_month") or 0))
    print("book:", [(p["coin"], p["side"], round(p["usd"])) for p in prof.get("positions", [])[:8]])
    print("copy weights:", {k: round(v, 3) for k, v in (prof.get("copy_weights") or {}).items()},
          "copy holdings:", state.get("holdings"), "cash usdt:", round(state.get("cash_usdt") or 0, 4))
    for l in (tail_lines(d / "decisions.jsonl", 20_000) if (d / "decisions.jsonl").exists() else [])[-6:]:
        print("  ", l[:220])


def summary(eng: Path) -> None:
    """A short block at the end of the log: what each check reads first."""
    print("\n================ SUMMARY ================")
    for d in sorted(eng.iterdir()) if eng.exists() else []:
        if not (d.is_dir() and (d / "equity.csv").exists()) or d.name == "shadow":
            continue
        rows = (d / "equity.csv").read_text().strip().splitlines()[1:]
        last = rows[-1].split(",") if rows else None
        hour = rows[-13].split(",") if len(rows) >= 13 else (rows[0].split(",") if rows else None)
        n_tr = max(0, len((d / "trades.csv").read_text().strip().splitlines()) - 1) if (d / "trades.csv").exists() else 0
        evs = [json.loads(l) for l in tail_lines(d / "decisions.jsonl", 400_000) if l.strip().startswith("{")] \
            if (d / "decisions.jsonl").exists() else []
        recent = [e for e in evs if e["ts"] > time.time() - 3600 and e["event"] not in ("start",)]
        state = json.loads((d / "state.json").read_text()) if (d / "state.json").exists() else {}
        if last:
            eq = float(last[1])
            print(f"{d.name:<20} equity {eq:>12,.0f} ({100 * (eq / 5_000_000 - 1):+.2f}%) 1h {float(last[1]) - float(hour[1]):+,.0f} "
                  f"dd {last[4]}% trades {n_tr} open {len(state.get('positions') or state.get('holdings') or {})} "
                  f"pending {len(state.get('pending') or {})} "
                  f"regime {state.get('regime')} core {(state.get('core') or {}).get('status')} "
                  f"last-hour {dict(Counter(e['event'] for e in recent))}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=int, default=60)
    a = ap.parse_args()
    eng = ROOT / "data" / "engine"
    print("now", time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()))
    for name in ("mode.env", "profile.env", "strategy-live-at.json"):
        p = eng / name
        print(f"{name}: {'present' if p.exists() else 'absent'}", (p.read_text().strip()[:300] if p.exists() and name != "profile.env" else ""))
    print("ENGINE_END_TIME seen by this process:", os.environ.get("ENGINE_END_TIME") or get_settings().engine_end_time)
    for d in sorted(eng.iterdir()) if eng.exists() else []:
        if d.is_dir() and d.name.startswith("trader-"):
            report_trader(d)
        elif d.is_dir() and (d / "state.json").exists():
            try:
                report_run(d, a.events)
            except Exception as e:   # one odd journal must not hide the rest of the report
                print(f"report of {d.name} failed: {type(e).__name__}: {e}")
    try:
        asyncio.run(market_facts())
    except Exception as e:
        print("market facts unavailable:", type(e).__name__, e)
    try:
        asyncio.run(signal_scan())
    except Exception as e:
        print("signal scan unavailable:", type(e).__name__, e)
    summary(eng)


if __name__ == "__main__":
    main()

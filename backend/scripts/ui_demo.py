"""Run the web app on fake but realistic data, for UI work without a venue's API (and for screenshots).

  uv run python scripts/ui_demo.py            # http://127.0.0.1:8000
Writes sample engine journals under data/engine (git-ignored) and swaps the default venue (Wallex-shaped data), its
real-account broker and the sentiment feed with deterministic fakes. Never use it on the VPS: it overwrites data/engine.
"""

from __future__ import annotations

import json
import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from conftest import FakeClient  # noqa: E402

PRICES = {"BTCUSDT": 110_250.0, "ETHUSDT": 3_910.0, "SOLUSDT": 186.4, "XRPUSDT": 2.41, "DOGEUSDT": 0.198,
          "PEPEUSDT": 0.0000101, "BNBUSDT": 1_084.0, "USOONUSDT": 164.2, "USDTTMN": 272_900.0,
          "BTCTMN": 30_100_000_000.0, "ETHTMN": 1_067_000_000.0, "USOONTMN": 44_800_000.0}


class DemoClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.prices = dict(PRICES)
        self.has_key = True

    def live_spot_broker(self):
        return DemoAccountBroker(self)

    async def markets(self):
        out = await super().markets()
        rnd = random.Random(3)
        for sym, m in out.items():
            m["stats"]["24h_ch"] = round(rnd.uniform(-5, 5), 2)
            m["faBaseAsset"] = {"BTC": "بیت‌کوین", "ETH": "اتریوم", "SOL": "سولانا", "XRP": "ریپل", "DOGE": "دوج‌کوین",
                                "PEPE": "پپه", "BNB": "بایننس کوین", "USOON": "نفت توکنیزه", "USDT": "تتر"}.get(
                m["baseAsset"], m["baseAsset"])
        return out

    async def candles(self, symbol, resolution, start, end=None):
        p = self.prices[symbol]
        step = {"1": 60, "60": 3600, "180": 10800, "360": 21600, "720": 43200, "1D": 86400}[resolution]
        end = end or start + 200 * step
        n = min(int((end - start) // step), 500)
        rnd, c, out = random.Random(symbol), p * 0.96, []
        for i in range(n):
            o = c
            c = o * (1 + rnd.gauss(0.0002, 0.006) + 0.002 * math.sin(i / 9))
            out.append({"t": start + i * step, "o": o, "h": max(o, c) * 1.003, "l": min(o, c) * 0.997, "c": c,
                        "v": rnd.uniform(5, 30)})
        return out


class DemoAccountBroker:
    def __init__(self, client):
        pass

    async def balances(self):
        return {"TMN": {"free": 37_283.0, "locked": 0.0, "total": 37_283.0},
                "USDT": {"free": 3.666834, "locked": 0.0, "total": 3.666834},
                "USOON": {"free": 0.08818521, "locked": 0.0, "total": 0.08818521}}


def write_journals(root: Path) -> None:
    from saraf.personas import PERSONAS, run_name
    now = int(time.time())
    cfg = {"engine_quote": "USDT", "engine_decider": "ai", "engine_end_time": "2026-10-26T20:29:00Z",
           "engine_max_exposure_pct": 0.3, "engine_max_position_pct": 0.125, "engine_max_positions": 3,
           "engine_risk_per_trade_pct": 0.01, "engine_breadth_gate_pct": 25.0, "engine_drawdown_pause_pct": 0.06,
           "engine_drawdown_stop_pct": 0.12, "engine_core_guard_pct": 0.06, "engine_core_exit_hours": 6,
           "engine_core_exit_slices": 3, "engine_flatten_before_end_min": 45, "engine_no_entry_before_end_min": 240,
           "engine_setups": "oversold_reversion,dip_in_uptrend", "engine_fee_rate": 0.0013}
    hdr = ("trade_id,symbol,setup,entry_time,entry_price,qty,cost_tmn,exit_time,exit_price,proceeds_tmn,pnl_tmn,"
           "pnl_pct,exit_reason,ai_confidence,entry_reasoning\n")

    def run(name, drift, n, trades=(), positions=None):
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        rnd, eq, rows = random.Random(name), 5_000_000.0, ["time,equity_tmn,cash_tmn,exposure_tmn,drawdown_pct"]
        for i in range(n):
            eq *= 1 + drift + rnd.gauss(0, 0.0007)
            rows.append(f"{now - (n - i) * 300},{eq:.0f},{eq * 0.2:.0f},{eq * 0.79:.0f},0.4")
        (d / "equity.csv").write_text("\n".join(rows) + "\n")
        t = lambda s: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - s))  # noqa: E731
        ev = [{"ts": now - n * 300, "time": t(n * 300), "event": "start", "mode": "live" if name == "live" else "shadow",
               "config": cfg},
              {"ts": now - 7200, "time": t(7200), "event": "regime", "regime": "neutral", "source": "llm",
               "reason": "عرضه و تقاضای بازار متعادل است؛ ۴۶٪ بازارهای تتری در ۲۴ ساعت مثبت‌اند و ریزش فراگیری دیده نمی‌شود."},
              {"ts": now - 3500, "time": t(3500), "event": "decision", "symbol": "ETHUSDT", "setup": "dip_in_uptrend",
               "action": "SKIP", "source": "llm", "decision": {"reasoning": "روند ساعتی ضعیف شده و دفتر سفارش سمت فروش سنگین است."}},
              {"ts": now - 1800, "time": t(1800), "event": "entry", "symbol": "SOLUSDT", "setup": "oversold_reversion",
               "reason": "RSI زیر ۳۰ و قیمت زیر باند پایین بولینگر در روند ساعتی خنثی."},
              {"ts": now - 600, "time": t(600), "event": "core", "action": "BUY_USDT", "reason": "core: hold USDT"}]
        if name == "live":   # what the live journal showed on 2026-10-23: refused market orders, then the fix
            refused = 'Wallex: اطلاعات وارد شده اشتباه‌است.: {"symbol": ["امکان ثبت سفارش قیمت بازار در USOONUSDT بازار وجود ندارد."]}'
            for k in range(6):
                ev += [{"ts": now - 400 + 15 * k, "time": t(400 - 15 * k), "event": "error", "symbol": s,
                        "error": f"consolidate sell failed: {m}"}
                       for s, m in (("BTCUSDT", refused.replace("USOONUSDT", "سکو")), ("USOONUSDT", refused))]
            ev += [{"ts": now - 300, "time": t(300), "event": "error", "symbol": "BTCUSDT",
                    "error": "consolidate sell failed: sell BTCUSDT did not fill: CANCELED"},
                   {"ts": now - 300, "time": t(300), "event": "sweep", "symbol": "BTCTMN",
                    "reason": "consolidate: BTC bought outside the engine sold into TMN on request, for the USDT core"},
                   {"ts": now - 300, "time": t(300), "event": "sweep", "symbol": "USOONUSDT",
                    "reason": "consolidate: USOON bought outside the engine sold into USDT on request, for the USDT core"}]
        (d / "decisions.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in ev) + "\n")
        (d / "trades.csv").write_text(hdr + "".join(
            f"T{i},{s},dip_in_uptrend,a,1,1,600000,2026-10-23 0{i}:00:00,1,1,{p * 6000:.0f},{p},{r},,x\n"
            for i, (s, p, r) in enumerate(trades)))
        (d / "state.json").write_text(json.dumps({
            "positions": positions or {}, "pending": {}, "regime": "neutral", "halted": False,
            "regime_reason": "عرضه و تقاضای بازار متعادل است؛ ۴۶٪ بازارهای تتری مثبت‌اند.",
            "core": {"status": "holding", "entry_rate": 273557.8, "slices_done": 0}, "peak": 5010000}))

    pos = {"T0007": {"id": "T0007", "symbol": "SOLUSDT", "setup": "oversold_reversion", "qty": 0.012, "entry_price": 184.1,
                     "stop": 179.8, "target": 190.2, "quote": "USDT", "cost_tmn": 600_000}}
    run("live", 0.00001, 260, [("BTCUSDT", 0.8, "take_profit"), ("PEPEUSDT", -1.2, "stop_loss")], pos)
    for i, p in enumerate(PERSONAS):
        run(run_name(p), [0.00003, 0.000035, -0.00001, -0.00002, 0.00002][i], 120 + 40 * i,
            [("XRPUSDT", 1.4, "take_profit"), ("DOGEUSDT", -0.9, "stop_loss")][: i % 3])
    # live trader personas (saraf/traders.py), shaped like the real Hyperliquid data seen 2026-10-23
    from saraf import traders
    whales = [("0x5b5d51203a0f9079f8aeb098a6523a13f298c060", None, 261.6e6, (4.09e6, 18.1e6, 109e6, 173.8e6),
               [("ETH", "short", 247e6), ("BTC", "short", 201e6), ("SOL", "short", 68e6), ("LINK", "short", 4.2e6)], 0.0),
              ("0x7fdafde5cfb5465924316eced2d3715494c517d1", "BobbyBigSize", 48.3e6, (-90e3, 2.9e6, 20.3e6, 140.1e6),
               [("BTC", "long", 22e6), ("kPEPE", "long", 3.1e6), ("HYPE", "long", 9e6)], 0.000025),
              ("0xb83de012dba672c76a7dbbbf3e459cb59d7d6e36", None, 240.8e6, (3.1e6, 17.1e6, 99.3e6, 133.6e6),
               [("SOL", "long", 40e6), ("ETH", "short", 30e6)], 0.000012)]
    sel = []
    for addr, name, acct, (d, w, m, a), book, drift in whales:
        sel.append({"address": addr, "name": name, "account_usd": acct, "pnl_day": d, "pnl_week": w, "pnl_month": m,
                    "pnl_allTime": a, "roi_month": m / acct})
        run(traders.run_name(addr), drift, 60)
        tdir = root / traders.run_name(addr)
        positions = [{"coin": c, "side": sd, "size": u / 1000, "usd": u, "entry": 1.0, "upnl": u * 0.02 * (1 if sd == "short" else -0.5),
                      "leverage": 10} for c, sd, u in book]
        weights, skipped = traders.target_weights({"account_usd": acct, "positions": positions}, set(PRICES) | {"PEPEUSDT"})
        (tdir / "trader.json").write_text(json.dumps({
            "address": addr, "name": name, "account_usd": acct, "positions": positions, "copy_weights": weights,
            "not_copied": skipped, "board": {"pnl_day": d, "pnl_week": w, "pnl_month": m, "pnl_allTime": a, "roi_month": m / acct},
            "fills": [{"coin": c, "dir": f"Open {'Long' if sd == 'long' else 'Short'}", "time": now - 900 * (i + 1), "usd": u / 20,
                       "closed_pnl": 0.0} for i, (c, sd, u) in enumerate(book)]
                     + [{"coin": "BTC", "dir": "Close Long", "time": now - 7200, "usd": 3.2e6, "closed_pnl": 412_000.0}]}))
        with (tdir / "decisions.jsonl").open("a") as f:
            for i, (c, sd, u) in enumerate(book):
                f.write(json.dumps({"ts": now - 600 * (i + 1), "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 600 * (i + 1))),
                                    "event": "trader", "coin": c, "symbol": f"{c}USDT", "action": "opened", "side": sd,
                                    "usd": u, "copied": sd == "long"}) + "\n")
    tr = root / "traders"
    tr.mkdir(exist_ok=True)
    (tr / "selection.json").write_text(json.dumps({"at": now, "traders": sel}))
    (root / "mode.env").write_text("ENGINE_RUN_ARGS=--mode live --confirm-live\n")
    (root / "profile.env").write_text("ENGINE_QUOTE=USDT\n")


def main() -> None:
    import uvicorn

    from saraf import server
    from saraf.agent import tools as agent_tools
    from saraf.market import sentiment

    write_journals(server.ROOT / "data" / "engine")
    client = DemoClient()
    server.ctx.venue = client
    server.ctx.venues._open[client.name] = client
    server.ctx.spot.venue = server.ctx.spot.info.venue = client
    server.ctx.futures.venue = server.ctx.futures.info.venue = client
    server.ctx.alerts.info.venue = client
    server.settings.llm_api_key = server.settings.llm_api_key or "demo"

    async def no_key_check():
        return None
    server.check_trading_key = no_key_check

    async def no_restart():   # saving settings would restart the systemd engine; the demo has none
        return None
    server._need_systemd = lambda: None
    server._restart_engine = no_restart

    async def scripted_ask(s, history, effective, engine, copying, allow_apply=False):
        """The strategy assistant without an LLM: «ریسک ...» halves the risk, anything else gets a proposal."""
        q = history[-1]["content"]
        if "ریسک" in q:
            ch = {"engine_risk_per_trade_pct": 0.005}
            return {"reply": "ریسک هر معامله نصف شد.", "proposal": {"changes": ch, "reasoning": "", "dropped": []},
                    "apply": allow_apply}
        ch = {"engine_breadth_gate_pct": 30.0}
        return {"reply": "پیشنهاد: دروازه فراگیری را ۳۰٪ کنید.", "proposal": {"changes": ch, "reasoning": "", "dropped": []},
                "apply": False}
    from saraf import strategy_chat
    strategy_chat.ask = scripted_ask

    async def fng(days=30):
        return {"value": 52, "classification": "Neutral", "history": [{"date": "2026-10-23", "value": 52}]}
    sentiment.global_fear_greed = fng
    uvicorn.run(server.app, host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    main()

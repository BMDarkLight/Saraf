"""Run the web app on fake but realistic data, for UI work without the Wallex API (and for screenshots).

  uv run python scripts/ui_demo.py            # http://127.0.0.1:8000
Writes sample engine journals under data/engine (git-ignored) and patches the Wallex client, the real-account
broker and the sentiment feed with deterministic fakes. Never use it on the VPS: it overwrites data/engine.
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
    from wallex_agent.personas import PERSONAS, run_name
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
        (d / "decisions.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in ev) + "\n")
        (d / "trades.csv").write_text(hdr + "".join(
            f"T{i},{s},dip_in_uptrend,a,1,1,600000,2026-10-23 0{i}:00:00,1,1,{p * 6000:.0f},{p},{r},,x\n"
            for i, (s, p, r) in enumerate(trades)))
        (d / "state.json").write_text(json.dumps({
            "positions": positions or {}, "pending": {}, "regime": "neutral", "halted": False,
            "regime_reason": "عرضه و تقاضای بازار متعادل است؛ ۴۶٪ بازارهای تتری مثبت‌اند.",
            "core": {"status": "holding", "entry_rate": 273557.8, "slices_done": 0}, "peak": 5010000}))

    pos = {"T0007": {"id": "T0007", "symbol": "SOLUSDT", "setup": "oversold_reversion", "qty": 3.2, "entry_price": 184.1,
                     "stop": 179.8, "target": 190.2, "quote": "USDT", "cost_tmn": 600_000}}
    run("live", 0.00001, 260, [("BTCUSDT", 0.8, "take_profit"), ("PEPEUSDT", -1.2, "stop_loss")], pos)
    for i, p in enumerate(PERSONAS):
        run(run_name(p), [0.00003, 0.000035, -0.00001, -0.00002, 0.00002][i], 120 + 40 * i,
            [("XRPUSDT", 1.4, "take_profit"), ("DOGEUSDT", -0.9, "stop_loss")][: i % 3])
    (root / "mode.env").write_text("ENGINE_RUN_ARGS=--mode live --confirm-live\n")
    (root / "profile.env").write_text("ENGINE_QUOTE=USDT\n")


def main() -> None:
    import uvicorn

    from wallex_agent import server
    from wallex_agent.agent import tools as agent_tools
    from wallex_agent.market import sentiment

    write_journals(server.ROOT / "data" / "engine")
    client = DemoClient()
    server.ctx.client = client
    server.ctx.spot.client = client
    server.ctx.futures.client = client
    server.LiveSpotBroker = DemoAccountBroker
    server.settings.wallex_api_key = "demo"
    server.settings.wallex_sub_account_client_id = "demo"
    server.settings.llm_api_key = server.settings.llm_api_key or "demo"

    async def no_key_check():
        return False
    server.check_trading_key = no_key_check

    async def fng(days=30):
        return {"value": 52, "classification": "Neutral", "history": [{"date": "2026-10-23", "value": 52}]}
    sentiment.global_fear_greed = fng
    uvicorn.run(server.app, host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    main()

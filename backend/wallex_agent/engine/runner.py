"""Long-running shadow/live loop with restart recovery."""

from __future__ import annotations

import asyncio
import os
import subprocess

from ..brokers import LiveSpotBroker, PaperSpotBroker
from ..config import ROOT, Settings
from ..wallex import WallexClient
from .backtest import make_decider
from .core import Engine
from .execution import SpotExec
from .feeds import LiveFeed
from .journal import Journal

SECRET_FIELDS = {"llm_api_key", "wallex_api_key", "tavily_api_key"}


def git_commit() -> str:
    if os.environ.get("GIT_COMMIT", "unknown") != "unknown":   # set at docker build time
        return os.environ["GIT_COMMIT"]
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


async def run_engine(s: Settings, mode: str, confirm_live: bool = False, max_loops: int | None = None) -> None:
    client = WallexClient(s.wallex_base_url, s.wallex_api_key)
    out = ROOT / "data" / "engine" / mode
    out.mkdir(parents=True, exist_ok=True)
    if mode == "live":
        if not (s.wallex_api_key and confirm_live):
            raise SystemExit("live mode needs WALLEX_API_KEY and the --confirm-live flag")
        spot = LiveSpotBroker(client)
    else:  # shadow: real-time data, fills simulated on the live order book, no real orders
        spot = PaperSpotBroker(client, s.model_copy(update={"data_dir": out, "paper_tmn": s.engine_capital_tmn,
                                                            "paper_usdt": 0.0}))
    journal = Journal(out, echo=True)
    engine = Engine(s, LiveFeed(client, s), SpotExec(spot), make_decider(s, s.engine_decider), journal,
                    state_path=out / "state.json")
    await engine.reconcile()
    snapshot = {k: v for k, v in s.model_dump(mode="json").items() if k.startswith(("engine_", "llm_")) and k not in SECRET_FIELDS}
    journal.log("start", engine.feed.now(), mode=mode, commit=git_commit(), config=snapshot)

    tf_s = s.engine_timeframe_min * 60
    last_bar = int(engine.feed.now() // tf_s)
    loops = 0
    try:
        while max_loops is None or loops < max_loops:
            loops += 1
            now = engine.feed.now()
            try:
                await engine.monitor()
                # wait ~20s after the boundary so the exchange has published the closed candle
                if int((now - 20) // tf_s) > last_bar:
                    last_bar = int((now - 20) // tf_s)
                    await engine.on_bar()
            except Exception as e:   # stay alive: log and retry next tick
                journal.log("error", now, error=f"{type(e).__name__}: {e}")
            if engine.state["flattened"] and not engine.positions:
                journal.log("stop", now, reason="challenge window over; all positions are in TMN")
                break
            await asyncio.sleep(s.engine_monitor_seconds)
    finally:
        engine.save()
        await client.aclose()

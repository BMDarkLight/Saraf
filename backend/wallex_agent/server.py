"""FastAPI server: SSE chat with the agent + dashboard data endpoints + static UI."""

from __future__ import annotations

import asyncio
import collections
import contextlib
import shutil
import csv
import json
import os
import time
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import tools as agent_tools
from .agent.context import AppContext
from .agent.loop import Agent
from .brokers import BrokerError, LiveSpotBroker, PaperFuturesBroker, PaperSpotBroker
from .config import ROOT, get_settings
from .market import analytics, sentiment
from .news import web
from .wallex import WallexError

settings = get_settings()
ENGINE_MODE_FILE = ROOT / "data" / "engine" / "mode.env"


def requested_engine_mode() -> str:
    """The engine's configured mode: the UI switch (data/engine/mode.env) or ENGINE_RUN_ARGS from .env."""
    try:
        args = ENGINE_MODE_FILE.read_text()
    except OSError:
        args = os.environ.get("ENGINE_RUN_ARGS", "")
    return "live" if "--mode live" in args else "shadow"


# One mode for the whole app: when the engine trades for real, so do the chat assistant's spot trades
settings.spot_mode = "live" if requested_engine_mode() == "live" and settings.wallex_api_key else "paper"
ctx = AppContext.build(settings)
agent = Agent(ctx)
futures_events: collections.deque = collections.deque(maxlen=50)


async def _ticker():
    while True:
        if isinstance(ctx.futures, PaperFuturesBroker):
            try:
                for ev in await ctx.futures.tick():
                    futures_events.appendleft({**ev, "seen_at": time.time()})
            except Exception:
                pass
        try:
            await ctx.alerts.check()
        except Exception:
            pass
        await asyncio.sleep(10)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_ticker())
    yield
    task.cancel()
    await ctx.client.aclose()


app = FastAPI(title="Wallex Agent", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def sse(gen):
    async def stream():
        try:
            async for ev in gen:
                yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def guarded(coro):
    try:
        return await coro
    except (WallexError, BrokerError) as e:
        raise HTTPException(502, str(e))


# ------------------------------------------------------------------- agent
class ChatIn(BaseModel):
    message: str
    session_id: str | None = None


@app.post("/api/chat")
async def chat(body: ChatIn):
    session = agent.session(body.session_id)

    async def gen():
        yield {"type": "session", "session_id": session.id}
        try:
            async for ev in agent.chat(session, body.message):
                yield ev
        finally:
            agent.save(session)
    return sse(gen())


class ActionIn(BaseModel):
    approve: bool


@app.post("/api/actions/{action_id}")
async def decide_action(action_id: str, body: ActionIn):
    try:
        action = await agent.execute_action(action_id, body.approve)
    except KeyError:
        raise HTTPException(404, "unknown action")

    async def gen():
        yield {"type": "action_update", "action": action.public()}
        try:
            async for ev in agent.after_action(action):
                yield ev
        finally:
            if action.session_id in agent.sessions:
                agent.save(agent.sessions[action.session_id])
    return sse(gen())


@app.get("/api/actions")
async def list_actions(session_id: str | None = None):
    return [a.public() for a in agent.pending.values() if not session_id or a.session_id == session_id]


@app.post("/api/sessions/{session_id}/reset")
async def reset_session(session_id: str):
    """Leaves the conversation (it stays saved and listed); drops only the in-memory copy."""
    agent.sessions.pop(session_id, None)
    return {"ok": True}


@app.get("/api/sessions")
async def list_sessions():
    return agent.store.list()


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    d = agent.store.load(session_id)
    if d is None:
        raise HTTPException(404, "unknown session")
    return {"id": d["id"], "title": d.get("title"), "updated": d.get("updated"), "transcript": d.get("transcript", [])}


class TranscriptIn(BaseModel):
    transcript: list


@app.put("/api/sessions/{session_id}")
async def save_transcript(session_id: str, body: TranscriptIn):
    if not agent.store.save_transcript(session_id, body.transcript):
        raise HTTPException(404, "unknown session")
    return {"ok": True}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    agent.sessions.pop(session_id, None)
    return {"ok": agent.store.delete(session_id)}


@app.get("/api/config")
async def config():
    return {"spot_mode": settings.spot_mode, "futures_mode": settings.futures_mode,
            "model": settings.llm_model, "require_confirmation": settings.require_confirmation,
            "has_wallex_key": bool(settings.wallex_api_key), "llm_configured": bool(settings.llm_api_key),
            "max_leverage": settings.max_leverage, "tools": sorted(agent_tools.TOOLS),
            "engine_mode": (engine_status() or {}).get("mode") or requested_engine_mode()}


# --------------------------------------------------------------- dashboard
@app.get("/api/markets")
async def markets(quote: str | None = None, sort: str = "volume", limit: int = 30, search: str | None = None):
    return await guarded(analytics.market_overview(ctx.client, quote, sort, limit, search))


@app.get("/api/movers")
async def movers(hours: int = 4, quote: str = "USDT", direction: str = "gainers", limit: int = 10):
    return await guarded(analytics.top_movers(ctx.client, hours, quote, limit, direction))


async def known_symbol(symbol: str) -> str:
    sym = symbol.upper()
    if sym not in await guarded(ctx.client.markets()):
        raise HTTPException(404, f"unknown market {sym}")
    return sym


@app.get("/api/depth/{symbol}")
async def depth(symbol: str, levels: int = 12):
    sym = await known_symbol(symbol)
    return await guarded(analytics.order_book_analysis(ctx.client, sym, max(1, min(levels, 50))))


@app.get("/api/trades/{symbol}")
async def market_trades(symbol: str, limit: int = 30):
    """The market's latest public trades (newest first) for the trade tape."""
    sym = await known_symbol(symbol)
    rows = await guarded(ctx.client.trades(sym))
    out = [{"time": t.get("timestamp"), "price": float(t["price"]), "qty": float(t["quantity"]),
            "side": "buy" if t.get("isBuyOrder") else "sell"} for t in rows[: max(1, min(limit, 100))]]
    return {"symbol": sym, "trades": out}


@app.get("/api/candles/{symbol}")
async def candles(symbol: str, resolution: str = "60", bars: int = 150):
    if resolution not in analytics.RES_SECONDS:
        raise HTTPException(400, f"resolution must be one of {sorted(analytics.RES_SECONDS)}")
    sym = await known_symbol(symbol)
    bars = max(1, min(bars, 1000))
    now = int(time.time())
    return await guarded(ctx.client.candles(sym, resolution, now - analytics.RES_SECONDS[resolution] * bars, now))


@app.get("/api/sentiment")
async def sentiment_ep(quote: str = "USDT"):
    try:
        glob = await sentiment.global_fear_greed(30)
    except Exception as e:
        glob = {"error": str(e)}
    return {"global": glob, "wallex_pulse": await guarded(sentiment.wallex_pulse(ctx.client, quote))}


@app.get("/api/news")
async def news(query: str | None = None, limit: int = 15):
    return await web.crypto_news(query, limit)


@app.get("/api/portfolio")
async def portfolio():
    out: dict = {"spot_mode": ctx.spot.mode, "futures_mode": ctx.futures.mode, "events": list(futures_events)[:10]}
    try:
        out["spot"] = await agent_tools.spot_balances(ctx, agent_tools.NoArgs())
        out["spot_orders"] = await ctx.spot.open_orders()
    except (WallexError, BrokerError) as e:
        out["spot_error"] = str(e)
    try:
        out["futures"] = await ctx.futures.account()
        out["positions"] = await ctx.futures.positions()
        out["futures_orders"] = await ctx.futures.open_orders()
    except (WallexError, BrokerError) as e:
        out["futures_error"] = str(e)
    return out


ENGINE_EVENTS = {"core", "core_decision", "regime", "decision", "risk_block", "entry_order", "entry", "entry_cancelled", "exit", "review",
                 "circuit_breaker", "error", "start", "stop"}


STRATEGY_RUN = "strategy"   # deploy/wallex-strategy.service: the USDT strategy in shadow beside the main engine


def engine_dir(mode: str | None = None) -> Path | None:
    """The journal directory of the engine run in `mode` (default: the app's mode; else whichever exists).
    A live engine and a shadow session can run side by side, so the newest file must not decide."""
    order = [mode] if mode else [requested_engine_mode(), "shadow", "live"]
    return next((d for d in (ROOT / "data" / "engine" / m for m in order) if (d / "state.json").exists()), None)


def engine_status(mode: str | None = None) -> dict | None:
    """The autonomous engine's state, read from its journal (it runs as a separate process)."""
    d = engine_dir(mode)
    if d is None:
        return None
    state = json.loads((d / "state.json").read_text())
    eq_rows = (d / "equity.csv").read_text().strip().splitlines()[1:] if (d / "equity.csv").exists() else []
    last = eq_rows[-1].split(",") if eq_rows else None
    events = []
    if (d / "decisions.jsonl").exists():
        with (d / "decisions.jsonl").open("rb") as f:   # only the tail: the journal grows all challenge long
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 200_000))
            lines = f.read().decode(errors="ignore").splitlines()[1:]
        for line in reversed(lines):
            with contextlib.suppress(ValueError):
                e = json.loads(line)
                if e.get("event") in ENGINE_EVENTS:
                    dec = e.get("decision") or {}
                    events.append({k: v for k, v in {
                        "time": e["time"], "event": e["event"], "symbol": e.get("symbol"), "setup": e.get("setup"),
                        "action": e.get("action"), "regime": e.get("regime"), "source": e.get("source"),
                        "reason": e.get("reason") or dec.get("reasoning") or e.get("error"),
                        "pnl_pct": e.get("pnl_pct"), "price": e.get("price")}.items() if v is not None})
            if len(events) >= 30:
                break
    trades = []
    if (d / "trades.csv").exists():
        with (d / "trades.csv").open() as f:
            trades = list(csv.DictReader(f))
    pnls = [float(t["pnl_tmn"] or 0) for t in trades]
    stats = {"trades": len(trades), "wins": sum(1 for x in pnls if x > 0), "realized_pnl_tmn": round(sum(pnls)),
             "win_rate_pct": round(100 * sum(1 for x in pnls if x > 0) / len(pnls), 1) if pnls else None}
    pending = [{"symbol": o.get("symbol"), "setup": o.get("setup"), "limit": o.get("limit"),
                "amount_tmn": o.get("amount_tmn"), "placed_at": o.get("placed_at"),
                "expires_at": o.get("expires_at")} for o in state.get("pending", {}).values()]
    recent = [{"id": t["trade_id"], "symbol": t["symbol"], "setup": t["setup"], "entry_time": t["entry_time"],
               "exit_time": t["exit_time"], "entry_price": float(t["entry_price"] or 0),
               "exit_price": float(t["exit_price"] or 0), "pnl_tmn": float(t["pnl_tmn"] or 0),
               "pnl_pct": float(t["pnl_pct"] or 0), "reason": t["exit_reason"]} for t in reversed(trades[-20:])]
    return {"mode": d.name, "updated": (d / "state.json").stat().st_mtime,
            "capital_tmn": settings.engine_capital_tmn,
            "equity_tmn": float(last[1]) if last else None, "cash_tmn": float(last[2]) if last else None,
            "drawdown_pct": float(last[4]) if last else None,
            "regime": state.get("regime"), "regime_reason": state.get("regime_reason"),
            "halted": state.get("halted"), "pause_until": state.get("pause_until"),
            "positions": list(state.get("positions", {}).values()), "pending": pending,
            "trades": recent, "stats": stats, "events": events, "core": state.get("core"),
            "exposure_tmn": float(last[3]) if last else None}


MODE_ARGS = {"live": "--mode live --confirm-live", "shadow": "--mode shadow"}
ENGINE_SERVICE = "wallex-engine"


class EngineModeIn(BaseModel):
    mode: Literal["live", "shadow"]
    # switching to real money must be an explicit, deliberate request, not a stray POST
    confirm: str = ""


@app.post("/api/engine/mode")
async def set_engine_mode(body: EngineModeIn):
    """Switch the autonomous engine between shadow and live: write data/engine/mode.env (read by the
    systemd unit after .env) and restart the engine service. Its state resumes per mode."""
    if body.mode == "live" and body.confirm != "LIVE":
        raise HTTPException(400, "switching to live needs confirm=LIVE")
    if not shutil.which("systemctl"):
        raise HTTPException(501, "the engine is not running as a systemd service here")
    ENGINE_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENGINE_MODE_FILE.write_text(f"ENGINE_RUN_ARGS={MODE_ARGS[body.mode]}\n")
    # the chat assistant follows the engine: real spot orders in live, paper in shadow
    settings.spot_mode = "live" if body.mode == "live" else "paper"
    ctx.spot = LiveSpotBroker(ctx.client) if body.mode == "live" else PaperSpotBroker(ctx.client, settings)
    proc = await asyncio.create_subprocess_exec("systemctl", "restart", ENGINE_SERVICE,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode:
        raise HTTPException(500, f"restart failed: {err.decode(errors='ignore')[:300]}")
    return {"ok": True, "mode": body.mode}


_key_check: dict = {"at": 0.0, "main": None}


async def check_trading_key() -> bool:
    """Is a main-account key configured for a trade-credit sub-account? (orders would fail with 1006)"""
    if not (settings.wallex_api_key and settings.wallex_sub_account_client_id):
        return False
    if time.time() - _key_check["at"] > 600 or _key_check["main"] is None:
        _key_check.update(at=time.time(), main=await ctx.client.key_is_main_account())
    ctx.key_is_main = bool(_key_check["main"])
    return ctx.key_is_main


@app.get("/api/account")
async def account():
    """The real Wallex account (trade-credit sub-account when configured), read-only, plus the engine."""
    out: dict = {"has_key": bool(settings.wallex_api_key), "sub_account": bool(settings.wallex_sub_account_client_id)}
    if await check_trading_key():
        out["key_warning"] = ("کلید API فعلی مال حساب اصلی است؛ موجودی اعتبار معاملاتی را می‌خواند ولی سفارش‌ها با موجودی "
                              "حساب اصلی سنجیده و رد می‌شوند. برای حساب «هکاتون» یک کلید API با دسترسی معامله بسازید.")
    if settings.wallex_api_key:
        try:
            out["spot"] = await agent_tools.value_balances(ctx, await LiveSpotBroker(ctx.client).balances())
        except (WallexError, BrokerError) as e:
            out["error"] = str(e)
    try:
        out["engine"] = engine_status()
    except (OSError, ValueError, KeyError) as e:
        out["engine_error"] = str(e)
    eng = out.get("engine")
    if eng and eng["positions"]:
        with contextlib.suppress(WallexError):
            markets = await ctx.client.markets()
            usdt_tmn = float((markets.get("USDTTMN") or {}).get("stats", {}).get("lastPrice") or 0)
            for p in eng["positions"]:   # mark open positions to the last price
                last = float((markets.get(p["symbol"]) or {}).get("stats", {}).get("lastPrice") or 0)
                rate = usdt_tmn if p.get("quote") == "USDT" else 1.0
                if last:
                    p["mark_price"] = last
                    p["unrealized_pnl_tmn"] = round((last - p["entry_price"]) * p["qty"] * rate)
                    p["unrealized_pnl_pct"] = round(100 * (last / p["entry_price"] - 1), 2)
    return out


def _last_start(d: Path) -> dict:
    """The newest `start` event of a run: its config snapshot is the strategy actually running."""
    if not (d / "decisions.jsonl").exists():
        return {}
    with (d / "decisions.jsonl").open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        for back in (300_000, 3_000_000, size):
            f.seek(max(0, size - back))
            for line in reversed(f.read().decode(errors="ignore").splitlines()):
                if '"event": "start"' in line:
                    with contextlib.suppress(ValueError):
                        return json.loads(line)
            if back >= size:
                break
    return {}


@app.get("/api/strategy")
async def strategy():
    """The running strategy for the strategy panel: phase, USDT core, satellite limits, the end-of-challenge
    schedule, and equity next to two benchmarks (hold TMN = capital, hold USDT from the first mark)."""
    runs = [m for m in (requested_engine_mode(), STRATEGY_RUN, "shadow", "live") if engine_dir(m)]
    # the run with the USDT core is the strategy; prefer the app's own mode when both have it
    mode = next((m for m in runs if (engine_status(m) or {}).get("core")), runs[0] if runs else None)
    if not mode:
        return {"engine": None}
    eng = engine_status(mode)
    start = _last_start(engine_dir(mode))
    cfg = start.get("config") or {}
    rows = []
    with contextlib.suppress(OSError):
        rows = [r.split(",") for r in (engine_dir(mode) / "equity.csv").read_text().strip().splitlines()[1:]]
    if start.get("ts") and mode != STRATEGY_RUN:   # its own dir spans restarts; live/shadow dirs saw other setups
        rows = [r for r in rows if float(r[0]) >= start["ts"] - 60] or rows[-1:]
    step = max(1, len(rows) // 240)
    pts = [(int(r[0]), float(r[1])) for r in rows[::step]]
    if rows and (len(rows) - 1) % step:   # always end on the newest mark
        pts.append((int(rows[-1][0]), float(rows[-1][1])))
    usdt = []
    if pts:
        with contextlib.suppress(Exception):   # the benchmark is optional; the panel works without it
            candles = await ctx.client.candles("USDTTMN", "60", pts[0][0] - 3600, int(time.time()))
            usdt = [(c["t"], c["c"]) for c in candles]
    bench, j = [], 0
    for t, _ in pts:
        while j + 1 < len(usdt) and usdt[j + 1][0] <= t:
            j += 1
        bench.append(usdt[j][1] if usdt else None)
    base = next((b for b in bench if b), None)
    capital = eng["capital_tmn"]
    first = pts[0][1] if pts else capital
    return {
        "mode": start.get("mode") or mode, "run": mode, "engine": eng, "quote": cfg.get("engine_quote", "TMN"), "started": start.get("time"),
        "end_time": cfg.get("engine_end_time") or settings.engine_end_time or None,
        "params": {k.removeprefix("engine_"): cfg.get(k) for k in (
            "engine_max_exposure_pct", "engine_max_position_pct", "engine_max_positions", "engine_risk_per_trade_pct",
            "engine_breadth_gate_pct", "engine_drawdown_pause_pct", "engine_drawdown_stop_pct", "engine_core_guard_pct",
            "engine_core_exit_hours", "engine_core_exit_slices", "engine_flatten_before_end_min",
            "engine_no_entry_before_end_min", "engine_fee_rate", "engine_setups", "engine_decider") if k in cfg},
        "curve": [{"t": t, "equity": round(e), "hold_usdt": round(first * b / base) if b and base else None,
                   "hold_tmn": round(first)} for (t, e), b in zip(pts, bench)],
    }


@app.get("/api/alerts")
async def alerts():
    return ctx.alerts.list()


@app.post("/api/alerts/{alert_id}/ack")
async def ack_alert(alert_id: str):
    ctx.alerts.ack(alert_id)
    return {"ok": True}


class TicketIn(BaseModel):
    session_id: str | None = None
    market: str
    symbol: str
    side: str
    amount: float
    order_type: str = "MARKET"
    price: float | None = None
    leverage: float = 1
    take_profit: float | None = None
    stop_loss: float | None = None


@app.post("/api/ui/ticket")
async def submit_ticket(t: TicketIn):
    """A generative-UI trade ticket was submitted: route it through the same risk guard +
    confirmation flow as an agent-initiated trade."""
    if t.market == "spot":
        name = "spot_place_order"
        args = {"symbol": t.symbol, "side": t.side, "type": t.order_type, "quote_amount": t.amount,
                "price": t.price, "reason": "ثبت‌شده از برگه سفارش"}
    else:
        name = "futures_open_position"
        args = {"symbol": t.symbol, "side": t.side, "leverage": t.leverage, "margin": t.amount,
                "order_type": t.order_type, "price": t.price, "take_profit": t.take_profit,
                "stop_loss": t.stop_loss, "reason": "ثبت‌شده از برگه سفارش"}
    ok, result, confirm = await agent.run_tool(name, json.dumps(args), agent.session(t.session_id))
    return {"ok": ok, "result": result, "action": confirm}


@app.post("/api/paper/reset")
async def paper_reset():
    if isinstance(ctx.spot, PaperSpotBroker):
        ctx.spot.reset(settings.paper_usdt, settings.paper_tmn)
    if isinstance(ctx.futures, PaperFuturesBroker):
        ctx.futures.reset(settings.paper_usdt, settings.paper_tmn)
    return {"ok": True}


# ----------------------------------------------------------------- static
DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "not found")
        f = (DIST / path).resolve()
        if not f.is_relative_to(DIST.resolve()):
            raise HTTPException(404, "not found")
        return FileResponse(f if path and f.is_file() else DIST / "index.html")


def main():
    import uvicorn
    uvicorn.run("wallex_agent.server:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()

"""FastAPI server: SSE chat with the agent + dashboard data endpoints + static UI."""

from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import tools as agent_tools
from .agent.context import AppContext
from .agent.loop import Agent
from .brokers import BrokerError, PaperFuturesBroker, PaperSpotBroker
from .config import ROOT, get_settings
from .market import analytics, sentiment
from .news import web
from .wallex import WallexError

settings = get_settings()
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
        async for ev in agent.chat(session, body.message):
            yield ev
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
        async for ev in agent.after_action(action):
            yield ev
    return sse(gen())


@app.get("/api/actions")
async def list_actions(session_id: str | None = None):
    return [a.public() for a in agent.pending.values() if not session_id or a.session_id == session_id]


@app.post("/api/sessions/{session_id}/reset")
async def reset_session(session_id: str):
    agent.sessions.pop(session_id, None)
    return {"ok": True}


@app.get("/api/config")
async def config():
    return {"spot_mode": settings.spot_mode, "futures_mode": settings.futures_mode,
            "model": settings.llm_model, "require_confirmation": settings.require_confirmation,
            "has_wallex_key": bool(settings.wallex_api_key), "llm_configured": bool(settings.llm_api_key),
            "max_leverage": settings.max_leverage, "tools": sorted(agent_tools.TOOLS)}


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
                "price": t.price, "reason": "submitted from trade ticket"}
    else:
        name = "futures_open_position"
        args = {"symbol": t.symbol, "side": t.side, "leverage": t.leverage, "margin": t.amount,
                "order_type": t.order_type, "price": t.price, "take_profit": t.take_profit,
                "stop_loss": t.stop_loss, "reason": "submitted from trade ticket"}
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

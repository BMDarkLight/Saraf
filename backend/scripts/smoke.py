"""Smoke test: call every read-only tool + a paper trade round-trip against live Wallex data."""
import asyncio, json, os, sys, tempfile
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
from saraf.config import get_settings
from saraf.agent.context import AppContext
from saraf.agent.loop import Agent


async def call(agent, session, name, **args):
    ok, res, confirm = await agent.run_tool(name, json.dumps(args), session)
    s = json.dumps(res, ensure_ascii=False, default=str)
    print(f"{'OK ' if ok else 'ERR'} {name:24} {s[:160]}")
    return ok, res, confirm


async def main():
    ctx = AppContext.build(get_settings())
    agent = Agent(ctx)
    s = agent.session("smoke")
    await call(agent, s, "list_markets", quote="USDT", limit=3)
    await call(agent, s, "get_ticker", symbol="BTCUSDT")
    await call(agent, s, "top_movers", hours=4, quote="USDT", limit=3)
    await call(agent, s, "order_book", symbol="BTCUSDT", levels=3)
    await call(agent, s, "recent_trades", symbol="USDTTMN", limit=5)
    await call(agent, s, "technical_analysis", symbol="ETHUSDT")
    await call(agent, s, "fear_greed_index")
    await call(agent, s, "market_risk", symbol="SOLUSDT")
    await call(agent, s, "global_coin_stats", coin="BTC")
    await call(agent, s, "crypto_news", query="bitcoin", limit=3)
    await call(agent, s, "web_search", query="bitcoin price today", news=True, limit=3)
    await call(agent, s, "futures_markets")
    # trading: confirmation flow + execution
    ok, res, conf = await call(agent, s, "spot_place_order", symbol="BTCUSDT", side="BUY", quote_amount=50, reason="smoke")
    a = await agent.execute_action(conf["id"], True); print("   ->", a.status, str(a.result)[:150])
    await call(agent, s, "spot_place_order", symbol="BTCUSDT", side="BUY", quote_amount=99999, reason="too big")
    ok, res, conf = await call(agent, s, "futures_open_position", symbol="ETHUSDT", side="LONG", leverage=5, margin=40,
                                stop_loss=1, reason="smoke")
    t = await ctx.client.markets(); px = float(t["ETHUSDT"]["stats"]["lastPrice"])
    ok, res, conf = await call(agent, s, "futures_open_position", symbol="ETHUSDT", side="LONG", leverage=5, margin=40,
                                stop_loss=round(px*0.97, 2), take_profit=round(px*1.05, 2), reason="smoke")
    a = await agent.execute_action(conf["id"], True); print("   ->", a.status, str(a.result)[:150])
    ok, acct, _ = await call(agent, s, "futures_account")
    pid = acct["positions"][0]["id"]
    ok, res, conf = await call(agent, s, "futures_close_position", position_id=pid, fraction=1, reason="smoke")
    a = await agent.execute_action(conf["id"], True); print("   ->", a.status, str(a.result)[:150])
    await call(agent, s, "spot_balances")
    await ctx.client.aclose()

asyncio.run(main())

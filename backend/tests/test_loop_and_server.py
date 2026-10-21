"""Agent-loop robustness under failure, and HTTP endpoints (fake market + scripted LLM)."""

import asyncio
import json
import time

import httpx
import pytest

from conftest import make_ctx, make_settings
from test_agent_loop import FakeLLM, chunk, collect, tc
from wallex_agent.agent.loop import Agent, repair_history, trim_history


@pytest.fixture
def agent(ctx):
    return Agent(ctx)


def call(name, args, id="c1", index=0):
    return chunk(tool_calls=[tc(index, id, name, json.dumps(args))])


def assert_valid_history(messages):
    open_ids = []
    for m in messages:
        if m["role"] == "tool":
            assert m["tool_call_id"] in open_ids, m
            open_ids.remove(m["tool_call_id"])
        else:
            assert not open_ids, f"tool calls {open_ids} have no results before {m['role']}"
            open_ids = [c["id"] for c in m.get("tool_calls") or []]
    assert not open_ids


# ------------------------------------------------------------------- loop
async def test_dropped_connection_mid_turn_does_not_break_the_session(agent):
    agent.llm = FakeLLM([[call("list_markets", {})], [chunk("ok")]])
    s = agent.session("s")
    gen = agent.chat(s, "markets?")
    async for ev in gen:
        if ev["type"] == "tool_call":
            break  # browser tab closed: generator abandoned before tool results were stored
    await gen.aclose()
    agent.llm = FakeLLM([[chunk("hello again")]])
    events = await collect(agent.chat(s, "still there?"))
    assert events[-1]["type"] == "done" and not any(e["type"] == "error" for e in events)
    assert_valid_history(agent.llm.seen[0][1:])


async def test_llm_failure_reports_error_and_session_recovers(agent):
    class Boom:
        chat = type("C", (), {"completions": type("X", (), {"create": staticmethod(lambda **k: (_ for _ in ()).throw(RuntimeError("503")))})()})()

    agent.llm = Boom()
    s = agent.session("s")
    events = await collect(agent.chat(s, "hi"))
    assert [e["type"] for e in events][-2:] == ["error", "done"]
    agent.llm = FakeLLM([[chunk("back")]])
    events = await collect(agent.chat(s, "hi again"))
    assert "".join(e.get("delta", "") for e in events if e["type"] == "text") == "back"


async def test_bad_tool_calls_are_reported_back_to_the_model(agent):
    agent.llm = FakeLLM([
        [chunk(tool_calls=[tc(0, "a", "no_such_tool", "{}"), tc(1, "b", "order_book", '{"symbol": ')])],
        [chunk("sorry")],
    ])
    events = await collect(agent.chat(agent.session("s"), "x"))
    results = [e for e in events if e["type"] == "tool_result"]
    assert [r["ok"] for r in results] == [False, False]
    assert "unknown tool" in results[0]["result"]["error"] and "invalid arguments" in results[1]["result"]["error"]
    assert events[-1]["type"] == "done"


async def test_step_limit(tmp_path, fake):
    a = Agent(make_ctx(make_settings(tmp_path, agent_max_steps=3), fake))
    a.llm = FakeLLM([[call("list_markets", {}, id=f"c{i}")] for i in range(3)])
    events = await collect(a.chat(a.session("s"), "loop forever"))
    assert sum(e["type"] == "tool_call" for e in events) == 3
    assert "step limit" in "".join(e.get("delta", "") for e in events if e["type"] == "text")


async def test_parallel_tool_calls_without_index(agent):
    agent.llm = FakeLLM([
        [chunk(tool_calls=[tc(None, "a", "get_ticker", '{"symbol": "BTCUSDT"}')]),
         chunk(tool_calls=[tc(None, "b", "get_ticker", '{"symbol": "ETHUSDT"}')])],
        [chunk("done")],
    ])
    events = await collect(agent.chat(agent.session("s"), "two prices"))
    syms = sorted(e["result"]["symbol"] for e in events if e["type"] == "tool_result")
    assert syms == ["BTCUSDT", "ETHUSDT"]


async def test_local_endpoint_without_key_is_allowed(tmp_path, fake):
    a = Agent(make_ctx(make_settings(tmp_path, llm_api_key="", llm_base_url="http://127.0.0.1:1234/v1"), fake))
    a.llm = FakeLLM([[chunk("hi")]])
    events = await collect(a.chat(a.session("s"), "x"))
    assert not any(e["type"] == "error" for e in events)


def test_repair_and_trim_history():
    msgs = [{"role": "user", "content": "a" * 500},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "x", "type": "function",
                                                                 "function": {"name": "t", "arguments": "{}"}}]},
            {"role": "user", "content": "b"},
            {"role": "tool", "tool_call_id": "zzz", "content": "orphan"}]
    repair_history(msgs)
    assert_valid_history(msgs)
    long = []
    for i in range(50):
        long += [{"role": "user", "content": f"q{i}" + "x" * 200},
                 {"role": "assistant", "content": "", "tool_calls": [{"id": f"t{i}", "type": "function",
                                                                      "function": {"name": "t", "arguments": "{}"}}]},
                 {"role": "tool", "tool_call_id": f"t{i}", "content": "y" * 300},
                 {"role": "assistant", "content": "answer"}]
    kept = trim_history(long, 3000)
    assert kept[0]["role"] == "user" and kept[-1] == long[-1] and len(kept) < len(long)
    assert_valid_history(kept)
    assert trim_history(long[-4:], 10) == long[-4:]  # the latest turn is always kept


# --------------------------------------------------------------- actions
async def propose(agent, **args):
    ok, res, confirm = await agent.run_tool("spot_place_order", json.dumps(
        {"symbol": "BTCUSDT", "side": "BUY", "quote_amount": 50, "reason": "t", **args}), agent.session("s"))
    assert ok, res
    return confirm["id"]


async def test_double_approval_executes_once(agent, ctx):
    aid = await propose(agent)
    a1, a2 = await asyncio.gather(agent.execute_action(aid, True), agent.execute_action(aid, True))
    assert a1.status == "executed"
    assert len(await ctx.spot.trades()) >= 1
    usdt = (await ctx.spot.balances())["USDT"]["total"]
    assert usdt > 1000 - 51  # only one 50 USDT buy happened


async def test_expired_confirmation_is_not_executed(agent, ctx):
    aid = await propose(agent)
    agent.pending[aid].created_at = time.time() - 3600
    a = await agent.execute_action(aid, True)
    assert a.status == "expired" and await ctx.spot.trades() == []
    agent.llm = FakeLLM([[chunk("it expired")]])
    events = await collect(agent.after_action(a))
    assert events[-1]["type"] == "done"


async def test_risk_is_rechecked_at_approval(agent, ctx):
    aid = await propose(agent)
    ctx.settings.max_order_notional_usdt = 10  # limits tightened (or price moved) after the proposal
    a = await agent.execute_action(aid, True)
    assert a.status == "failed" and "risk guard" in a.result["error"]


async def test_rejection(agent, ctx):
    aid = await propose(agent)
    assert (await agent.execute_action(aid, False)).status == "rejected"
    assert (await agent.execute_action(aid, True)).status == "rejected"


# ---------------------------------------------------------------- server
@pytest.fixture
async def http(agent, monkeypatch):
    from wallex_agent import server
    from wallex_agent.market import sentiment

    monkeypatch.setattr(server, "agent", agent)
    monkeypatch.setattr(server, "ctx", agent.ctx)

    async def fng(days=7):
        return {"value": 50, "classification": "Neutral", "history": []}
    monkeypatch.setattr(sentiment, "global_fear_greed", fng)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://t") as c:
        yield c


async def sse(resp) -> list[dict]:
    return [json.loads(l[6:]) for l in resp.text.splitlines() if l.startswith("data: ")]


async def test_dashboard_endpoints(http):
    for path in ["/api/config", "/api/markets?quote=USDT", "/api/movers?hours=4", "/api/depth/BTCUSDT",
                 "/api/candles/BTCUSDT?resolution=60&bars=20", "/api/portfolio", "/api/alerts", "/api/sentiment",
                 "/api/actions"]:
        r = await http.get(path)
        assert r.status_code == 200, (path, r.text[:200])
    assert (await http.get("/api/candles/BTCUSDT?resolution=7")).status_code == 400
    assert (await http.get("/api/depth/NOPEUSDT")).status_code in (400, 404)
    assert (await http.get("/api/does-not-exist")).status_code == 404
    for path in ["/%2e%2e/%2e%2e/backend/pyproject.toml", "/..%2f..%2f.env"]:
        r = await http.get(path)
        assert "hatchling" not in r.text and "LLM_" not in r.text, path


async def test_chat_and_action_endpoints(http, agent):
    agent.llm = FakeLLM([[call("spot_place_order", {"symbol": "BTCUSDT", "side": "BUY", "quote_amount": 20,
                                                    "reason": "t"})], [chunk("approve it")], [chunk("done")]])
    events = await sse(await http.post("/api/chat", json={"message": "buy"}))
    assert events[0]["type"] == "session" and events[-1]["type"] == "done"
    action = next(e["action"] for e in events if e["type"] == "confirm")
    events = await sse(await http.post(f"/api/actions/{action['id']}", json={"approve": True}))
    assert events[0]["action"]["status"] == "executed"
    assert (await http.post("/api/actions/nope", json={"approve": True})).status_code == 404
    r = await http.post("/api/ui/ticket", json={"market": "futures", "symbol": "BTCUSDT", "side": "LONG",
                                                 "amount": 20, "leverage": 50})
    assert r.json()["ok"] is False

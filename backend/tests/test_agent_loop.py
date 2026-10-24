"""Agent loop test with a scripted fake OpenAI-compatible LLM (no API key needed).

Market data comes from the live Wallex public API, trades run in paper mode.
"""

import json
from types import SimpleNamespace as NS

import pytest

from saraf.agent.context import AppContext
from saraf.agent.loop import Agent
from conftest import make_settings


def chunk(content=None, tool_calls=None):
    return NS(choices=[NS(delta=NS(content=content, tool_calls=tool_calls))])


def tc(index, id=None, name=None, args=None):
    return NS(index=index, id=id, function=NS(name=name, arguments=args))


class FakeStream:
    def __init__(self, chunks):
        self._it = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class FakeLLM:
    """Replays a script of turns; each turn is a list of stream chunks."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.seen = []
        self.chat = NS(completions=NS(create=self.create))

    async def create(self, **kw):
        self.seen.append(kw["messages"])
        return FakeStream(self.turns.pop(0))


@pytest.fixture
def agent(tmp_path):
    return Agent(AppContext.build(make_settings(tmp_path, llm_compat_schemas=False)))


async def collect(gen):
    return [e async for e in gen]


async def test_read_tools_then_answer(agent):
    agent.llm = FakeLLM([
        # turn 1: two parallel tool calls, arguments streamed in pieces
        [chunk(tool_calls=[tc(0, "c1", "top_movers", '{"hours": 4,'), tc(1, "c2", "order_book", '{"symbol"')]),
         chunk(tool_calls=[tc(0, args=' "limit": 3}'), tc(1, args=': "BTCUSDT"}')])],
        # turn 2: final answer
        [chunk("Top movers listed."), chunk(" Done.")],
    ])
    events = await collect(agent.chat(agent.session("s1"), "best markets last 4h?"))
    kinds = [e["type"] for e in events]
    assert kinds.count("tool_call") == 2 and kinds[-1] == "done"
    results = {e["name"]: e for e in events if e["type"] == "tool_result"}
    assert results["top_movers"]["ok"] and len(results["top_movers"]["result"]["markets"]) <= 3
    assert results["order_book"]["result"]["symbol"] == "BTCUSDT"
    assert "".join(e["delta"] for e in events if e["type"] == "text") == "Top movers listed. Done."
    # tool results were fed back to the model
    assert any(m["role"] == "tool" for m in agent.llm.seen[1])


async def test_trade_requires_confirmation(agent):
    args = json.dumps({"symbol": "BTCUSDT", "side": "BUY", "quote_amount": 20, "reason": "test"})
    agent.llm = FakeLLM([
        [chunk(tool_calls=[tc(0, "c1", "spot_place_order", args)])],
        [chunk("Awaiting your approval.")],
        [chunk("Order filled.")],
    ])
    events = await collect(agent.chat(agent.session("s2"), "buy 20 usdt of btc"))
    confirm = next(e for e in events if e["type"] == "confirm")["action"]
    assert confirm["status"] == "pending"
    before = await agent.ctx.spot.balances()
    assert before["USDT"]["total"] == agent.ctx.settings.paper_usdt  # nothing executed yet

    action = await agent.execute_action(confirm["id"], approve=True)
    assert action.status == "executed", action.result
    after = await agent.ctx.spot.balances()
    assert after["USDT"]["total"] < before["USDT"]["total"] and after["BTC"]["total"] > 0
    follow = await collect(agent.after_action(action))
    assert follow[-1]["type"] == "done"


async def test_risk_guard_blocks_oversized_order(agent):
    args = json.dumps({"symbol": "ETHUSDT", "side": "LONG", "leverage": 50, "margin": 10, "reason": "x"})
    agent.llm = FakeLLM([[chunk(tool_calls=[tc(0, "c1", "futures_open_position", args)])], [chunk("Blocked.")]])
    events = await collect(agent.chat(agent.session("s3"), "50x long eth"))
    res = next(e for e in events if e["type"] == "tool_result")
    assert not res["ok"] and "risk guard" in res["result"]["error"]
    assert not any(e["type"] == "confirm" for e in events)


def test_tool_groups_join_when_mentioned_and_stay():
    from saraf.agent.tools import TOOLS
    from saraf.agent.toolsets import select_tools

    names = list(TOOLS)
    core = select_tools(names, [{"role": "user", "content": "قیمت تتر"}])
    assert "get_ticker" in core and "futures_open_position" not in core and "render_dashboard" not in core
    assert len(core) <= len(names) - 12
    # spot orders are always available, however the user phrases it (regression: «تبدیل کن» had none)
    assert "spot_place_order" in select_tools(names, [{"role": "user", "content": "کل پولمو تبدیل کن تتر"}])

    msgs = [{"role": "user", "content": "یه پوزیشن لانگ ETHUSDT با اهرم ۳ باز کن"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function",
             "function": {"name": "futures_open_position", "arguments": "{}"}}]},
            {"role": "user", "content": "حالا حد ضررش رو عوض کن"}]
    assert "futures_set_tp_sl" in select_tools(names, msgs)


def test_engine_key_is_not_journaled():
    from saraf.engine.runner import SECRET_FIELDS
    assert {"llm_api_key", "engine_llm_api_key", "wallex_api_key"} <= SECRET_FIELDS

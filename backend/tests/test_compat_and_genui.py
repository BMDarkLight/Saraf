"""Regression tests for small-model quirks seen with Gemma 4 E4B on LM Studio, plus the
generative-UI and alert tools. Market data comes from the default venue's live public API."""

import json
import os

import pytest

from saraf.agent.context import AppContext
from saraf.agent.loop import Agent, compat_history, for_model, parse_text_tool_calls
from saraf.agent.tools import TOOLS, openai_tools
from conftest import make_settings


@pytest.fixture
def agent(tmp_path):
    return Agent(AppContext.build(make_settings(tmp_path, llm_compat_schemas=True)))


async def run(agent, name, **args):
    return await agent.run_tool(name, json.dumps(args), agent.session("t"))


# ------------------------------------------------------- Gemma regressions
def test_recovers_tool_call_written_as_text():
    # verbatim Gemma output after a failed call
    text = '[calling tools] get_candles({"bars":100,"resolution":"60","symbol":"ETHUSDT"})'
    rest, calls = parse_text_tool_calls(text)
    assert rest == ""
    assert calls[0]["function"]["name"] == "get_candles"
    assert json.loads(calls[0]["function"]["arguments"])["symbol"] == "ETHUSDT"


def test_ignores_unknown_names_and_keeps_prose():
    rest, calls = parse_text_tool_calls('Price looks strong. foo({"a": 1}) and order_book({"symbol": "BTCUSDT"})')
    assert [c["function"]["name"] for c in calls] == ["order_book"]
    assert rest.startswith("Price looks strong.") and "order_book" not in rest


async def test_numeric_resolution_is_coerced(agent):
    ok, res, _ = await run(agent, "technical_analysis", symbol="ETHUSDT", resolution=60)  # Gemma sent an int
    assert ok and res["resolution"] == "60"


def test_compat_schemas_have_no_enum_anyof_or_refs():
    s = json.dumps(openai_tools(compat=True))
    assert "anyOf" not in s and "$ref" not in s and '"enum"' not in s


def test_compat_history_has_no_tool_roles():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "get_ticker", "arguments": '{"symbol":"BTCUSDT"}'}},
            {"id": "b", "type": "function", "function": {"name": "order_book", "arguments": '{"symbol":"BTCUSDT"}'}}]},
        {"role": "tool", "tool_call_id": "a", "content": "{}"},
        {"role": "tool", "tool_call_id": "b", "content": "{}"},
    ]
    out = compat_history(msgs)
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert "tool_calls" not in out[1] and out[2]["content"].count("[tool result") == 2


# ------------------------------------------------------------ generative UI
@pytest.mark.skipif(os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
                    reason="calls the default venue's live API, unreliable from GitHub's US runners")
async def test_genui_tools_render_and_hide_payload_from_model(agent):
    cases = {
        "show_comparison_chart": dict(symbols="BTCUSDT,ETHUSDT,NOPEUSDT", hours=24),
        "show_market_heatmap": dict(quote="USDT", limit=12),
        "show_price_chart": dict(symbol="BTCUSDT", resolution=60,
                                 levels=[{"price": 80000, "label": "Support", "kind": "support"}]),
        "show_trade_ticket": dict(market="futures", symbol="ETHUSDT", side="BUY", amount=50, leverage=3),
        "show_position_planner": dict(symbol="SOLUSDT", side="SHORT", leverage=4, margin=40),
        "render_dashboard": dict(title="Report", blocks=[{"type": "metric", "label": "BTC", "value": "83k", "delta": 2.1},
                                                         {"type": "table", "columns": ["a"], "rows": [["1"]]}]),
    }
    for name, args in cases.items():
        ok, res, confirm = await run(agent, name, **args)
        assert ok and confirm is None, (name, res)
        assert res["ui"]["component"], name
        seen = for_model(res)
        assert "ui" not in seen and seen["ui_rendered"] == res["ui"]["component"]
    ok, res, _ = await run(agent, "show_comparison_chart", symbols="BTCUSDT,NOPEUSDT", hours=24)
    assert res["missing_symbols"] == ["NOPEUSDT"]
    ok, res, _ = await run(agent, "show_trade_ticket", market="futures", symbol="ETHUSDT", side="BUY", amount=50)
    assert res["ui"]["side"] == "LONG" and res["ui"]["max_leverage"]


async def test_ticket_submission_goes_through_risk_and_confirmation(agent):
    from saraf import server
    server.agent, server.ctx = agent, agent.ctx
    out = await server.submit_ticket(server.TicketIn(market="spot", symbol="BTCUSDT", side="BUY", amount=20))
    assert out["ok"] and out["action"]["status"] == "pending"
    out = await server.submit_ticket(server.TicketIn(market="futures", symbol="ETHUSDT", side="LONG",
                                                     amount=50, leverage=50))
    assert not out["ok"] and "risk guard" in out["result"]["error"]


# -------------------------------------------------------------------- alerts
async def test_price_alert_lifecycle(agent):
    ok, a, _ = await run(agent, "create_price_alert", symbol="BTCUSDT", condition="above", price=1, note="test")
    assert ok and a["status"] == "active"
    ok, b, _ = await run(agent, "create_price_alert", symbol="BTCUSDT", condition="above", price=10**9)
    fired = await agent.ctx.alerts.check()
    assert [f["id"] for f in fired] == [a["id"]]
    ok, res, _ = await run(agent, "cancel_price_alert", alert_id=b["id"])
    assert res["status"] == "canceled"
    assert len(TOOLS) >= 35

"""Risk guard + tool-layer behaviour through Agent.run_tool (no network)."""

import json

import pytest

from conftest import make_ctx, make_settings
from saraf.agent.loop import Agent


@pytest.fixture
def agent(ctx):
    return Agent(ctx)


async def run(agent, name, **args):
    return await agent.run_tool(name, json.dumps(args), agent.session("t"))


async def test_futures_needs_a_size(agent):
    ok, res, confirm = await run(agent, "futures_open_position", symbol="BTCUSDT", side="LONG", leverage=2, reason="x")
    assert not ok and confirm is None and "margin" in res["error"]


@pytest.mark.parametrize("args,needle", [
    (dict(leverage=11, margin=10), "MAX_LEVERAGE"),
    (dict(leverage=5, margin=10, stop_loss=120), "wrong side"),
    (dict(leverage=5, margin=10, take_profit=80), "wrong side"),
    (dict(leverage=10, margin=10, stop_loss=85), "liquidation"),
    (dict(leverage=2, margin=600), "exceeds"),
])
async def test_futures_risk_violations(agent, args, needle):
    ok, res, confirm = await run(agent, "futures_open_position", symbol="BTCUSDT", side="LONG", reason="x", **args)
    assert not ok and confirm is None and needle in res["error"], res


async def test_spot_limits_per_quote_currency(agent):
    ok, res, _ = await run(agent, "spot_place_order", symbol="BTCUSDT", side="BUY", quote_amount=501, reason="x")
    assert not ok and "exceeds" in res["error"]
    ok, res, _ = await run(agent, "spot_place_order", symbol="BTCTMN", side="BUY", quote_amount=40_000_000, reason="x")
    assert ok and res["status"] == "awaiting_user_confirmation"
    ok, res, _ = await run(agent, "spot_place_order", symbol="BTCUSDT", side="BUY", quantity=6, type="LIMIT",
                           price=90, reason="x")
    assert not ok and "540" in res["error"]


async def test_allowlist(tmp_path, fake):
    a = Agent(make_ctx(make_settings(tmp_path, symbol_allowlist="ETHUSDT"), fake))
    ok, res, _ = await run(a, "spot_place_order", symbol="BTCUSDT", side="BUY", quote_amount=10, reason="x")
    assert not ok and "ALLOWLIST" in res["error"]
    ok, res, _ = await run(a, "spot_close_asset", asset="BTC", quote="USDT", reason="x")
    assert not ok and "ALLOWLIST" in res["error"]


async def test_set_tp_sl_tool_is_validated_before_confirmation(agent, ctx):
    pos = await ctx.futures.open_position("BTCUSDT", "LONG", 5, margin=100)
    ok, res, confirm = await run(agent, "futures_set_tp_sl", position_id=pos["id"], take_profit=90)
    assert not ok and confirm is None


async def test_no_confirmation_mode_executes_directly(tmp_path, fake):
    a = Agent(make_ctx(make_settings(tmp_path, require_confirmation=False), fake))
    ok, res, confirm = await run(a, "spot_place_order", symbol="BTCUSDT", side="BUY", quote_amount=10, reason="x")
    assert ok and confirm is None and res["status"] == "FILLED"


async def test_spot_close_asset_sells_everything(agent, ctx):
    await ctx.spot.place_order("BTCUSDT", "BUY", "MARKET", quote_amount=100)
    ok, _, confirm = await run(agent, "spot_close_asset", asset="btc", quote="USDT", reason="x")
    action = await agent.execute_action(confirm["id"], True)
    assert action.status == "executed", action.result
    assert (await ctx.spot.balances()).get("BTC", {}).get("total", 0) < 1e-5


async def test_read_tools_on_fake_market(agent):
    for name, args in [("list_markets", {"quote": "USDT"}), ("get_ticker", {"symbol": "ethusdt"}),
                       ("order_book", {"symbol": "BTCUSDT"}), ("recent_trades", {"symbol": "BTCUSDT"}),
                       ("technical_analysis", {"symbol": "BTCUSDT", "resolution": "60"}),
                       ("market_risk", {"symbol": "BTCUSDT"}), ("top_movers", {"hours": 4}),
                       ("spot_balances", {}), ("futures_account", {}), ("futures_markets", {}),
                       ("get_candles", {"symbol": "BTCUSDT", "bars": 10})]:
        ok, res, _ = await run(agent, name, **args)
        assert ok and "error" not in json.dumps(res)[:200], (name, res)
    ok, res, _ = await run(agent, "get_ticker", symbol="NOPE")
    assert ok and "error" in res


async def test_read_webpage_blocks_private_addresses(agent):
    for url in ["http://localhost:8000/api/portfolio", "http://127.0.0.1:1234/v1/models", "http://192.168.1.1/",
                "http://[::1]/", "file:///etc/passwd"]:
        ok, res, _ = await run(agent, "read_webpage", url=url)
        assert "allowed" in res.get("error", ""), (url, res)


async def test_slow_tool_times_out_instead_of_hanging(agent, monkeypatch):
    import asyncio
    from saraf.agent import tools as T

    async def slow(ctx, a):
        await asyncio.sleep(30)
    monkeypatch.setattr(T.TOOLS["list_markets"], "handler", slow)
    monkeypatch.setattr("saraf.agent.loop.TOOL_TIMEOUT", 0.2)
    ok, res, _ = await run(agent, "list_markets")
    assert not ok and "timed out" in res["error"]

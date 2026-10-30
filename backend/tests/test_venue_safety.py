"""Real-money safety around connections: the engine's venue, pending cards, client swaps, the default switch."""

import json

import httpx
import pytest

from conftest import FakeClient, make_ctx, make_settings
from saraf import connections
from saraf.agent.loop import Agent
from saraf.venues import Venues


def test_overlay_leaves_the_default_venue_to_the_server(tmp_path):
    connections.save(tmp_path, {"default_venue": "nobitex", "bitpin_api_key": "k"})
    s = make_settings(tmp_path)
    connections.overlay(s)
    # the engine reads get_settings() too; it must keep trading the venue its state was built on
    assert s.default_venue == "wallex" and s.bitpin_api_key == "k"


async def test_card_fails_after_the_default_venue_changed(tmp_path):
    ctx = make_ctx(make_settings(tmp_path), FakeClient())
    agent = Agent(ctx)
    ok, res, card = await agent.run_tool("spot_place_order", json.dumps(
        {"symbol": "BTCUSDT", "side": "BUY", "quote_amount": 50, "reason": "t"}), agent.session("s"))
    assert ok, res

    class Other(FakeClient):
        name = "other"
    other = Other()
    from saraf.brokers import PaperSpotBroker
    ctx.venue, ctx.spot = other, PaperSpotBroker(other, ctx.settings)
    a = await agent.execute_action(card["id"], True)
    assert a.status == "failed" and "wallex" in a.result["error"]
    assert not await ctx.spot.trades()


async def test_reload_does_not_close_a_client_still_in_use(tmp_path):
    vs = Venues(make_settings(tmp_path))
    old = vs.get("binance")
    new = await vs.reload("binance")
    assert new is not old and not old._http.is_closed
    await vs.aclose()
    assert old._http.is_closed and new._http.is_closed


@pytest.fixture
async def api(tmp_path, monkeypatch):
    from saraf import server

    ctx = make_ctx(make_settings(tmp_path), FakeClient())
    monkeypatch.setattr(server, "ctx", ctx)
    monkeypatch.setattr(server, "agent", Agent(ctx))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://t") as c:
        c.ctx = ctx
        yield c


async def test_default_must_have_an_order_book(api):
    r = await api.post("/api/connections/default", json={"venue": "navasan"})
    assert r.status_code == 400 and api.ctx.venue.name == "wallex"


async def test_live_default_switch_to_a_venue_that_cannot_trade_is_refused(api):
    api.ctx.settings.spot_mode = "live"
    r = await api.post("/api/connections/default", json={"venue": "bitpin", "confirm": "LIVE"})
    assert r.status_code == 400 and "key" in r.text
    assert api.ctx.venue.name == "wallex" and api.ctx.settings.spot_mode == "live"


async def test_api_keys_are_masked_too(api):
    await api.put("/api/connections/bitpin", json={"values": {"bitpin_api_key": "public-ish-key-9999",
                                                              "bitpin_secret_key": "s3cr3t-s3cr3t"}})
    text = (await api.get("/api/connections")).text
    assert "public-ish-key-9999" not in text and "••••9999" in text

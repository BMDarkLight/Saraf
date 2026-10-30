"""Settings endpoints: venue connections, the default venue and the chat model."""

import json

import httpx
import pytest

from conftest import FakeClient, make_ctx, make_settings
from saraf import connections
from saraf.agent.loop import Agent
from saraf.brokers import PaperSpotBroker


@pytest.fixture
async def api(tmp_path, monkeypatch):
    from saraf import server

    ctx = make_ctx(make_settings(tmp_path), FakeClient())
    agent = Agent(ctx)
    monkeypatch.setattr(server, "ctx", ctx)
    monkeypatch.setattr(server, "agent", agent)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://t") as c:
        c.ctx, c.agent = ctx, agent
        yield c


async def test_listing_has_no_secret_values(api):
    r = await api.put("/api/connections/bitpin", json={"values": {"bitpin_api_key": "pubkey-1",
                                                                  "bitpin_secret_key": "very-secret-value-42"}})
    assert r.status_code == 200, r.text
    listing = (await api.get("/api/connections")).json()
    text = json.dumps(listing)
    assert "very-secret-value-42" not in text
    bitpin = next(v for v in listing["venues"] if v["name"] == "bitpin")
    fields = {f["key"]: f for f in bitpin["fields"]}
    assert fields["bitpin_secret_key"] == {**fields["bitpin_secret_key"], "set": True, "source": "saved",
                                           "hint": "••••e-42"}
    assert fields["bitpin_api_key"]["hint"] == "••••ey-1"   # API keys are masked too
    assert bitpin["has_key"] and listing["default"] == "wallex"
    assert api.ctx.settings.bitpin_secret_key == "very-secret-value-42"


async def test_empty_field_keeps_and_delete_clears(api):
    await api.put("/api/connections/bitpin", json={"values": {"bitpin_api_key": "a", "bitpin_secret_key": "b"}})
    await api.put("/api/connections/bitpin", json={"values": {"bitpin_api_key": "", "bitpin_secret_key": "c"}})
    assert connections.load(api.ctx.settings.data_dir) == {"bitpin_api_key": "a", "bitpin_secret_key": "c"}
    r = await api.delete("/api/connections/bitpin")
    assert r.status_code == 200 and connections.load(api.ctx.settings.data_dir) == {}
    assert api.ctx.settings.bitpin_api_key == ""


async def test_put_rejects_foreign_keys(api):
    r = await api.put("/api/connections/bitpin", json={"values": {"wallex_api_key": "x"}})
    assert r.status_code == 400
    r = await api.put("/api/connections/nope", json={"values": {}})
    assert r.status_code == 404


async def test_live_default_change_needs_confirm(api):
    api.ctx.settings.spot_mode = "live"
    api.ctx.settings.nobitex_token = "tok"
    r = await api.post("/api/connections/default", json={"venue": "nobitex"})
    assert r.status_code == 400 and "LIVE" in r.text
    r = await api.put("/api/connections/wallex", json={"values": {"wallex_api_key": "k"}})
    assert r.status_code == 400 and "LIVE" in r.text


async def test_default_switch_rebuilds_paper_broker_and_keeps_balances_apart(api):
    spot = api.ctx.spot
    await spot.place_order("BTCUSDT", "BUY", "MARKET", quantity=1)
    r = await api.post("/api/connections/default", json={"venue": "binance"})
    assert r.status_code == 200, r.text
    ctx = api.ctx
    assert ctx.venue.name == "binance" and isinstance(ctx.spot, PaperSpotBroker) and ctx.spot is not spot
    assert ctx.settings.default_venue == "binance"
    assert "BTC" not in await ctx.spot.balances()   # the old venue's paper fills stay with it
    assert connections.load(ctx.settings.data_dir)["default_venue"] == "binance"
    r = await api.post("/api/connections/default", json={"venue": "nope"})
    assert r.status_code == 400


async def test_test_endpoint_reports_errors(api):
    r = await api.post("/api/connections/navasan/test")
    assert r.status_code == 200 and r.json()["ok"] is False and "API key" in r.json()["message"]


async def test_llm_settings(api):
    r = await api.put("/api/settings/llm", json={"values": {"llm_model": "local-model",
                                                            "llm_api_key": "sk-abcdefgh1234"}})
    assert r.status_code == 200
    body = (await api.get("/api/settings/llm")).json()
    assert "sk-abcdefgh1234" not in json.dumps(body)
    fields = {f["key"]: f for f in body["fields"]}
    assert fields["llm_model"]["hint"] == "local-model" and fields["llm_api_key"]["hint"] == "••••1234"
    assert api.ctx.settings.llm_model == "local-model"
    assert api.agent.llm.api_key == "sk-abcdefgh1234"
    r = await api.put("/api/settings/llm", json={"values": {"bitpin_api_key": "x"}})
    assert r.status_code == 400


async def test_account_for_another_venue(api, monkeypatch):
    r = await api.get("/api/account?venue=tetherland")
    assert r.status_code == 200 and r.json()["venue"] == "tetherland" and r.json()["has_key"] is False

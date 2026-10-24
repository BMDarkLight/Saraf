"""The venue layer: registry, per-call venue routing in the tools, capability checks."""

import json

import pytest

from conftest import FakeClient, make_ctx, make_settings
from saraf.agent.tools import TOOLS
from saraf.brokers import PaperSpotBroker
from saraf.venues import Venue, VenueError, VenueUnsupported, available, create, register
from saraf.venues.wallex import WallexClient


class OtherVenue(FakeClient):
    name = "other"
    title = "Other"

    def __init__(self):
        super().__init__()
        self.prices = {"GOLDIRR": 1_000_000.0}


register("other")(lambda settings: OtherVenue())


def test_registry_creates_wallex_from_settings(tmp_path):
    s = make_settings(tmp_path, wallex_api_key="k", wallex_sub_account_client_id="SUB")
    v = create("wallex", s)
    assert isinstance(v, WallexClient) and v.has_key and v.sub_account == "SUB"
    assert v.capabilities.trading and v.capabilities.asset_class == "crypto"
    assert "wallex" in available()
    with pytest.raises(VenueError, match="unknown venue"):
        create("nope", s)


async def test_tools_read_another_venue_by_name(ctx):
    a = TOOLS["list_markets"].args(venue="other")
    rows = await TOOLS["list_markets"].handler(ctx, a)
    assert [r["symbol"] for r in rows] == ["GOLDIRR"]
    # without a venue the default (the fake Wallex) answers
    rows = await TOOLS["list_markets"].handler(ctx, TOOLS["list_markets"].args())
    assert "BTCUSDT" in [r["symbol"] for r in rows]


async def test_list_venues_marks_the_default(ctx):
    out = await TOOLS["list_venues"].handler(ctx, TOOLS["list_venues"].args())
    by_name = {v["name"]: v for v in out}
    assert by_name["wallex"]["default"] and not by_name["other"]["default"]
    assert {"trading", "futures", "asset_class", "candles"} <= set(by_name["wallex"])
    json.dumps(out)


async def test_data_only_venue_refuses_orders_and_paper_state_is_per_venue(tmp_path):
    class DataOnly(FakeClient):
        name = "dataonly"

    v = DataOnly()
    with pytest.raises(VenueUnsupported):
        v.live_spot_broker()
    with pytest.raises(VenueUnsupported):
        await v.place_order("BTCUSDT", "BUY", "MARKET", "1")
    s = make_settings(tmp_path)
    PaperSpotBroker(v, s).state.save()
    PaperSpotBroker(FakeClient(), s).state.save()
    assert (tmp_path / "paper_spot-dataonly.json").exists() and (tmp_path / "paper_spot.json").exists()


def test_every_venue_implements_market_data():
    for cls in (WallexClient,):
        assert issubclass(cls, Venue)
        assert not getattr(cls, "__abstractmethods__", None)


def test_ctx_market_is_the_default_venue_without_a_name(tmp_path, fake):
    c = make_ctx(make_settings(tmp_path), fake)
    assert c.market() is fake and c.market("WALLEX") is fake

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
    from saraf.venues.tse import TseClient
    for cls in (WallexClient, TseClient):
        assert issubclass(cls, Venue)
        assert not getattr(cls, "__abstractmethods__", None)


def test_ctx_market_is_the_default_venue_without_a_name(tmp_path, fake):
    c = make_ctx(make_settings(tmp_path), fake)
    assert c.market() is fake and c.market("WALLEX") is fake


# ------------------------------------------------------------------------------------------------ TSE stub
def tsetmc_transport():
    import httpx

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p.endswith("/GetMarketWatch"):
            return httpx.Response(200, json={"marketwatch": [
                {"insCode": "46348559193224090", "lva": "فولاد", "lvc": "فولاد مبارکه اصفهان", "pdv": 5520, "pcl": 5500,
                 "py": 5400, "pmn": 5390, "pmx": 5600, "qtc": 120_000_000, "qtj": 6.6e11,
                 "blDs": [{"pmd": 5510, "pmo": 5520, "qmd": 300_000, "qmo": 100_000}]}]})
        if "/BestLimits/" in p:
            return httpx.Response(200, json={"bestLimits": [
                {"number": 2, "pMeDem": 5500, "qTitMeDem": 1000, "pMeOf": 5530, "qTitMeOf": 800},
                {"number": 1, "pMeDem": 5510, "qTitMeDem": 500, "pMeOf": 5520, "qTitMeOf": 400}]})
        if "/GetTrade/" in p:
            return httpx.Response(200, json={"trade": [
                {"nTran": 1, "hEven": 90512, "qTitTran": 100, "pTran": 5500, "canceled": 0},
                {"nTran": 2, "hEven": 101500, "qTitTran": 50, "pTran": 5520, "canceled": 0}]})
        if "/GetClosingPriceDailyList/" in p:
            return httpx.Response(200, json={"closingPriceDaily": [
                {"dEven": 20261024, "priceFirst": 5450, "priceMax": 5600, "priceMin": 5390, "pClosing": 5500, "qTotTran5J": 1e8},
                {"dEven": 20261021, "priceFirst": 5300, "priceMax": 5450, "priceMin": 5280, "pClosing": 5400, "qTotTran5J": 9e7}]})
        return httpx.Response(404, json={})
    return httpx.MockTransport(handler)


async def test_tse_normalises_tsetmc_into_the_shared_schema():
    from saraf.market import analytics
    from saraf.venues.tse import TseClient

    v = TseClient(transport=tsetmc_transport())
    m = (await v.markets())["فولاد"]
    assert m["quoteAsset"] == "IRR" and m["stats"]["lastPrice"] == 5520 and m["stats"]["24h_ch"] == 1.85
    book = await v.depth("فولاد")
    assert book["bid"][0] == {"price": 5510, "quantity": 500} and book["ask"][0]["price"] == 5520
    trades = await v.trades("فولاد")
    assert trades[0]["timestamp"] == "10:15:00" and trades[0]["sum"] == 5520 * 50
    bars = await v.candles("فولاد", "1D", day_start_of(20261020), day_start_of(20261025))
    assert [b["c"] for b in bars] == [5400, 5500]
    rows = await analytics.market_overview(v, quote="IRR")
    assert rows[0]["symbol"] == "فولاد"
    with pytest.raises(VenueError, match="daily"):
        await v.candles("فولاد", "60", 0)
    with pytest.raises(NotImplementedError, match="Easy Trader"):
        await v.place_order("فولاد", "BUY", "LIMIT", "10", "5500")
    with pytest.raises(NotImplementedError):
        v.live_spot_broker()
    assert not v.capabilities.trading and v.capabilities.asset_class == "equity"
    await v.aclose()


def day_start_of(d):
    from saraf.venues.tse.client import day_start
    return day_start(d)

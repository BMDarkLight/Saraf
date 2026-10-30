"""Shared pieces for venue adapters: number formatting, the generic live broker, connection tests."""

import pytest

from conftest import FakeClient
from saraf.brokers.base import BrokerError
from saraf.brokers.live import VenueSpotBroker
from saraf.venues import Capabilities, VenueError, VenueUnsupported
from saraf.venues.http import fmt_dec, rial_to_toman


def test_fmt_dec_never_uses_exponent():
    assert fmt_dec(0.00001) == "0.00001"
    assert fmt_dec(12.50) == "12.5"
    assert fmt_dec(3.0) == "3"
    assert fmt_dec(1e-9) == "0.000000001"
    assert rial_to_toman("2143712112100") == 214371211210.0


class TradingVenue(FakeClient):
    name = "tradey"
    title = "Tradey"
    capabilities = Capabilities(asset_class="crypto", trading=True, account=True)

    def __init__(self):
        super().__init__()
        self.sent = []
        self.fail = False

    async def place_order(self, symbol, side, type, quantity, price=None, client_id=None):
        if self.fail:
            raise VenueError("insufficient balance")
        self.sent.append((symbol, side, type, quantity, price))
        return {"clientOrderId": client_id, "status": "NEW", "active": True}

    async def balances(self):
        return {"USDT": {"value": "10", "locked": "2"}, "BTC": {"value": "0", "locked": "0"}}


async def test_generic_broker_rounds_and_wraps_errors():
    v = TradingVenue()
    b = v.live_spot_broker()
    assert isinstance(b, VenueSpotBroker) and b.mode == "live"
    await b.place_order("BTCUSDT", "buy", "limit", quantity=0.1239999, price=99.123)
    sym, side, typ, qty, price = v.sent[0]
    assert (sym, side, typ, qty, price) == ("BTCUSDT", "BUY", "LIMIT", "0.123999", "99.12")
    assert await b.balances() == {"USDT": {"free": 8.0, "locked": 2.0, "total": 10.0}}
    v.fail = True
    with pytest.raises(BrokerError, match="^Tradey: insufficient balance"):
        await b.place_order("BTCUSDT", "SELL", "MARKET", quantity=1)


async def test_test_connection_uses_markets_without_key():
    v = TradingVenue()
    out = await v.test_connection()
    assert out["ok"] and "4" in out["message"]
    v.has_key = True
    out = await v.test_connection()
    assert out["ok"] and "USDT" in out["message"]


async def test_test_connection_reports_errors():
    v = TradingVenue()
    v.has_key = True

    async def broken():
        raise VenueError("HTTP 401 from /wallets")
    v.balances = broken
    out = await v.test_connection()
    assert not out["ok"] and "401" in out["message"]


def test_data_only_venue_has_no_live_broker():
    class DataOnly(FakeClient):
        name = "d"
        capabilities = Capabilities(asset_class="fx")
    with pytest.raises(VenueUnsupported):
        DataOnly().live_spot_broker()

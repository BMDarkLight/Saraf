"""Bitpin adapter against recorded responses."""

import base64
import json
import time

import httpx
import pytest

from saraf.venues import VenueError
from saraf.venues.bitpin import BitpinClient

MARKETS = [
    {"symbol": "BTC_IRT", "base": "BTC", "quote": "IRT", "tradable": True, "suspended": False, "price_precision": 0,
     "base_amount_precision": 8, "quote_amount_precision": 0},
    {"symbol": "BTC_USDT", "base": "BTC", "quote": "USDT", "tradable": True, "suspended": False,
     "price_precision": 2, "base_amount_precision": 6, "quote_amount_precision": 2},
    {"symbol": "OLD_IRT", "base": "OLD", "quote": "IRT", "tradable": False, "suspended": True, "price_precision": 0,
     "base_amount_precision": 2, "quote_amount_precision": 0},
]
TICKERS = [{"symbol": "BTC_IRT", "price": "21349008000", "daily_change_price": -3.48, "low": "20765496040",
            "high": "22165063300", "timestamp": 1793375409.3},
           {"symbol": "BTC_USDT", "price": "77387.11", "daily_change_price": -5.28, "low": "76591", "high": "81873"}]


def jwt(exp: float) -> str:
    part = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{part({'alg': 'HS256'})}.{part({'exp': exp})}.sig"


def make(routes: dict, seen: list | None = None, **kw) -> BitpinClient:
    routes = {("GET", "/api/v1/mkt/markets/"): MARKETS, ("GET", "/api/v1/mkt/tickers/"): TICKERS, **routes}

    def handler(req: httpx.Request):
        if seen is not None:
            seen.append(req)
        key = (req.method, req.url.path)
        if key in routes:
            r = routes[key]
            r = r(req) if callable(r) else r
            return r if isinstance(r, httpx.Response) else httpx.Response(200, json=r)
        return httpx.Response(404, json={"detail": "Not found."})
    return BitpinClient(transport=httpx.MockTransport(handler), **kw)


async def test_symbols_and_toman_markets():
    m = await make({}).markets()
    assert set(m) == {"BTCTMN", "BTCUSDT"}
    btc = m["BTCTMN"]
    assert btc["quoteAsset"] == "TMN" and btc["stats"]["lastPrice"] == 21349008000.0
    assert btc["stepSize"] == 8 and btc["tickSize"] == 0 and btc["stats"]["24h_ch"] == -3.48
    assert m["BTCUSDT"]["tickSize"] == 2


async def test_book_uses_venue_symbol():
    seen = []
    c = make({("GET", "/api/v1/mth/orderbook/BTC_IRT/"): {"asks": [["101", "1"]], "bids": [["99", "2"]]}}, seen)
    book = await c.depth("BTCTMN")
    assert book == {"bid": [{"price": 99.0, "quantity": 2.0}], "ask": [{"price": 101.0, "quantity": 1.0}]}


async def test_token_refreshes_on_401_once():
    calls = {"auth": 0, "wallets": 0}

    def auth(req):
        calls["auth"] += 1
        assert json.loads(req.content) == {"api_key": "k", "secret_key": "s"}
        return {"access": jwt(time.time() + 900), "refresh": jwt(time.time() + 86400)}

    def wallets(req):
        calls["wallets"] += 1
        if calls["wallets"] == 1:
            return httpx.Response(401, json={"detail": "token expired"})
        assert req.headers["Authorization"].startswith("Bearer ")
        return [{"asset": "IRT", "balance": "500", "frozen": "100"}, {"asset": "BTC", "balance": "0.5", "frozen": "0"}]

    c = make({("POST", "/api/v1/usr/authenticate/"): auth, ("GET", "/api/v1/wlt/wallets/"): wallets},
             api_key="k", secret_key="s")
    bal = await c.balances()
    assert bal["TMN"] == {"value": 600.0, "locked": 100.0} and bal["BTC"]["value"] == 0.5
    assert calls == {"auth": 2, "wallets": 2}


async def test_second_401_raises():
    c = make({("POST", "/api/v1/usr/authenticate/"): lambda r: {"access": jwt(time.time() + 900), "refresh": "x"},
              ("GET", "/api/v1/wlt/wallets/"): httpx.Response(401, json={"detail": "bad key"})},
             api_key="k", secret_key="s")
    with pytest.raises(VenueError, match="bad key"):
        await c.balances()


async def test_order_maps_identifier_and_state():
    seen = []

    def place(req):
        b = json.loads(req.content)
        return {"id": 5, "symbol": b["symbol"], "type": b["type"], "side": b["side"], "price": b["price"],
                "base_amount": b["base_amount"], "identifier": b["identifier"], "state": "active",
                "dealed_base_amount": "0.1", "dealed_quote_amount": "10", "created_at": "2026-10-30T10:00:00Z"}

    c = make({("POST", "/api/v1/usr/authenticate/"): {"access": jwt(time.time() + 900), "refresh": "r"},
              ("POST", "/api/v1/odr/orders/"): place}, seen, api_key="k", secret_key="s")
    o = await c.place_order("BTCTMN", "BUY", "LIMIT", "0.5", price="100", client_id="cid1")
    sent = json.loads(seen[-1].content)
    assert sent == {"symbol": "BTC_IRT", "type": "limit", "side": "buy", "base_amount": "0.5", "price": "100",
                    "identifier": "cid1"}
    assert o["clientOrderId"] == "cid1" and o["status"] == "PARTIALLY_FILLED" and o["active"]
    assert o["executedQty"] == 0.1 and o["executedSum"] == 10.0 and o["executedPrice"] == 100.0


async def test_candle_resolution_mismatch_raises():
    c = make({("GET", "/api/v1/mkt/candles/"): [{"open": 1, "close": 2, "low": "1", "high": "2", "volume": 3,
                                                  "ts": 100.0, "resolution": "30m"}]})
    with pytest.raises(VenueError, match="resolution"):
        await c.candles("BTCTMN", "60", 0, 200)


async def test_get_order_ignores_a_list_row_for_another_identifier():
    auth = {"access": jwt(time.time() + 900), "refresh": "r"}
    other = {"id": 8, "symbol": "BTC_IRT", "identifier": "someone-else", "state": "closed", "base_amount": "1",
             "dealed_base_amount": "1", "dealed_quote_amount": "5"}
    c = make({("POST", "/api/v1/usr/authenticate/"): auth, ("GET", "/api/v1/odr/orders/"): [other]},
             api_key="k", secret_key="s")
    with pytest.raises(VenueError, match="no order"):
        await c.get_order("mine")

"""Ramzinex adapter against recorded responses."""

import base64
import json
import time

import httpx
import pytest

from saraf.venues import VenueError
from saraf.venues.ramzinex import RamzinexClient


def pair(pid, base, quote, buy, sell, close, pp=0, bp=5):
    return {"pair_id": pid, "base_currency_id": pid * 10, "base_currency_symbol": {"en": base},
            "quote_currency_id": 2 if quote == "irr" else 3, "quote_currency_symbol": {"en": quote},
            "base_precision": bp, "price_precision": pp, "buy": buy, "sell": sell, "is_delist": 0,
            "is_temporarily_suspended": 0, "tv_symbol": {"ramzinex": f"{base}{quote}"},
            "financial": {"last24h": {"close": close, "highest": close * 1.1, "lowest": close * 0.9,
                                      "change_percent": -3.11, "base_volume": 2.0, "quote_volume": 4 * close}}}


PAIRS = {"status": 0, "data": [pair(2, "btc", "irr", 2118000000010, 2120000000000, 2121999933330),
                               pair(13, "eth", "usdt", 2221.61, 2233.48, 2230.0, pp=2, bp=4),
                               {**pair(99, "old", "irr", 1, 2, 1), "is_delist": 1}]}


def jwt(exp):
    part = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{part({})}.{part({'exp': exp})}.s"


def make(routes: dict, seen=None, **kw) -> RamzinexClient:
    routes = {("GET", "/exchange/api/v1.0/exchange/pairs"): PAIRS,
              ("POST", "/exchange/api/v1.0/exchange/auth/api_key/getToken"): {"status": 0,
                                                                               "data": {"token": jwt(time.time() + 3600)}},
              **routes}

    def handler(req):
        if seen is not None:
            seen.append(req)
        r = routes.get((req.method, req.url.path))
        if r is None:
            return httpx.Response(404, json={"status": -1, "message": "no route"})
        r = r(req) if callable(r) else r
        return r if isinstance(r, httpx.Response) else httpx.Response(200, json=r)
    return RamzinexClient(transport=httpx.MockTransport(handler), **kw)


async def test_pairs_map_to_symbols_and_toman():
    m = await make({}).markets()
    assert set(m) == {"BTCTMN", "ETHUSDT"}
    btc = m["BTCTMN"]
    assert btc["quoteAsset"] == "TMN" and btc["stats"]["bidPrice"] == 211800000001.0
    assert btc["stats"]["lastPrice"] == pytest.approx(212199993333.0)
    assert btc["tickSize"] == 1 and btc["stepSize"] == 5
    assert m["ETHUSDT"]["stats"]["askPrice"] == 2233.48 and m["ETHUSDT"]["tickSize"] == 2


async def test_book_and_candles_use_pair_id_and_rial():
    seen = []
    c = make({("GET", "/exchange/api/v1.0/exchange/orderbooks/2/buys_sells"): {"status": 0, "data": {
                 "buys": [[2120000000010, 8.8e-05, 1, False, None, 19, 1]], "sells": [[2164009900000, 5e-06, 1]]}},
              ("GET", "/exchange/api/v1.0/exchange/chart/tv/history"): {"s": "ok", "t": [1], "o": [2110000000000],
                                                                        "h": [2], "l": [1], "c": [2], "v": [0.1]}}, seen)
    book = await c.depth("BTCTMN")
    assert book["bid"] == [{"price": 212000000001.0, "quantity": 8.8e-05}]
    bars = await c.candles("BTCTMN", "60", 0, 100)
    assert seen[-1].url.params["symbol"] == "BTCIRR" and bars[0]["o"] == 211000000000.0


async def test_both_auth_headers_sent():
    seen = []
    c = make({("GET", "/exchange/api/v1.0/exchange/users/me/funds/summaryDesktop"): {"status": 0, "data": [
        {"currency": {"symbol": {"en": "irr"}}, "total": 50000, "in_orders": 10000},
        {"currency": {"symbol": {"en": "btc"}}, "total": 0.5, "in_orders": 0}]}}, seen, api_key="k", secret="s")
    bal = await c.balances()
    assert bal["TMN"] == {"value": 5000.0, "locked": 1000.0} and bal["BTC"]["value"] == 0.5
    last = seen[-1]
    assert last.headers["x-api-key"] == "k" and last.headers["Authorization2"].startswith("Bearer ")


async def test_client_id_survives_restart(tmp_path):
    def place(req):
        assert json.loads(req.content) == {"pair_id": 2, "amount": 0.01, "price": 1000000, "type": "buy"}
        return {"status": 0, "data": {"order_id": 777}}

    order = {"status": 0, "data": {"id": 777, "pair_id": 2, "type_en": "buy", "amount_nr": 0.01,
                                   "order_price_nr": 1000000, "average_price_nr": 1000000, "filled_nr": 0.01,
                                   "status_id": 3}}
    routes = {("POST", "/exchange/api/v1.0/exchange/users/me/orders/limit"): place,
              ("GET", "/exchange/api/v1.0/exchange/users/me/orders2/777"): order}
    c = make(routes, api_key="k", secret="s", data_dir=tmp_path)
    o = await c.place_order("BTCTMN", "BUY", "LIMIT", "0.01", price="100000", client_id="mine")
    assert o["clientOrderId"] == "mine" and o["status"] == "NEW"
    again = make(routes, api_key="k", secret="s", data_dir=tmp_path)
    got = await again.get_order("mine")
    assert got["clientOrderId"] == "mine" and got["status"] == "FILLED" and got["executedSum"] == pytest.approx(1000.0)


async def test_market_buy_sends_rial_amount():
    seen = []
    routes = {("POST", "/exchange/api/v2.0/exchange/users/me/orders/market"): {"status": 0, "data": {"order_id": 5}},
              ("GET", "/exchange/api/v1.0/exchange/orderbooks/2/buys_sells"): {"status": 0, "data": {
                  "buys": [[2118000000000, 1]], "sells": [[2120000000000, 1]]}}}
    c = make(routes, seen, api_key="k", secret="s")
    await c.place_order("BTCTMN", "BUY", "MARKET", "0.001")
    body = json.loads(seen[-1].content)
    # 0.001 BTC at the 212,000,000,000-toman ask, sent as rial
    assert body["type"] == "buy" and body["pair_id"] == 2 and body["amount"] == pytest.approx(2_120_000_000)


async def test_error_status_raises():
    c = make({("POST", "/exchange/api/v1.0/exchange/users/me/orders/limit"): {"status": -1,
                                                                             "description": {"en": "low balance"}}},
             api_key="k", secret="s")
    with pytest.raises(VenueError, match="low balance"):
        await c.place_order("BTCTMN", "BUY", "LIMIT", "0.01", price="100000")

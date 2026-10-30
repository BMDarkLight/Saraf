"""Nobitex adapter against recorded responses (shapes taken from the live public API and the docs)."""

import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption

from saraf.venues import VenueError
from saraf.venues.nobitex import NobitexClient, sign

OPTIONS = {"status": "ok", "nobitex": {
    "amountPrecisions": {"BTCIRT": "0.000001", "BTCUSDT": "0.000001", "USDTIRT": "0.01"},
    "pricePrecisions": {"BTCIRT": "10", "BTCUSDT": "0.01", "USDTIRT": "10"},
    "minOrders": {"rls": "500000", "usdt": "0.5"}}}
STATS = {"status": "ok", "stats": {
    "btc-rls": {"isClosed": False, "bestSell": "2143712112200", "bestBuy": "2143000000000", "latest": "2143712112100",
                "dayLow": "2090000000000", "dayHigh": "2203764800000", "dayChange": "-2.92",
                "volumeSrc": "13.06", "volumeDst": "27899321918005"},
    "btc-usdt": {"isClosed": False, "bestSell": "77442.69", "bestBuy": "77350", "latest": "77400", "dayLow": "76561",
                 "dayHigh": "81248", "dayChange": "-4.73", "volumeSrc": "30.7", "volumeDst": "2403481.18"},
    "usdt-rls": {"isClosed": True, "latest": "1100000"}}}


def mock(routes: dict, seen: list | None = None):
    def handler(req: httpx.Request):
        if seen is not None:
            seen.append(req)
        for (method, path), body in routes.items():
            if req.method == method and req.url.path == path:
                return httpx.Response(200, json=body(req) if callable(body) else body)
        return httpx.Response(404, json={"status": "failed", "message": "not found"})
    return httpx.MockTransport(handler)


def client(routes, seen=None, **kw):
    return NobitexClient(transport=mock({("GET", "/v2/options"): OPTIONS, ("GET", "/market/stats"): STATS, **routes},
                                        seen), **kw)


async def test_rial_markets_become_toman():
    m = await client({}).markets()
    btc = m["BTCTMN"]
    assert btc["quoteAsset"] == "TMN" and btc["baseAsset"] == "BTC"
    assert btc["stats"]["lastPrice"] == 214371211210.0
    assert btc["stats"]["bidPrice"] == 214300000000.0
    assert btc["tickSize"] == 0 and btc["stepSize"] == 6 and btc["minNotional"] == 50_000
    assert m["BTCUSDT"]["stats"]["lastPrice"] == 77400.0 and m["BTCUSDT"]["tickSize"] == 2
    assert m["BTCUSDT"]["minNotional"] == 0.5
    assert "USDTTMN" not in m   # closed market


async def test_depth_and_trades_in_toman():
    c = client({("GET", "/v3/orderbook/BTCIRT"): {"status": "ok", "bids": [["2143000000000", "0.1"]],
                                                   "asks": [["2143712112200", "0.2"]]},
                ("GET", "/v2/trades/BTCIRT"): {"status": "ok", "trades": [
                    {"time": 1793375278089, "price": "2143712112100", "volume": "0.0001", "type": "buy"}]}})
    book = await c.depth("BTCTMN")
    assert book["bid"][0] == {"price": 214300000000.0, "quantity": 0.1}
    t = (await c.trades("BTCTMN"))[0]
    assert t["price"] == 214371211210.0 and t["isBuyOrder"] and t["timestamp"].startswith("2026-")


def test_signature_verifies():
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    b64 = base64.urlsafe_b64encode(raw).decode()
    sig = sign(b64, "1700000000", "POST", "/market/orders/add", '{"a":1}')
    key.public_key().verify(base64.urlsafe_b64decode(sig), b'1700000000POST/market/orders/add{"a":1}')


async def test_limit_order_sends_rial_price_and_maps_order():
    seen = []

    def add(req):
        body = json.loads(req.content)
        return {"status": "ok", "order": {"id": 9, "type": body["type"], "execution": "Limit", "market": "BTC-RLS",
                                          "price": body["price"], "amount": body["amount"], "matchedAmount": "0.001",
                                          "unmatchedAmount": "0", "totalPrice": "1000", "averagePrice": "1000",
                                          "status": "Done", "clientOrderId": body["clientOrderId"],
                                          "created_at": "2026-10-30T10:00:00+00:00"}}
    c = client({("POST", "/market/orders/add"): add}, seen, token="tok")
    o = await c.place_order("BTCTMN", "BUY", "LIMIT", "0.001", price="100", client_id="abc")
    sent = json.loads(seen[-1].content)
    assert sent == {"type": "buy", "execution": "limit", "srcCurrency": "btc", "dstCurrency": "rls",
                    "amount": "0.001", "price": "1000", "clientOrderId": "abc"}
    assert seen[-1].headers["Authorization"] == "Token tok"
    assert o["clientOrderId"] == "abc" and o["status"] == "FILLED" and not o["active"]
    assert o["symbol"] == "BTCTMN" and o["side"] == "BUY" and o["price"] == 100.0
    assert o["executedQty"] == 0.001 and o["executedSum"] == pytest.approx(0.1)


async def test_failed_status_raises():
    c = client({("POST", "/market/orders/add"): {"status": "failed", "code": "InsufficientBalance",
                                                 "message": "Balance too low"}}, token="tok")
    with pytest.raises(VenueError, match="Balance too low"):
        await c.place_order("BTCTMN", "SELL", "MARKET", "1")


async def test_private_call_without_credentials_is_refused():
    with pytest.raises(VenueError, match="API key"):
        await client({}).balances()


async def test_candles_map_resolution():
    seen = []
    c = client({("GET", "/market/udf/history"): {"s": "ok", "t": [1], "o": ["21174747834"], "h": ["2"], "l": ["1"],
                                                 "c": ["2"], "v": ["0.5"]}}, seen)
    bars = await c.candles("BTCTMN", "1D", 0, 100)
    assert seen[-1].url.params["resolution"] == "D" and seen[-1].url.params["symbol"] == "BTCIRT"
    assert bars[0]["o"] == 21174747834.0   # candles are already in toman
    with pytest.raises(VenueError):
        await c.candles("BTCTMN", "7", 0, 100)


async def test_rial_price_has_no_float_noise():
    seen = []

    def add(req):
        b = json.loads(req.content)
        return {"status": "ok", "order": {"id": 1, "type": "buy", "market": "SHIB-RLS", "price": b["price"],
                                          "amount": b["amount"], "matchedAmount": "0", "status": "Active"}}
    c = client({("POST", "/market/orders/add"): add}, seen, token="tok")
    await c.place_order("SHIBTMN", "BUY", "LIMIT", "1000", price="0.57")
    assert json.loads(seen[-1].content)["price"] == "5.7"

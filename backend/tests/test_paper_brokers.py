"""Deterministic tests of the paper spot/futures simulators (no network)."""

import pytest

from wallex_agent.brokers import BrokerError
from wallex_agent.brokers.futures import TAKER_FEE, liquidation_price


# ===================================================================== spot
async def test_market_buy_spends_quote_and_charges_fee(ctx):
    o = await ctx.spot.place_order("BTCUSDT", "BUY", "MARKET", quote_amount=50)
    b = await ctx.spot.balances()
    assert o["status"] == "FILLED"
    assert b["USDT"]["total"] == pytest.approx(1000 - o["executedSum"])
    assert 49.5 < o["executedSum"] <= 50.0001
    assert b["BTC"]["total"] == pytest.approx(o["executedQty"] * 0.998)


async def test_market_buy_can_spend_the_whole_balance(ctx):
    # "convert all my USDT": sized in quote, so no slippage buffer may block it (it used to need +1%)
    o = await ctx.spot.place_order("BTCUSDT", "BUY", "MARKET", quote_amount=1000)
    b = await ctx.spot.balances()
    assert o["status"] == "FILLED"
    assert 0 <= b["USDT"]["total"] < 1


async def test_quantity_sized_buy_keeps_the_slippage_buffer(ctx):
    with pytest.raises(BrokerError, match="insufficient USDT"):
        await ctx.spot.place_order("BTCUSDT", "BUY", "MARKET", quantity=9.99)   # ~1000 USDT + buffer


async def test_market_buy_never_overdraws_on_thin_expensive_book(ctx, fake):
    # top level is tiny; the rest of the book is 50% higher: walking it must not push USDT negative
    fake.books["BTCUSDT"] = {"ask": [{"price": 100.1, "quantity": 0.01}, {"price": 150, "quantity": 100}],
                             "bid": [{"price": 99.9, "quantity": 100}]}
    await ctx.spot.place_order("BTCUSDT", "BUY", "MARKET", quote_amount=990)
    b = await ctx.spot.balances()
    assert b["USDT"]["total"] >= -1e-9


async def test_insufficient_balance_and_min_notional_and_unknown_market(ctx):
    with pytest.raises(BrokerError, match="insufficient"):
        await ctx.spot.place_order("BTCUSDT", "BUY", "MARKET", quote_amount=5000)
    with pytest.raises(BrokerError, match="insufficient"):
        await ctx.spot.place_order("BTCUSDT", "SELL", "MARKET", quantity=1)
    with pytest.raises(BrokerError, match="min notional"):
        await ctx.spot.place_order("BTCUSDT", "BUY", "MARKET", quote_amount=0.5)
    with pytest.raises(BrokerError, match="unknown market"):
        await ctx.spot.place_order("NOPEUSDT", "BUY", "MARKET", quote_amount=10)


async def test_limit_buy_rests_locks_then_fills_when_price_drops(ctx, fake):
    o = await ctx.spot.place_order("BTCUSDT", "BUY", "LIMIT", quantity=2, price=90)
    assert o["status"] == "NEW"
    b = await ctx.spot.balances()
    assert b["USDT"]["locked"] == pytest.approx(180)
    assert b["USDT"]["free"] == pytest.approx(820)
    fake.set_price("BTCUSDT", 89)
    b = await ctx.spot.balances()
    assert b["USDT"]["locked"] == pytest.approx(0)
    assert b["USDT"]["total"] == pytest.approx(820)
    assert (await ctx.spot.get_order(o["clientOrderId"]))["status"] == "FILLED"


async def test_cancel_releases_lock_exactly(ctx):
    o = await ctx.spot.place_order("BTCUSDT", "BUY", "LIMIT", quantity=1, price=50)
    await ctx.spot.cancel_order(o["clientOrderId"])
    b = await ctx.spot.balances()
    assert b["USDT"] == {"free": 1000, "locked": 0, "total": 1000}
    assert await ctx.spot.open_orders() == []


async def test_locked_funds_cannot_be_double_spent(ctx):
    await ctx.spot.place_order("BTCUSDT", "BUY", "LIMIT", quantity=9, price=90)  # locks 810
    with pytest.raises(BrokerError, match="insufficient"):
        await ctx.spot.place_order("BTCUSDT", "BUY", "MARKET", quote_amount=300)


async def test_limit_sell_rests_and_fills(ctx, fake):
    await ctx.spot.place_order("BTCUSDT", "BUY", "MARKET", quote_amount=500)
    btc = (await ctx.spot.balances())["BTC"]["free"]
    o = await ctx.spot.place_order("BTCUSDT", "SELL", "LIMIT", quantity=btc, price=120)
    assert (await ctx.spot.balances())["BTC"]["free"] == pytest.approx(0, abs=1e-6)
    fake.set_price("BTCUSDT", 125)
    await ctx.spot.balances()
    assert (await ctx.spot.get_order(o["clientOrderId"]))["status"] == "FILLED"


async def test_tmn_market_integer_price(ctx):
    o = await ctx.spot.place_order("BTCTMN", "BUY", "LIMIT", quote_amount=1_000_000, price=29_000_000.7)
    assert o["price"] == 29_000_000


# ================================================================== futures
async def test_long_lifecycle_pnl_and_fees(ctx, fake):
    pos = await ctx.futures.open_position("BTCUSDT", "LONG", leverage=5, margin=100)
    entry = pos["entry_price"]
    acct = await ctx.futures.account()
    fee = 500 * TAKER_FEE
    assert acct["wallets"]["USDT"]["available"] == pytest.approx(1000 - 100 - fee)
    fake.set_price("BTCUSDT", 110)
    [p] = await ctx.futures.positions()
    assert p["unrealized_pnl"] == pytest.approx((110 - entry) * p["quantity"])
    ev = await ctx.futures.close_position(pos["id"])
    assert ev["realized_pnl"] > 40
    assert (await ctx.futures.positions()) == []


async def test_short_profits_when_price_falls(ctx, fake):
    pos = await ctx.futures.open_position("BTCUSDT", "SHORT", leverage=2, margin=100)
    fake.set_price("BTCUSDT", 90)
    [p] = await ctx.futures.positions()
    assert p["unrealized_pnl"] > 0 and p["roe_percent"] > 0


async def test_take_profit_and_stop_loss_trigger_on_tick(ctx, fake):
    a = await ctx.futures.open_position("BTCUSDT", "LONG", 3, margin=50, take_profit=105, stop_loss=95)
    b = await ctx.futures.open_position("ETHUSDT", "SHORT", 3, margin=50, take_profit=9, stop_loss=11)
    fake.set_price("BTCUSDT", 106)
    fake.set_price("ETHUSDT", 11.5)
    events = await ctx.futures.tick()
    reasons = {e["position_id"]: e["reason"] for e in events}
    assert reasons == {a["id"]: "TAKE_PROFIT", b["id"]: "STOP_LOSS"}


async def test_liquidation_loses_exactly_the_margin(ctx, fake):
    await ctx.futures.open_position("BTCUSDT", "LONG", 10, margin=100)
    before = (await ctx.futures.account())["wallets"]["USDT"]["available"]
    fake.set_price("BTCUSDT", 80)
    [ev] = await ctx.futures.tick()
    assert ev["reason"] == "LIQUIDATION" and ev["realized_pnl"] == pytest.approx(-100)
    after = (await ctx.futures.account())["wallets"]["USDT"]["available"]
    assert after == pytest.approx(before)


async def test_partial_close_scales_quantity_and_margin(ctx):
    pos = await ctx.futures.open_position("BTCUSDT", "LONG", 4, margin=100)
    await ctx.futures.close_position(pos["id"], 0.25)
    [p] = await ctx.futures.positions()
    assert p["quantity"] == pytest.approx(pos["quantity"] * 0.75)
    assert p["margin"] == pytest.approx(75)


async def test_limit_order_reserves_fills_and_cancel_refunds(ctx, fake):
    o = await ctx.futures.open_position("BTCUSDT", "LONG", 5, margin=100, order_type="LIMIT", price=95)
    assert (await ctx.futures.account())["wallets"]["USDT"]["available"] == pytest.approx(1000 - o["reserved"])
    o2 = await ctx.futures.open_position("BTCUSDT", "LONG", 5, margin=50, order_type="LIMIT", price=50)
    await ctx.futures.cancel_order(o2["id"])
    fake.set_price("BTCUSDT", 94)
    [ev] = await ctx.futures.tick()
    assert ev["type"] == "LIMIT_FILLED"
    [p] = await ctx.futures.positions()
    assert p["entry_price"] == 95
    assert (await ctx.futures.account())["wallets"]["USDT"]["available"] == pytest.approx(1000 - 100 - 500 * TAKER_FEE)


async def test_rejects_non_leverage_market_and_excess_leverage(ctx):
    with pytest.raises(BrokerError, match="not a leverage-enabled"):
        await ctx.futures.open_position("XYZUSDT", "LONG", 2, margin=10)
    with pytest.raises(BrokerError, match="leverage must be between"):
        await ctx.futures.open_position("ETHUSDT", "LONG", 8, margin=10)  # ETH max is 5x


async def test_set_tp_sl_rejects_values_that_would_fire_immediately(ctx):
    pos = await ctx.futures.open_position("BTCUSDT", "LONG", 5, margin=100)
    with pytest.raises(BrokerError):
        await ctx.futures.set_tp_sl(pos["id"], take_profit=90, stop_loss=None)   # TP below price on a long
    with pytest.raises(BrokerError):
        await ctx.futures.set_tp_sl(pos["id"], take_profit=None, stop_loss=101)  # SL above price on a long
    liq = liquidation_price("LONG", pos["entry_price"], 5)
    with pytest.raises(BrokerError):
        await ctx.futures.set_tp_sl(pos["id"], take_profit=None, stop_loss=liq - 1)
    p = await ctx.futures.set_tp_sl(pos["id"], take_profit=120, stop_loss=95)
    assert (p["take_profit"], p["stop_loss"]) == (120, 95)
    assert await ctx.futures.tick() == []


async def test_max_open_positions_counts_pending_limit_orders(tmp_path, fake):
    from conftest import make_ctx, make_settings
    c = make_ctx(make_settings(tmp_path, max_open_positions=2), fake)
    await c.futures.open_position("BTCUSDT", "LONG", 2, margin=10)
    await c.futures.open_position("BTCUSDT", "LONG", 2, margin=10, order_type="LIMIT", price=50)
    with pytest.raises(BrokerError, match="max open positions"):
        await c.futures.open_position("ETHUSDT", "LONG", 2, margin=10)


async def test_state_survives_restart(tmp_path, fake):
    from conftest import make_ctx, make_settings
    s = make_settings(tmp_path)
    c1 = make_ctx(s, fake)
    await c1.spot.place_order("BTCUSDT", "BUY", "MARKET", quote_amount=100)
    await c1.futures.open_position("BTCUSDT", "SHORT", 2, margin=20)
    c2 = make_ctx(s, fake)
    assert (await c2.spot.balances())["BTC"]["total"] > 0
    assert len(await c2.futures.positions()) == 1


async def test_wallex_error_with_list_error_code_keeps_the_real_reason():
    import httpx
    from wallex_agent.wallex import WallexClient, WallexError

    def handler(req):
        return httpx.Response(422, json={"success": False, "message": "خطا", "result": {"error_code": [1006]}})

    c = WallexClient(api_key="k")
    c._http = httpx.AsyncClient(base_url="https://x", transport=httpx.MockTransport(handler))
    with pytest.raises(WallexError, match="insufficient balance"):
        await c.place_order("USDTTMN", "BUY", "MARKET", "18.3")


async def test_order_requests_are_not_repeated_when_the_reply_is_lost(monkeypatch):
    """A POST that timed out or got a 502/504 may already have placed the order: sending it again could
    buy twice. Reads still retry, and so does a POST that never connected or was refused with 429."""
    import httpx
    from wallex_agent.wallex import WallexClient, WallexError
    from wallex_agent.wallex import client as client_mod

    async def no_sleep(_):
        pass

    monkeypatch.setattr(client_mod.asyncio, "sleep", no_sleep)
    ok = httpx.Response(200, json={"success": True, "result": {"clientOrderId": "c1", "status": "NEW"}})

    def mocked(first):
        calls = []

        def handler(req):
            calls.append(req.method)
            if len(calls) > 1:
                return ok
            if isinstance(first, Exception):
                raise first
            return httpx.Response(first, json={"success": False, "message": "busy"})

        c = WallexClient(api_key="k")
        c._http = httpx.AsyncClient(base_url="https://x", transport=httpx.MockTransport(handler))
        return c, calls

    for first in (httpx.ReadTimeout("lost reply"), 502, 504):
        c, calls = mocked(first)
        with pytest.raises(WallexError):
            await c.place_order("USDTTMN", "BUY", "MARKET", "1.8")
        assert calls == ["POST"]
        c, calls = mocked(first)
        with pytest.raises(WallexError):
            await c.cancel_order("c1")
        assert calls == ["DELETE"]
        c, calls = mocked(first)
        assert (await c.get_order("c1"))["clientOrderId"] == "c1" and calls == ["GET", "GET"]

    for first in (httpx.ConnectError("no route"), 429):
        c, calls = mocked(first)
        assert (await c.place_order("USDTTMN", "BUY", "MARKET", "1.8"))["clientOrderId"] == "c1"
        assert calls == ["POST", "POST"]


async def test_live_whole_balance_buy_goes_as_limit_at_best_ask(fake):
    """Wallex rejected a 4,975,067-of-5,000,000 MARKET buy (1006): it reserves above the ask. A LIMIT at
    the best ask reserves exactly qty x price and still fills at once."""
    from wallex_agent.brokers import LiveSpotBroker
    sent = {}

    async def place_order(symbol, side, type, quantity, price=None, client_id=None):
        sent.update(symbol=symbol, type=type, quantity=float(quantity), price=float(price))
        return {"status": "FILLED"}

    fake.place_order = place_order
    await LiveSpotBroker(fake).place_order("BTCTMN", "BUY", "MARKET", quote_amount=5_000_000)
    best_ask = min(float(lv["price"]) for lv in (await fake.depth("BTCTMN"))["ask"])
    assert sent["type"] == "LIMIT" and best_ask <= sent["price"] < best_ask + 1   # marketable, one tick at most
    assert sent["quantity"] * sent["price"] * 1.003 < 5_000_000      # fits even after a 0.3% taker fee


async def test_detects_a_main_account_key():
    """A credit-account key sees its 5M without the sub-account header; a main-account key sees 0
    there (both can list /sub-accounts, so that is not a test)."""
    import httpx
    from wallex_agent.wallex import WallexClient

    def own_balance(tmn):
        def handler(req):
            return httpx.Response(200, json={"success": True, "result": {"balances": {"TMN": {"value": tmn}}}})
        return handler

    for handler, expected in ((own_balance("0"), True), (own_balance("5000000"), False)):
        c = WallexClient(api_key="k", sub_account="X-PROP-1")
        c._http = httpx.AsyncClient(base_url="https://x", transport=httpx.MockTransport(handler))
        assert await c.key_is_main_account() is expected


async def test_live_order_requests_match_wallex_docs():
    """Shapes of every live order call, against a mocked Wallex: place (market/limit, buy/sell),
    status, cancel (client id in the PATH, per the docs) - all with the credit sub-account header."""
    import json as _json
    import httpx
    from wallex_agent.wallex import WallexClient

    calls = []

    def handler(req):
        calls.append((req.method, req.url.path, dict(req.url.params), _json.loads(req.content or b"{}"),
                      req.headers.get("sub-account-client-id")))
        return httpx.Response(200, json={"success": True, "result": {"clientOrderId": "c1", "status": "NEW"}})

    c = WallexClient(api_key="k", sub_account="SUB")
    c._http = httpx.AsyncClient(base_url="https://x", transport=httpx.MockTransport(handler))
    await c.place_order("USDTTMN", "buy", "limit", "1.8", price="272000")
    await c.place_order("USDTTMN", "sell", "market", "1.8")
    await c.get_order("c1")
    await c.cancel_order("c1")
    (m1, p1, _, b1, h1), (m2, p2, _, b2, _), (m3, p3, _, _, _), (m4, p4, q4, _, h4) = calls
    assert (m1, p1, b1) == ("POST", "/v1/account/orders",
                            {"symbol": "USDTTMN", "side": "BUY", "type": "LIMIT", "quantity": "1.8", "price": "272000"})
    assert b2 == {"symbol": "USDTTMN", "side": "SELL", "type": "MARKET", "quantity": "1.8"}   # no price on MARKET
    assert (m3, p3) == ("GET", "/v1/account/orders/c1")
    assert (m4, p4, q4) == ("DELETE", "/v1/account/orders/c1", {})
    assert h1 == h4 == "SUB"


async def test_live_market_sell_refused_goes_as_limit_through_the_book(fake):
    """Seen live 2026-10-23 on USOONUSDT: «امکان ثبت سفارش قیمت بازار در USOONUSDT بازار وجود ندارد». The same sell
    goes as a LIMIT at the worst bid level it needs, so it still fills at once."""
    from wallex_agent.brokers import LiveSpotBroker
    from wallex_agent.wallex import WallexError
    sent = []

    async def place_order(symbol, side, type, quantity, price=None, client_id=None):
        sent.append((type, float(quantity), price and float(price)))
        if type == "MARKET":
            raise WallexError('اطلاعات وارد شده اشتباه‌است.: {"symbol": ["امکان ثبت سفارش قیمت بازار در BTCUSDT بازار وجود ندارد."]}')
        return {"status": "FILLED", "clientOrderId": "x"}

    fake.place_order = place_order
    await LiveSpotBroker(fake).place_order("BTCUSDT", "SELL", "MARKET", quantity=7.0)   # 5 per level: needs two
    bids = sorted((float(lv["price"]) for lv in (await fake.depth("BTCUSDT"))["bid"]), reverse=True)
    assert [s[0] for s in sent] == ["MARKET", "LIMIT"] and sent[1][1] == 7.0
    assert bids[1] - 0.01 < sent[1][2] <= bids[1]                  # the second level, rounded down to the tick


async def test_live_other_rejections_are_not_retried_as_limit(fake):
    from wallex_agent.brokers import BrokerError, LiveSpotBroker
    from wallex_agent.wallex import WallexError
    calls = []

    async def place_order(symbol, side, type, quantity, price=None, client_id=None):
        calls.append(type)
        raise WallexError("insufficient balance")

    fake.place_order = place_order
    with pytest.raises(BrokerError, match="insufficient"):
        await LiveSpotBroker(fake).place_order("BTCUSDT", "SELL", "MARKET", quantity=1.0)
    assert calls == ["MARKET"]


async def test_an_order_left_open_after_settling_is_cancelled():
    """A sell placed as a limit through the book on a moving book can rest; it must not stay on the book."""
    from wallex_agent.engine.execution import SpotExec

    class Spot:
        def __init__(self):
            self.bal, self.canceled = {"OIL": 1.0, "USDT": 0.0}, []

        async def balances(self):
            return {k: {"free": v, "total": v} for k, v in self.bal.items()}

        async def place_order(self, symbol, side, type, quantity=None, price=None, quote_amount=None):
            self.bal["OIL"], self.bal["USDT"] = 0.4, 90.0    # 0.6 filled, the rest rests
            return {"clientOrderId": "o1", "status": "NEW", "active": True, "executedQty": "0.6"}

        async def get_order(self, oid):
            gone = "o1" in self.canceled
            return {"clientOrderId": oid, "status": "CANCELED" if gone else "NEW", "active": not gone, "executedQty": "0.6"}

        async def cancel_order(self, oid):
            self.canceled.append(oid)

    import wallex_agent.engine.execution as ex
    spot = Spot()
    orig = ex.asyncio.sleep
    async def no_sleep(_):
        return None
    ex.asyncio.sleep = no_sleep
    try:
        fill = await SpotExec(spot, quote="USDT").sell("OILUSDT", 1.0, "OIL")
    finally:
        ex.asyncio.sleep = orig
    assert spot.canceled == ["o1"] and fill.qty == 0.6 and fill.tmn == 90.0

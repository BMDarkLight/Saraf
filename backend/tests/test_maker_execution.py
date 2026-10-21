"""Maker (limit-order) execution: simulator fill rules, engine order lifecycle, restart safety."""

import json

import pytest

from conftest import FakeClient, make_settings
from test_engine import T0, ScriptedDecider, run, sim_feed
from wallex_agent.brokers import PaperSpotBroker
from wallex_agent.engine.core import Engine
from wallex_agent.engine.execution import SimExec, SpotExec
from wallex_agent.engine.features import Bar
from wallex_agent.engine.feeds import SimFeed
from wallex_agent.engine.journal import Journal


def path_feed(prices: list[tuple[float, float, float]], start=T0):
    """1m bars from (low, high, close) triples; open = previous close."""
    bars, prev = [], prices[0][2]
    for i, (lo, hi, c) in enumerate(prices):
        bars.append(Bar(start + i * 60, prev, hi, lo, c, 1.0))
        prev = c
    return SimFeed({"AAATMN": bars}, {"AAATMN": []}, 15, {"AAATMN": 0.002})


# ------------------------------------------------------------ SimExec rules
async def test_limit_buy_needs_trade_through_and_charges_maker_fee():
    feed = path_feed([(100, 101, 100.5), (100, 101, 100.2), (99.0, 100.4, 99.5)])   # 1m: touches 100, 2m: trades below
    ex = SimExec(feed, 10_000, fee_rate=0.0025, maker_fee_rate=0.001)
    feed.t = T0
    oid = await ex.place_limit("AAATMN", "BUY", 10, 100.0, "AAA")
    assert await ex.cash() == pytest.approx(9_000) and await ex.total_cash() == pytest.approx(10_000)
    feed.t = T0 + 120   # bars 0 and 1 closed: low == 100 only touches the limit
    assert (await ex.order_status(oid)).status == "open"
    feed.t = T0 + 180   # bar 2 traded through 100
    st = await ex.order_status(oid)
    assert st.status == "filled" and st.price == 100 and st.filled_qty == pytest.approx(10 * 0.999)
    assert ex.fees_paid == pytest.approx(1.0)


async def test_cancel_returns_reserved_funds_and_coins():
    feed = path_feed([(100, 101, 100.5)] * 3)
    ex = SimExec(feed, 10_000, 0.0025)
    feed.t = T0
    b = await ex.place_limit("AAATMN", "BUY", 10, 90.0, "AAA")
    st = await ex.cancel(b)
    assert st.status == "canceled" and await ex.cash() == pytest.approx(10_000)
    ex.holdings["AAA"] = 5
    s_ = await ex.place_limit("AAATMN", "SELL", 5, 200.0, "AAA")
    assert ex.holdings["AAA"] == 0
    await ex.cancel(s_)
    assert ex.holdings["AAA"] == 5


# ------------------------------------------------------- engine lifecycle
def make(tmp_path, feed, decider=None, **over):
    cfg = dict(engine_universe="AAATMN,BBBTMN", engine_min_score=0.0, engine_min_atr_pct=0.05,
               engine_execution="maker", engine_maker_fee_rate=0.001)
    s = make_settings(tmp_path, **{**cfg, **over})
    broker = SimExec(feed, s.engine_capital_tmn, s.engine_fee_rate, maker_fee_rate=s.maker_fee)
    return Engine(s, feed, broker, decider or ScriptedDecider(), Journal(tmp_path / "run")), broker


def events(tmp_path):
    return [json.loads(l) for l in (tmp_path / "run" / "decisions.jsonl").read_text().splitlines()]


async def test_maker_engine_full_lifecycle(tmp_path):
    feed = sim_feed()
    engine, broker = make(tmp_path, feed)
    await run(engine, feed, T0 + 86_400, T0 + 4 * 86_400 - 60)
    await engine.flatten(feed.t, "end")
    ev = events(tmp_path)
    kinds = {e["event"] for e in ev}
    assert {"entry_order", "entry"} <= kinds
    entries = [e for e in ev if e["event"] == "entry"]
    assert all(e["execution"] == "maker" and e["take_profit_order"] for e in entries)
    tps = [e for e in ev if e["event"] == "exit" and e["reason"] == "take_profit"]
    for e in tps:   # resting take-profit fills exactly at its limit
        entry = next(x for x in entries if x["position_id"] == e["position_id"])
        assert e["price"] == pytest.approx(entry["target"])
    # every entry order ends filled or cancelled; nothing left behind
    assert not engine.pending and not engine.positions
    assert all(o["status"] != "open" for o in broker.orders.values())
    assert not broker.holdings or all(v < 1e-9 for v in broker.holdings.values())


async def test_unfilled_entry_is_cancelled_and_cash_restored(tmp_path):
    feed = sim_feed()
    engine, broker = make(tmp_path, feed, engine_entry_timeout_bars=1)
    feed.t = T0 + 3 * 86_400   # enough history for the 1h indicators
    from wallex_agent.engine.strategy import Candidate
    from wallex_agent.engine.features import compute
    bars, htf = await feed.bars("AAATMN")
    f = compute(bars, htf)
    # a limit far below the market can never fill
    q = await feed.quote("AAATMN")
    feed_quote = feed.quote

    async def low_bid(sym):
        return {**(await feed_quote(sym)), "bid": q["bid"] * 0.8}
    feed.quote = low_bid
    await engine._consider(Candidate("AAATMN", "test", 0.9, f["close"], f["atr"], 2.0, 3.0, []), f, feed.t)
    feed.quote = feed_quote
    assert len(engine.pending) == 1 and await broker.cash() < engine.s.engine_capital_tmn
    for _ in range(16):
        feed.t += 60
        await engine.monitor()
    assert not engine.pending and not engine.positions
    assert await broker.cash() == pytest.approx(engine.s.engine_capital_tmn)
    assert any(e["event"] == "entry_cancelled" for e in events(tmp_path))


async def test_stop_cancels_take_profit_before_market_exit(tmp_path):
    feed = sim_feed(drift=-0.05)
    engine, broker = make(tmp_path, feed, engine_entry_chase_atr=99)   # keep entries alive so stops happen
    await run(engine, feed, T0 + 86_400, T0 + 4 * 86_400 - 60)
    stops = [e for e in events(tmp_path) if e["event"] == "exit" and e["reason"] in ("stop_loss", "trailing_stop")]
    assert stops, "expected stop exits in a falling market"
    # no take-profit order remains open for a closed position
    open_sells = [o for o in broker.orders.values() if o["side"] == "SELL" and o["status"] == "open"]
    assert len(open_sells) == len(engine.positions)


async def test_taker_mode_still_available(tmp_path):
    feed = sim_feed()
    engine, broker = make(tmp_path, feed, engine_execution="taker")
    await run(engine, feed, T0 + 86_400, T0 + 3 * 86_400)
    ev = events(tmp_path)
    assert not any(e["event"] == "entry_order" for e in ev)
    assert all(e["execution"] == "taker" for e in ev if e["event"] == "entry")


# ------------------------------------------------- shadow + restart safety
async def test_paper_broker_limit_round_trip_and_restart_reconcile(tmp_path):
    fake = FakeClient()
    s = make_settings(tmp_path, paper_tmn=5_000_000, paper_usdt=0, engine_execution="maker",
                      engine_universe="BTCTMN")
    spot = PaperSpotBroker(fake, s)
    ex = SpotExec(spot, fee_rate=0.002)
    oid = await ex.place_limit("BTCTMN", "BUY", 0.03, 29_000_000, "BTC")   # below the market: rests
    assert (await ex.order_status(oid)).status == "open"
    fake.set_price("BTCTMN", 28_000_000)
    st = await ex.order_status(oid)
    assert st.status == "filled" and st.filled_qty == pytest.approx(0.03 * 0.998)

    class LiveishFeed:
        def now(self):
            return T0

        async def quote(self, sym):
            p = fake.prices[sym]
            return {"bid": p, "ask": p, "last": p}

    state = tmp_path / "state.json"
    eng = Engine(s, LiveishFeed(), ex, ScriptedDecider(), Journal(tmp_path / "j"), state)
    await eng._open_position({"symbol": "BTCTMN", "base": "BTC", "setup": "t", "atr": 300_000, "rules": [],
                              "stop_atr": 2, "target_atr": 3, "confidence": 1, "reasoning": "t", "sizing": "t"},
                             st.filled_qty, st.price, st.tmn, T0)
    [p] = eng.positions.values()
    assert p["tp_order"] and (await ex.holding("BTC")) < 1e-9   # coins are locked in the take-profit
    restarted = Engine(s, LiveishFeed(), SpotExec(spot, 0.002), ScriptedDecider(), Journal(tmp_path / "j"), state)
    await restarted.reconcile()
    assert len(restarted.positions) == 1   # not dropped just because the coins are locked


async def test_spread_alone_does_not_cancel_the_entry(tmp_path):
    """Regression (found in shadow testing): a wide spread made every entry look like it 'ran away'."""
    feed = sim_feed()
    engine, broker = make(tmp_path, feed, engine_entry_chase_atr=0.5, engine_entry_timeout_bars=4)
    feed.t = T0 + 3 * 86_400
    from wallex_agent.engine.strategy import Candidate
    from wallex_agent.engine.features import compute
    bars, htf = await feed.bars("AAATMN")
    f = compute(bars, htf)
    feed.spreads["AAATMN"] = 3 * f["atr"] / f["close"]          # spread = 3 ATR, far wider than the chase limit
    await engine._consider(Candidate("AAATMN", "test", 0.9, f["close"], f["atr"], 2.0, 3.0, []), f, feed.t)
    assert len(engine.pending) == 1
    feed.t += 60
    await engine.monitor()
    assert not any(e["event"] == "entry_cancelled" and "ran away" in e["reason"] for e in events(tmp_path))


async def test_fill_between_checks_does_not_fake_a_drawdown(tmp_path):
    """Regression (found in shadow testing): an entry that filled before the engine recorded it made
    equity look 25% lower and tripped the drawdown halt."""
    fake = FakeClient()
    s = make_settings(tmp_path, paper_tmn=5_000_000, paper_usdt=0, engine_execution="maker",
                      engine_universe="BTCTMN", engine_capital_tmn=5_000_000)
    spot = PaperSpotBroker(fake, s)
    ex = SpotExec(spot, 0.002)

    class Feed:
        def now(self):
            return T0

        async def quote(self, sym):
            p = fake.prices[sym]
            return {"bid": p, "ask": p, "last": p}

        async def bar_range(self, sym):
            p = fake.prices[sym]
            return {"low": p, "high": p, "open": p}

    eng = Engine(s, Feed(), ex, ScriptedDecider(), Journal(tmp_path / "j"))
    oid = await ex.place_limit("BTCTMN", "BUY", 0.04, 29_000_000, "BTC")
    eng.pending[oid] = {"symbol": "BTCTMN", "base": "BTC", "setup": "t", "atr": 300_000, "rules": [], "stop_atr": 2,
                        "target_atr": 3, "confidence": 1, "reasoning": "t", "sizing": "t", "limit": 29_000_000,
                        "ask_at_order": 29_000_000, "placed_at": T0, "expires_at": T0 + 900}
    fake.set_price("BTCTMN", 28_900_000)          # fills inside the paper broker before the engine looks
    eq, _, _ = await eng.equity()
    assert eq > 4_900_000                           # the coins are valued even before they are recorded
    await eng.monitor()
    assert not eng.state["halted"] and len(eng.positions) == 1

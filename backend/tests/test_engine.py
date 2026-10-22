"""Engine tests on synthetic, seeded market data (no network, no LLM)."""

import json
import math
import random

import pytest

from conftest import FakeClient, make_settings
from test_agent_loop import FakeLLM, chunk
from wallex_agent.brokers import PaperSpotBroker
from wallex_agent.engine import strategy
from wallex_agent.engine.core import Engine
from wallex_agent.engine.decider import AIDecider, Decision, ResponseCache, RuleDecider
from wallex_agent.engine.execution import SimExec, SpotExec
from wallex_agent.engine.features import Bar, closed, compute, resample
from wallex_agent.engine.feeds import LiveFeed, SimFeed
from wallex_agent.engine.journal import Journal
from wallex_agent.engine.metrics import compute_metrics

T0 = 1_790_000_000 - 1_790_000_000 % 86_400


def synth(seed=7, days=4, start=100.0, drift=0.0, crash_at=None):
    """1m bars: noisy sine waves (mean-reverting chop) with an optional sharp dip."""
    rnd = random.Random(seed)
    bars, p = [], start
    for i in range(days * 1440):
        t = T0 + i * 60
        target = start * (1 + 0.03 * math.sin(i / 300) + drift * i / 1440)
        if crash_at and crash_at <= i < crash_at + 45:
            target *= 0.94
        p += (target - p) * 0.05 + rnd.gauss(0, start * 0.0012)
        o, c = p, p + rnd.gauss(0, start * 0.0006)
        bars.append(Bar(t, o, max(o, c) * 1.0008, min(o, c) * 0.9992, c, 10 + rnd.random() * 5))
    return bars


def hourly(m1):
    return resample(m1, 60)


def sim_feed(**kw):
    m1 = {"AAATMN": synth(**kw), "BBBTMN": synth(seed=11)}
    h1 = {s: hourly(b) for s, b in m1.items()}
    return SimFeed(m1, h1, 15, {s: 0.002 for s in m1})


class ScriptedDecider:
    name = "scripted"

    def __init__(self, action="TAKE", regime="neutral", review="HOLD", size=1.0):
        self.action, self.regime, self.review, self.size = action, regime, review, size
        self.seen = []
        self.calls = 0

    async def decide_entry(self, cand, ctx):
        self.seen.append((cand, ctx))
        return Decision({"action": self.action, "confidence": 0.8, "size_fraction": self.size, "stop_atr": 2.0,
                         "target_atr": 3.0, "reasoning": "scripted"}, "rules")

    async def review_position(self, position, ctx):
        return Decision({"action": self.review, "new_stop": None, "reasoning": "scripted review"}, "rules")

    async def assess_regime(self, ctx):
        return Decision({"regime": self.regime, "reasoning": "scripted regime"}, "rules")


async def run(engine, feed, start, end):
    t = start
    while t <= end:
        feed.t = t
        await engine.monitor()
        if t % (15 * 60) == 0:
            await engine.on_bar()
        t += 60


def make(tmp_path, feed, decider, **over):
    s = make_settings(tmp_path, engine_universe="AAATMN,BBBTMN", engine_min_score=0.0, engine_min_atr_pct=0.05,
                      **over)
    broker = SimExec(feed, s.engine_capital_tmn, s.engine_fee_rate)
    return Engine(s, feed, broker, decider, Journal(tmp_path / "run")), broker


# ----------------------------------------------------------------- features
def test_resample_and_closed_bars():
    m1 = synth(days=1)
    b15 = resample(m1, 15)
    assert len(b15) == 96 and b15[1].t - b15[0].t == 900
    assert b15[0].o == m1[0].o and b15[0].c == m1[14].c and b15[0].h == max(b.h for b in m1[:15])
    assert len(closed(b15, 15, T0 + 900 * 3 + 30)) == 3   # the 4th bar is still open


def test_no_lookahead_in_sim_feed():
    feed = sim_feed()
    feed.t = T0 + 2 * 86_400 + 600   # 10 minutes into a 15m bar
    import asyncio
    bars, htf = asyncio.run(feed.bars("AAATMN"))
    assert bars[-1].t + 900 <= feed.t and htf[-1].t + 3600 <= feed.t
    assert asyncio.run(feed.quote("AAATMN"))["last"] == feed.m1["AAATMN"][2 * 1440 + 9].c


def test_oversold_setup_fires_on_a_crash():
    m1 = synth(crash_at=3 * 1440 + 300)   # day 3: enough history for the 1h indicators
    i = 3 * 1440 + 340
    bars = closed(resample(m1[:i], 15), 15, m1[i].t)
    f = compute(bars, closed(hourly(m1[:i]), 60, m1[i].t))
    cands = strategy.candidates("AAATMN", f, 0.05, 10)
    assert any(c.setup == "oversold_reversion" for c in cands), f
    assert all(c.rules for c in cands)


# -------------------------------------------------------------------- engine
async def test_engine_trades_within_risk_limits_and_logs_reasons(tmp_path):
    feed = sim_feed()
    engine, broker = make(tmp_path, feed, ScriptedDecider())
    await run(engine, feed, T0 + 86_400, T0 + 4 * 86_400 - 60)
    await engine.flatten(feed.t, "end")
    events = [json.loads(l) for l in (tmp_path / "run" / "decisions.jsonl").read_text().splitlines()]
    entries = [e for e in events if e["event"] == "entry"]
    exits = [e for e in events if e["event"] == "exit"]
    assert entries and len(exits) == len(entries)
    cap = engine.s.engine_capital_tmn
    for e in entries:
        assert e["cost_tmn"] <= cap * engine.s.engine_max_position_pct * 1.01   # per-position cap
        assert e["stop"] < e["price"] < e["target"] and e["reason"] and e["sizing"]
    assert all(e["reason"] for e in exits)
    assert not broker.holdings or all(v < 1e-9 for v in broker.holdings.values())   # all back in TMN
    m = compute_metrics(tmp_path / "run", cap)
    assert m["trades"] == len(exits)


async def test_risk_off_regime_and_skip_open_nothing(tmp_path):
    for decider in (ScriptedDecider(regime="risk_off"), ScriptedDecider(action="SKIP")):
        feed = sim_feed()
        engine, broker = make(tmp_path / decider.regime / decider.action, feed, decider)
        await run(engine, feed, T0 + 86_400, T0 + 3 * 86_400)
        assert broker.holdings == {} and await broker.cash() == engine.s.engine_capital_tmn


async def test_drawdown_breaker_halts_and_flattens(tmp_path):
    feed = sim_feed(drift=-0.03)   # steady 3%/day decline
    engine, broker = make(tmp_path, feed, ScriptedDecider(), engine_drawdown_stop_pct=0.01,
                          engine_drawdown_pause_pct=0.005, engine_max_position_pct=0.5,
                          engine_risk_per_trade_pct=0.05)
    await run(engine, feed, T0 + 86_400, T0 + 4 * 86_400 - 60)
    events = [json.loads(l) for l in (tmp_path / "run" / "decisions.jsonl").read_text().splitlines()]
    assert any(e["event"] == "circuit_breaker" for e in events)
    if engine.state["halted"]:
        assert not engine.positions
        halt_ts = next(e["ts"] for e in events if e["event"] == "circuit_breaker" and "halt" in e["reason"])
        assert not any(e["event"] == "entry" and e["ts"] > halt_ts for e in events)


async def test_challenge_end_flattens_to_toman(tmp_path):
    feed = sim_feed()
    end_ts = T0 + 3 * 86_400
    from datetime import datetime, timezone
    engine, broker = make(tmp_path, feed, ScriptedDecider(),
                          engine_end_time=datetime.fromtimestamp(end_ts, timezone.utc).isoformat())
    await run(engine, feed, T0 + 86_400, end_ts)
    assert engine.state["flattened"] and not engine.positions
    events = [json.loads(l) for l in (tmp_path / "run" / "decisions.jsonl").read_text().splitlines()]
    last_entry = max((e["ts"] for e in events if e["event"] == "entry"), default=0)
    assert last_entry <= end_ts - engine.s.engine_no_entry_before_end_min * 60


async def test_backtest_is_deterministic(tmp_path):
    results = []
    for i in range(2):
        feed = sim_feed()
        engine, broker = make(tmp_path / str(i), feed, RuleDecider(0.0))
        await run(engine, feed, T0 + 86_400, T0 + 4 * 86_400 - 60)
        await engine.flatten(feed.t, "end")
        results.append((round(await broker.cash(), 6), (tmp_path / str(i) / "run" / "trades.csv").read_text()))
    assert results[0] == results[1]


# --------------------------------------------------------------------- AI
async def test_ai_decider_parses_caches_and_replays(tmp_path):
    reply = 'Sure! ```json\n{"action": "TAKE", "confidence": 0.7, "size_fraction": 0.5, "stop_atr": 2, ' \
            '"target_atr": 3.5, "reasoning": "clean dip"}\n```'
    llm = FakeLLM([[chunk(reply)]])

    async def create(**kw):   # non-streaming fake
        from types import SimpleNamespace as NS
        return NS(choices=[NS(message=NS(content=reply))])
    llm.chat.completions.create = create
    cache = ResponseCache(tmp_path / "cache.jsonl")
    ai = AIDecider(llm, "m", cache, 0.0025)
    cand = strategy.Candidate("AAATMN", "oversold_reversion", 0.7, 100, 1, 2.5, 3.5, ["r"])
    d1 = await ai.decide_entry(cand, {"x": 1})
    assert d1.source == "llm" and d1.payload["action"] == "TAKE" and d1.payload["size_fraction"] == 0.5
    replay = AIDecider(llm, "m", ResponseCache(tmp_path / "cache.jsonl"), 0.0025, offline=True)
    d2 = await replay.decide_entry(cand, {"x": 1})
    assert d2.source == "cache" and d2.payload == d1.payload and replay.calls == 0
    d3 = await replay.decide_entry(cand, {"x": 2})   # unseen prompt, offline: fail-safe
    assert d3.source == "fallback" and d3.payload["action"] == "SKIP"


async def test_ai_decider_fails_safe_on_garbage_and_errors(tmp_path):
    from types import SimpleNamespace as NS
    outputs = iter(["I think you should buy!", '{"action": "TAKE", "confidence": 5}'])

    async def create(**kw):
        return NS(choices=[NS(message=NS(content=next(outputs)))])

    async def boom(**kw):
        raise TimeoutError("slow")
    llm = NS(chat=NS(completions=NS(create=create)))
    ai = AIDecider(llm, "m", ResponseCache(tmp_path / "c.jsonl"), 0.0025)
    cand = strategy.Candidate("AAATMN", "oversold_reversion", 0.7, 100, 1, 2.5, 3.5, [])
    for i in range(2):
        d = await ai.decide_entry(cand, {"i": i})
        assert d.payload["action"] == "SKIP" and d.source == "fallback" and d.error
    llm.chat.completions.create = boom
    r = await ai.review_position({"symbol": "AAATMN"}, {})
    assert r.payload["action"] == "HOLD" and "fail-safe" in r.payload["reasoning"]


# ----------------------------------------------------------------- shadow
async def test_spot_exec_round_trip_on_paper_broker(tmp_path):
    fake = FakeClient()
    s = make_settings(tmp_path, paper_tmn=5_000_000, paper_usdt=0)
    ex = SpotExec(PaperSpotBroker(fake, s))
    buy = await ex.buy("BTCTMN", 1_000_000, "BTC")
    assert buy.qty > 0 and 999_000 < buy.tmn <= 1_000_000
    sell = await ex.sell("BTCTMN", buy.qty, "BTC")
    assert 0.99 < sell.tmn / buy.tmn < 1.0     # lost only fees + spread
    assert await ex.holding("BTC") < 1e-6


async def test_ai_skip_with_zero_stop_is_a_real_decision(tmp_path):
    """Regression (Qwen on GapGPT): SKIPs came back with stop_atr 0 and were discarded as invalid,
    so the fail-safe - not the model - made 36 of 38 decisions."""
    from types import SimpleNamespace as NS
    reply = '{"action": "skip", "confidence": 0.85, "size_fraction": 0.0, "stop_atr": 0.0, "target_atr": 0.0, ' \
            '"reasoning": "15m EMAs sloping down"}'

    async def create(**kw):
        assert kw.get("extra_body") == {"reasoning_effort": "none"}
        return NS(choices=[NS(message=NS(content=reply))])
    ai = AIDecider(NS(chat=NS(completions=NS(create=create))), "m", ResponseCache(tmp_path / "c.jsonl"), 0.001,
                   extra_body={"reasoning_effort": "none"})
    cand = strategy.Candidate("AAATMN", "oversold_reversion", 0.7, 100, 1, 2.5, 3.5, [])
    d = await ai.decide_entry(cand, {})
    assert d.source == "llm" and d.payload["action"] == "SKIP" and d.payload["confidence"] == 0.85


async def test_universe_skips_credit_blocked_markets(tmp_path):
    class Markets:
        async def markets(self):
            st = {"bidPrice": "100", "askPrice": "100.1", "24h_quoteVolume": "1"}
            return {"BTCTMN": {"quoteAsset": "TMN", "stats": {**st, "24h_quoteVolume": "9"}},
                    "TONTMN": {"quoteAsset": "TMN", "stats": {**st, "24h_quoteVolume": "8"}},   # blocked on credit
                    "ETHTMN": {"quoteAsset": "TMN", "stats": st}}

    feed = LiveFeed(Markets(), make_settings(tmp_path, engine_universe_size=2))
    assert await feed.universe() == ["BTCTMN", "ETHTMN"]
    feed = LiveFeed(Markets(), make_settings(tmp_path, engine_universe_size=2, engine_blocked_markets=""))
    assert await feed.universe() == ["BTCTMN", "TONTMN"]


async def test_client_sends_sub_account_header_on_auth_calls():
    import httpx
    from wallex_agent.wallex import WallexClient

    seen = []

    def handler(req):
        seen.append(dict(req.headers))
        return httpx.Response(200, json={"success": True, "result": {"balances": {}}})

    c = WallexClient(api_key="k", sub_account="SUB-1")
    c._http = httpx.AsyncClient(base_url="https://x", transport=httpx.MockTransport(handler))
    await c.balances()
    assert seen[0]["x-api-key"] == "k" and seen[0]["sub-account-client-id"] == "SUB-1"


async def test_equity_counts_coins_the_engine_did_not_buy(tmp_path):
    """Converting TMN by hand (chat assistant, same account) must not look like a crash to the drawdown stop."""
    from types import SimpleNamespace as NS
    fake = FakeClient()
    s = make_settings(tmp_path, paper_tmn=5_000_000, paper_usdt=0)
    ex = SpotExec(PaperSpotBroker(fake, s))
    await ex.spot.place_order("BTCTMN", "BUY", "MARKET", quote_amount=4_000_000)   # not an engine position

    class Feed:
        async def quote(self, symbol):
            bid, ask = fake._bid_ask(symbol)
            return {"bid": bid, "ask": ask, "last": fake.prices[symbol]}

    eq, cash, exposure = await Engine.equity(NS(broker=ex, feed=Feed(), positions={}, pending={}))
    assert cash < 1_100_000
    assert eq > 4_950_000          # only fees and half the spread are gone, not 80% of the account

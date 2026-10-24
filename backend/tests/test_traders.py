"""Live trader personas: following real Hyperliquid traders' public positions with a simulated copy on Wallex."""

import csv
import json

import httpx
import pytest

from saraf import traders
from saraf.traders import Follower, Hyperliquid, describe_changes, pick_leaders, target_weights, wallex_base

PRICES = {"BTCUSDT": {"bid": 99.9, "ask": 100.1}, "ETHUSDT": {"bid": 9.99, "ask": 10.01},
          "PEPEUSDT": {"bid": 0.00001, "ask": 0.0000101}}


def pos(*ps, account=1_000_000.0):
    return {"account_usd": account,
            "positions": [{"coin": c, "side": side, "size": size, "usd": usd, "entry": 1.0, "upnl": 0.0, "leverage": 5}
                          for c, side, size, usd in ps]}


def test_coin_names_map_to_wallex_bases():
    assert wallex_base("BTC") == "BTC" and wallex_base("kPEPE") == "PEPE" and wallex_base("@107") is None


def test_only_longs_on_wallex_markets_are_copied_and_leverage_is_dropped():
    w, skipped = target_weights(pos(("BTC", "long", 1, 300_000), ("ETH", "short", 1, 200_000), ("HYPE", "long", 1, 100_000),
                                    ("kPEPE", "long", 1, 100_000)), set(PRICES))
    assert w == {"BTCUSDT": 0.3, "PEPEUSDT": 0.1}
    assert skipped == ["ETH short", "HYPE not on Wallex"]
    w, _ = target_weights(pos(("BTC", "long", 1, 3_000_000), ("ETH", "long", 1, 1_000_000)), set(PRICES))   # 4x levered
    assert w == pytest.approx({"BTCUSDT": 0.75, "ETHUSDT": 0.25})


def test_trader_actions_are_described():
    old = pos(("BTC", "long", 1.0, 100), ("ETH", "short", 10, 100), ("SOL", "long", 5, 50))["positions"]
    new = pos(("BTC", "long", 2.0, 200), ("ETH", "long", 10, 100), ("DOGE", "short", 1, 10))["positions"]
    acts = {(a["coin"], a["action"], a["side"]) for a in describe_changes(old, new)}
    assert acts == {("BTC", "added", "long"), ("ETH", "flipped", "long"), ("SOL", "closed", "long"),
                    ("DOGE", "opened", "short")}


def test_leaders_are_consistently_profitable_large_accounts():
    rows = [{"address": f"0x{i}", "account_usd": acct, "pnl_week": wk, "pnl_month": mo, "pnl_allTime": at}
            for i, (acct, wk, mo, at) in enumerate([(5e6, 1, 9, 1), (5e6, -1, 20, 1), (1e6, 1, 30, 1), (9e6, 1, 5, -1),
                                                    (3e6, 1, 7, 1)])]
    assert [r["address"] for r in pick_leaders(rows, n=3)] == ["0x0", "0x4"]


async def test_leaderboard_is_read_as_a_stream():
    """The real leaderboard is ~40 MB; rows split across chunks must still parse, small accounts are dropped."""
    def row(i, acct, name=None):
        return {"ethAddress": f"0x{i:040x}", "accountValue": str(acct), "displayName": name, "prize": 0,
                "windowPerformances": [["day", {"pnl": "1", "roi": "0.1", "vlm": "5"}],
                                       ["week", {"pnl": "2", "roi": "0.1", "vlm": "5"}],
                                       ["month", {"pnl": "3", "roi": "0.2", "vlm": "5"}],
                                       ["allTime", {"pnl": "4", "roi": "0.3", "vlm": "5"}]]}
    rows = [row(1, 5e6, "Big"), row(2, 10), row(3, 3e6)]
    rows[2] = dict(reversed(list(rows[2].items())))   # the live feed's key order is not ethAddress-first
    body = json.dumps({"leaderboardRows": rows}, indent=1).encode()

    class Chunked(httpx.AsyncByteStream):
        async def __aiter__(self):
            for i in range(0, len(body), 37):   # odd chunk size: rows always straddle chunks
                yield body[i:i + 37]

    hl = Hyperliquid()
    hl.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=Chunked())))
    rows = await hl.leaderboard(min_account=2e6)
    assert [(r["address"][-1], r["name"], r["pnl_month"], r["account_usd"]) for r in rows] == [
        ("1", "Big", 3.0, 5e6), ("3", None, 3.0, 3e6)]
    await hl.aclose()


def test_follower_copies_longs_closes_with_the_trader_and_logs_what_it_did(tmp_path):
    f = Follower({"address": "0x" + "ab" * 20, "name": "Whale", "source": "leaderboard"}, tmp_path / "run")
    f.start(rate_ask=270_000, now=1000)
    cash0 = f.state["cash_usdt"]
    assert cash0 == pytest.approx(5_000_000 / 270_000 * 0.997)
    f.step(pos(("BTC", "long", 1, 500_000), ("ETH", "short", 1, 400_000)), PRICES, 269_000, now=1060)
    assert set(f.state["holdings"]) == {"BTCUSDT"}                          # the short is not copied
    assert f.state["holdings"]["BTCUSDT"] * 100.1 == pytest.approx(0.5 * cash0 * 0.9987, rel=1e-3)
    f.step(pos(("ETH", "short", 1, 400_000)), PRICES, 269_000, now=1120)    # the trader closes BTC
    assert f.state["holdings"] == {} and f.state["cash_usdt"] < cash0      # spread and two fees lost
    events = [json.loads(l) for l in (tmp_path / "run" / "decisions.jsonl").read_text().splitlines()]
    trader = [e for e in events if e["event"] == "trader"]
    assert trader[0]["action"] == "closed" and trader[0]["copied"] and "BTC" in trader[0]["reason"]
    assert [e["event"] for e in events if e["event"] in ("entry", "exit")] == ["entry", "exit"]
    rows = list(csv.DictReader((tmp_path / "run" / "trades.csv").open()))
    assert len(rows) == 1 and rows[0]["exit_reason"] == "trader_closed" and float(rows[0]["pnl_pct"]) < 0
    eq = (tmp_path / "run" / "equity.csv").read_text().strip().splitlines()
    assert len(eq) == 2 and 4_900_000 < float(eq[1].split(",")[1]) < 5_000_000


def test_small_weight_changes_do_not_churn(tmp_path):
    f = Follower({"address": "0x" + "cd" * 20}, tmp_path / "run")
    f.start(rate_ask=270_000, now=0)
    f.step(pos(("BTC", "long", 1, 500_000)), PRICES, 270_000, now=60)
    n = f.state["holdings"]["BTCUSDT"]
    f.step(pos(("BTC", "long", 1.01, 510_000)), PRICES, 270_000, now=120)   # +1%: below the 2% rebalance band
    assert f.state["holdings"]["BTCUSDT"] == n


def test_followed_puts_the_owners_traders_first(tmp_path):
    a, b = "0x" + "11" * 20, "0x" + "22" * 20
    traders.save_json(tmp_path / "pinned.json", [{"address": b.upper().replace("0X", "0x"), "name": "Mine"},
                                                 {"address": "not-an-address"}])
    traders.save_json(tmp_path / "selection.json", {"at": 1, "traders": [{"address": a}, {"address": b}]})
    out = traders.followed(tmp_path)
    assert [(t["address"], t["source"]) for t in out] == [(b, "pinned"), (a, "leaderboard")]


async def test_pick_prefers_traders_a_spot_copy_can_follow():
    """The leaderboard's top accounts were all short on 2026-10-23: their copies sat in USDT. The pick now reads each
    candidate's book and keeps the profitable ones with real longs on Wallex markets."""
    rows = [{"address": a, "account_usd": 5e6, "pnl_week": 1, "pnl_month": m, "pnl_allTime": 1}
            for a, m in (("0xshort", 90), ("0xlong", 50), ("0xempty", 80), ("0xsmall_long", 70))]
    books = {"0xshort": pos(("BTC", "short", 1, 4_000_000)), "0xlong": pos(("BTC", "long", 1, 2_000_000)),
             "0xempty": pos(account=5_000), "0xsmall_long": pos(("ETH", "long", 1, 50_000))}

    class HL:
        async def positions(self, a):
            return books[a]
    picked = await traders.pick_copyable(HL(), rows, set(PRICES), n=2)
    assert [p["address"] for p in picked] == ["0xlong", "0xshort"]          # copyable first, then the best of the rest
    assert picked[0]["copyable_long"] == 1.0

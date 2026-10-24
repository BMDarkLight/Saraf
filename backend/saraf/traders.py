"""Live trader personas: follow real traders' public positions on Hyperliquid and copy them on Wallex (simulated).

Hyperliquid is an on-chain perpetuals exchange: every account's open positions and fills are public through its
info API (no key). The followed traders are either picked from its public leaderboard by verified profit, or added by
the owner by address. A trader is shown by the name they set on Hyperliquid themselves, or the name the owner gave
the address; an address is never labelled as a known person otherwise.

Each followed trader gets a shadow session (data/engine/trader-<address prefix>): every minute their open positions
are read, their long positions in coins Wallex lists on USDT markets become target weights (their leverage dropped:
the weights are capped at 100% of our capital), and a simulated portfolio is moved toward those weights at the live
Wallex prices with the account's real fees. Shorts cannot be copied on Wallex spot: those coins stay in USDT. Every
change in the trader's positions is logged with what the copy did about it.

  uv run python -m saraf.traders run        # the saraf-traders service: all followers in one process
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

from .config import ROOT, Settings
from .engine.journal import Journal
from .wallex import WallexClient

HL_INFO = "https://api.hyperliquid.xyz/info"
HL_BOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
DIR = ROOT / "data" / "engine" / "traders"          # selection.json, pinned.json
ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")

CAPITAL_TMN = 5_000_000.0
USDT_FEE = 0.0013          # taker fee on USDT markets (challenge account)
TMN_FEE = 0.003            # taker fee on TMN markets (the initial TMN -> USDT)
REBALANCE_MIN = 0.02       # move toward the target only when off by more than 2% of equity: no fee churn
LEADERS = 4                # traders picked from the leaderboard
MIN_ACCOUNT_USD = 2_000_000
POLL_SECONDS = 60
FILLS_SECONDS = 300
MARK_SECONDS = 300
RESELECT_SECONDS = 86_400


def run_name(address: str) -> str:
    return f"trader-{address.lower()[2:10]}"


def wallex_base(coin: str) -> str | None:
    """Hyperliquid perp coin -> Wallex base asset. kPEPE/kBONK are quoted per 1000 tokens (weights don't care);
    "@107"-style names are Hyperliquid spot pairs, not followed."""
    if coin.startswith("@"):
        return None
    if coin.startswith("k") and coin[1:].isupper():
        coin = coin[1:]
    return coin.upper()


# ------------------------------------------------------------------------------------------------- Hyperliquid
class Hyperliquid:
    def __init__(self, timeout: float = 20.0):
        self.http = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self.http.aclose()

    async def info(self, body: dict) -> Any:
        r = await self.http.post(HL_INFO, json=body)
        r.raise_for_status()
        return r.json()

    async def positions(self, address: str) -> dict:
        """{"account_usd", "positions": [{"coin", "side", "size", "usd", "entry", "upnl", "leverage"}]}."""
        st = await self.info({"type": "clearinghouseState", "user": address})
        out = []
        for ap in st.get("assetPositions", []):
            p = ap.get("position") or {}
            szi = float(p.get("szi") or 0)
            if not szi:
                continue
            out.append({"coin": p["coin"], "side": "long" if szi > 0 else "short", "size": abs(szi),
                        "usd": abs(float(p.get("positionValue") or 0)), "entry": float(p.get("entryPx") or 0),
                        "upnl": float(p.get("unrealizedPnl") or 0),
                        "leverage": (p.get("leverage") or {}).get("value")})
        return {"account_usd": float((st.get("marginSummary") or {}).get("accountValue") or 0), "positions": out}

    async def fills(self, address: str, n: int = 12) -> list[dict]:
        """The trader's latest fills, merged per order (one row per coin/direction/second)."""
        rows = await self.info({"type": "userFills", "user": address})
        merged: dict[tuple, dict] = {}
        for f in rows[:300]:
            if f.get("coin", "").startswith("@"):
                continue
            key = (f["coin"], f.get("dir"), int(f["time"]) // 1000)
            m = merged.setdefault(key, {"coin": f["coin"], "dir": f.get("dir"), "time": int(f["time"]) // 1000,
                                        "usd": 0.0, "closed_pnl": 0.0, "px": float(f["px"])})
            m["usd"] += float(f["px"]) * float(f["sz"])
            m["closed_pnl"] += float(f.get("closedPnl") or 0)
        return sorted(merged.values(), key=lambda m: -m["time"])[:n]

    async def leaderboard(self, min_account: float = MIN_ACCOUNT_USD) -> list[dict]:
        """The public leaderboard (~40 MB of JSON), read as a stream one trader at a time so the VPS never holds it
        all; only accounts above `min_account` are kept."""
        out: list[dict] = []
        dec = json.JSONDecoder()
        buf, pos, inside = "", 0, False
        async with self.http.stream("GET", HL_BOARD, timeout=120) as r:
            r.raise_for_status()
            async for chunk in r.aiter_text():
                buf += chunk
                if not inside:   # skip to the rows array, whatever comes before it
                    i = buf.find('"leaderboardRows"')
                    j = buf.find("[", i) if i >= 0 else -1
                    if j < 0:
                        continue
                    inside, pos = True, j + 1
                while True:   # one row object at a time, in whatever key order the feed uses
                    while pos < len(buf) and buf[pos] in " \t\r\n,":
                        pos += 1
                    if pos >= len(buf) or buf[pos] == "]":
                        break
                    try:
                        row, end = dec.raw_decode(buf, pos)
                    except ValueError:   # the row continues in the next chunk
                        break
                    pos = end
                    if float(row.get("accountValue") or 0) >= min_account:
                        out.append(_board_row(row))
                buf, pos = buf[pos:], 0
        return out


def _board_row(row: dict) -> dict:
    perf = {w: d for w, d in row.get("windowPerformances", [])}
    return {"address": row["ethAddress"].lower(), "name": row.get("displayName"),
            "account_usd": float(row.get("accountValue") or 0),
            **{f"pnl_{w}": float((perf.get(w) or {}).get("pnl") or 0) for w in ("day", "week", "month", "allTime")},
            **{f"roi_{w}": float((perf.get(w) or {}).get("roi") or 0) for w in ("week", "month", "allTime")}}


def pick_leaders(rows: list[dict], n: int = LEADERS) -> list[dict]:
    """Consistently profitable large accounts: positive over the week, the month and all time, ranked by the month's
    profit. Named accounts (a display name the trader set) first among near-equals is not attempted: profit decides."""
    good = [r for r in rows if r["pnl_week"] > 0 and r["pnl_month"] > 0 and r["pnl_allTime"] > 0
            and r["account_usd"] >= MIN_ACCOUNT_USD]
    return sorted(good, key=lambda r: -r["pnl_month"])[:n]


async def pick_copyable(hl: "Hyperliquid", rows: list[dict], tradable: set[str], n: int = LEADERS,
                        min_long: float = 0.10, pool: int = 40) -> list[dict]:
    """Among the consistently profitable large accounts, the ones a Wallex spot copy can actually follow right now:
    at least `min_long` of their account in longs on Wallex USDT markets (a book of shorts copies to plain USDT).
    Ranked by 30-day profit; if fewer qualify, the most profitable of the rest fill the list."""
    cands = pick_leaders(rows, n=pool)
    good, rest = [], []
    for r in cands:
        try:
            pos = await hl.positions(r["address"])
        except Exception:
            continue
        if pos["account_usd"] < MIN_ACCOUNT_USD / 2:   # emptied since the leaderboard was computed
            continue
        w, _ = target_weights(pos, tradable)
        (good if sum(w.values()) >= min_long else rest).append({**r, "account_usd": pos["account_usd"],
                                                                "copyable_long": round(sum(w.values()), 3)})
        if len(good) >= n:
            break
    return (good + rest)[:n]


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    tmp.replace(path)


def followed(directory: Path = DIR) -> list[dict]:
    """The leaderboard picks plus the owner's pinned traders (pinned first, no duplicates)."""
    pinned = [{**p, "address": p["address"].lower(), "source": "pinned"} for p in load_json(directory / "pinned.json", [])
              if ADDRESS.match(p.get("address", ""))]
    picked = [{**p, "source": "leaderboard"} for p in load_json(directory / "selection.json", {}).get("traders", [])]
    seen, out = set(), []
    for p in pinned + picked:
        if p["address"] not in seen:
            seen.add(p["address"])
            out.append(p)
    return out


# ------------------------------------------------------------------------------------------------- the copy
def target_weights(pos: dict, tradable: set[str]) -> tuple[dict[str, float], list[str]]:
    """The trader's long positions as weights of their account, on Wallex USDT markets, capped at 100% in total.
    Returns ({symbol: weight}, notes on what cannot be copied)."""
    acct = pos["account_usd"]
    weights: dict[str, float] = {}
    skipped = []
    for p in pos["positions"]:
        base = wallex_base(p["coin"])
        sym = f"{base}USDT" if base else None
        if p["side"] == "short":
            skipped.append(f"{p['coin']} short")
        elif not sym or sym not in tradable:
            skipped.append(f"{p['coin']} not on Wallex")
        elif acct > 0:
            weights[sym] = weights.get(sym, 0.0) + p["usd"] / acct
    total = sum(weights.values())
    if total > 1:
        weights = {k: v / total for k, v in weights.items()}
    return weights, skipped


def describe_changes(old: list[dict], new: list[dict]) -> list[dict]:
    """What the trader did between two position snapshots: opened / closed / added / reduced / flipped."""
    o = {(p["coin"]): p for p in old}
    n = {(p["coin"]): p for p in new}
    out = []
    for coin in sorted(set(o) | set(n)):
        a, b = o.get(coin), n.get(coin)
        if a and not b:
            out.append({"coin": coin, "action": "closed", "side": a["side"], "usd": a["usd"]})
        elif b and not a:
            out.append({"coin": coin, "action": "opened", "side": b["side"], "usd": b["usd"]})
        elif a and b and a["side"] != b["side"]:
            out.append({"coin": coin, "action": "flipped", "side": b["side"], "usd": b["usd"]})
        elif a and b and abs(b["size"] - a["size"]) > 0.05 * a["size"]:
            out.append({"coin": coin, "action": "added" if b["size"] > a["size"] else "reduced", "side": b["side"],
                        "usd": b["usd"], "change_usd": abs(b["size"] - a["size"]) * (b["usd"] / b["size"] if b["size"] else 0)})
    return out


class Follower:
    """One followed trader's simulated copy. State: USDT cash, coin quantities and their USDT cost."""

    def __init__(self, trader: dict, journal_dir: Path):
        self.t = trader
        self.dir = journal_dir
        self.journal = Journal(journal_dir)
        self.state_path = journal_dir / "state.json"
        self.state = load_json(self.state_path, None) or {"cash_usdt": None, "holdings": {}, "cost": {},
                                                           "positions": None, "peak": CAPITAL_TMN, "n_trades": 0}
        self.last_mark = 0.0

    def save(self) -> None:
        save_json(self.state_path, self.state)

    def equity_usdt(self, prices: dict[str, dict]) -> float:
        return self.state["cash_usdt"] + sum(q * prices[s]["bid"] for s, q in self.state["holdings"].items() if s in prices)

    def start(self, rate_ask: float, now: float) -> None:
        if self.state["cash_usdt"] is None:   # 5M TMN into USDT at the ask, with the TMN-market fee
            self.state["cash_usdt"] = CAPITAL_TMN / rate_ask * (1 - TMN_FEE)
            self.journal.log("start", now, mode="shadow", trader=self.t["address"], name=self.t.get("name"),
                             source=self.t.get("source"), config={"capital_tmn": CAPITAL_TMN, "fee_rate": USDT_FEE})
            self.save()

    def step(self, pos: dict, prices: dict[str, dict], rate_bid: float, now: float) -> None:
        """Log what the trader changed, then move the copy toward their long weights."""
        old = self.state["positions"]
        if old is not None:
            for ch in describe_changes(old, pos["positions"]):
                base = wallex_base(ch["coin"])
                copyable = ch["side"] == "long" and base and f"{base}USDT" in prices
                self.journal.log("trader", now, symbol=f"{base}USDT" if base else ch["coin"], **ch,
                                 copied=bool(copyable),
                                 reason=f"{ch['action']} {ch['side']} {ch['coin']} ${ch['usd']:,.0f}"
                                        + ("" if copyable else " (not copyable on Wallex spot)"))
        self.state["positions"] = pos["positions"]
        weights, _ = target_weights(pos, set(prices))
        eq = self.equity_usdt(prices)
        held = self.state["holdings"]
        for sym in sorted(set(held) | set(weights)):
            px = prices.get(sym)
            if not px or not px["bid"] or not px["ask"]:
                continue
            have = held.get(sym, 0.0) * px["bid"]
            want = weights.get(sym, 0.0) * eq
            diff = want - have
            if abs(diff) < max(REBALANCE_MIN * eq, 1.0) and not (want == 0 and have > 0):
                continue
            if diff > 0:
                spend = min(diff, self.state["cash_usdt"])
                if spend < 1.0:
                    continue
                qty = spend / px["ask"] * (1 - USDT_FEE)
                held[sym] = held.get(sym, 0.0) + qty
                self.state["cost"][sym] = self.state["cost"].get(sym, 0.0) + spend
                self.state["cash_usdt"] -= spend
                self.journal.log("entry", now, symbol=sym, setup="copy", qty=qty, price=px["ask"], usdt=spend,
                                 reason=f"copy: target {100 * weights.get(sym, 0):.1f}% of capital")
            else:
                qty = held[sym] if want == 0 else min(held[sym], -diff / px["bid"])
                proceeds = qty * px["bid"] * (1 - USDT_FEE)
                cost = self.state["cost"].get(sym, 0.0) * qty / held[sym]
                held[sym] -= qty
                self.state["cost"][sym] = self.state["cost"].get(sym, 0.0) - cost
                if held[sym] * px["bid"] < 0.01:
                    held.pop(sym)
                    self.state["cost"].pop(sym, None)
                self.state["cash_usdt"] += proceeds
                self.state["n_trades"] += 1
                pnl_usdt = proceeds - cost
                self.journal.trade({
                    "trade_id": f"C{self.state['n_trades']:04d}", "symbol": sym, "setup": "copy",
                    "entry_price": round(cost / qty, 8) if qty else "", "qty": qty, "cost_tmn": round(cost * rate_bid),
                    "exit_time": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)), "exit_price": px["bid"],
                    "proceeds_tmn": round(proceeds * rate_bid), "pnl_tmn": round(pnl_usdt * rate_bid),
                    "pnl_pct": round(100 * pnl_usdt / cost, 3) if cost else 0,
                    "exit_reason": "trader_closed" if want == 0 else "trader_reduced"})
                self.journal.log("exit", now, symbol=sym, qty=qty, price=px["bid"], pnl_pct=round(100 * pnl_usdt / cost, 3)
                                 if cost else 0, reason="copy: the trader " + ("closed" if want == 0 else "reduced") + " it")
        self.save()
        if now - self.last_mark >= MARK_SECONDS:
            self.last_mark = now
            eq_tmn = self.equity_usdt(prices) * rate_bid
            self.state["peak"] = max(self.state["peak"], eq_tmn)
            exposure = (self.equity_usdt(prices) - self.state["cash_usdt"]) * rate_bid
            self.journal.mark(now, eq_tmn, self.state["cash_usdt"] * rate_bid, exposure, 1 - eq_tmn / self.state["peak"])
            self.save()

    def profile(self, pos: dict, fills: list[dict] | None, board: dict | None, prices: dict[str, dict]) -> None:
        """trader.json: what the UI shows about the real trader."""
        weights, skipped = target_weights(pos, set(prices))
        prof = load_json(self.dir / "trader.json", {})
        prof.update({"address": self.t["address"], "name": self.t.get("name"), "source": self.t.get("source"),
                     "updated": time.time(), "account_usd": pos["account_usd"],
                     "positions": sorted(pos["positions"], key=lambda p: -p["usd"])[:12],
                     "copy_weights": weights, "not_copied": skipped})
        if fills is not None:
            prof["fills"] = fills
        if board:
            prof["board"] = {k: v for k, v in board.items() if k.startswith(("pnl_", "roi_"))}
        save_json(self.dir / "trader.json", prof)


# ------------------------------------------------------------------------------------------------- the service
async def wallex_prices(client: WallexClient) -> tuple[dict[str, dict], float, float]:
    m = await client.markets()
    prices = {}
    for sym, v in m.items():
        if v.get("quoteAsset") != "USDT":
            continue
        st = v.get("stats") or {}
        try:
            prices[sym] = {"bid": float(st.get("bidPrice") or 0), "ask": float(st.get("askPrice") or 0)}
        except ValueError:   # "-" on inactive markets
            continue
    u = m["USDTTMN"]["stats"]
    return prices, float(u["bidPrice"]), float(u["askPrice"])


async def run(s: Settings, directory: Path = DIR, max_loops: int | None = None) -> None:
    hl = Hyperliquid()
    client = WallexClient(s.wallex_base_url)
    followers: dict[str, Follower] = {}
    board: dict[str, dict] = {}
    last_fills: dict[str, float] = {}
    loops = 0
    try:
        while max_loops is None or loops < max_loops:
            loops += 1
            now = time.time()
            sel = load_json(directory / "selection.json", {})
            try:
                prices, rate_bid, rate_ask = await wallex_prices(client)
            except Exception as e:
                print(f"[traders] wallex prices failed: {e}", flush=True)
                await asyncio.sleep(POLL_SECONDS)
                continue
            # daily (books change), and every 10 minutes while the pick is empty (a failed or empty leaderboard read)
            if now - sel.get("at", 0) > (RESELECT_SECONDS if sel.get("traders") else 600) or sel.get("v") != 2:
                try:
                    rows = await hl.leaderboard()
                    picked = await pick_copyable(hl, rows, set(prices))
                    board = {r["address"]: r for r in rows}
                    sel = {"at": now, "v": 2, "traders": picked}
                    save_json(directory / "selection.json", sel)
                    print(f"[traders] picked {[p['address'] for p in picked]}", flush=True)
                except Exception as e:
                    print(f"[traders] leaderboard failed: {type(e).__name__}: {e}", flush=True)
            for t in sel.get("traders", []):
                board.setdefault(t["address"], t)
            for t in followed(directory):
                f = followers.get(t["address"])
                if not f:
                    f = followers[t["address"]] = Follower(t, ROOT / "data" / "engine" / run_name(t["address"]))
                    f.start(rate_ask, now)
                f.t.update(t)
                try:
                    pos = await hl.positions(t["address"])
                    fills = None
                    if now - last_fills.get(t["address"], 0) >= FILLS_SECONDS:
                        fills = await hl.fills(t["address"])
                        last_fills[t["address"]] = now
                    f.step(pos, prices, rate_bid, now)
                    f.profile(pos, fills, board.get(t["address"]), prices)
                except Exception as e:
                    f.journal.log("error", now, error=f"follow {t['address']}: {type(e).__name__}: {e}")
            await asyncio.sleep(POLL_SECONDS if max_loops is None else 0)
    finally:
        await hl.aclose()
        await client.aclose()


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["run"]:
        asyncio.run(run(Settings()))
    else:
        print("usage: python -m saraf.traders run")

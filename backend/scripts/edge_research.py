"""Edge research for the TMN-scored challenge: which returns survive a ~0.6% round trip?

  1. USDT/TMN drift over 72h windows (the toman's depreciation) and when to enter it
  2. Triangular gaps: X/TMN vs X/USDT x USDT/TMN (history from hourly closes + live executable books)
  3. Holding BTC/ETH vs USDT over 72h windows, in TMN

Run on the VPS (Wallex API):  uv run python scripts/edge_research.py
"""

import asyncio
import statistics as st
import time

from saraf.brokers.base import safe_float
from saraf.engine.markets import CREDIT_BLOCKED
from saraf.wallex import WallexClient

FEE = 0.003           # taker, per side, as /v1/account/fee reports for the credit account
DAY = 86_400


async def hourly(c: WallexClient, sym: str, days: int) -> list[dict]:
    end, out = int(time.time()), []
    for start in range(end - days * DAY, end, 30 * DAY):   # the API caps a request's range
        out += await c.candles(sym, "60", start, min(start + 30 * DAY, end))
    seen, rows = set(), []
    for r in sorted(out, key=lambda r: r["t"]):
        if r["t"] not in seen:
            seen.add(r["t"])
            rows.append(r)
    return rows


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def window_stats(closes: list[float], h: int) -> dict:
    rets = [closes[i + h] / closes[i] - 1 for i in range(len(closes) - h)]
    net = [r - 2 * FEE for r in rets]
    return {"n": len(rets), "mean": st.mean(rets), "median": st.median(rets), "p10": pct(rets, 0.1),
            "p90": pct(rets, 0.9), "worst": min(rets), "win_net": sum(r > 0 for r in net) / len(net),
            "mean_net": st.mean(net)}


def fmt(d: dict) -> str:
    return " ".join(f"{k}={v * 100:+.2f}%" if isinstance(v, float) and k != "win_net" else
                    (f"{k}={v:.0%}" if k == "win_net" else f"{k}={v}") for k, v in d.items())


async def main() -> None:
    c = WallexClient()
    import sys
    if "--skip-usdt" in sys.argv:
        u = await hourly(c, "USDTTMN", 30)
    else:
        u = None
    print("== 1. USDT/TMN drift (hourly, 180d)")
    u = u or await hourly(c, "USDTTMN", 180)
    uc = [r["c"] for r in u]
    print(f"bars={len(u)} first={uc[0]:,.0f} last={uc[-1]:,.0f} total={uc[-1] / uc[0] - 1:+.2%}")
    for h in (24, 72):
        print(f"  {h}h hold:", fmt(window_stats(uc, h)))
    # entry timing: forward 72h return by where price sits vs its 72h mean, and after a 24h spike
    buckets: dict[str, list[float]] = {"below 72h avg": [], "above 72h avg": [], "after +2% day": [],
                                       "after -1% day": []}
    for i in range(72, len(uc) - 72):
        fwd = uc[i + 72] / uc[i] - 1
        avg = st.mean(uc[i - 72:i])
        buckets["below 72h avg" if uc[i] < avg else "above 72h avg"].append(fwd)
        d24 = uc[i] / uc[i - 24] - 1
        if d24 > 0.02:
            buckets["after +2% day"].append(fwd)
        if d24 < -0.01:
            buckets["after -1% day"].append(fwd)
    for k, v in buckets.items():
        if v:
            print(f"  entry {k:15s} n={len(v):4d} fwd72h mean={st.mean(v):+.2%} win_net={sum(x > 2 * FEE for x in v) / len(v):.0%}")

    print("\n== 2a. triangular gaps from hourly closes (30d), liquid pairs")
    markets = await c.markets()
    pairs = []
    for sym, m in markets.items():
        if m["quoteAsset"] != "TMN" or sym in CREDIT_BLOCKED or sym == "USDTTMN":
            continue
        base = m["baseAsset"]
        if f"{base}USDT" in markets and safe_float((m.get("stats") or {}).get("24h_quoteVolume")) > 2e9:
            pairs.append(base)
    print(f"  pairs with TMN+USDT markets and >2B TMN/24h volume: {len(pairs)}: {', '.join(pairs[:20])}")
    u30 = {r["t"]: r["c"] for r in u if r["t"] > time.time() - 30 * DAY}
    gap_hits = {}
    for base in pairs[:15]:
        t = {r["t"]: r["c"] for r in await hourly(c, f"{base}TMN", 30)}
        s = {r["t"]: r["c"] for r in await hourly(c, f"{base}USDT", 30)}
        devs = [t[k] / (s[k] * u30[k]) - 1 for k in t if k in s and k in u30]
        if devs:
            gap_hits[base] = (len(devs), st.mean(abs(d) for d in devs), sum(abs(d) > 3 * FEE for d in devs))
    for base, (n, mabs, big) in sorted(gap_hits.items(), key=lambda kv: -kv[1][2]):
        print(f"  {base:6s} hours={n:4d} mean|dev|={mabs:.2%} hours with |dev|>{3 * FEE:.1%}: {big}")

    print("\n== 2b. live executable triangular gaps (best bid/ask, 3 legs, 0.3% each)")
    best = []
    for _ in range(6):
        books = await c.depth_all()

        def top(sym):
            b = books.get(sym) or {}
            bid = max((float(x["price"]) for x in b.get("bid", []) or []), default=0)
            ask = min((float(x["price"]) for x in b.get("ask", []) or []), default=0)
            return bid, ask
        ub, ua = top("USDTTMN")
        for base in pairs:
            tb, ta = top(f"{base}TMN")
            sb, sa = top(f"{base}USDT")
            if not all((ub, ua, tb, ta, sb, sa)):
                continue
            via_usdt = tb / (ua * sa) * (1 - FEE) ** 3 - 1     # TMN->USDT->X->TMN
            via_tmn = (sb * ub) / ta * (1 - FEE) ** 3 - 1      # TMN->X->USDT->TMN
            best.append((max(via_usdt, via_tmn), base))
        await asyncio.sleep(10)
    best.sort(reverse=True)
    print(f"  samples={len(best)} profitable after fees: {sum(g > 0 for g, _ in best)}; top: "
          + ", ".join(f"{b} {g:+.2%}" for g, b in best[:6]))

    print("\n== 3. 72h holds in TMN: BTC, ETH vs USDT (90d)")
    for sym in ("BTCTMN", "ETHTMN", "USDTTMN"):
        cl = [r["c"] for r in await hourly(c, sym, 90)]
        print(f"  {sym:8s}", fmt(window_stats(cl, 72)))
    await c.aclose()


if __name__ == "__main__":
    asyncio.run(main())

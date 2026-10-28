"""Setup research: net expectancy (after fees + spread) of each signal setup for a grid of
stop/target multiples, split into an in-sample and an out-of-sample window.

usage: uv run python scripts/research.py --days 28 --oos-days 7 --tf 15,30,60
Parameters should be chosen on in-sample only; the out-of-sample column is the honest check.
"""

import argparse
import asyncio
import time
from collections import defaultdict

from saraf.config import ROOT, get_settings
from saraf.engine.feeds import LiveFeed, load_history
from saraf.engine.features import closed, compute, resample
from saraf.engine.strategy import candidates
from saraf.venues import create

GRID = [(1.0, 2.0), (1.5, 2.0), (1.5, 3.0), (2.0, 3.0), (2.0, 4.0), (2.5, 5.0)]


def simulate(path, entry, atr, s_mult, t_mult, cost):
    stop, target = entry - s_mult * atr, entry + t_mult * atr
    px = path[-1].c if path else entry
    for b in path:
        if b.l <= stop:
            px = stop
            break
        if b.h >= target:
            px = target
            break
    return 100 * (px / entry * (1 - cost) - 1)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=28)
    ap.add_argument("--oos-days", type=float, default=7)
    ap.add_argument("--tf", default="15,30,60")
    ap.add_argument("--horizon-bars", type=int, default=32)
    a = ap.parse_args()
    s = get_settings()
    client = create(s.default_venue, s)
    end = (time.time() // 3600) * 3600
    start = end - a.days * 86400
    split = end - a.oos_days * 86400
    syms = await LiveFeed(client, s).universe()
    m1, h1 = await load_history(client, syms, int(start), int(end), ROOT / "data" / "history")
    await client.aclose()
    cost = 2 * s.engine_fee_rate + 0.003 + 0.001  # fees + spread + slippage, both sides
    for tf in [int(x) for x in a.tf.split(",")]:
        res = defaultdict(lambda: defaultdict(list))  # (setup, grid) -> {"is"/"oos": [pnl]}
        counts = defaultdict(int)
        for sym in syms:
            bars = resample(m1[sym], tf)
            hb = h1[sym]
            times1 = [b.t for b in m1[sym]]
            for i in range(60, len(bars) - 1):
                now = bars[i].t + tf * 60
                if now < start:
                    continue
                hist = [b for b in hb if b.t + 3600 <= now][-150:]
                f = compute(bars[max(0, i - 199): i + 1], hist)
                for c in candidates(sym, f, s.engine_min_atr_pct, s.engine_max_atr_pct):
                    counts[c.setup] += 1
                    import bisect
                    j = bisect.bisect_left(times1, now)
                    k = bisect.bisect_left(times1, now + a.horizon_bars * tf * 60)
                    path = m1[sym][j:k]
                    for g in GRID:
                        res[(c.setup, g)]["is" if now < split else "oos"].append(
                            simulate(path, c.price, c.atr, g[0], g[1], cost))
        print(f"\n=== timeframe {tf}m | cost {100 * cost:.2f}% round trip | candidates {dict(counts)}")
        print(f"{'setup':16} {'stop/tgt':9} {'IS n':>5} {'IS avg%':>8} {'IS win%':>7} | {'OOS n':>5} {'OOS avg%':>8} {'OOS win%':>8}")
        for (setup, g), d in sorted(res.items()):
            i_, o_ = d["is"], d["oos"]
            fmt = lambda v: (f"{len(v):5} {sum(v) / len(v):8.3f} {100 * sum(x > 0 for x in v) / len(v):7.1f}" if v else f"{0:5} {'-':>8} {'-':>7}")
            print(f"{setup:16} {g[0]:.1f}/{g[1]:.1f}   {fmt(i_)} | {fmt(o_)}")


asyncio.run(main())

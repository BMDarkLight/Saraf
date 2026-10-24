"""Hold USDT vs TMN on a slow USDT/TMN trend rule (0.3% per switch), in-sample 120d / out-of-sample 60d."""

import asyncio
import statistics as st

from edge_research import hourly
from rotation_research import ema
from saraf.venues.wallex import WallexClient

FEE = 0.003


def run(c: list[float], rule, lo: int, hi: int) -> str:
    held_usdt, tmn, usdt, sw, eq = True, 0.0, 1.0, 0, []
    for i in range(lo, hi):
        want = rule(i)
        if want != held_usdt:
            if want:
                usdt, tmn = tmn / c[i] * (1 - FEE), 0.0
            else:
                tmn, usdt = usdt * c[i] * (1 - FEE), 0.0
            held_usdt, sw = want, sw + 1
        eq.append(tmn + usdt * c[i])
    r72 = [eq[j + 72] / eq[j] - 1 for j in range(len(eq) - 72)]
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak, mdd = max(peak, v), max(mdd, 1 - v / max(peak, v))
    return (f"total={eq[-1] / eq[0] - 1:+7.2%} 72h_mean={st.mean(r72):+.2%} 72h_p10={sorted(r72)[len(r72) // 10]:+.2%} "
            f"72h_win={sum(r > 0 for r in r72) / len(r72):.0%} maxDD={mdd:.2%} switches={sw}")


async def main() -> None:
    cl = WallexClient()
    c = [r["c"] for r in await hourly(cl, "USDTTMN", 180)]
    e72, e168 = ema(c, 72), ema(c, 168)
    rules = {
        "always USDT": lambda i: True,
        "USDT if > EMA168": lambda i: c[i] > e168[i],
        "USDT if > EMA168, 1% band": lambda i: c[i] > e168[i] * 0.99,
        "USDT unless 3d drop > 3%": lambda i: i < 72 or c[i] / c[i - 72] - 1 > -0.03,
    }
    split = len(c) - 60 * 24
    for name, (lo, hi) in (("IN-SAMPLE 120d", (168, split)), ("OUT-OF-SAMPLE 60d", (split, len(c)))):
        print(f"== {name}")
        for k, rule in rules.items():
            print(f"  {k:28s} {run(c, rule, lo, hi)}")
    await cl.aclose()


asyncio.run(main())

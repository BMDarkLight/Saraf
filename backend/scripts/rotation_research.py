"""USDT-core rotation, measured in TMN.

Hold USDT (it rides the toman's depreciation); switch into BTC or ETH through the cheap USDT markets
(0.13% taker) only while a trend rule says so. Value is always marked in TMN: units x X/USDT x USDT/TMN.
In-sample = first 120 days, out-of-sample = last 60 days (rules picked before looking at OOS).

  uv run python scripts/rotation_research.py
"""

import asyncio
import statistics as st

from edge_research import hourly
from saraf.config import get_settings
from saraf.venues import create

USDT_FEE = 0.0013     # taker on USDT markets for the credit account
SPLIT_DAYS = 60


def ema(xs: list[float], n: int) -> list[float]:
    k, out, e = 2 / (n + 1), [], xs[0]
    for x in xs:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


def signal_factory(rule: str, closes: dict[str, list[float]]):
    e = {s: {n: ema(c, n) for n in (24, 72, 168)} for s, c in closes.items()}

    def want(i: int) -> str:
        best, score = "USDT", 0.0
        for s, c in closes.items():
            if i < 168:
                continue
            if rule == "above_ema72":
                ok, m = c[i] > e[s][72][i] * 1.005, c[i] / c[i - 72] - 1
            elif rule == "ema24_over_72":
                ok, m = e[s][24][i] > e[s][72][i] and c[i] > e[s][24][i], c[i] / c[i - 72] - 1
            else:   # "trend_168": slow, few switches
                ok, m = c[i] > e[s][168][i] * 1.01 and e[s][72][i] > e[s][168][i], c[i] / c[i - 168] - 1
            if ok and m > score:
                best, score = s, m
        return best
    return want


def run(rule: str, usdt: list[float], closes: dict[str, list[float]], lo: int, hi: int) -> dict:
    want = signal_factory(rule, closes)
    held, units, switches = "USDT", 1.0, 0          # 1 USDT at start
    eq = []
    for i in range(lo, hi):
        w = want(i)
        if w != held:
            usd = units if held == "USDT" else units * closes[held][i] * (1 - USDT_FEE)
            units = usd if w == "USDT" else usd / closes[w][i] * (1 - USDT_FEE)
            held, switches = w, switches + 1
        usd = units if held == "USDT" else units * closes[held][i]
        eq.append(usd * usdt[i])                      # in TMN
    rets72 = [eq[j + 72] / eq[j] - 1 for j in range(len(eq) - 72)]
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    return {"total": eq[-1] / eq[0] - 1, "72h_mean": st.mean(rets72), "72h_p10": sorted(rets72)[len(rets72) // 10],
            "72h_win": sum(r > 0 for r in rets72) / len(rets72), "maxDD": mdd, "switches": switches}


async def main() -> None:
    c = create(get_settings().default_venue, get_settings())
    u = {r["t"]: r["c"] for r in await hourly(c, "USDTTMN", 180)}
    raw = {s: {r["t"]: r["c"] for r in await hourly(c, f"{s}USDT", 180)} for s in ("BTC", "ETH")}
    ts = sorted(t for t in u if all(t in raw[s] for s in raw))
    usdt = [u[t] for t in ts]
    closes = {s: [raw[s][t] for t in ts] for s in raw}
    split = len(ts) - SPLIT_DAYS * 24
    print(f"hours={len(ts)}  in-sample={split}  out-of-sample={len(ts) - split}")
    for name, (lo, hi) in (("IN-SAMPLE (first 120d)", (168, split)), ("OUT-OF-SAMPLE (last 60d)", (split, len(ts)))):
        print(f"\n== {name}")
        base = {"hold USDT": usdt, "hold BTC": [closes["BTC"][i] * usdt[i] for i in range(len(ts))],
                "hold ETH": [closes["ETH"][i] * usdt[i] for i in range(len(ts))]}
        for k, v in base.items():
            seg = v[lo:hi]
            r72 = [seg[j + 72] / seg[j] - 1 for j in range(len(seg) - 72)]
            peak, mdd = seg[0], 0.0
            for x in seg:
                peak, mdd = max(peak, x), max(mdd, 1 - x / max(peak, x))
            print(f"  {k:18s} total={seg[-1] / seg[0] - 1:+7.2%} 72h_mean={st.mean(r72):+.2%} "
                  f"72h_p10={sorted(r72)[len(r72) // 10]:+.2%} 72h_win={sum(r > 0 for r in r72) / len(r72):.0%} maxDD={mdd:.2%}")
        for rule in ("above_ema72", "ema24_over_72", "trend_168"):
            m = run(rule, usdt, closes, lo, hi)
            print(f"  rot:{rule:14s} total={m['total']:+7.2%} 72h_mean={m['72h_mean']:+.2%} 72h_p10={m['72h_p10']:+.2%} "
                  f"72h_win={m['72h_win']:.0%} maxDD={m['maxDD']:.2%} switches={m['switches']}")
    await c.aclose()


if __name__ == "__main__":
    asyncio.run(main())

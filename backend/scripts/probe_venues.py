"""Can this machine reach every venue's public market data? Read-only; no keys needed.

  uv run python scripts/probe_venues.py            # all venues
  uv run python scripts/probe_venues.py nobitex    # one or more by name
For each venue: how many markets, and a sample market's last price and best bid/ask from its order book.
"""

from __future__ import annotations

import asyncio
import sys
import time

from saraf.config import get_settings
from saraf.venues import VenueError, available, create

SAMPLES = ("BTCTMN", "USDTTMN", "BTCUSDT", "USDTMN")


async def probe(name: str) -> None:
    v = create(name, get_settings())
    t = time.monotonic()
    try:
        markets = await v.markets()
        sym = next((s for s in SAMPLES if s in markets), next(iter(markets), None))
        line = f"{len(markets):5} markets"
        if sym:
            line += f"  {sym} last {markets[sym]['stats'].get('lastPrice')}"
            if v.capabilities.order_book:
                book = await v.depth(sym)
                bid = max((float(lv["price"]) for lv in book["bid"]), default=0)
                ask = min((float(lv["price"]) for lv in book["ask"]), default=0)
                line += f"  book {bid:.10g} / {ask:.10g}"
        print(f"{name:11} ok   {time.monotonic() - t:5.2f}s {line}")
    except VenueError as e:
        print(f"{name:11} FAIL {time.monotonic() - t:5.2f}s {e}")
    finally:
        await v.aclose()


async def main(names: list[str]) -> None:
    for name in names or available():
        await probe(name)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))

"""Can the VPS reach the public trader-data sources the live personas would use? Read-only, prints a short sample.

  uv run python scripts/probe_feeds.py
Hyperliquid (on-chain perps exchange: every trader's fills and positions are public, no key) is the main source;
the others are checked as fallbacks.
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx

HL_INFO = "https://api.hyperliquid.xyz/info"
HL_BOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"


async def probe(c: httpx.AsyncClient, name: str, method: str, url: str, **kw) -> object | None:
    t = time.monotonic()
    try:
        r = await c.request(method, url, **kw)
        print(f"{name:22} HTTP {r.status_code} in {time.monotonic() - t:.2f}s, {len(r.content)} bytes")
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        print(f"{name:22} FAILED after {time.monotonic() - t:.2f}s: {type(e).__name__}: {e}")
        return None


async def main() -> None:
    async with httpx.AsyncClient(timeout=20) as c:
        meta = await probe(c, "hyperliquid meta", "POST", HL_INFO, json={"type": "meta"})
        if meta:
            print("   perp coins:", [u["name"] for u in meta.get("universe", [])][:25])
        board = await probe(c, "hyperliquid leaderboard", "GET", HL_BOARD)
        top = []
        if board:
            rows = board.get("leaderboardRows", [])
            def pnl(r, w):
                return next((float(p[1]["pnl"]) for p in r.get("windowPerformances", []) if p[0] == w), 0.0)
            top = sorted(rows, key=lambda r: -pnl(r, "month"))[:8]
            print(f"   {len(rows)} traders; top by 30-day PnL:")
            for r in top:
                print(f"   {r['ethAddress']} name={r.get('displayName')!r} acct=${float(r['accountValue']):,.0f} "
                      f"day=${pnl(r, 'day'):,.0f} week=${pnl(r, 'week'):,.0f} month=${pnl(r, 'month'):,.0f} "
                      f"all=${pnl(r, 'allTime'):,.0f}")
        if top:
            addr = top[0]["ethAddress"]
            fills = await probe(c, "hyperliquid userFills", "POST", HL_INFO, json={"type": "userFills", "user": addr})
            if fills:
                print(f"   {len(fills)} recent fills of {addr}; newest 3:",
                      json.dumps([{k: f.get(k) for k in ("coin", "side", "px", "sz", "dir", "time", "closedPnl")}
                                  for f in fills[:3]]))
            st = await probe(c, "hyperliquid positions", "POST", HL_INFO, json={"type": "clearinghouseState", "user": addr})
            if st:
                print("   open positions:", [(p["position"]["coin"], p["position"]["szi"], p["position"].get("entryPx"))
                                            for p in st.get("assetPositions", [])][:8])
        await probe(c, "etherscan (no key)", "GET", "https://api.etherscan.io/api",
                    params={"module": "stats", "action": "ethprice"})
        await probe(c, "solana rpc", "POST", "https://api.mainnet-beta.solana.com",
                    json={"jsonrpc": "2.0", "id": 1, "method": "getHealth"})


if __name__ == "__main__":
    asyncio.run(main())

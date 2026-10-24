"""Generative UI tools: the agent composes interactive components inside the chat.

Each tool returns `{"ui": {"component": ..., ...}, ...summary}`. The browser renders `ui`;
the LLM only sees the summary (see loop.for_model), which keeps small-model context lean.
"""

from __future__ import annotations

import asyncio
import time
from typing import Literal

from pydantic import BaseModel, Field

from ..brokers import MarketInfo
from ..brokers.futures import liquidation_price
from ..market import analytics
from .context import AppContext
from .tools import Resolution, Symbol, tool


# ------------------------------------------------------------ comparison
class CompareArgs(BaseModel):
    symbols: str = Field(description="comma-separated markets, e.g. 'BTCUSDT,ETHUSDT,SOLUSDT' (2-6)")
    hours: int = Field(24, ge=2, le=720, description="look-back window in hours")


@tool("show_comparison_chart", "GENERATIVE UI: draw an interactive chart comparing the % performance of several "
      "markets over the same window. Use when the user wants to compare coins.", CompareArgs)
async def show_comparison_chart(ctx: AppContext, a: CompareArgs):
    symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()][:6]
    res = "60" if a.hours <= 168 else "1D"
    now = int(time.time())
    start = now - a.hours * 3600

    async def load(sym: str):
        try:
            cs = await ctx.venue.candles(sym, res, start, now)
        except Exception:
            return None
        if not cs or not cs[0]["o"]:
            return None
        base = cs[0]["o"]
        return {"symbol": sym, "points": [[c["t"], round(100 * (c["c"] / base - 1), 3)] for c in cs]}

    series = [s for s in await asyncio.gather(*(load(s) for s in symbols)) if s]
    ranking = sorted(({"symbol": s["symbol"], "change_pct": s["points"][-1][1]} for s in series),
                     key=lambda r: r["change_pct"], reverse=True)
    missing = sorted(set(symbols) - {s["symbol"] for s in series})
    return {"ui": {"component": "comparison_chart", "hours": a.hours, "series": series},
            "ranking": ranking, "missing_symbols": missing}


# --------------------------------------------------------------- heatmap
class HeatmapArgs(BaseModel):
    quote: Literal["USDT", "TMN"] = "USDT"
    limit: int = Field(36, ge=8, le=80, description="number of markets (most traded first)")


@tool("show_market_heatmap", "GENERATIVE UI: render a market heatmap (tile size = 24h volume, color = 24h change). "
      "Use for 'how is the market', overview or sector questions.", HeatmapArgs)
async def show_market_heatmap(ctx: AppContext, a: HeatmapArgs):
    rows = await analytics.market_overview(ctx.venue, quote=a.quote, sort_by="volume", limit=a.limit)
    tiles = [{"symbol": r["symbol"], "name": r["fa_name"] or r["name"], "change": r["change_24h"],
              "volume": r["quote_volume_24h"], "last": r["last"]} for r in rows if r["last"]]
    up = sum(1 for t in tiles if t["change"] > 0)
    return {"ui": {"component": "heatmap", "quote": a.quote, "tiles": tiles},
            "markets": len(tiles), "advancing": up, "declining": len(tiles) - up,
            "best": max(tiles, key=lambda t: t["change"])["symbol"] if tiles else None,
            "worst": min(tiles, key=lambda t: t["change"])["symbol"] if tiles else None}


# ------------------------------------------------------- annotated chart
class Level(BaseModel):
    price: float
    label: str = Field(description="short label, e.g. 'Support', 'Entry', 'TP1'")
    kind: Literal["support", "resistance", "entry", "take_profit", "stop_loss", "other"] = "other"


class ChartArgs(BaseModel):
    symbol: str = Symbol
    resolution: Resolution = "60"
    levels: list[Level] = Field(default_factory=list, description="price lines to draw on the chart")
    title: str | None = None


@tool("show_price_chart", "GENERATIVE UI: show a candlestick chart in the chat with your own annotated price levels "
      "(support/resistance/entry/TP/SL). Use after analysis to visualize the levels you mention.", ChartArgs)
async def show_price_chart(ctx: AppContext, a: ChartArgs):
    q = await MarketInfo(ctx.venue).quote(a.symbol)
    return {"ui": {"component": "price_chart", "symbol": q["symbol"], "resolution": a.resolution,
                   "levels": [lv.model_dump() for lv in a.levels], "title": a.title},
            "last_price": q["last"], "levels_drawn": len(a.levels)}


# ----------------------------------------------------------- trade ticket
class TicketArgs(BaseModel):
    market: Literal["spot", "futures"]
    symbol: str = Symbol
    side: Literal["BUY", "SELL", "LONG", "SHORT"]
    amount: float | None = Field(None, description="spot: quote amount to spend/receive; futures: margin")
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    price: float | None = None
    leverage: float | None = Field(None, description="futures only")
    take_profit: float | None = None
    stop_loss: float | None = None
    note: str | None = Field(None, description="why you suggest this ticket")


@tool("show_trade_ticket", "GENERATIVE UI: show an editable, pre-filled order form. The user can adjust size/"
      "leverage/TP/SL and submit it (it then goes through the risk guard and confirmation). Use when the user "
      "wants to trade but details are open, or to let them tweak your suggestion.", TicketArgs)
async def show_trade_ticket(ctx: AppContext, a: TicketArgs):
    q = await MarketInfo(ctx.venue).quote(a.symbol)
    max_lev = None
    if a.market == "futures":
        lev = {m["symbol"]: m["max_leverage"] for m in await ctx.venue.futures_markets()}
        max_lev = min(float(lev[q["symbol"]]), ctx.settings.max_leverage) if q["symbol"] in lev else None
    side = a.side if a.market == "spot" else ("LONG" if a.side in ("BUY", "LONG") else "SHORT")
    if a.market == "spot":
        side = "BUY" if a.side in ("BUY", "LONG") else "SELL"
    return {"ui": {"component": "trade_ticket", "market": a.market, "symbol": q["symbol"], "side": side,
                   "amount": a.amount, "order_type": a.order_type, "price": a.price, "leverage": a.leverage or 1,
                   "take_profit": a.take_profit, "stop_loss": a.stop_loss, "note": a.note,
                   "quote": q["quote"], "bid": q["bid"], "ask": q["ask"], "last": q["last"],
                   "max_leverage": max_lev, "leverage_available": max_lev is not None or a.market == "spot"},
            "status": "ticket shown; nothing is executed until the user submits and confirms"}


# -------------------------------------------------------- position planner
class PlannerArgs(BaseModel):
    symbol: str = Symbol
    side: Literal["LONG", "SHORT"]
    leverage: float = Field(3, ge=1, le=125)
    margin: float = Field(50, gt=0, description="collateral in quote asset")
    entry: float | None = Field(None, description="defaults to the current price")
    take_profit: float | None = None
    stop_loss: float | None = None


@tool("show_position_planner", "GENERATIVE UI: interactive futures risk/reward planner with sliders for leverage, "
      "margin, TP and SL; shows liquidation price, PnL at TP/SL and R:R live. Use when the user is planning a "
      "leveraged trade or asks 'what if'.", PlannerArgs)
async def show_position_planner(ctx: AppContext, a: PlannerArgs):
    q = await MarketInfo(ctx.venue).quote(a.symbol)
    entry = a.entry or q["mark"]
    ta = await analytics.technical_analysis(ctx.venue, a.symbol, "60")
    atr = ta.get("atr14") or entry * 0.01
    sign = 1 if a.side == "LONG" else -1
    sl = a.stop_loss or entry - sign * 1.5 * atr
    tp = a.take_profit or entry + sign * 3 * atr
    qty = a.margin * a.leverage / entry
    liq = liquidation_price(a.side, entry, a.leverage)
    pnl_tp, pnl_sl = (tp - entry) * qty * sign, (sl - entry) * qty * sign
    return {"ui": {"component": "position_planner", "symbol": q["symbol"], "quote": q["quote"], "side": a.side,
                   "entry": entry, "leverage": a.leverage, "margin": a.margin, "take_profit": tp, "stop_loss": sl,
                   "atr": atr, "support": ta.get("support"), "resistance": ta.get("resistance"),
                   "max_leverage": ctx.settings.max_leverage},
            "initial_plan": {"entry": entry, "liquidation": liq, "pnl_at_tp": pnl_tp, "pnl_at_sl": pnl_sl,
                             "reward_risk": round(abs(pnl_tp / pnl_sl), 2) if pnl_sl else None}}


# -------------------------------------------------------- generic dashboard
class Block(BaseModel):
    type: Literal["metric", "text", "callout", "table", "progress"]
    label: str | None = Field(None, description="metric/progress label or block heading")
    value: str | None = Field(None, description="metric value, e.g. '83,250 USDT'")
    delta: float | None = Field(None, description="metric % change (colored green/red)")
    tone: Literal["good", "bad", "neutral", "info", "warning"] = "neutral"
    text: str | None = Field(None, description="markdown text for text/callout blocks")
    columns: list[str] | None = Field(None, description="table header")
    rows: list[list[str]] | None = Field(None, description="table rows (strings)")
    percent: float | None = Field(None, description="progress 0-100")


class DashboardArgs(BaseModel):
    title: str
    blocks: list[Block] = Field(description="metrics are laid out in a grid; others stack full width")


@tool("render_dashboard", "GENERATIVE UI: compose a custom report card from blocks (metric tiles with deltas, "
      "tables, progress bars, callouts, text). Use to present a structured summary, e.g. a portfolio review, "
      "a coin report, or a trade plan.", DashboardArgs)
async def render_dashboard(ctx: AppContext, a: DashboardArgs):
    return {"ui": {"component": "dashboard", "title": a.title,
                   "blocks": [b.model_dump(exclude_none=True) for b in a.blocks]},
            "blocks_rendered": len(a.blocks)}

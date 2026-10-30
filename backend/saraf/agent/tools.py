"""Tools exposed to the LLM. Each tool = pydantic args model + async handler.

Tools flagged `mutating=True` move money; they pass the risk guard and (when
REQUIRE_CONFIRMATION is on) wait for the user's explicit approval in the UI.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Annotated, Any, Awaitable, Callable, Literal

import httpx
from pydantic import BaseModel, BeforeValidator, Field

from ..market import analytics, sentiment
from ..news import web
from .context import AppContext


@dataclass
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    handler: Callable[[AppContext, Any], Awaitable[Any]]
    mutating: bool = False
    summarize: Callable[[Any], str] | None = None

    def schema(self, compat: bool = False) -> dict:
        params = self.args.model_json_schema()
        defs = params.pop("$defs", {})
        params.pop("title", None)
        _simplify(params, compat, defs)
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": params}}


def _simplify(node: dict, compat: bool, defs: dict) -> None:
    """Make a schema digestible for strict chat templates (e.g. Gemma in LM Studio), recursively.

    Always: inline `$ref`s, `Optional[X]` (anyOf [X, null]) -> X, drop titles.
    compat: also move `enum` into the description, since some templates can't render lists.
    """
    if "$ref" in node:
        node.update(defs[node.pop("$ref").split("/")[-1]])
    node.pop("title", None)
    variants = [v for v in node.pop("anyOf", []) if v.get("type") != "null"]
    if variants:
        node.update(variants[0])
        if "$ref" in node:
            _simplify(node, compat, defs)
    if compat and "enum" in node:
        values = ", ".join(str(v) for v in node.pop("enum"))
        node["description"] = f"{node.get('description', '')} One of: {values}.".strip()
        node.setdefault("type", "string")
    for prop in node.get("properties", {}).values():
        _simplify(prop, compat, defs)
    if isinstance(node.get("items"), dict):
        _simplify(node["items"], compat, defs)


def _num_to_str(v: Any) -> Any:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v) if isinstance(v, int) else v


# Small models send resolution as a number (60) instead of the string "60"
Resolution = Annotated[Literal["1", "60", "180", "360", "720", "1D"], BeforeValidator(_num_to_str)]


TOOLS: dict[str, Tool] = {}


def tool(name: str, description: str, args: type[BaseModel], mutating: bool = False,
         summarize: Callable[[Any], str] | None = None):
    def deco(fn):
        TOOLS[name] = Tool(name, description, args, fn, mutating, summarize)
        return fn
    return deco


class NoArgs(BaseModel):
    pass


Symbol = Field(description="market symbol on the venue, e.g. BTCUSDT, ETHTMN, USDTTMN")


class VenueArg(BaseModel):
    venue: str | None = Field(None, description="venue to read (see list_venues); omit for the default venue")


# =========================================================== market data
@tool("list_venues", "Venues Saraf can read: crypto exchanges, stock exchange, currency rates, with what each "
      "supports (trading, futures, order book, candle resolutions). The default venue is where orders go.", NoArgs)
async def list_venues(ctx: AppContext, a: NoArgs):
    return ctx.venues.describe()


class ListMarketsArgs(VenueArg):
    quote: Literal["USDT", "TMN", "IRR"] | None = Field(None, description="filter by quote asset")
    sort_by: Literal["volume", "change", "-change", "change_7d", "-change_7d"] = Field(
        "volume", description="volume, change (24h gainers), -change (24h losers), change_7d, -change_7d")
    limit: int = Field(15, ge=1, le=100)
    search: str | None = Field(None, description="filter by symbol or coin name (English or Persian)")


@tool("list_markets", "List spot markets on a venue with price, 24h/7d change and volume. Use to find symbols, "
      "or for 24h gainers/losers and most traded markets.", ListMarketsArgs)
async def list_markets(ctx: AppContext, a: ListMarketsArgs):
    return await analytics.market_overview(ctx.market(a.venue), a.quote, a.sort_by, a.limit, a.search)


class TickerArgs(VenueArg):
    symbol: str = Symbol


@tool("get_ticker", "Live price snapshot for one market: last, bid/ask, 24h high/low/change/volume.", TickerArgs)
async def get_ticker(ctx: AppContext, a: TickerArgs):
    rows = await analytics.market_overview(ctx.market(a.venue), search=a.symbol, limit=200)
    exact = [r for r in rows if r["symbol"] == a.symbol.upper()]
    return exact[0] if exact else {"error": f"market {a.symbol} not found", "did_you_mean": [r["symbol"] for r in rows[:5]]}


class MoversArgs(VenueArg):
    hours: int = Field(4, ge=1, le=72, description="look-back window in hours")
    quote: Literal["USDT", "TMN"] = "USDT"
    direction: Literal["gainers", "losers"] = Field(
        "gainers", description="gainers = most profitable / biggest price rise (سودده، صعودی); "
                               "losers = biggest price drop (نزولی، ضررده)")
    limit: int = Field(10, ge=1, le=30)


@tool("top_movers", "Most profitable (gainers) or worst (losers) markets over the last N hours, computed "
      "from hourly candles across the venue's most liquid markets.", MoversArgs)
async def top_movers(ctx: AppContext, a: MoversArgs):
    return {"window_hours": a.hours, "quote": a.quote, "direction": a.direction,
            "markets": await analytics.top_movers(ctx.market(a.venue), a.hours, a.quote, a.limit, a.direction)}


class DepthArgs(VenueArg):
    symbol: str = Symbol
    levels: int = Field(10, ge=1, le=50, description="price levels per side to return")


@tool("order_book", "Order book depth analysis: spread, liquidity within 0.5/1/2/5% of mid, bid/ask "
      "imbalance, large walls and top levels.", DepthArgs)
async def order_book(ctx: AppContext, a: DepthArgs):
    return await analytics.order_book_analysis(ctx.market(a.venue), a.symbol, a.levels)


class TradesArgs(VenueArg):
    symbol: str = Symbol
    limit: int = Field(20, ge=1, le=100)


@tool("recent_trades", "Latest public trades on a market, with buy/sell flow summary.", TradesArgs)
async def recent_trades(ctx: AppContext, a: TradesArgs):
    trades = (await ctx.market(a.venue).trades(a.symbol))[: a.limit]
    buy = sum(float(t["sum"]) for t in trades if t.get("isBuyOrder"))
    sell = sum(float(t["sum"]) for t in trades if not t.get("isBuyOrder"))
    return {"symbol": a.symbol.upper(), "buy_value": buy, "sell_value": sell,
            "buy_share_pct": round(100 * buy / (buy + sell), 1) if buy + sell else None, "trades": trades}


class CandlesArgs(VenueArg):
    symbol: str = Symbol
    resolution: Resolution = Field("60", description="1=1m, 60=1h, 1D=daily")
    bars: int = Field(48, ge=5, le=500)


@tool("get_candles", "OHLCV candles for a market (use technical_analysis for indicators).", CandlesArgs)
async def get_candles(ctx: AppContext, a: CandlesArgs):
    now = int(time.time())
    cs = await ctx.market(a.venue).candles(a.symbol, a.resolution, now - analytics.RES_SECONDS[a.resolution] * a.bars, now)
    return {"symbol": a.symbol.upper(), "resolution": a.resolution, "candles": cs[-a.bars:]}


class TAArgs(VenueArg):
    symbol: str = Symbol
    resolution: Resolution = "60"


@tool("technical_analysis", "Indicators for a market: trend, EMA20/50, RSI14, MACD, ATR, Bollinger bands, "
      "support/resistance.", TAArgs)
async def technical_analysis(ctx: AppContext, a: TAArgs):
    return await analytics.technical_analysis(ctx.market(a.venue), a.symbol, a.resolution)


class FGArgs(VenueArg):
    quote: Literal["USDT", "TMN"] = "USDT"


@tool("fear_greed_index", "Fear & Greed: the global crypto index (alternative.me, 7-day history) plus "
      "'Market Pulse', a local index computed from the venue's market breadth, momentum and buy pressure.", FGArgs)
async def fear_greed_index(ctx: AppContext, a: FGArgs):
    try:
        glob = await sentiment.global_fear_greed()
    except Exception as e:
        glob = {"error": str(e)}
    return {"global": glob, "market_pulse": await sentiment.market_pulse(ctx.market(a.venue), a.quote)}


@tool("market_risk", "Risk score (0-100) for a market from volatility, liquidity, spread and RSI extremes, "
      "with suggested max leverage and stop distance.", TickerArgs)
async def market_risk(ctx: AppContext, a: TickerArgs):
    return await sentiment.market_risk(ctx.market(a.venue), a.symbol)


class CoinArgs(BaseModel):
    coin: str | None = Field(None, description="coin key like BTC; omit for top 15 by rank")


@tool("global_coin_stats", "Global market stats for coins: rank, market cap, dominance, ATH, 1h-1y changes.", CoinArgs)
async def global_coin_stats(ctx: AppContext, a: CoinArgs):
    if a.coin:
        return await ctx.venue.coin_stats(a.coin)
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.get("https://api.coingecko.com/api/v3/coins/markets", params={
            "vs_currency": "usd", "per_page": 15, "price_change_percentage": "1h,24h,7d,30d"})
    keep = ("symbol", "name", "market_cap_rank", "current_price", "market_cap", "total_volume", "ath",
            "ath_change_percentage", "price_change_percentage_1h_in_currency", "price_change_percentage_24h_in_currency",
            "price_change_percentage_7d_in_currency", "price_change_percentage_30d_in_currency")
    return [{k: c.get(k) for k in keep} for c in r.json()]


# =============================================================== internet
class NewsArgs(BaseModel):
    query: str | None = Field(None, description="keywords to filter by, e.g. 'bitcoin ETF' or 'solana'")
    hours: int = Field(48, ge=1, le=168)
    limit: int = Field(12, ge=1, le=30)


@tool("crypto_news", "Latest crypto headlines from CoinTelegraph, CoinDesk, Decrypt, The Block, Bitcoin "
      "Magazine and ArzDigital (Persian).", NewsArgs)
async def crypto_news(ctx: AppContext, a: NewsArgs):
    return await web.crypto_news(a.query, a.limit, a.hours)


class SearchArgs(BaseModel):
    query: str
    news: bool = Field(False, description="search news instead of general web")
    limit: int = Field(8, ge=1, le=15)


@tool("web_search", "Search the internet (general or news). Use for macro events, regulations, project "
      "updates, or anything not in the feeds.", SearchArgs)
async def web_search(ctx: AppContext, a: SearchArgs):
    return await web.web_search(a.query, a.limit, a.news, ctx.settings.tavily_api_key)


class UrlArgs(BaseModel):
    url: str


@tool("read_webpage", "Fetch a web page and return its main text (to read a news article in full).", UrlArgs)
async def read_webpage(ctx: AppContext, a: UrlArgs):
    return await web.read_url(a.url)


# ================================================================ account
@tool("spot_balances", "Spot wallet balances (free / locked / total) with estimated USDT and TMN value.", NoArgs)
async def spot_balances(ctx: AppContext, a: NoArgs):
    return {"mode": ctx.spot.mode, **await value_balances(ctx, await ctx.spot.balances())}


async def value_balances(ctx: AppContext, bals: dict, venue=None) -> dict:
    """Adds value_usdt to each balance and totals in USDT and TMN (at last prices on `venue`, default the
    default venue)."""
    markets = await (venue or ctx.venue).markets()

    def px(asset: str, quote: str) -> float:
        if asset == quote:
            return 1.0
        m = markets.get(f"{asset}{quote}")
        return float((m or {}).get("stats", {}).get("lastPrice") or 0)

    usdt_tmn = px("USDT", "TMN")
    total_usdt = 0.0
    for asset, b in bals.items():
        v = b["total"] * (px(asset, "USDT") or (px(asset, "TMN") / usdt_tmn if usdt_tmn else 0))
        if asset == "TMN" and usdt_tmn:
            v = b["total"] / usdt_tmn
        b["value_usdt"] = v
        total_usdt += v
    return {"balances": bals, "total_value_usdt": total_usdt,
            "total_value_tmn": total_usdt * usdt_tmn if usdt_tmn else None}


class OpenOrdersArgs(BaseModel):
    symbol: str | None = None


@tool("spot_open_orders", "List active spot orders.", OpenOrdersArgs)
async def spot_open_orders(ctx: AppContext, a: OpenOrdersArgs):
    return {"mode": ctx.spot.mode, "orders": await ctx.spot.open_orders(a.symbol)}


@tool("spot_trade_history", "Recent executed spot trades of the user.", OpenOrdersArgs)
async def spot_trade_history(ctx: AppContext, a: OpenOrdersArgs):
    return {"mode": ctx.spot.mode, "trades": await ctx.spot.trades(a.symbol)}


@tool("futures_account", "Futures account: wallets (available, margin, unrealized PnL, equity) and positions.", NoArgs)
async def futures_account(ctx: AppContext, a: NoArgs):
    acct = await ctx.futures.account()
    acct["positions"] = await ctx.futures.positions()
    acct["open_orders"] = await ctx.futures.open_orders()
    return acct


@tool("futures_history", "Futures trade history: opens, closes, TP/SL hits, liquidations with realized PnL.", NoArgs)
async def futures_history(ctx: AppContext, a: NoArgs):
    return await ctx.futures.history(30)


@tool("futures_markets", "Markets that support leverage (futures) and their max leverage.", NoArgs)
async def futures_markets(ctx: AppContext, a: NoArgs):
    return {m["symbol"]: {"max_leverage": m["max_leverage"], "price": m["price"]}
            for m in await ctx.venue.futures_markets()}


# ================================================================ trading
class SpotOrderArgs(BaseModel):
    symbol: str = Symbol
    side: Literal["BUY", "SELL"]
    type: Literal["MARKET", "LIMIT"] = "MARKET"
    quantity: float | None = Field(None, description="amount in BASE asset (e.g. BTC). Give this OR quote_amount")
    quote_amount: float | None = Field(None, description="amount in QUOTE asset to spend/receive (USDT or TMN)")
    price: float | None = Field(None, description="limit price (LIMIT only)")
    reason: str = Field(description="one-line rationale shown to the user")


_QUOTE_FA = {"TMN": "تومان", "USDT": "تتر"}


def _num(x: float) -> str:
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:,.8g}"


def _spot_summary(a: SpotOrderArgs) -> str:
    sym = a.symbol.upper()
    quote = next((q for q in _QUOTE_FA if sym.endswith(q)), "")
    base = sym[: -len(quote)] if quote else sym
    kind = {"MARKET": "بازار", "LIMIT": "محدود"}.get(a.type, a.type)
    side = "خرید" if a.side == "BUY" else "فروش"
    amt = f"{_num(a.quantity)} {base}" if a.quantity is not None else f"{_num(a.quote_amount)} {_QUOTE_FA.get(quote, quote)}"
    px = f" با قیمت {_num(a.price)}" if a.type == "LIMIT" and a.price else ""
    return f"{side} {kind} {sym} — {amt}{px}"


@tool("spot_place_order", "Place a spot BUY/SELL order (MARKET or LIMIT). Requires user confirmation.",
      SpotOrderArgs, mutating=True, summarize=_spot_summary)
async def spot_place_order(ctx: AppContext, a: SpotOrderArgs):
    return await ctx.spot.place_order(a.symbol, a.side, a.type, a.quantity, a.price, a.quote_amount)


class CancelArgs(BaseModel):
    order_id: str = Field(description="clientOrderId / order id")


@tool("spot_cancel_order", "Cancel an active spot order.", CancelArgs, mutating=True,
      summarize=lambda a: f"لغو سفارش اسپات {a.order_id}")
async def spot_cancel_order(ctx: AppContext, a: CancelArgs):
    return await ctx.spot.cancel_order(a.order_id)


class SellAllArgs(BaseModel):
    asset: str = Field(description="asset to fully sell, e.g. BTC")
    quote: Literal["USDT", "TMN"] = "USDT"
    reason: str


@tool("spot_close_asset", "Close a spot holding: market-sell the entire free balance of an asset.", SellAllArgs,
      mutating=True, summarize=lambda a: f"فروش کل {a.asset.upper()} آزاد به {_QUOTE_FA.get(a.quote, a.quote)}")
async def spot_close_asset(ctx: AppContext, a: SellAllArgs):
    bals = await ctx.spot.balances()
    free = bals.get(a.asset.upper(), {}).get("free", 0)
    if free <= 0:
        return {"error": f"no free {a.asset.upper()} balance"}
    return await ctx.spot.place_order(f"{a.asset.upper()}{a.quote}", "SELL", "MARKET", quantity=free)


class FuturesOpenArgs(BaseModel):
    symbol: str = Symbol
    side: Literal["LONG", "SHORT"]
    leverage: float = Field(ge=1, le=125)
    margin: float | None = Field(None, description="collateral in quote asset (USDT/TMN). Give this OR quantity")
    quantity: float | None = Field(None, description="position size in base asset")
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    price: float | None = Field(None, description="limit entry price")
    take_profit: float | None = None
    stop_loss: float | None = None
    reason: str = Field(description="one-line rationale shown to the user")


def _fut_summary(a: FuturesOpenArgs) -> str:
    side = "لانگ" if a.side == "LONG" else "شورت"
    size = f"مارجین {_num(a.margin)}" if a.margin is not None else f"مقدار {_num(a.quantity)}"
    extra = "".join([f" · حد سود {_num(a.take_profit)}" if a.take_profit else "",
                     f" · حد ضرر {_num(a.stop_loss)}" if a.stop_loss else ""])
    px = f" با قیمت {_num(a.price)}" if a.order_type == "LIMIT" and a.price else ""
    return f"فیوچرز {side} {a.symbol.upper()} اهرم {a.leverage:g}× — {size}{px}{extra}"


@tool("futures_open_position", "Open a leveraged LONG/SHORT futures position (isolated margin). "
      "Always set a stop_loss unless the user explicitly refuses. Requires user confirmation.",
      FuturesOpenArgs, mutating=True, summarize=_fut_summary)
async def futures_open_position(ctx: AppContext, a: FuturesOpenArgs):
    return await ctx.futures.open_position(a.symbol, a.side, a.leverage, a.margin, a.quantity, a.order_type,
                                           a.price, a.take_profit, a.stop_loss)


class FuturesCloseArgs(BaseModel):
    position_id: str
    fraction: float = Field(1.0, gt=0, le=1, description="1 = close all, 0.5 = close half")
    reason: str


@tool("futures_close_position", "Close all or part of a futures position at market.", FuturesCloseArgs,
      mutating=True, summarize=lambda a: f"بستن {a.fraction:.0%} پوزیشن {a.position_id}")
async def futures_close_position(ctx: AppContext, a: FuturesCloseArgs):
    return await ctx.futures.close_position(a.position_id, a.fraction)


class TPSLArgs(BaseModel):
    position_id: str
    take_profit: float | None = Field(None, description="new TP price; 0 removes it")
    stop_loss: float | None = Field(None, description="new SL price; 0 removes it")


@tool("futures_set_tp_sl", "Set or change take-profit / stop-loss of a futures position.", TPSLArgs,
      mutating=True, summarize=lambda a: f"تنظیم حد سود {a.take_profit} و حد ضرر {a.stop_loss} برای {a.position_id}")
async def futures_set_tp_sl(ctx: AppContext, a: TPSLArgs):
    return await ctx.futures.set_tp_sl(a.position_id, a.take_profit, a.stop_loss)


@tool("futures_cancel_order", "Cancel a pending futures limit order.", CancelArgs, mutating=True,
      summarize=lambda a: f"لغو سفارش فیوچرز {a.order_id}")
async def futures_cancel_order(ctx: AppContext, a: CancelArgs):
    return await ctx.futures.cancel_order(a.order_id)


# ================================================================= alerts
class AlertArgs(BaseModel):
    symbol: str = Symbol
    condition: Literal["above", "below"] = Field(description="fire when price goes above / below `price`")
    price: float
    note: str | None = Field(None, description="what to remind the user of when it fires")


@tool("create_price_alert", "Create a price alert; the user gets a live notification in the app when it fires.",
      AlertArgs)
async def create_price_alert(ctx: AppContext, a: AlertArgs):
    from ..brokers import MarketInfo
    q = await MarketInfo(ctx.venue).quote(a.symbol)
    return ctx.alerts.create(q["symbol"], a.condition, a.price, a.note, q["last"])


class AlertListArgs(BaseModel):
    status: Literal["active", "triggered", "canceled"] | None = None


@tool("list_price_alerts", "List price alerts (active, triggered or canceled).", AlertListArgs)
async def list_price_alerts(ctx: AppContext, a: AlertListArgs):
    return ctx.alerts.list(a.status)


class AlertIdArgs(BaseModel):
    alert_id: str


@tool("cancel_price_alert", "Cancel an active price alert.", AlertIdArgs)
async def cancel_price_alert(ctx: AppContext, a: AlertIdArgs):
    return ctx.alerts.cancel(a.alert_id) or {"error": "alert not found"}


def openai_tools(compat: bool = False, names: list[str] | None = None) -> list[dict]:
    return [t.schema(compat) for n, t in TOOLS.items() if names is None or n in names]


from . import genui  # noqa: E402,F401  (registers the generative UI tools)

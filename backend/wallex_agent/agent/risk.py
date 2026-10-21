"""Pre-trade risk guard. Runs before a mutating tool is proposed to the user, so
limit violations are reported to the model immediately instead of after approval."""

from __future__ import annotations

from ..brokers import MarketInfo
from ..brokers.futures import liquidation_price
from .context import AppContext


class RiskViolation(Exception):
    pass


def _limit_for(ctx: AppContext, quote: str) -> float:
    return ctx.settings.max_order_notional_tmn if quote == "TMN" else ctx.settings.max_order_notional_usdt


async def check(ctx: AppContext, tool_name: str, args) -> dict:
    """Returns a dict of computed risk facts; raises RiskViolation if the action breaks a limit."""
    s = ctx.settings
    symbol = getattr(args, "symbol", None)
    if symbol is None and tool_name == "spot_close_asset":
        symbol = f"{args.asset.upper()}{args.quote}"
    if symbol and s.allowlist and symbol.upper() not in s.allowlist:
        raise RiskViolation(f"{symbol.upper()} is not in SYMBOL_ALLOWLIST")

    facts: dict = {}
    if tool_name == "spot_place_order":
        q = await MarketInfo(ctx.client).quote(args.symbol)
        ref = args.price or (q["ask"] if args.side == "BUY" else q["bid"]) or q["last"]
        notional = args.quote_amount if args.quantity is None else args.quantity * ref
        facts = {"estimated_notional": notional, "quote": q["quote"], "reference_price": ref}
        if notional and notional > _limit_for(ctx, q["quote"]):
            raise RiskViolation(f"order value {notional:,.2f} {q['quote']} exceeds the per-order limit "
                                f"{_limit_for(ctx, q['quote']):,.0f} {q['quote']}")
    elif tool_name == "futures_open_position":
        if args.leverage > s.max_leverage:
            raise RiskViolation(f"leverage {args.leverage:g}x exceeds MAX_LEVERAGE {s.max_leverage}x")
        q = await MarketInfo(ctx.client).quote(args.symbol)
        ref = args.price or q["mark"]
        notional = args.quantity * ref if args.quantity is not None else (args.margin or 0) * args.leverage
        facts = {"estimated_notional": notional, "quote": q["quote"], "reference_price": ref,
                 "margin": notional / args.leverage if args.leverage else None}
        if notional > _limit_for(ctx, q["quote"]) * args.leverage:
            raise RiskViolation(f"position notional {notional:,.2f} {q['quote']} exceeds limit "
                                f"(per-order limit x leverage = {_limit_for(ctx, q['quote']) * args.leverage:,.0f})")
        if args.stop_loss:
            side = 1 if args.side == "LONG" else -1
            if (args.stop_loss - ref) * side >= 0:
                raise RiskViolation("stop_loss is on the wrong side of the entry price")
            liq = liquidation_price(args.side, ref, args.leverage)
            if (args.stop_loss - liq) * side <= 0:
                raise RiskViolation(f"stop_loss {args.stop_loss:g} is beyond the liquidation price ~{liq:.6g}")
            facts["liquidation_price"] = liq
            facts["risk_at_stop"] = abs(ref - args.stop_loss) / ref * notional
        else:
            facts["warning"] = "no stop loss set"
        if args.take_profit:
            side = 1 if args.side == "LONG" else -1
            if (args.take_profit - ref) * side <= 0:
                raise RiskViolation("take_profit is on the wrong side of the entry price")
    return facts

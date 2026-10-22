"""Which tool schemas to send with a chat request.

All 35 schemas cost ~4.5k tokens on every LLM step, and most questions ("قیمت تتر") need only the
read-only market tools. Heavier groups join once the conversation mentions them and stay for the rest
of the session, so follow-ups ("ببندش", "حالا شورت بزن") keep working.
"""

from __future__ import annotations

GROUPS: dict[str, set[str]] = {
    "futures": {"futures_account", "futures_history", "futures_markets", "futures_open_position",
                "futures_close_position", "futures_set_tp_sl", "futures_cancel_order", "show_position_planner"},
    "trade": {"spot_place_order", "spot_cancel_order", "spot_close_asset", "show_trade_ticket"},
    "viz": {"show_comparison_chart", "show_market_heatmap", "show_price_chart", "render_dashboard"},
}

TRIGGERS: dict[str, tuple[str, ...]] = {
    "futures": ("فیوچرز", "اهرم", "لوریج", "لانگ", "شورت", "پوزیشن", "مارجین", "لیکوئید", "futures", "leverage",
                "long", "short", "position", "margin", "liquidat", "perp"),
    "trade": ("بخر", "بفروش", "خرید", "فروش", "سفارش", "معامله کن", "لغو", "ببند", "برگه", "buy", "sell", "order",
              "cancel", "ticket"),
    "viz": ("نمودار", "چارت", "نقشه", "حرارتی", "مقایسه", "گزارش", "داشبورد", "رسم", "chart", "heatmap", "compare",
            "report", "dashboard", "plot"),
}


def select_tools(all_names: list[str], messages: list[dict]) -> list[str]:
    """Core tools always; a group when any user message mentions it or an earlier turn already used it."""
    said = " ".join(str(m.get("content") or "") for m in messages if m.get("role") == "user").lower()
    used = {c["function"]["name"] for m in messages for c in (m.get("tool_calls") or [])}
    off = set()
    for g, names in GROUPS.items():
        if not (names & used or any(t in said for t in TRIGGERS[g])):
            off |= names
    return [n for n in all_names if n not in off]

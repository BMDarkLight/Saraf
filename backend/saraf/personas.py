"""Copy-trading personas: named strategy presets, each with a public profile and its own track record.

Every persona runs on the VPS as a shadow session (real Wallex data, simulated fills on the live order book, no
real orders) with its own journal in data/engine/persona-<id>, so its profile shows what it actually did, not a
made-up history. The "strategist" persona is the existing strategy session (data/engine/strategy), which already
runs the AI decider. Copying a persona writes its preset to data/engine/tuning.env (tuning.py) and restarts the
main engine, which then trades with the persona's rules.

Personas other than the strategist use the rule decider: several sessions asking the LLM every bar would spend the
engine's model budget (it ran out once already, 2026-10-22), and the rules beat the AI in every backtest so far.
"""

from __future__ import annotations

from typing import Any

PERSONAS: list[dict[str, Any]] = [
    {
        "id": "strategist", "run": "strategy", "name": "استراتژیست", "handle": "strategist",
        "tagline": "هسته تتری + ماهواره خرید افت با تأیید هوش مصنوعی",
        "bio": "همان استراتژی اصلی: تومان را به تتر می‌برد تا از کاهش ارزش تومان سود ببرد و با بخشی از سرمایه افت‌های "
               "بازارهای تتری را می‌خرد. هوش مصنوعی هر ورود را تأیید یا رد می‌کند و زمان خروج پایانی را انتخاب می‌کند.",
        "glyph": "◆", "hue": 212, "risk": 2, "style": ["هسته تتری", "خرید افت", "AI"],
        "preset": {},   # the strategy profile itself (deploy/strategy.env)
    },
    {
        "id": "guardian", "name": "نگهبان تتر", "handle": "usdt_guardian",
        "tagline": "فقط تتر نگه می‌دارد؛ بدون معامله رمزارز",
        "bio": "تنها لبه‌ای که در آزمون‌ها بعد از کارمزد باقی ماند، کاهش ارزش تومان بود. نگهبان کل سرمایه را به تتر می‌برد، "
               "با محافظ ۵٪ از ریزش تتر/تومان دفاع می‌کند و پیش از پایان چالش همه را به تومان برمی‌گرداند.",
        "glyph": "₮", "hue": 152, "risk": 1, "style": ["هسته تتری", "کم‌معامله"],
        "preset": {"engine_setups": "", "engine_decider": "rules", "engine_core_guard_pct": 0.05},
    },
    {
        "id": "dip_hunter", "name": "شکارچی افت", "handle": "dip_hunter",
        "tagline": "افت‌ها را تهاجمی‌تر می‌خرد: ۳ پوزیشن، ریسک ۱٫۵٪",
        "bio": "همان ستاپ‌های خرید افت با دست بازتر: دروازه فراگیری ۱۵٪، آستانه امتیاز پایین‌تر، سه پوزیشن هم‌زمان و "
               "ریسک ۱٫۵٪ در هر معامله. معامله بیشتر، نوسان بیشتر.",
        "glyph": "▼", "hue": 14, "risk": 4, "style": ["خرید افت", "پرمعامله", "قواعد"],
        "preset": {"engine_decider": "rules", "engine_max_exposure_pct": 0.8, "engine_max_position_pct": 0.3,
                   "engine_max_positions": 3, "engine_risk_per_trade_pct": 0.015, "engine_breadth_gate_pct": 15.0,
                   "engine_min_score": 0.45, "engine_cooldown_bars": 2},
    },
    {
        "id": "trend_rider", "name": "موج‌سوار", "handle": "trend_rider",
        "tagline": "آزمایشی: شکست سقف و پولبک روند",
        "bio": "روند را دنبال می‌کند: شکست سقف ۲۰ کندل با حجم بالا و پولبک در روند صعودی. در تحقیق ۲۸ روزه روی بازارهای "
               "تومانی این ستاپ‌ها بعد از کارمزد لبه نداشتند؛ این پرسونا برای سنجیدن همان نتیجه روی بازارهای تتری است.",
        "glyph": "▲", "hue": 268, "risk": 4, "style": ["دنبال‌کننده روند", "آزمایشی", "قواعد"],
        "preset": {"engine_decider": "rules", "engine_setups": "trend_pullback,breakout",
                   "engine_max_exposure_pct": 0.6, "engine_max_position_pct": 0.3, "engine_max_positions": 2,
                   "engine_breadth_gate_pct": 35.0},
    },
    {
        "id": "cautious", "name": "محتاط", "handle": "cautious",
        "tagline": "فقط قوی‌ترین سیگنال‌ها، یک پوزیشن، محافظ تنگ",
        "bio": "هسته تتری با محافظ ۳٪ و ماهواره‌ای که فقط در بازار سالم (دست‌کم ۴۰٪ بازارها مثبت) و با امتیاز بالا وارد "
               "می‌شود؛ یک پوزیشن در هر لحظه و توقف خرید با ۳٪ افت.",
        "glyph": "●", "hue": 190, "risk": 2, "style": ["هسته تتری", "گزیده", "قواعد"],
        "preset": {"engine_decider": "rules", "engine_max_positions": 1, "engine_breadth_gate_pct": 40.0,
                   "engine_min_score": 0.65, "engine_core_guard_pct": 0.03, "engine_drawdown_pause_pct": 0.03,
                   "engine_drawdown_stop_pct": 0.06},
    },
]
BY_ID = {p["id"]: p for p in PERSONAS}


def run_name(p: dict) -> str:
    """The journal directory under data/engine that holds the persona's track record."""
    return p.get("run") or f"persona-{p['id']}"


def session_ids() -> list[str]:
    """Personas that need their own shadow session (saraf-persona@<id>.service)."""
    return [p["id"] for p in PERSONAS if not p.get("run")]


if __name__ == "__main__":   # used by deploy/deploy.sh
    print(" ".join(session_ids()))

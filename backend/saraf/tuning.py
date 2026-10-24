"""Strategy tuning from the UI: a bounded set of engine settings, saved as data/engine/tuning.env.

saraf-engine.service loads that file after the strategy profile and before the challenge window, so a saved
setting replaces the profile's value on the next engine restart. Only the parameters below can be set, each
within bounds that keep the risk rules meaningful; the quote currency, the capital and the challenge end are not
tunable here. Personas (personas.py) are named sets of these same parameters.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .engine.strategy import ALL_SETUPS

# key (Settings field), type, bounds or choices, Persian label, group, help
PARAMS: list[dict[str, Any]] = [
    # --- satellite size and risk
    {"key": "engine_max_exposure_pct", "type": "pct", "min": 0.0, "max": 1.0, "step": 0.05, "group": "risk",
     "label": "سقف کل ماهواره", "help": "حداکثر سهم حساب در پوزیشن‌های باز (در وضعیت خنثی × ۰٫۶۷)"},
    {"key": "engine_max_position_pct", "type": "pct", "min": 0.05, "max": 0.5, "step": 0.05, "group": "risk",
     "label": "سقف هر پوزیشن", "help": "حداکثر سهم حساب در یک معامله"},
    {"key": "engine_max_positions", "type": "int", "min": 1, "max": 5, "step": 1, "group": "risk",
     "label": "حداکثر پوزیشن هم‌زمان"},
    {"key": "engine_risk_per_trade_pct", "type": "pct", "min": 0.0025, "max": 0.03, "step": 0.0025, "group": "risk",
     "label": "ریسک هر معامله", "help": "زیان در صورت خوردن حد ضرر، با کارمزد"},
    {"key": "engine_drawdown_pause_pct", "type": "pct", "min": 0.02, "max": 0.15, "step": 0.01, "group": "risk",
     "label": "توقف خرید در افت", "help": "افت از اوج که خرید جدید را ۱۲ ساعت متوقف می‌کند"},
    {"key": "engine_drawdown_stop_pct", "type": "pct", "min": 0.04, "max": 0.25, "step": 0.01, "group": "risk",
     "label": "تسویه کامل در افت", "help": "افت از اوج که همه چیز را می‌فروشد و موتور را متوقف می‌کند"},
    # --- entries
    {"key": "engine_setups", "type": "multi", "choices": list(ALL_SETUPS), "group": "entry",
     "label": "ستاپ‌های ورود", "help": "خالی = ماهواره خاموش (فقط هسته تتری)"},
    {"key": "engine_min_score", "type": "float", "min": 0.3, "max": 0.95, "step": 0.05, "group": "entry",
     "label": "حداقل امتیاز سیگنال"},
    {"key": "engine_breadth_gate_pct", "type": "float", "min": 0.0, "max": 60.0, "step": 5.0, "group": "entry",
     "label": "دروازه فراگیری بازار (٪ مثبت)", "help": "کمتر از این درصد بازارِ مثبت در ۲۴ ساعت = خرید ممنوع"},
    {"key": "engine_dip_atr", "type": "float", "min": 0.0, "max": 8.0, "step": 0.5, "group": "entry",
     "label": "اندازه افت لازم (برحسب ATR)", "help": "۰ = افت ثابت ۱٫۵٪ در یک ساعت؛ بیشتر از ۰ = افت نسبت به نوسان همان بازار"},
    {"key": "engine_min_atr_pct", "type": "float", "min": 0.1, "max": 1.5, "step": 0.05, "group": "entry",
     "label": "حداقل نوسان (ATR٪)"},
    {"key": "engine_universe_size", "type": "int", "min": 3, "max": 20, "step": 1, "group": "entry",
     "label": "تعداد بازارهای پایش‌شده"},
    {"key": "engine_cooldown_bars", "type": "int", "min": 0, "max": 16, "step": 1, "group": "time",
     "label": "استراحت بعد از خروج (کندل)"},
    # --- execution and exits
    {"key": "engine_execution", "type": "choice", "choices": ["maker", "taker"], "group": "exec",
     "label": "نوع سفارش", "help": "maker: سفارش محدود روی بهترین قیمت؛ taker: سفارش بازار"},
    {"key": "engine_entry_chase_atr", "type": "float", "min": 0.05, "max": 1.0, "step": 0.05, "group": "exec",
     "label": "لغو ورود اگر قیمت فرار کرد (ATR)"},
    {"key": "engine_entry_timeout_bars", "type": "int", "min": 1, "max": 8, "step": 1, "group": "time",
     "label": "مهلت پرشدن سفارش ورود (کندل)"},
    {"key": "engine_time_stop_bars", "type": "int", "min": 8, "max": 96, "step": 4, "group": "time",
     "label": "توقف زمانی (کندل ۱۵ دقیقه)"},
    # --- AI
    {"key": "engine_decider", "type": "choice", "choices": ["ai", "rules"], "group": "ai",
     "label": "تصمیم‌گیر ورود", "help": "ai: مدل زبانی تأیید/رد و اندازه می‌دهد؛ rules: فقط قواعد"},
    {"key": "engine_ai_reviews", "type": "choice", "choices": ["off", "tighten", "full"], "group": "ai",
     "label": "بازبینی پوزیشن‌ها با AI"},
    # --- USDT core
    {"key": "engine_core_enabled", "type": "bool", "group": "core", "label": "هسته تتری"},
    {"key": "engine_core_guard_pct", "type": "pct", "min": 0.02, "max": 0.15, "step": 0.01, "group": "core",
     "label": "محافظ تتر", "help": "افت تتر/تومان از اوج که هسته را به تومان می‌برد"},
    {"key": "engine_core_exit_hours", "type": "float", "min": 1.0, "max": 24.0, "step": 1.0, "group": "time",
     "label": "پنجره خروج پایانی (ساعت)"},
    {"key": "engine_core_exit_slices", "type": "int", "min": 1, "max": 6, "step": 1, "group": "time",
     "label": "تعداد بخش‌های فروش تتر"},
    {"key": "engine_final_sweep", "type": "bool", "group": "core", "label": "فروش همه رمزارزها در پایان چالش"},
    # copy-trading a real trader: the address is set by the copy button (group "follow" has no settings card)
    {"key": "engine_follow_address", "type": "address", "group": "follow", "label": "تریدر کپی‌شده"},
    {"key": "engine_follow_pct", "type": "pct", "min": 0.1, "max": 1.0, "step": 0.05, "group": "risk",
     "label": "سهم سرمایه در کپی تریدر", "help": "وقتی یک تریدر واقعی کپی می‌شود، حداکثر این سهم از حساب پوزیشن‌های لانگ او را دنبال می‌کند"},
]
PARAMS += [
    # --- timings (the challenge window and the schedule around it); the bar-based ones are 15-minute bars
    {"key": "engine_end_time", "type": "datetime", "group": "time", "label": "پایان چالش",
     "help": "همه چیز پیش از این زمان به تومان فروخته می‌شود (به وقت تهران وارد کنید)"},
    {"key": "engine_no_entry_before_end_min", "type": "int", "min": 0, "max": 1440, "step": 15, "group": "time",
     "label": "توقف خرید جدید پیش از پایان (دقیقه)"},
    {"key": "engine_flatten_before_end_min", "type": "int", "min": 15, "max": 240, "step": 5, "group": "time",
     "label": "فروش همه چیز پیش از پایان (دقیقه)"},
    {"key": "engine_pause_hours", "type": "float", "min": 1.0, "max": 48.0, "step": 1.0, "group": "time",
     "label": "مدت توقف خرید پس از افت (ساعت)"},
]
for _p in PARAMS:   # order the timing group: window first, then the bar-based timers
    _p.setdefault("order", ["engine_end_time", "engine_no_entry_before_end_min", "engine_flatten_before_end_min",
                            "engine_core_exit_hours", "engine_core_exit_slices", "engine_pause_hours",
                            "engine_time_stop_bars", "engine_entry_timeout_bars", "engine_cooldown_bars"].index(_p["key"])
                   if _p["group"] == "time" else 0)
PARAMS.sort(key=lambda p: (p["group"] != "time", p["order"]) if p["group"] == "time" else (True, 0))
BY_KEY = {p["key"]: p for p in PARAMS}
GROUPS = {"time": "زمان‌بندی", "risk": "اندازه و ریسک", "entry": "ورود", "exec": "اجرا و خروج", "ai": "هوش مصنوعی",
          "core": "هسته تتری"}


class TuningError(ValueError):
    pass


def _utc_iso(raw: Any, future: bool = True) -> str:
    """An ISO time with a UTC offset (e.g. 2026-10-26T23:59:00+03:30) -> '2026-10-26T20:29:00Z'."""
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except ValueError:
        raise TuningError(f"{raw!r} is not an ISO date-time")
    if t.tzinfo is None:
        raise TuningError("the end time needs a UTC offset, e.g. +03:30 for Tehran")
    import time as _time
    if future and t.timestamp() < _time.time() + 3600:   # a past end would sell everything to TMN at once and stop the engine
        raise TuningError("the end time must be at least an hour from now")
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate(values: dict[str, Any], new: bool = True) -> dict[str, Any]:
    """Coerce and bound-check UI values. Unknown keys or out-of-range numbers are errors, not silently clamped:
    the person must see that the engine will not run what they typed. `new=False` reads values saved earlier: an end
    time saved then is kept even once it is near or past (only a newly entered one must be an hour ahead)."""
    out: dict[str, Any] = {}
    for key, raw in values.items():
        p = BY_KEY.get(key)
        if not p:
            raise TuningError(f"{key} is not tunable")
        t = p["type"]
        try:
            if t == "datetime":
                v = _utc_iso(raw, future=new)
            elif t == "address":
                v = str(raw).strip().lower()
                if v and not re.match(r"^0x[0-9a-f]{40}$", v):
                    raise TuningError(f"{key}: not a 0x... address")
            elif t == "bool":
                v: Any = raw if isinstance(raw, bool) else str(raw).strip().lower() in ("1", "true", "yes", "on")
            elif t == "int":
                v = int(raw)
            elif t in ("float", "pct"):
                v = float(raw)
            elif t == "choice":
                v = str(raw)
                if v not in p["choices"]:
                    raise TuningError(f"{key} must be one of {p['choices']}")
            else:   # multi
                items = raw if isinstance(raw, list) else [x for x in str(raw).split(",") if x.strip()]
                items = [str(x).strip() for x in items]
                bad = [x for x in items if x not in p["choices"]]
                if bad:
                    raise TuningError(f"{key}: unknown {bad}")
                v = ",".join(dict.fromkeys(items))
        except (TypeError, ValueError) as e:
            if isinstance(e, TuningError):
                raise
            raise TuningError(f"{key}: {raw!r} is not a valid {t}")
        if t in ("int", "float", "pct") and not (p["min"] - 1e-9 <= v <= p["max"] + 1e-9):
            raise TuningError(f"{key} must be between {p['min']} and {p['max']}")
        out[key] = v
    check_pair(out)
    return out


def check_pair(values: dict[str, Any]) -> None:
    pause, stop = values.get("engine_drawdown_pause_pct"), values.get("engine_drawdown_stop_pct")
    if pause is not None and stop is not None and pause >= stop:
        raise TuningError("the drawdown pause must be below the drawdown stop")


# The challenge window and the sell-off schedule around it: the person's, not a trading style
WINDOW_KEYS = ("engine_end_time", "engine_no_entry_before_end_min", "engine_flatten_before_end_min",
               "engine_core_exit_hours", "engine_core_exit_slices")


def keep_on_copy(overrides: dict[str, Any]) -> dict[str, Any]:
    """Overrides that survive copying a persona or stopping a copy (a persona never sets these)."""
    return {k: v for k, v in overrides.items() if k in WINDOW_KEYS}


def to_env(values: dict[str, Any]) -> str:
    def fmt(v):
        return ("true" if v else "false") if isinstance(v, bool) else str(v)
    return "".join(f"{k.upper()}={fmt(v)}\n" for k, v in values.items())


def read_env(path: Path) -> dict[str, Any]:
    """The overrides saved in a tuning file, typed (unknown or broken lines are ignored)."""
    out: dict[str, Any] = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        k, v = line.split("=", 1)
        k = k.strip().lower()
        if k in BY_KEY:
            try:
                out.update(validate({k: v.strip()}, new=False))
            except TuningError:
                pass
    return out

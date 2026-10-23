"""A small chat about the strategy: the person describes an idea ("trade more", "be safer", "why no trades?"), the
model explains in Persian and may propose concrete parameter changes. A proposal is only a suggestion: it is
validated against the tuning schema (tuning.py) and shown as a diff; nothing changes until the person applies it
through the same tuning endpoint as the form (with the live-trading confirmation).
"""

from __future__ import annotations

import json
import re
from typing import Any

from openai import AsyncOpenAI

from .config import Settings
from .personas import PERSONAS
from .tuning import BY_KEY, PARAMS, TuningError, validate

SYSTEM = """You are the strategy assistant of an autonomous trading engine on the Wallex exchange (Iran).
The person running it chats with you about the strategy. Answer in Persian (Farsi), briefly and concretely
(at most ~6 short sentences or a short list). You can change the tunable parameters below with two tools:
- `apply_changes`: sets them NOW (the engine restarts with them). Use it whenever the person asks you to set, change,
  apply, raise, lower, turn on/off or "dial" something, or confirms a proposal ("yes", "do it", "apply"). Include
  EVERY parameter the request implies, all at once; you may change any number of them.
- `propose_changes`: only a suggestion shown as a diff, when the person asks for advice or options without asking you
  to make the change.
If `apply_changes` is not available, applying is switched off in the UI: propose instead and say so. Never claim a
change happened unless you called `apply_changes`; the tool result tells you what was applied.
Times: the person is in Tehran (UTC+03:30). engine_end_time takes ISO-8601 with an offset, e.g.
2026-10-26T23:59:00+03:30. Bar-based timers count 15-minute bars (4 bars = 1 hour).

HOW THE STRATEGY WORKS
- The challenge scores realized TMN return on 5,000,000 TMN of spot trade credit (no leverage), plus drawdown,
  stability and activity. Everything is sold back to TMN shortly before the end.
- USDT core: free TMN is held as USDT, because the toman's depreciation was the only edge that survived fees in
  testing (USDT/TMN +78% over 180 days; 72h windows positive 65% of the time). A guard moves it to TMN if USDT/TMN
  falls a set % from its peak.
- Satellite: rule setups (oversold_reversion, dip_in_uptrend; trend_pullback/breakout/mean_reversion lost in
  research) on X/USDT markets, gated by market breadth; the AI decider approves/sizes each entry. Fees: USDT
  markets 0.10% maker / 0.13% taker, TMN markets 0.25% / 0.30%.
- Sizing: 5M TMN is only ~18 USDT at today's rate (USDT/TMN ~273k); a USDT-market order must be at least 1 USDT
  (~330k TMN with the engine's 1.2x margin). Coins bought outside the engine (the live account holds USOON) count
  as exposure, so they use up the satellite's exposure cap (x0.67 in a neutral regime).
- Why it trades little: the dip setups fire rarely (hours without a candidate), and the breadth gate closes in
  broad sell-offs.
- Evidence so far: rules-only beat the AI decider in every backtest; dip buying into broad sell-offs was the
  biggest loss source (hence the breadth gate); a 7-day backtest of the current profile: +1.65%, max DD 2.4%,
  5 trades (core +2.8% per slice, 2 satellite stop-losses).

Be honest about trade-offs: more trades usually means more fees and drawdown here. If an idea cannot be expressed
with these parameters, say so and suggest the closest thing. Only propose keys from the list, within bounds."""

def _tool(name: str, description: str) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": {
            "changes": {"type": "object", "description": "parameter key -> new value (only keys from the list)"},
            "reasoning": {"type": "string", "description": "one or two sentences in Persian: why these values"},
        }, "required": ["changes", "reasoning"]}}}


TOOL = _tool("propose_changes", "Suggest new values for tunable parameters; the person decides whether to apply them.")
APPLY_TOOL = _tool("apply_changes", "Set tunable parameters now (the engine restarts with them). Use when asked to change "
                                    "settings or when the person confirms a proposal.")


def _params_block(effective: dict[str, Any]) -> str:
    rows = []
    for p in PARAMS:
        bounds = (f"{p['min']}..{p['max']}" if "min" in p else
                  "ISO-8601 with offset" if p["type"] == "datetime" else
                  f"one of {p['choices']}" if p["type"] == "choice" else
                  f"comma list from {p['choices']}" if p["type"] == "multi" else "true/false")
        rows.append(f"- {p['key']} = {effective.get(p['key'])!r}  ({p['type']}, {bounds}) {p['label']}")
    return "\n".join(rows)


def build_messages(history: list[dict], effective: dict, engine: dict, copying: str | None) -> list[dict]:
    personas = "; ".join(f"{p['id']} ({p['tagline']}): {json.dumps(p['preset'])}" for p in PERSONAS)
    ctx = (f"CURRENT PARAMETERS\n{_params_block(effective)}\n\nENGINE NOW\n{json.dumps(engine, ensure_ascii=False)}\n\n"
           f"PERSONAS (presets the person can copy): {personas}\nCurrently copying: {copying or 'none'}")
    msgs = [{"role": "system", "content": SYSTEM + "\n\n" + ctx}]
    for m in history[-12:]:
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
            msgs.append({"role": m["role"], "content": m["content"][:4000]})
    return msgs


def _proposal(raw: Any, effective: dict) -> dict | None:
    """A validated proposal, keeping only real changes; invalid keys/values are dropped and reported."""
    if not isinstance(raw, dict):
        return None
    changes = raw.get("changes") if isinstance(raw.get("changes"), dict) else {}
    ok, dropped = {}, []
    for k, v in changes.items():
        k = k if k in BY_KEY else f"engine_{k}" if f"engine_{k}" in BY_KEY else k
        try:
            val = validate({k: v})[k]
        except TuningError as e:
            dropped.append(str(e))
            continue
        if val != effective.get(k):
            ok[k] = val
    try:
        validate(ok | {k: effective[k] for k in ("engine_drawdown_pause_pct", "engine_drawdown_stop_pct")
                       if k in effective and k not in ok})
    except TuningError as e:
        dropped.append(str(e))
        ok = {k: v for k, v in ok.items() if not k.startswith("engine_drawdown")}
    if not ok and not dropped:
        return None
    return {"changes": ok, "reasoning": str(raw.get("reasoning") or ""), "dropped": dropped}


async def ask(s: Settings, history: list[dict], effective: dict, engine: dict, copying: str | None,
              allow_apply: bool = False) -> dict:
    """One assistant turn. Returns {"reply", "proposal", "apply"}: `apply` is True when the model asked to apply the
    proposal now (only offered when allow_apply); the caller applies it and may tell the model the outcome."""
    llm = AsyncOpenAI(base_url=s.llm_base_url, api_key=s.llm_api_key or "missing")
    extra = json.loads(s.engine_llm_extra_body) if s.engine_llm_extra_body.strip() else None
    kwargs: dict[str, Any] = dict(model=s.llm_model, messages=build_messages(history, effective, engine, copying),
                                  temperature=0.3, max_completion_tokens=900)
    if not s.llm_compat_schemas:
        kwargs["tools"] = [TOOL, APPLY_TOOL] if allow_apply else [TOOL]
    if extra:
        kwargs["extra_body"] = extra
    resp = await llm.chat.completions.create(**kwargs)
    msg = resp.choices[0].message
    text = msg.content or ""
    proposal, apply = None, False
    for call in msg.tool_calls or []:
        if call.function.name in ("propose_changes", "apply_changes"):
            try:
                p = _proposal(json.loads(call.function.arguments or "{}"), effective)
            except ValueError:
                continue
            if p:
                proposal = p if proposal is None else {**proposal, "changes": {**proposal["changes"], **p["changes"]},
                                                       "dropped": proposal["dropped"] + p["dropped"]}
                apply = apply or (call.function.name == "apply_changes" and allow_apply)
    if proposal is None:   # models without tool calls: a JSON object with "changes" in the text
        m = re.search(r"\{[\s\S]*\"changes\"[\s\S]*\}", text)
        if m:
            try:
                proposal = _proposal(json.loads(m.group(0)), effective)
                text = (text[: m.start()] + text[m.end():]).strip()
            except ValueError:
                pass
    if not text and proposal:
        text = proposal["reasoning"]
    return {"reply": text.strip() or "…", "proposal": proposal, "apply": bool(apply and proposal and proposal["changes"])}

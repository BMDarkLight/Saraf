"""Pre-flight check for a new host (e.g. Liara): can it reach Wallex and the LLM with this config?

Read-only: public markets, then (with a key) balances and fee tier, then one LLM call shaped like an
engine decision. Never places or cancels orders and never prints keys.
"""

from __future__ import annotations

import json
import time

from openai import AsyncOpenAI

from ..config import Settings
from ..llm_compat import _fixes, chat_create
from ..wallex import WallexClient
from .decider import _extract_json


def _line(ok: bool | None, name: str, detail: str) -> bool:
    print(f"[{'OK  ' if ok else 'SKIP' if ok is None else 'FAIL'}] {name}: {detail}")
    return ok is not False


async def run_check(s: Settings) -> bool:
    ok = True
    client = WallexClient(s.wallex_base_url, s.wallex_api_key)
    try:
        try:
            markets = await client.markets()
            tmn = sum(1 for m in markets if m.endswith("TMN"))
            ok &= _line(True, "wallex public", f"{len(markets)} markets ({tmn} TMN) from {s.wallex_base_url}")
        except Exception as e:
            ok &= _line(False, "wallex public", f"{type(e).__name__}: {e}")

        if not s.wallex_api_key:
            _line(None, "wallex auth", "WALLEX_API_KEY not set")
        else:
            try:
                bal = await client.balances()
                t = bal.get("TMN", {})
                ok &= _line(True, "wallex auth", f"balances readable; TMN value={t.get('value')} locked={t.get('locked')}")
            except Exception as e:
                ok &= _line(False, "wallex auth", f"{type(e).__name__}: {e} (key valid? this IP whitelisted?)")
            try:
                fee = await client.fees()
                _line(True, "wallex fees", json.dumps(fee, ensure_ascii=False, default=str)[:300]
                      + f" | configured taker={s.engine_fee_rate} maker={s.maker_fee}")
            except Exception as e:
                _line(None, "wallex fees", f"{type(e).__name__}: {e}")
    finally:
        await client.aclose()

    model = s.engine_llm_model or s.llm_model
    extra = json.loads(s.engine_llm_extra_body) if s.engine_llm_extra_body.strip() else None
    llm = AsyncOpenAI(base_url=s.llm_base_url, api_key=s.llm_api_key or "missing")
    t0 = time.monotonic()
    try:
        resp = await chat_create(
            llm, model=model, temperature=0, max_tokens=s.engine_llm_max_tokens,
            messages=[{"role": "system", "content": "You are a trading desk's risk reviewer. Answer with JSON only."},
                      {"role": "user", "content": 'Reply {"action": "SKIP", "reasoning": "<one short sentence>"}.'}],
            **({"extra_body": extra} if extra else {}))
        dt, raw = time.monotonic() - t0, resp.choices[0].message.content or ""
        fixes = sorted(_fixes.get(model, ()))
        note = f" (adapted: {', '.join(fixes)})" if fixes else ""
        if not raw.strip():
            ok &= _line(False, "llm", f"{model} answered with empty content in {dt:.1f}s{note}: reasoning "
                        "probably used the whole budget; raise ENGINE_LLM_MAX_TOKENS or set a lower "
                        "reasoning_effort in ENGINE_LLM_EXTRA_BODY")
        else:
            try:
                _extract_json(raw)
                ok &= _line(True, "llm", f"{model} at {s.llm_base_url} answered valid JSON in {dt:.1f}s{note}")
            except ValueError:
                ok &= _line(False, "llm", f"{model} answered but not JSON in {dt:.1f}s{note}: {raw[:200]!r}")
    except Exception as e:
        ok &= _line(False, "llm", f"{model} at {s.llm_base_url}: {type(e).__name__}: {str(e)[:300]}")
    print("all checks passed" if ok else "some checks FAILED")
    return ok

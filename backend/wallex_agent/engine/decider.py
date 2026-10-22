"""Decision makers.

AIDecider: an LLM acts as the trading desk's decision-maker. For each rule-generated candidate
it sees the full context (multi-timeframe features, order book, market regime, sentiment, news,
portfolio and drawdown) and returns a structured decision: TAKE/SKIP, conviction, position
size fraction and its own stop/target distances. It also reviews open positions (HOLD/EXIT/
TIGHTEN) and sets the market regime, which scales risk. Deterministic risk rules bound every
number it returns, and any failure falls back to the safe action (SKIP / HOLD).

Reproducibility: temperature 0 and a persistent response cache keyed by the exact prompt, so a
backtest replays identical decisions and every live decision can be audited later.

RuleDecider: the same interface without an LLM (score threshold). Backtests run both to show
what the AI adds.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .strategy import Candidate


def _bound(x, lo: float, hi: float, default: float) -> float:
    """Out-of-range numbers are clamped, not rejected: models put 0 in irrelevant fields (e.g. the
    stop of a SKIP), and one stray number must not discard an otherwise valid decision."""
    try:
        return min(max(float(x), lo), hi)
    except (TypeError, ValueError):
        return default


class EntryDecision(BaseModel):
    action: Literal["TAKE", "SKIP"]
    confidence: float = 0.0
    size_fraction: float = 0.0          # fraction of the maximum allowed size
    stop_atr: float = 2.5
    target_atr: float = 3.5
    reasoning: str = ""

    @field_validator("action", mode="before")
    @classmethod
    def _action(cls, v):
        return str(v).strip().upper()

    @field_validator("confidence", "size_fraction", mode="before")
    @classmethod
    def _unit(cls, v):
        return _bound(v, 0.0, 1.0, 0.0)

    @field_validator("stop_atr", mode="before")
    @classmethod
    def _stop(cls, v):
        return _bound(v, 0.8, 3.0, 2.5)

    @field_validator("target_atr", mode="before")
    @classmethod
    def _target(cls, v):
        return _bound(v, 1.2, 6.0, 3.5)

    @model_validator(mode="after")
    def _take_needs_size(self):
        if self.action == "TAKE" and self.size_fraction <= 0:
            raise ValueError("TAKE without a positive size_fraction")
        return self


class PositionReview(BaseModel):
    action: Literal["HOLD", "EXIT", "TIGHTEN"]
    new_stop: float | None = None
    reasoning: str = ""

    @field_validator("action", mode="before")
    @classmethod
    def _action(cls, v):
        return str(v).strip().upper()

    @field_validator("new_stop", mode="before")
    @classmethod
    def _stop(cls, v):
        try:
            return float(v) if v not in (None, "", 0, "null") else None
        except (TypeError, ValueError):
            return None


class Regime(BaseModel):
    regime: Literal["risk_on", "neutral", "risk_off"]
    reasoning: str = ""

    @field_validator("regime", mode="before")
    @classmethod
    def _regime(cls, v):
        return str(v).strip().lower().replace("-", "_").replace(" ", "_")


@dataclass
class Decision:
    """What the engine logs: the decision plus provenance."""
    payload: dict
    source: str                 # "llm" | "cache" | "rules" | "fallback"
    prompt_hash: str | None = None
    error: str | None = None
    extra: dict = field(default_factory=dict)


SYSTEM = """You are the head trader of an autonomous crypto trading desk on the Wallex exchange (Iran).
Capital is in Toman (TMN); profit only counts once realized back in Toman. The desk is judged on net
return after fees AND on keeping drawdown low, over a 3-day competition. Round-trip fees are about {fee2:.2f}%.

HOW THIS DESK MAKES MONEY (measured on 28 days of Wallex TMN data, in- and out-of-sample):
- Both setups are counter-trend DIP BUYS: "oversold_reversion" (close below the lower Bollinger band, RSI < 30)
  and "dip_in_uptrend" (1h up-trend, sharp 1-hour pullback). Their edge comes FROM buying short-term weakness:
  falling 15m EMAs, an EMA20 below EMA50, red candles and oversold RSI are the setup itself, not a reason to skip.
- Base rate: about 45-55% of these trades win and the average is a small positive return after costs.
  Momentum/breakout trades had no edge here; do not apply trend-following rules to these dip buys.
- Your role is a VETO: the risk manager already sizes every trade for its stop (about 1% of equity at risk).
  Approve normal candidates at full size and reject the weak ones on CONCRETE evidence. Skipping everything,
  or shrinking everything, earns nothing.

SKIP when there is specific evidence the trade is worse than the base rate, e.g.: a broad crash is still
accelerating (most markets falling hard together, BTC 1h trend down with negative 4h momentum), a very thin or
heavily sell-side order book, a wide spread relative to the target, coin-specific negative news, or the 1h trend
is down AND the dip is not stretched (RSI above 30). Otherwise TAKE with size_fraction 1.0. Use 0.5 only when
one specific risk flag is present but not enough to veto, and name that flag in the reasoning.
stop_atr/target_atr: distances in ATRs from entry (defaults 2.5 / 3.5 work well); the target must clear fees.
Respond with ONE JSON object only, no prose, matching the requested schema."""

ENTRY_SCHEMA = ('{"action": "TAKE"|"SKIP", "confidence": 0..1, "size_fraction": 0..1, '
                '"stop_atr": 0.8..3.0, "target_atr": 1.2..6.0, "reasoning": "<=2 sentences, in Persian (Farsi)"}')
REVIEW_SCHEMA = '{"action": "HOLD"|"EXIT"|"TIGHTEN", "new_stop": number|null, "reasoning": "<=2 sentences, in Persian (Farsi)"}'
REGIME_SCHEMA = '{"regime": "risk_on"|"neutral"|"risk_off", "reasoning": "<=2 sentences, in Persian (Farsi)"}'


def _extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in response")
    return json.loads(text[start:end + 1])


class ResponseCache:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, str] = {}
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self.data[rec["key"]] = rec["response"]

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def put(self, key: str, response: str) -> None:
        self.data[key] = response
        with self.path.open("a") as f:
            f.write(json.dumps({"key": key, "response": response}, ensure_ascii=False) + "\n")


class AIDecider:
    name = "ai"

    def __init__(self, llm: AsyncOpenAI, model: str, cache: ResponseCache, fee_rate: float,
                 timeout: float = 45.0, offline: bool = False, extra_body: dict | None = None):
        self.llm, self.model, self.cache = llm, model, cache
        self.extra_body = extra_body or {}   # provider switches, e.g. {"reasoning_effort": "none"} for Qwen
        self.fee_rate, self.timeout = fee_rate, timeout
        self.offline = offline  # backtest replay: cache only, never call the API
        self.calls = 0

    async def _ask(self, user: str, schema: type[BaseModel]) -> Decision:
        system = SYSTEM.format(fee2=2 * 100 * self.fee_rate)
        opts = json.dumps(self.extra_body, sort_keys=True) if self.extra_body else ""
        key = hashlib.sha256(f"{self.model}\n{opts}\n{system}\n{user}".encode()).hexdigest()
        raw, source = self.cache.get(key), "cache"
        if raw is None:
            if self.offline:
                return Decision({}, "fallback", key, "not in cache (offline replay)")
            source = "llm"
            try:
                self.calls += 1
                resp = await asyncio.wait_for(self.llm.chat.completions.create(
                    # max_completion_tokens: newer OpenAI models reject the legacy max_tokens
                    model=self.model, temperature=0, max_completion_tokens=400,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    **({"extra_body": self.extra_body} if self.extra_body else {})),
                    self.timeout)
                raw = resp.choices[0].message.content or ""
            except Exception as e:
                return Decision({}, "fallback", key, f"LLM call failed: {type(e).__name__}: {e}")
        try:
            parsed = schema.model_validate(_extract_json(raw))
        except (ValueError, ValidationError) as e:
            return Decision({}, "fallback", key, f"invalid LLM output: {str(e)[:200]}", {"raw": raw[:500]})
        if source == "llm":
            self.cache.put(key, raw)
        return Decision(parsed.model_dump(), source, key)

    async def decide_entry(self, cand: Candidate, ctx: dict) -> Decision:
        user = (f"PROPOSED TRADE (rule layer)\n{json.dumps(cand.dict(), ensure_ascii=False, default=str)}\n\n"
                f"CONTEXT\n{json.dumps(ctx, ensure_ascii=False, default=str)}\n\n"
                f"Decide. Schema: {ENTRY_SCHEMA}")
        d = await self._ask(user, EntryDecision)
        if not d.payload:
            d.payload = {"action": "SKIP", "confidence": 0, "size_fraction": 0, "stop_atr": cand.stop_atr,
                         "target_atr": cand.target_atr, "reasoning": f"fail-safe skip ({d.error})"}
        return d

    async def review_position(self, position: dict, ctx: dict) -> Decision:
        user = (f"OPEN POSITION\n{json.dumps(position, ensure_ascii=False, default=str)}\n\n"
                f"CONTEXT\n{json.dumps(ctx, ensure_ascii=False, default=str)}\n\n"
                "Review it. EXIT closes at market now; TIGHTEN raises the stop to new_stop (must be above the "
                f"current stop and below the current price); HOLD keeps the plan. Schema: {REVIEW_SCHEMA}")
        d = await self._ask(user, PositionReview)
        if not d.payload:
            d.payload = {"action": "HOLD", "new_stop": None, "reasoning": f"fail-safe hold ({d.error})"}
        return d

    async def assess_regime(self, ctx: dict) -> Decision:
        user = (f"MARKET SNAPSHOT\n{json.dumps(ctx, ensure_ascii=False, default=str)}\n\n"
                "Classify the regime for this desk's long-only dip buying over the next hours. risk_off halts ALL "
                "new entries, so reserve it for a broad, severe sell-off that is still accelerating (most markets "
                "down sharply together, BTC 1h trend down with falling momentum) or major negative news. Normal "
                "pullbacks and mixed markets are neutral; dips are when this desk trades. risk_on: broad strength. "
                f"Schema: {REGIME_SCHEMA}")
        d = await self._ask(user, Regime)
        if not d.payload:
            d.payload = {"regime": "neutral", "reasoning": f"fail-safe neutral ({d.error})"}
        return d


class RuleDecider:
    """Baseline without AI: take every candidate above the score threshold at full size."""
    name = "rules"

    def __init__(self, min_score: float):
        self.min_score = min_score
        self.calls = 0

    async def decide_entry(self, cand: Candidate, ctx: dict) -> Decision:
        take = cand.score >= self.min_score
        return Decision({"action": "TAKE" if take else "SKIP", "confidence": cand.score, "size_fraction": 1.0,
                         "stop_atr": cand.stop_atr, "target_atr": cand.target_atr,
                         "reasoning": f"rule baseline: score {cand.score} {'>=' if take else '<'} {self.min_score}"},
                        "rules")

    async def review_position(self, position: dict, ctx: dict) -> Decision:
        return Decision({"action": "HOLD", "new_stop": None, "reasoning": "rule baseline holds to stop/target"}, "rules")

    async def assess_regime(self, ctx: dict) -> Decision:
        breadth = ctx.get("breadth_pct_up_24h", 50)
        regime = "risk_off" if breadth < 25 else "risk_on" if breadth > 65 else "neutral"
        return Decision({"regime": regime, "reasoning": f"rule baseline: breadth {breadth}%"}, "rules")

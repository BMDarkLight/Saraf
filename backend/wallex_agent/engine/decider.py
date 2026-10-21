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
from pydantic import BaseModel, Field, ValidationError

from .strategy import Candidate


class EntryDecision(BaseModel):
    action: Literal["TAKE", "SKIP"]
    confidence: float = Field(ge=0, le=1)
    size_fraction: float = Field(ge=0, le=1, description="fraction of the maximum allowed size")
    stop_atr: float = Field(ge=0.8, le=3.0)
    target_atr: float = Field(ge=1.2, le=6.0)
    reasoning: str


class PositionReview(BaseModel):
    action: Literal["HOLD", "EXIT", "TIGHTEN"]
    new_stop: float | None = None
    reasoning: str


class Regime(BaseModel):
    regime: Literal["risk_on", "neutral", "risk_off"]
    reasoning: str


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
return after fees AND on keeping drawdown low, over a 3-day competition. Round-trip fees are about {fee2:.2f}%,
so marginal trades lose money: prefer fewer, high-quality trades.

A rule-based signal layer proposes trades; you make the decision. Be decisive and consistent:
- Reject setups that fight the higher-timeframe trend, have poor reward/risk after fees, thin liquidity
  (wide spread, sell-heavy book), or contradict clearly negative news/regime.
- Size with conviction: size_fraction near 1 only for clean setups with confirmation.
- stop_atr/target_atr: distances in ATRs from entry. Target must clear fees comfortably.
Respond with ONE JSON object only, no prose, matching the requested schema."""

ENTRY_SCHEMA = ('{"action": "TAKE"|"SKIP", "confidence": 0..1, "size_fraction": 0..1, '
                '"stop_atr": 0.8..3.0, "target_atr": 1.2..6.0, "reasoning": "<=2 sentences"}')
REVIEW_SCHEMA = '{"action": "HOLD"|"EXIT"|"TIGHTEN", "new_stop": number|null, "reasoning": "<=2 sentences"}'
REGIME_SCHEMA = '{"regime": "risk_on"|"neutral"|"risk_off", "reasoning": "<=2 sentences"}'


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
                 timeout: float = 45.0, offline: bool = False):
        self.llm, self.model, self.cache = llm, model, cache
        self.fee_rate, self.timeout = fee_rate, timeout
        self.offline = offline  # backtest replay: cache only, never call the API
        self.calls = 0

    async def _ask(self, user: str, schema: type[BaseModel]) -> Decision:
        system = SYSTEM.format(fee2=2 * 100 * self.fee_rate)
        key = hashlib.sha256(f"{self.model}\n{system}\n{user}".encode()).hexdigest()
        raw, source = self.cache.get(key), "cache"
        if raw is None:
            if self.offline:
                return Decision({}, "fallback", key, "not in cache (offline replay)")
            source = "llm"
            try:
                self.calls += 1
                resp = await asyncio.wait_for(self.llm.chat.completions.create(
                    model=self.model, temperature=0, max_tokens=400,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}]),
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
                "Classify the regime for long-only spot trading over the next hours. risk_off means the desk "
                f"should not open new longs. Schema: {REGIME_SCHEMA}")
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

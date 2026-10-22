"""chat_create adapts to providers that reject temperature / max_tokens (gpt-5.x reasoning models)."""

from types import SimpleNamespace as NS

import httpx
import pytest
from openai import BadRequestError

from wallex_agent import llm_compat
from wallex_agent.engine.decider import AIDecider, EntryDecision, ResponseCache


def bad_request(msg):
    req = httpx.Request("POST", "http://x/v1/chat/completions")
    return BadRequestError(msg, response=httpx.Response(400, request=req), body=None)


class StrictLLM:
    """Behaves like a reasoning model: no custom temperature, no max_tokens."""

    def __init__(self, answer='{"action": "SKIP"}'):
        self.answer, self.calls = answer, []
        self.chat = NS(completions=NS(create=self.create))

    async def create(self, **kw):
        self.calls.append(kw)
        if "temperature" in kw:
            raise bad_request("Unsupported value: 'temperature' does not support 0 with this model.")
        if "max_tokens" in kw:
            raise bad_request("Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead.")
        return NS(choices=[NS(message=NS(content=self.answer))])


@pytest.fixture(autouse=True)
def fresh_fixes():
    llm_compat._fixes.clear()
    yield
    llm_compat._fixes.clear()


async def test_adapts_and_remembers_per_model():
    llm = StrictLLM()
    await llm_compat.chat_create(llm, model="gpt-5.6-sol", temperature=0, max_tokens=400, messages=[])
    assert len(llm.calls) == 3
    final = llm.calls[-1]
    assert "temperature" not in final and final["max_completion_tokens"] == 400
    llm.calls.clear()
    await llm_compat.chat_create(llm, model="gpt-5.6-sol", temperature=0, max_tokens=400, messages=[])
    assert len(llm.calls) == 1   # learned: straight through


async def test_unrelated_bad_request_is_raised():
    class Broken:
        chat = NS(completions=NS(create=None))

        async def _create(**kw):
            raise bad_request("model not found")

    Broken.chat.completions.create = Broken._create
    with pytest.raises(BadRequestError):
        await llm_compat.chat_create(Broken, model="nope", temperature=0, max_tokens=10, messages=[])


async def test_decider_works_with_reasoning_model(tmp_path):
    answer = ('{"action": "TAKE", "confidence": 0.7, "size_fraction": 1.0, "stop_atr": 1.5, '
              '"target_atr": 2.0, "reasoning": "ok"}')
    d = AIDecider(StrictLLM(answer), "gpt-5.6-sol", ResponseCache(tmp_path / "c.jsonl"), 0.001, max_tokens=1500)
    res = await d._ask("x", EntryDecision)
    assert res.source == "llm" and not res.error and res.payload["action"] == "TAKE"

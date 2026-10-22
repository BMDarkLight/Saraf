"""Chat-completions call that adapts to the provider's parameter rules.

OpenAI-style reasoning models (gpt-5.x and later, e.g. via BoxAPI) reject a non-default
temperature and want max_completion_tokens instead of max_tokens; older and local models are the
other way round. On a 400 that names one of these parameters, retry once without it (or renamed)
and remember the fix per model, so later calls go straight through.
"""

from openai import AsyncOpenAI, BadRequestError

_fixes: dict[str, set[str]] = {}   # model -> {"drop_temperature", "max_completion_tokens"}


def _apply(model: str, kwargs: dict) -> dict:
    kw, fixes = dict(kwargs), _fixes.get(model, set())
    if "drop_temperature" in fixes:
        kw.pop("temperature", None)
    if "max_completion_tokens" in fixes and "max_tokens" in kw:
        kw["max_completion_tokens"] = kw.pop("max_tokens")
    return kw


def _learn(model: str, kwargs: dict, err: BadRequestError) -> bool:
    msg, fixes = str(err).lower(), _fixes.setdefault(model, set())
    before = len(fixes)
    if "temperature" in msg and "temperature" in kwargs:
        fixes.add("drop_temperature")
    if "max_tokens" in msg and "max_tokens" in kwargs:
        fixes.add("max_completion_tokens")
    return len(fixes) > before


async def chat_create(llm: AsyncOpenAI, **kwargs):
    model = kwargs["model"]
    for _ in range(3):   # at most one fix per parameter, then give up
        kw = _apply(model, kwargs)
        try:
            return await llm.chat.completions.create(**kw)
        except BadRequestError as e:
            if not _learn(model, kw, e):
                raise
    return await llm.chat.completions.create(**_apply(model, kwargs))

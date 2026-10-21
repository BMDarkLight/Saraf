"""The agent loop: OpenAI-compatible chat completions + tool calling, streamed as events.

Events yielded (all JSON-serializable dicts):
  {"type": "text", "delta": str}
  {"type": "tool_call", "id", "name", "args"}
  {"type": "tool_result", "id", "name", "ok", "result"}
  {"type": "confirm", "action": {...}}          # a trade awaiting user approval
  {"type": "error", "message"}
  {"type": "done"}
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from openai import AsyncOpenAI
from pydantic import ValidationError

from ..brokers import BrokerError
from ..wallex import WallexError
from . import risk
from .context import AppContext
from .prompts import system_prompt
from .tools import TOOLS, openai_tools

MAX_TOOL_RESULT_CHARS = 12_000


@dataclass
class PendingAction:
    id: str
    session_id: str
    tool: str
    args: dict
    summary: str
    reason: str | None
    risk: dict
    created_at: float = field(default_factory=time.time)
    status: str = "pending"  # pending | executed | rejected | failed
    result: Any = None

    def public(self) -> dict:
        return {"id": self.id, "tool": self.tool, "args": self.args, "summary": self.summary,
                "reason": self.reason, "risk": self.risk, "status": self.status, "result": self.result}


@dataclass
class Session:
    id: str
    messages: list[dict] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _jsonable(x: Any) -> Any:
    return json.loads(json.dumps(x, default=str, ensure_ascii=False))


def compat_history(messages: list[dict]) -> list[dict]:
    """Rewrite tool exchanges as plain text for chat templates that can't render
    `role: tool` messages (Gemma on LM Studio fails with "Unknown test: sequence").
    New tool calls still use the native `tools` API; only the history is flattened."""
    out: list[dict] = []
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            calls = "; ".join(f"{c['function']['name']}({c['function']['arguments']})" for c in m["tool_calls"])
            out.append({"role": "assistant", "content": f"{m.get('content') or ''}\n[calling tools] {calls}".strip()})
        elif m["role"] == "tool":
            block = f"[tool result {m['tool_call_id']}] {m['content']}"
            if out and out[-1]["role"] == "user" and out[-1].get("_tool"):
                out[-1]["content"] += "\n" + block
            else:
                out.append({"role": "user", "content": block, "_tool": True})
        else:
            out.append(m)
    for m in out:
        if m.pop("_tool", False):
            m["content"] += ("\n\nThese are the tool results. Call more tools if needed, otherwise answer the "
                             "user's request in their language using these results.")
    return out


_CALL_RE = re.compile(r"\b([a-z_][a-z0-9_]*)\(\s*\{")


def parse_text_tool_calls(text: str) -> tuple[str, list[dict]]:
    """Small models sometimes imitate the flattened history and write `tool_name({...})`
    as text instead of a native call. Recover those calls; return (remaining_text, calls)."""
    calls, spans = [], []
    dec = json.JSONDecoder()
    for m in _CALL_RE.finditer(text):
        if m.group(1) not in TOOLS:
            continue
        try:
            args, end = dec.raw_decode(text, m.end() - 1)
        except json.JSONDecodeError:
            continue
        calls.append({"id": f"textcall_{uuid.uuid4().hex[:8]}", "type": "function",
                      "function": {"name": m.group(1), "arguments": json.dumps(args, ensure_ascii=False)}})
        close = text.find(")", end)
        spans.append((m.start(), close + 1 if close >= 0 else end))
    for start, end in reversed(spans):
        text = text[:start] + text[end:]
    text = text.replace("[calling tools]", "").strip(" ;\n")
    return text, calls


def for_model(result: Any) -> Any:
    """What the LLM sees of a tool result: generative-UI payloads are for the browser only."""
    if isinstance(result, dict) and "ui" in result:
        return {k: v for k, v in result.items() if k != "ui"} | {"ui_rendered": result["ui"].get("component")}
    return result


def _truncate(payload: Any) -> str:
    s = json.dumps(payload, default=str, ensure_ascii=False)
    if len(s) > MAX_TOOL_RESULT_CHARS:
        s = s[:MAX_TOOL_RESULT_CHARS] + '..."(truncated)"'
    return s


class Agent:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        s = ctx.settings
        self.llm = AsyncOpenAI(base_url=s.llm_base_url, api_key=s.llm_api_key or "missing")
        self.sessions: dict[str, Session] = {}
        self.pending: dict[str, PendingAction] = {}

    def session(self, session_id: str | None) -> Session:
        sid = session_id or uuid.uuid4().hex
        if sid not in self.sessions:
            self.sessions[sid] = Session(sid)
        return self.sessions[sid]

    # --------------------------------------------------------------- tools
    async def run_tool(self, name: str, raw_args: str, session: Session) -> tuple[bool, Any, dict | None]:
        """Returns (ok, result, confirm_action)."""
        t = TOOLS.get(name)
        if not t:
            return False, {"error": f"unknown tool {name}"}, None
        try:
            args = t.args.model_validate_json(raw_args or "{}")
        except ValidationError as e:
            return False, {"error": "invalid arguments", "details": json.loads(e.json())}, None

        if t.mutating:
            try:
                facts = await risk.check(self.ctx, name, args)
            except risk.RiskViolation as e:
                return False, {"error": f"blocked by risk guard: {e}"}, None
            except (BrokerError, WallexError) as e:
                return False, {"error": str(e)}, None
            if self.ctx.settings.require_confirmation:
                action = PendingAction(
                    id=uuid.uuid4().hex[:12], session_id=session.id, tool=name, args=args.model_dump(),
                    summary=t.summarize(args) if t.summarize else name, reason=getattr(args, "reason", None),
                    risk=_jsonable(facts),
                )
                self.pending[action.id] = action
                return True, {"status": "awaiting_user_confirmation", "action_id": action.id,
                              "summary": action.summary, "risk": action.risk}, action.public()
        try:
            result = await t.handler(self.ctx, args)
            return True, _jsonable(result), None
        except (BrokerError, WallexError) as e:
            return False, {"error": str(e)}, None
        except Exception as e:  # tool bugs shouldn't kill the conversation
            return False, {"error": f"{type(e).__name__}: {e}"}, None

    async def execute_action(self, action_id: str, approve: bool) -> PendingAction:
        action = self.pending.get(action_id)
        if not action:
            raise KeyError(action_id)
        if action.status != "pending":
            return action
        if not approve:
            action.status = "rejected"
            return action
        t = TOOLS[action.tool]
        try:
            action.result = _jsonable(await t.handler(self.ctx, t.args.model_validate(action.args)))
            action.status = "executed"
        except Exception as e:
            action.result = {"error": str(e)}
            action.status = "failed"
        return action

    # ---------------------------------------------------------------- loop
    async def _complete(self, messages: list[dict]) -> AsyncIterator[dict]:
        """One LLM call. Yields text deltas, then a final {"type": "_message", "message": ...}."""
        s = self.ctx.settings
        if s.llm_compat_schemas:
            messages = compat_history(messages)
        kwargs = dict(model=s.llm_model, messages=messages, tools=openai_tools(s.llm_compat_schemas),
                      temperature=s.llm_temperature)
        if not s.llm_stream:
            resp = await self.llm.chat.completions.create(**kwargs)
            m = resp.choices[0].message
            if m.content:
                yield {"type": "text", "delta": m.content}
            msg = {"role": "assistant", "content": m.content or ""}
            if m.tool_calls:
                msg["tool_calls"] = [{"id": tc.id, "type": "function",
                                      "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                                     for tc in m.tool_calls]
            yield {"type": "_message", "message": msg}
            return

        stream = await self.llm.chat.completions.create(**kwargs, stream=True)
        content, calls = [], {}
        async for chunk in stream:
            if not chunk.choices:
                continue
            d = chunk.choices[0].delta
            reasoning = getattr(d, "reasoning_content", None) or getattr(d, "reasoning", None)
            if isinstance(reasoning, str) and reasoning:
                yield {"type": "reasoning", "delta": reasoning}
            if d.content:
                content.append(d.content)
                yield {"type": "text", "delta": d.content}
            for tc in d.tool_calls or []:
                c = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    c["id"] = tc.id
                if tc.function and tc.function.name:
                    c["name"] += tc.function.name
                if tc.function and tc.function.arguments:
                    c["arguments"] += tc.function.arguments
        msg = {"role": "assistant", "content": "".join(content)}
        if calls:
            msg["tool_calls"] = [{"id": c["id"] or f"call_{i}", "type": "function",
                                  "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                                 for i, c in sorted(calls.items())]
        elif s.llm_compat_schemas:
            text, parsed = parse_text_tool_calls(msg["content"])
            if parsed:
                msg = {"role": "assistant", "content": text, "tool_calls": parsed}
        yield {"type": "_message", "message": msg}

    async def chat(self, session: Session, user_text: str) -> AsyncIterator[dict]:
        async with session.lock:
            session.messages.append({"role": "user", "content": user_text})
            async for ev in self._run(session):
                yield ev

    async def _run(self, session: Session) -> AsyncIterator[dict]:
        if not self.ctx.settings.llm_api_key and "localhost" not in self.ctx.settings.llm_base_url:
            yield {"type": "error", "message": "LLM_API_KEY is not set. Configure it in .env"}
            yield {"type": "done"}
            return
        for _ in range(self.ctx.settings.agent_max_steps):
            messages = [{"role": "system", "content": system_prompt(self.ctx.settings)}] + session.messages
            assistant = None
            try:
                async for ev in self._complete(messages):
                    if ev["type"] == "_message":
                        assistant = ev["message"]
                    else:
                        yield ev
            except Exception as e:
                yield {"type": "error", "message": f"LLM error: {e}"}
                break
            session.messages.append(assistant)
            calls = assistant.get("tool_calls") or []
            if not calls:
                break
            for c in calls:
                try:
                    shown = json.loads(c["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    shown = c["function"]["arguments"]
                yield {"type": "tool_call", "id": c["id"], "name": c["function"]["name"], "args": shown}
            results = await asyncio.gather(*(self.run_tool(c["function"]["name"], c["function"]["arguments"], session)
                                             for c in calls))
            for c, (ok, result, confirm) in zip(calls, results):
                yield {"type": "tool_result", "id": c["id"], "name": c["function"]["name"], "ok": ok, "result": result}
                if confirm:
                    yield {"type": "confirm", "action": confirm}
                session.messages.append({"role": "tool", "tool_call_id": c["id"], "content": _truncate(for_model(result))})
        else:
            yield {"type": "text", "delta": "\n\n_(step limit reached)_"}
        yield {"type": "done"}

    async def after_action(self, action: PendingAction) -> AsyncIterator[dict]:
        """Tell the agent what happened to a confirmation and let it follow up."""
        session = self.session(action.session_id)
        async with session.lock:
            verdict = {"executed": "approved and executed", "failed": "approved but execution FAILED",
                       "rejected": "REJECTED by the user (not executed)"}[action.status]
            session.messages.append({"role": "user", "content":
                f"[system notice] Action {action.id} ({action.summary}) was {verdict}. "
                f"Result: {_truncate(action.result)}. Briefly confirm the outcome to the user."})
            async for ev in self._run(session):
                yield ev

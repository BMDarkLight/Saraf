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

from openai import APIConnectionError, APIStatusError, AsyncOpenAI
from pydantic import ValidationError

from ..brokers import BrokerError
from ..venues import VenueError
from . import risk
from .context import AppContext
from .prompts import system_prompt
from .tools import TOOLS, openai_tools
from .sessions import SessionStore
from .toolsets import select_tools

MAX_TOOL_RESULT_CHARS = 6_000   # every result is resent on each later step of the turn
TOOL_TIMEOUT = 45.0          # seconds; a stuck network call must not freeze the whole turn
ACTION_TTL = 600.0           # seconds a trade confirmation stays approvable


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
    status: str = "pending"  # pending | executing | executed | rejected | failed | expired
    result: Any = None
    mode: str = "paper"      # paper | live: what the card shows, and what approval will execute in
    venue: str = ""          # the default venue when the card was made; approval runs only on that venue

    def public(self) -> dict:
        return {"id": self.id, "tool": self.tool, "args": self.args, "summary": self.summary,
                "reason": self.reason, "risk": self.risk, "status": self.status, "result": self.result,
                "mode": self.mode}


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


def repair_history(messages: list[dict]) -> None:
    """Make history valid for strict providers, in place: every assistant tool call gets a tool
    result (a turn interrupted by a dropped connection leaves them dangling) and orphan tool
    results are dropped."""
    out: list[dict] = []
    pending: list[str] = []
    for m in messages:
        if m["role"] == "tool":
            if m.get("tool_call_id") in pending:
                pending.remove(m["tool_call_id"])
                out.append(m)
            continue
        out.extend({"role": "tool", "tool_call_id": cid, "content": '{"error": "interrupted before completion"}'}
                   for cid in pending)
        pending = [c["id"] for c in m.get("tool_calls") or []] if m["role"] == "assistant" else []
        out.append(m)
    out.extend({"role": "tool", "tool_call_id": cid, "content": '{"error": "interrupted before completion"}'}
               for cid in pending)
    messages[:] = out


def trim_history(messages: list[dict], budget_chars: int) -> list[dict]:
    """Keep the most recent messages within a character budget, cutting only at user turns so
    tool calls and their results stay together. The latest user turn is always kept."""
    size, cut = 0, len(messages)
    for i in range(len(messages) - 1, -1, -1):
        size += len(json.dumps(messages[i], ensure_ascii=False))
        if messages[i]["role"] == "user":
            if size > budget_chars and cut < len(messages):
                break
            cut = i
    return messages[cut:]


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
        self.store = SessionStore(ctx.settings.data_dir)

    def reset_llm(self) -> None:
        """Rebuild the LLM client after its endpoint or key changed in the settings."""
        s = self.ctx.settings
        self.llm = AsyncOpenAI(base_url=s.llm_base_url, api_key=s.llm_api_key or "missing")

    def session(self, session_id: str | None) -> Session:
        sid = session_id or uuid.uuid4().hex
        if sid not in self.sessions:   # a saved conversation reopened after a restart keeps its context
            saved = self.store.load(sid) or {}
            self.sessions[sid] = Session(sid, list(saved.get("messages") or []))
        return self.sessions[sid]

    def save(self, session: Session) -> None:
        self.store.save_history(session.id, session.messages)

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
                facts = await asyncio.wait_for(risk.check(self.ctx, name, args), TOOL_TIMEOUT)
            except risk.RiskViolation as e:
                return False, {"error": f"blocked by risk guard: {e}"}, None
            except (BrokerError, VenueError) as e:
                return False, {"error": str(e)}, None
            except asyncio.TimeoutError:
                return False, {"error": f"risk check timed out after {TOOL_TIMEOUT:g}s"}, None
            if self.ctx.settings.require_confirmation:
                try:
                    summary = t.summarize(args) if t.summarize else name
                except Exception:
                    summary = f"{name} {args.model_dump(exclude_none=True)}"
                action = PendingAction(
                    id=uuid.uuid4().hex[:12], session_id=session.id, tool=name, args=args.model_dump(),
                    summary=summary, reason=getattr(args, "reason", None), risk=_jsonable(facts),
                    mode=self._mode(name), venue=self.ctx.venue.name,
                )
                self.pending[action.id] = action
                return True, {"status": "awaiting_user_confirmation", "action_id": action.id,
                              "summary": action.summary, "risk": action.risk}, action.public()
        try:
            result = await asyncio.wait_for(t.handler(self.ctx, args), TOOL_TIMEOUT)
            return True, _jsonable(result), None
        except asyncio.TimeoutError:
            return False, {"error": f"{name} timed out after {TOOL_TIMEOUT:g}s; try again or use another tool"}, None
        except (BrokerError, VenueError) as e:
            return False, {"error": str(e)}, None
        except Exception as e:  # tool bugs shouldn't kill the conversation
            return False, {"error": f"{type(e).__name__}: {e}"}, None

    def _mode(self, tool: str) -> str:
        return (self.ctx.futures if tool.startswith("futures_") else self.ctx.spot).mode

    async def execute_action(self, action_id: str, approve: bool) -> PendingAction:
        action = self.pending.get(action_id)
        if not action:
            raise KeyError(action_id)
        if action.status != "pending":
            return action  # already decided (also stops double-clicks executing twice)
        if not approve:
            action.status = "rejected"
            return action
        if time.time() - action.created_at > ACTION_TTL:
            action.status = "expired"
            action.result = {"error": f"confirmation expired after {ACTION_TTL / 60:g} minutes; prices may have moved"}
            return action
        if self._mode(action.tool) != action.mode:   # switched test <-> real after the card was shown
            action.status = "failed"
            action.result = {"error": "حالت معامله (آزمایشی/واقعی) بعد از ساخت این کارت عوض شده؛ دوباره درخواست بدهید."}
            return action
        if action.venue and action.venue != self.ctx.venue.name:
            action.status = "failed"
            action.result = {"error": f"صرافی پیش‌فرض بعد از ساخت این کارت عوض شده ({action.venue} ← "
                                      f"{self.ctx.venue.name})؛ دوباره درخواست بدهید."}
            return action
        action.status = "executing"  # claimed synchronously, before any await
        t = TOOLS[action.tool]
        try:
            args = t.args.model_validate(action.args)
            await risk.check(self.ctx, action.tool, args)  # the market may have moved since the proposal
            action.result = _jsonable(await asyncio.wait_for(t.handler(self.ctx, args), TOOL_TIMEOUT))
            action.status = "executed"
        except risk.RiskViolation as e:
            action.result = {"error": f"blocked by risk guard at execution: {e}"}
            action.status = "failed"
        except Exception as e:
            msg = str(e) or type(e).__name__
            if action.mode == "live":
                msg = self.ctx.venue.explain_order_error(msg, self.ctx.key_is_main)
            action.result = {"error": msg}
            action.status = "failed"
        return action

    # ---------------------------------------------------------------- loop
    async def _complete(self, messages: list[dict]) -> AsyncIterator[dict]:
        """One LLM call. Yields text deltas, then a final {"type": "_message", "message": ...}."""
        s = self.ctx.settings
        if s.llm_compat_schemas:
            messages = compat_history(messages)
        names = select_tools(list(TOOLS), messages)
        if self.ctx.spot.mode == "live":   # the credit account is spot-only; no paper futures beside real money
            names = [n for n in names if not n.startswith("futures_") and n != "show_position_planner"]
        kwargs = dict(model=s.llm_model, messages=messages, tools=openai_tools(s.llm_compat_schemas, names),
                      temperature=s.llm_temperature)
        if s.llm_max_output_tokens:
            kwargs["max_completion_tokens"] = s.llm_max_output_tokens
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
                key = tc.index
                if key is None:  # some local servers omit the index; a new id means a new call
                    key = len(calls) if (tc.id and all(c["id"] != tc.id for c in calls.values())) or not calls \
                        else max(calls)
                c = calls.setdefault(key, {"id": "", "name": "", "arguments": ""})
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
        s = self.ctx.settings
        if not s.llm_api_key and "api.openai.com" in s.llm_base_url:
            yield {"type": "error", "message": "LLM_API_KEY is not set. Configure it in .env"}
            yield {"type": "done"}
            return
        repair_history(session.messages)
        for _ in range(s.agent_max_steps):
            history = trim_history(session.messages, s.agent_history_chars)
            messages = [{"role": "system", "content": system_prompt(s)}] + history
            assistant = None
            try:
                async for ev in self._complete(messages):
                    if ev["type"] == "_message":
                        assistant = ev["message"]
                    else:
                        yield ev
            except APIConnectionError:
                yield {"type": "error", "message": f"Cannot reach the LLM at {s.llm_base_url} — is the model server "
                                                   "(LM Studio / Ollama / provider) running and LLM_BASE_URL correct?"}
                break
            except APIStatusError as e:
                yield {"type": "error", "message": f"LLM error {e.status_code}: {e.message}"}
                break
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
                       "rejected": "REJECTED by the user (not executed)",
                       "expired": "NOT executed because the confirmation expired"}.get(action.status, action.status)
            session.messages.append({"role": "user", "content":
                f"[system notice] Action {action.id} ({action.summary}) was {verdict}. "
                f"Result: {_truncate(action.result)}. Briefly confirm the outcome to the user."})
            async for ev in self._run(session):
                yield ev

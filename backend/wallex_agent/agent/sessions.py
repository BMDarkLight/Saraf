"""Saved chat sessions: one JSON file per conversation under data/sessions/.

Each file holds the LLM-side history (so a reopened conversation continues with its context) and the
browser's transcript (tool cards, generative UI, confirmations), which the UI renders as-is.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

_ID = re.compile(r"^[a-f0-9]{8,64}$")


class SessionStore:
    def __init__(self, root: Path):
        self.dir = root / "sessions"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, sid: str) -> Path | None:
        return self.dir / f"{sid}.json" if _ID.match(sid) else None

    def load(self, sid: str) -> dict | None:
        p = self._path(sid)
        if not p or not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except ValueError:
            return None

    def _write(self, sid: str, data: dict) -> None:
        p = self._path(sid)
        if not p:
            return
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, default=str))
        tmp.replace(p)   # atomic: a crash mid-write never leaves a half file

    def save_history(self, sid: str, messages: list[dict]) -> None:
        if not messages:
            return
        data = self.load(sid) or {"id": sid, "created": time.time(), "transcript": []}
        data["messages"] = messages
        data["updated"] = time.time()
        if not data.get("title"):
            first = next((m.get("content") for m in messages if m.get("role") == "user"), "") or ""
            data["title"] = first.strip().splitlines()[0][:80] if first.strip() else "گفتگو"
        self._write(sid, data)

    def save_transcript(self, sid: str, transcript: list) -> bool:
        data = self.load(sid)
        if data is None:   # only conversations the agent has actually seen
            return False
        data["transcript"] = transcript
        data["updated"] = time.time()
        self._write(sid, data)
        return True

    def list(self, limit: int = 100) -> list[dict]:
        rows = []
        for p in self.dir.glob("*.json"):
            try:
                d = json.loads(p.read_text())
            except ValueError:
                continue
            rows.append({"id": d["id"], "title": d.get("title") or "گفتگو", "updated": d.get("updated", 0),
                         "turns": sum(1 for m in d.get("messages", []) if m.get("role") == "user")})
        return sorted(rows, key=lambda r: r["updated"], reverse=True)[:limit]

    def delete(self, sid: str) -> bool:
        p = self._path(sid)
        if p and p.exists():
            p.unlink()
            return True
        return False

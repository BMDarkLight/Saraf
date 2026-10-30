"""Connections set from the UI: venue credentials, the default venue and the chat model, saved in
data/connections.json (readable by the owner only).

`get_settings()` lays these over the environment, so the server, the engine and the scripts all see the same
values; `.env` stays the first-run setup and the fallback. Nothing else can be stored here, so the risk limits and
the engine profile cannot be changed through this file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

LLM_KEYS = ("llm_base_url", "llm_api_key", "llm_model")
SECRET_LLM = {"llm_api_key"}


def store_path(data_dir: Path) -> Path:
    return Path(data_dir) / "connections.json"


def load(data_dir: Path) -> dict[str, str]:
    try:
        data = json.loads(store_path(data_dir).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def allowed_keys() -> set[str]:
    from .venues import registry

    keys = {"default_venue", *LLM_KEYS}
    for name in registry.available():
        keys |= {c.key for c in registry.credentials_of(name)}
    return keys


def save(data_dir: Path, changes: dict[str, str | None]) -> dict[str, str]:
    """Merge `changes` into the store: "" leaves a value as it is, None removes it. Returns the new store."""
    bad = sorted(set(changes) - allowed_keys())
    if bad:
        raise ValueError(f"not a connection setting: {', '.join(bad)}")
    data = load(data_dir)
    for k, v in changes.items():
        if v is None:
            data.pop(k, None)
        elif str(v).strip():
            data[k] = str(v).strip()
    path = store_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return data


# id(settings) -> what each overlaid field held before (the environment's value), to fall back to on removal
_baseline: dict[int, dict[str, Any]] = {}


def apply(settings: Any, values: dict[str, str]) -> None:
    base = _baseline.setdefault(id(settings), {})
    for k, v in values.items():
        if k in type(settings).model_fields:
            base.setdefault(k, getattr(settings, k))
            setattr(settings, k, v)


def sync(settings: Any, keys: Any, saved: dict[str, str]) -> None:
    """Make `keys` on a live Settings match the store: saved values win, removed ones go back to the env's."""
    base = _baseline.get(id(settings), {})
    apply(settings, {k: saved[k] for k in keys if k in saved})
    for k in keys:
        if k not in saved and k in base:
            setattr(settings, k, base[k])


def field_status(settings: Any, saved: dict[str, str], key: str, secret: bool) -> dict:
    """What the UI may know about one value: whether it is set, where it came from, and a hint that never
    reveals a secret (its last 4 characters, and only when it is long enough for that to be safe)."""
    value = str(getattr(settings, key, "") or "")
    if not value:
        return {"set": False, "source": None, "hint": ""}
    source = "saved" if key in saved else "env"
    if not secret:
        return {"set": True, "source": source, "hint": value}
    return {"set": True, "source": source, "hint": "••••" + (value[-4:] if len(value) >= 8 else "")}

"""The connections store: saved venue credentials, default venue and model settings that overlay env."""

import json
import stat

import pytest

from conftest import make_settings
from saraf import connections
from saraf.config import Settings
from saraf.venues import Venues


def test_empty_keeps_none_clears(tmp_path):
    connections.save(tmp_path, {"wallex_api_key": "abc", "llm_model": "gpt-x"})
    connections.save(tmp_path, {"wallex_api_key": "", "llm_model": None})
    assert connections.load(tmp_path) == {"wallex_api_key": "abc"}


def test_unknown_key_rejected(tmp_path):
    with pytest.raises(ValueError, match="max_leverage"):
        connections.save(tmp_path, {"max_leverage": "100"})
    assert connections.load(tmp_path) == {}


def test_file_is_private(tmp_path):
    connections.save(tmp_path, {"bitpin_secret_key": "s"})
    mode = stat.S_IMODE(connections.store_path(tmp_path).stat().st_mode)
    assert mode == 0o600


def test_corrupt_file_loads_empty(tmp_path):
    connections.store_path(tmp_path).write_text("{not json")
    assert connections.load(tmp_path) == {}
    connections.store_path(tmp_path).write_text(json.dumps(["a"]))
    assert connections.load(tmp_path) == {}


def test_overlay_beats_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WALLEX_API_KEY", "from-env")
    connections.save(tmp_path, {"wallex_api_key": "saved"})
    s = Settings(_env_file=None, data_dir=tmp_path)
    assert s.wallex_api_key == "from-env"
    connections.apply(s, connections.load(tmp_path))
    assert s.wallex_api_key == "saved"


def test_status_never_contains_secret(tmp_path):
    s = make_settings(tmp_path, wallex_api_key="envkey1234")
    connections.save(tmp_path, {"bitpin_secret_key": "supersecret9876"})
    saved = connections.load(tmp_path)
    connections.apply(s, saved)
    st = connections.field_status(s, saved, "bitpin_secret_key", secret=True)
    assert st == {"set": True, "source": "saved", "hint": "••••9876"}
    st = connections.field_status(s, saved, "wallex_api_key", secret=True)
    assert st == {"set": True, "source": "env", "hint": "••••1234"}
    assert connections.field_status(s, saved, "nobitex_token", secret=True) == {"set": False, "source": None, "hint": ""}
    s.llm_model = "gpt-4o"
    assert connections.field_status(s, saved, "llm_model", secret=False)["hint"] == "gpt-4o"


def test_short_secret_hint_hides_everything(tmp_path):
    s = make_settings(tmp_path, wallex_api_key="abc")
    assert connections.field_status(s, {}, "wallex_api_key", secret=True)["hint"] == "••••"


async def test_reload_returns_new_instance_with_new_key(tmp_path):
    s = make_settings(tmp_path)
    vs = Venues(s)
    old = vs.get("bitpin")
    s.bitpin_api_key, s.bitpin_secret_key = "k", "s"
    new = await vs.reload("bitpin")
    assert new is not old and new.has_key and vs.get("bitpin") is new

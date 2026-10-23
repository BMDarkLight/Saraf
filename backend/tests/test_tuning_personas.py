"""Strategy tuning, copy-trading personas and the strategy chat (no network, no LLM)."""

import asyncio
import json
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from conftest import make_settings
from wallex_agent import personas, strategy_chat, tuning


def test_tuning_validates_bounds_types_and_cross_rules():
    ok = tuning.validate({"engine_max_positions": "2", "engine_core_enabled": "false", "engine_setups": ["dip_in_uptrend"],
                          "engine_decider": "rules", "engine_max_exposure_pct": 0.5})
    assert ok == {"engine_max_positions": 2, "engine_core_enabled": False, "engine_setups": "dip_in_uptrend",
                  "engine_decider": "rules", "engine_max_exposure_pct": 0.5}
    for bad in ({"engine_max_positions": 9}, {"engine_quote": "TMN"}, {"engine_decider": "magic"},
                {"engine_setups": "moonshot"}, {"engine_drawdown_pause_pct": 0.1, "engine_drawdown_stop_pct": 0.08},
                {"engine_max_exposure_pct": "lots"}):
        with pytest.raises(tuning.TuningError):
            tuning.validate(bad)


def test_tuning_env_roundtrip_feeds_the_engine_settings(tmp_path):
    vals = tuning.validate({"engine_setups": "", "engine_breadth_gate_pct": 15, "engine_final_sweep": False})
    f = tmp_path / "tuning.env"
    f.write_text(tuning.to_env(vals))
    assert tuning.read_env(f) == vals
    import os
    env = dict(line.split("=", 1) for line in f.read_text().splitlines())
    s = make_settings(tmp_path, **{k.lower(): v for k, v in env.items()})
    assert s.engine_setups == "" and s.engine_breadth_gate_pct == 15 and s.engine_final_sweep is False


def test_every_persona_preset_is_valid_and_has_a_profile():
    ids = set()
    for p in personas.PERSONAS:
        tuning.validate(p["preset"])
        assert p["name"] and p["tagline"] and p["bio"] and 1 <= p["risk"] <= 5 and 0 <= p["hue"] < 360
        ids.add(p["id"])
    assert len(ids) == len(personas.PERSONAS)
    assert "strategist" not in personas.session_ids() and "guardian" in personas.session_ids()


@pytest.fixture
def srv(tmp_path, monkeypatch):
    from wallex_agent import server

    calls = []

    class Proc:
        returncode = 0
        async def communicate(self):
            return b"", b""

    async def fake_exec(*args, **kw):
        calls.append(args)
        return Proc()
    eng = tmp_path / "data/engine"
    eng.mkdir(parents=True)
    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "ENGINE_MODE_FILE", eng / "mode.env")
    monkeypatch.setattr(server, "STRATEGY_PROFILE_FILE", eng / "profile.env")
    monkeypatch.setattr(server, "TUNING_FILE", eng / "tuning.env")
    monkeypatch.setattr(server, "COPY_FILE", eng / "copy.json")
    monkeypatch.setattr(server.shutil, "which", lambda name: "/bin/systemctl")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    (eng / "mode.env").write_text("ENGINE_RUN_ARGS=--mode shadow\n")
    return NS(server=server, c=TestClient(server.app), calls=calls, eng=eng, tmp=tmp_path)


def test_tuning_endpoint_saves_restarts_and_guards_live(srv):
    c = srv.c
    r = c.get("/api/strategy/tuning").json()
    assert {p["key"] for p in r["params"]} >= {"engine_max_exposure_pct", "engine_setups"} and not r["live"]
    assert c.post("/api/strategy/tuning", json={"values": {"engine_max_positions": 99}}).status_code == 400
    assert c.post("/api/strategy/tuning", json={"values": {"engine_max_positions": 1}}).json()["ok"]
    assert "ENGINE_MAX_POSITIONS=1" in (srv.eng / "tuning.env").read_text()
    assert srv.calls[-1] == ("systemctl", "restart", "wallex-engine")
    c.post("/api/strategy/tuning", json={"values": {"engine_breadth_gate_pct": 20}})
    assert tuning.read_env(srv.eng / "tuning.env") == {"engine_max_positions": 1, "engine_breadth_gate_pct": 20.0}
    (srv.eng / "mode.env").write_text("ENGINE_RUN_ARGS=--mode live --confirm-live\n")   # real money: confirm first
    n = len(srv.calls)
    assert c.post("/api/strategy/tuning", json={"values": {"engine_max_positions": 2}}).status_code == 400
    assert len(srv.calls) == n
    assert c.post("/api/strategy/tuning", json={"reset": True, "confirm": "LIVE"}).json()["ok"]
    assert not (srv.eng / "tuning.env").exists()


def test_copy_persona_applies_its_preset_and_stop_copy_reverts(srv):
    c = srv.c
    run = srv.eng / "persona-dip_hunter"
    run.mkdir()
    (run / "equity.csv").write_text("time,equity_tmn,cash_tmn,exposure_tmn,drawdown_pct\n100,5000000,0,0,0\n"
                                    "400,4900000,0,0,2\n700,5100000,0,0,0\n")
    (run / "trades.csv").write_text("trade_id,symbol,setup,entry_time,entry_price,qty,cost_tmn,exit_time,exit_price,"
                                    "proceeds_tmn,pnl_tmn,pnl_pct,exit_reason,ai_confidence,entry_reasoning\n"
                                    "T1,BTCUSDT,dip_in_uptrend,a,1,1,1,b,1,1,12000,0.8,take_profit,,r\n")
    (run / "state.json").write_text(json.dumps({"positions": {}, "core": {"status": "holding"}}))
    r = c.get("/api/personas").json()
    hunter = next(p for p in r["personas"] if p["id"] == "dip_hunter")
    assert hunter["return_pct"] == 2.0 and hunter["max_drawdown_pct"] == 2.0 and hunter["trades"] == 1
    assert hunter["win_rate_pct"] == 100 and hunter["active"] and len(hunter["curve"]) == 3
    assert c.post("/api/personas/nobody/copy", json={}).status_code == 404
    assert c.post("/api/personas/dip_hunter/copy", json={}).json()["copying"]["id"] == "dip_hunter"
    over = tuning.read_env(srv.eng / "tuning.env")
    assert over["engine_max_positions"] == 3 and over["engine_decider"] == "rules"
    assert "ENGINE_QUOTE=USDT" in (srv.eng / "profile.env").read_text()   # personas sit on the strategy profile
    c.post("/api/strategy/tuning", json={"values": {"engine_max_positions": 2}})
    assert c.get("/api/personas").json()["copying"]["modified"]
    assert c.post("/api/personas/stop-copy", json={}).json()["ok"]
    assert not (srv.eng / "tuning.env").exists() and c.get("/api/personas").json()["copying"] is None


def test_chat_proposal_is_validated_and_only_keeps_real_changes():
    eff = {"engine_max_positions": 2, "engine_breadth_gate_pct": 25.0, "engine_drawdown_pause_pct": 0.06,
           "engine_drawdown_stop_pct": 0.12}
    p = strategy_chat._proposal({"changes": {"max_positions": 3, "engine_breadth_gate_pct": 25, "engine_quote": "TMN",
                                             "engine_min_score": 7}, "reasoning": "x"}, eff)
    assert p["changes"] == {"engine_max_positions": 3} and len(p["dropped"]) == 2
    assert strategy_chat._proposal({"changes": {"engine_max_positions": 2}}, eff) is None
    bad = strategy_chat._proposal({"changes": {"engine_drawdown_pause_pct": 0.13}}, eff)
    assert bad["changes"] == {} and bad["dropped"]


async def test_chat_reads_a_tool_call_proposal(tmp_path, monkeypatch):
    call = NS(function=NS(name="propose_changes",
                          arguments=json.dumps({"changes": {"engine_breadth_gate_pct": 15}, "reasoning": "بیشتر"})))

    class FakeLLM:
        def __init__(self, **kw):
            self.chat = NS(completions=NS(create=self.create))

        async def create(self, **kw):
            assert kw["tools"][0]["function"]["name"] == "propose_changes"
            assert "CURRENT PARAMETERS" in kw["messages"][0]["content"]
            return NS(choices=[NS(message=NS(content="دروازه را باز کنیم.", tool_calls=[call]))])
    monkeypatch.setattr(strategy_chat, "AsyncOpenAI", FakeLLM)
    r = await strategy_chat.ask(make_settings(tmp_path), [{"role": "user", "content": "more trades"}],
                                {"engine_breadth_gate_pct": 25.0}, {}, None)
    assert r["reply"] and r["proposal"]["changes"] == {"engine_breadth_gate_pct": 15.0}

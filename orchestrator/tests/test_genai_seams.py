"""real_summon_one context + grounding (managed-agents finalize).

The Gemini client is monkeypatched, so these run with no network and without
the optional [live] google-genai dep installed.
"""

from __future__ import annotations

import json
import types

import pytest

from orchestrator import genai_seams as gs
from orchestrator.knowledge import KnowledgeAdapter
from orchestrator.proto.events import SummonGuild


class _FakeModels:
    def __init__(self, text: str):
        self._text = text
        self.last: dict | None = None

    def generate_content(self, model, contents, config=None):
        self.last = {"model": model, "contents": contents, "config": config}
        return types.SimpleNamespace(text=self._text)


class _FakeClient:
    def __init__(self, text: str):
        self.models = _FakeModels(text)


def _summon(briefing: str) -> SummonGuild:
    return SummonGuild(callId="c", topic="comm-timeout",
                       smes=["@power", "@signal"], briefing=briefing)


def test_summon_prompt_includes_persona_briefing_and_siblings(monkeypatch):
    fake = _FakeClient(json.dumps({
        "confidence": 0.9, "claim": "Missing cell stack.",
        "rationale": "Per datasheet §7 the AFE needs the stack to wake.",
    }))
    monkeypatch.setattr(gs, "_genai", lambda: fake)

    brief = "Operator said: comm timeout\nBoard under test (bq79616): U2 BQ79616.\nDocumented net limits: J3≤30.0V."
    resp = gs.real_summon_one("@power", _summon(brief))

    prompt = fake.models.last["contents"]
    assert "Power Engineer" in prompt                 # persona / lane (system instruction)
    assert "Operator said: comm timeout" in prompt    # the grounded briefing
    assert "J3≤30.0V" in prompt                        # documented limit reached the SME
    assert "@signal" in prompt                         # sibling SMEs
    assert resp.smeId == "@power" and abs(resp.confidence - 0.9) < 1e-6
    assert resp.claim == "Missing cell stack."


def test_proposed_action_is_grounded_by_orchestrator(monkeypatch):
    pa = {"tool": "set_psu",
          "args": {"target": "J3", "voltage_v": 30.0, "current_limit_a": 0.5},
          "instruction": "Set PSU to 30 V across J3.", "risk": "HIGH"}
    fake = _FakeClient(json.dumps({
        "confidence": 0.92, "claim": "Apply the cell stack.", "rationale": "r",
        "proposedAction": pa,
    }))
    monkeypatch.setattr(gs, "_genai", lambda: fake)

    resp = gs.real_summon_one("@power", _summon("b"), knowledge=KnowledgeAdapter())
    assert len(resp.proposedActions) == 1
    a = resp.proposedActions[0]
    assert a.tool == "set_psu" and a.actor == "operator" and a.risk == "HIGH"
    assert a.instruction == "Set PSU to 30 V across J3."
    # the citation comes from the orchestrator's KnowledgeAdapter, not the model.
    assert a.documentedLimitRef and "J3" in a.documentedLimitRef


def test_malformed_proposed_action_is_dropped(monkeypatch):
    fake = _FakeClient(json.dumps({
        "confidence": 0.5, "claim": "c", "rationale": "r",
        "proposedAction": {"no_tool": True},  # malformed
    }))
    monkeypatch.setattr(gs, "_genai", lambda: fake)
    resp = gs.real_summon_one("@power", _summon("b"), knowledge=KnowledgeAdapter())
    assert resp.proposedActions == []


def test_falls_back_to_stub_on_error(monkeypatch):
    class _Boom:
        @property
        def models(self):
            raise RuntimeError("no client")

    monkeypatch.setattr(gs, "_genai", lambda: _Boom())
    resp = gs.real_summon_one("@power", _summon("b"))
    assert "[stub]" in resp.claim  # never-fail-stop (01 §7)


# ── managed-agents (Interactions API) path ──────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_env():
    gs._sme_env.clear()
    yield
    gs._sme_env.clear()


class _ManagedClient:
    """A client exposing `.interactions.create` (the managed-agents surface)."""
    def __init__(self, text: str, env_id: str = "env-1"):
        self._text, self._env, self.calls = text, env_id, []
        outer = self

        class _Inter:
            def create(self, **kw):
                outer.calls.append(kw)
                return types.SimpleNamespace(output_text=outer._text, environment_id=outer._env)

        self.interactions = _Inter()


def test_uses_interactions_create_when_available(monkeypatch):
    c = _ManagedClient(json.dumps({"confidence": 0.9, "claim": "Missing stack.", "rationale": "§7"}),
                       env_id="env-power")
    monkeypatch.setattr(gs, "_genai", lambda: c)
    resp = gs.real_summon_one("@power", _summon("Operator said: comm timeout"))
    kw = c.calls[0]
    assert kw["agent"] == gs.ANTIGRAVITY_AGENT
    assert "Operator said: comm timeout" in kw["input"]   # grounded briefing as input
    assert "Power Engineer" in kw["system_instruction"]   # persona as system instruction
    assert kw["environment"] == "remote"                  # first call provisions
    assert resp.claim == "Missing stack." and abs(resp.confidence - 0.9) < 1e-6
    assert gs._sme_env["@power"] == "env-power"            # warm env cached


def test_reuses_warm_env_on_second_summon(monkeypatch):
    c = _ManagedClient(json.dumps({"confidence": 0.8, "claim": "c", "rationale": "r"}), env_id="env-1")
    monkeypatch.setattr(gs, "_genai", lambda: c)
    gs.real_summon_one("@power", _summon("b"))
    gs.real_summon_one("@power", _summon("b"))
    assert [k["environment"] for k in c.calls] == ["remote", "env-1"]  # 2nd reuses warm env


def test_managed_disabled_via_env_uses_flash(monkeypatch):
    monkeypatch.setenv("FORGE_USE_MANAGED_AGENTS", "0")
    c = _ManagedClient("unused")          # has .interactions ...
    captured: dict = {}

    class _Models:
        def generate_content(self, model, contents, config=None):
            captured["contents"] = contents
            return types.SimpleNamespace(text=json.dumps({"confidence": 0.5, "claim": "flash", "rationale": "r"}))

    c.models = _Models()                  # ... and .models
    monkeypatch.setattr(gs, "_genai", lambda: c)
    resp = gs.real_summon_one("@power", _summon("Operator said: x"))
    assert c.calls == []                  # interactions NOT used when opted out
    assert resp.claim == "flash"          # came via Flash
    assert "Power Engineer" in captured["contents"]


def test_managed_loose_json_in_prose(monkeypatch):
    c = _ManagedClient('Analysis:\n```json\n{"confidence":0.7,"claim":"ok","rationale":"r"}\n```\nDone.')
    monkeypatch.setattr(gs, "_genai", lambda: c)
    resp = gs.real_summon_one("@power", _summon("b"))
    assert resp.claim == "ok" and abs(resp.confidence - 0.7) < 1e-6


def test_prewarm_caches_envs(monkeypatch):
    c = _ManagedClient(json.dumps({"confidence": 0.5, "claim": "ready", "rationale": "r"}), env_id="warm")
    monkeypatch.setattr(gs, "_genai", lambda: c)
    out = gs.prewarm_smes(["@power", "@signal"])
    assert out == {"@power": "warm", "@signal": "warm"}
    assert len(c.calls) == 2

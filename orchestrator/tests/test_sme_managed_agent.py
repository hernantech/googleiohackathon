"""Opt-in per-SME managed-agent path (Part C.2).

FORGE_SME_USE_SANDBOX=1 makes real_summon_one run each SME as a REAL Antigravity
managed agent (interactions.create with a per-SME warm environment reused across
turns) instead of the default Flash tool-loop. DEFAULT OFF — the tool-loop stays
the live default.

Offline, no network: a fake `client.interactions` stands in for the real
google-genai Antigravity Interactions API (shaped like the SDK: create() takes
agent/input/system_instruction/environment and returns an object with
output_text + environment_id). We assert:

  * default (flag unset) → the Flash tool-loop path runs, sandbox NEVER touched;
  * flag on → the SME is summoned via interactions.create with the persona as
    system_instruction and the briefing as input, returning a real SmeResponse;
  * each SME gets its OWN warm environment, provisioned once and REUSED across
    turns (no re-create per turn);
  * prewarm_smes() provisions one env per roster SME up front (no-op when off);
  * a sandbox failure on the opt-in path degrades to the stub (never-fail-stop).
"""

from __future__ import annotations

import json
import types as _pytypes

import pytest

from orchestrator import genai_seams as gs
from orchestrator.knowledge import KnowledgeAdapter
from orchestrator.proto.events import SummonGuild


# ───────────────────────── fake managed-agent Interactions API ─────────────

class _FakeSmeInteractions:
    """Records every create(). environment="remote" returns a NEW per-SME env id
    (keyed off the system_instruction so each SME gets a distinct one);
    environment=<id> reuses it and returns the scripted reply for that SME."""

    def __init__(self, replies: dict[str, str] | None = None):
        self._replies = replies or {}
        self.calls: list[dict] = []
        self._env_seq = 0

    def create(self, *, agent, input, system_instruction=None, environment=None, **kw):
        self.calls.append({
            "agent": agent, "input": input,
            "system_instruction": system_instruction, "environment": environment,
        })
        if environment == "remote":
            self._env_seq += 1
            return _pytypes.SimpleNamespace(
                id=f"it-{self._env_seq}", environment_id=f"env-{self._env_seq}",
                output_text="ready", steps=[])
        # reuse turn: pick the reply by which SME mentions itself in the input.
        reply = "{}"
        for sme, r in self._replies.items():
            if sme in (input or "") or sme in (system_instruction or ""):
                reply = r
                break
        else:
            # default reply when no SME-specific script matched
            reply = next(iter(self._replies.values()), "{}")
        return _pytypes.SimpleNamespace(
            id="it-reuse", environment_id=environment, output_text=reply, steps=[])


class _FakeClient:
    def __init__(self, interactions):
        self.interactions = interactions


def _summon(briefing="diagnose", smes=("@power", "@signal")) -> SummonGuild:
    return SummonGuild(callId="c", topic="t", smes=list(smes), briefing=briefing)


@pytest.fixture(autouse=True)
def _reset():
    gs.reset_sme_env_for_tests()
    gs.reset_sme_packs_for_tests()
    yield
    gs.reset_sme_env_for_tests()
    gs.reset_sme_packs_for_tests()


@pytest.fixture
def _sandbox_on(monkeypatch):
    monkeypatch.setenv("FORGE_SME_USE_SANDBOX", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
    monkeypatch.setattr(gs, "_sandbox_enabled", lambda: True)


# ───────────────────────── default path is the tool-loop ───────────────────

def test_default_path_is_flash_tool_loop_not_sandbox(monkeypatch):
    """With the flag UNSET, real_summon_one uses the Flash tool-loop and never
    touches the managed-agent sandbox."""
    monkeypatch.delenv("FORGE_SME_USE_SANDBOX", raising=False)
    assert gs._sme_sandbox_enabled() is False

    called = {"loop": 0, "sandbox": 0}

    def fake_loop(system, brief, siblings, knowledge, on_tool_call=None):
        called["loop"] += 1
        return {"confidence": 0.8, "claim": "loop.", "rationale": "r"}, []

    def fake_sandbox(*a, **k):
        called["sandbox"] += 1
        raise AssertionError("sandbox path taken when flag off")

    monkeypatch.setattr(gs, "_run_sme_tool_loop", fake_loop)
    monkeypatch.setattr(gs, "_summon_via_sandbox", fake_sandbox)

    resp = gs.real_summon_one("@power", _summon(), knowledge=KnowledgeAdapter())
    assert resp.claim == "loop."
    assert called["loop"] == 1 and called["sandbox"] == 0


# ───────────────────────── opt-in sandbox path ─────────────────────────────

def test_sandbox_path_summons_via_interactions(monkeypatch, _sandbox_on):
    """Flag on → the SME runs via interactions.create with the persona as the
    system_instruction and the briefing as the input, returning a SmeResponse."""
    reply = json.dumps({"confidence": 0.9, "claim": "rail ok.",
                        "rationale": "Per board-doc J3 ≤ 30V."})
    fake = _FakeSmeInteractions(replies={"@power": reply})
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(fake))

    resp = gs.real_summon_one("@power", _summon(briefing="check the J3 rail"),
                              knowledge=KnowledgeAdapter())
    assert resp.smeId == "@power" and resp.claim == "rail ok."
    assert abs(resp.confidence - 0.9) < 1e-6

    # provisioning create (environment="remote") then the turn create (reuse).
    remote = [c for c in fake.calls if c["environment"] == "remote"]
    assert len(remote) == 1
    turn = [c for c in fake.calls if c["environment"] != "remote"]
    assert turn and "check the J3 rail" in turn[0]["input"]      # briefing is input
    assert "@power" in (turn[0]["system_instruction"] or "")     # persona is system
    assert turn[0]["agent"] == gs.ANTIGRAVITY_AGENT


def test_each_sme_has_own_env_reused_across_turns(monkeypatch, _sandbox_on):
    """Each SME gets its OWN warm environment, provisioned once and reused on the
    next turn (no re-create per turn)."""
    fake = _FakeSmeInteractions(replies={
        "@power": json.dumps({"confidence": 0.8, "claim": "p.", "rationale": "r"}),
        "@signal": json.dumps({"confidence": 0.8, "claim": "s.", "rationale": "r"}),
    })
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(fake))

    gs.real_summon_one("@power", _summon(), knowledge=KnowledgeAdapter())
    gs.real_summon_one("@signal", _summon(), knowledge=KnowledgeAdapter())
    env_after_first = dict(gs._sme_env)
    assert set(env_after_first) == {"@power", "@signal"}
    assert env_after_first["@power"] != env_after_first["@signal"]  # distinct envs

    # second turn for @power reuses its env — no new "remote" create.
    remote_before = len([c for c in fake.calls if c["environment"] == "remote"])
    gs.real_summon_one("@power", _summon(), knowledge=KnowledgeAdapter())
    remote_after = len([c for c in fake.calls if c["environment"] == "remote"])
    assert remote_after == remote_before  # reused, not re-provisioned
    assert gs._sme_env["@power"] == env_after_first["@power"]


def test_sandbox_path_streams_completion(monkeypatch, _sandbox_on):
    """The opt-in path surfaces the completed turn through on_tool_call (the same
    streaming-sink contract the tool-loop honors)."""
    reply = json.dumps({"confidence": 0.7, "claim": "ok.", "rationale": "r"})
    fake = _FakeSmeInteractions(replies={"@power": reply})
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(fake))

    seen: list[dict] = []
    gs.real_summon_one("@power", _summon(), knowledge=KnowledgeAdapter(),
                       on_tool_call=seen.append)
    assert any(c["name"] == "managed_agent" for c in seen)


def test_sandbox_failure_degrades_to_stub(monkeypatch, _sandbox_on):
    """A sandbox failure on the opt-in path degrades to the stub, never raises."""
    class _Boom(_FakeSmeInteractions):
        def create(self, **kw):
            if kw.get("environment") == "remote":
                return super().create(**kw)  # provisioning succeeds
            raise RuntimeError("agent exploded")
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(_Boom()))

    resp = gs.real_summon_one("@power", _summon())
    assert "[stub]" in resp.claim  # never-fail-stop (01 §7)


# ───────────────────────── prewarm ─────────────────────────────────────────

def test_prewarm_provisions_one_env_per_roster_sme(monkeypatch, _sandbox_on):
    fake = _FakeSmeInteractions()
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(fake))

    envs = gs.prewarm_smes(["@power", "@signal", "@firmware"])
    assert set(envs) == {"@power", "@signal", "@firmware"}
    # one provisioning create per SME, all distinct env ids.
    remote = [c for c in fake.calls if c["environment"] == "remote"]
    assert len(remote) == 3
    assert len(set(envs.values())) == 3

    # idempotent: a second prewarm does not re-provision.
    gs.prewarm_smes(["@power"])
    remote2 = [c for c in fake.calls if c["environment"] == "remote"]
    assert len(remote2) == 3


def test_prewarm_no_op_when_flag_off(monkeypatch):
    monkeypatch.delenv("FORGE_SME_USE_SANDBOX", raising=False)

    def _explode():
        raise AssertionError("_genai touched when sandbox path off")
    monkeypatch.setattr(gs, "_genai", _explode)
    assert gs.prewarm_smes(["@power"]) == {}
    assert gs._sme_env == {}

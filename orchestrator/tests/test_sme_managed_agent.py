"""Tool-capable per-SME managed-agent path (HYBRID) — now the DEFAULT summon path.

real_summon_one runs each SME as a REAL, TOOL-CAPABLE per-SME managed agent. The
path is a HYBRID, dictated by the live-verified Interactions API (google-genai
2.6.0): custom function tools ARE supported on interactions.create, but ONLY on a
MODEL-based interaction — the Antigravity managed AGENT rejects custom function
tools at runtime. So:

  1. GATHER — a bounded model-based interactions.create() tool-loop declares the
     SAME knowledge tools + run_analysis the Flash loop uses, executes each
     emitted function-call against the per-session KnowledgeAdapter (the shared
     _dispatch_tool seam), streams each call through on_tool_call, and continues
     the interaction (previous_interaction_id + function_result input).
  2. REASON — the retrieved, cited grounding is folded into an enriched briefing
     fed to the SME's OWN warm Antigravity managed agent (agent=..., environment=
     <warm per-SME env>) for the final SmeResponse JSON.

This is now the DEFAULT; `FORGE_SME_USE_SANDBOX=0` is the escape hatch back to the
pure Flash tool-loop.

Offline, no network: a fake `client.interactions` stands in for the real
google-genai Interactions API, modeling both the model-based GATHER turns and the
agent-based provisioning + REASON turns. We assert:

  * the GATHER step grounds via the custom tools on a MODEL interaction, while the
    REASON step runs the AGENT in the warm env (no custom tools on the agent);
  * the path CITES (documentedLimitRef from the orchestrator, never the model)
    and STREAMS (each tool call + the managed_agent notice via on_tool_call);
  * the gather function results are fed back and the interaction is CONTINUED, and
    the retrieved fact reaches the REASON turn's enriched briefing;
  * the sandbox path is the DEFAULT (flag unset → sandbox, not the Flash loop);
  * FORGE_SME_USE_SANDBOX=0 → the Flash tool-loop, sandbox NEVER touched;
  * each SME gets its OWN warm environment, provisioned once and REUSED;
  * prewarm_smes() provisions one env per roster SME up front (no-op when off);
  * keepwarm_sme_envs() pings every provisioned per-SME env;
  * a sandbox failure FALLS BACK to the Flash tool-loop, then the stub.
"""

from __future__ import annotations

import json
import types as _pytypes

import pytest

from orchestrator import genai_seams as gs
from orchestrator.knowledge import KnowledgeAdapter
from orchestrator.proto.events import SummonGuild


# ───────────────────────── fake managed-agent Interactions API ─────────────

def _fc_step(name: str, args: dict, call_id: str = "c1"):
    """A FunctionCallStep-shaped object (type=='function_call' + id/name/arguments)."""
    return _pytypes.SimpleNamespace(
        type="function_call", id=call_id, name=name, arguments=dict(args))


class _FakeSmeInteractions:
    """Records every create(), modeling the HYBRID per-SME managed-agent path:

      * agent=..., environment="remote"  → provisioning: a NEW per-SME env id.
      * model=FLASH_MODEL, tools=...      → GATHER tool-loop turn (custom function
        tools are allowed on a model interaction). A `scripts` map (sme -> list of
        turns) drives it: each turn is either a list of FunctionCallStep(s) (the
        model wants to call tools) or None (it concluded gathering). Turns are
        consumed per continuation.
      * agent=..., environment=<env id>   → the final REASON turn in the SME's warm
        sandbox; returns the scripted reply for that SME (the SmeResponse JSON).

    With no script for an SME, the gather loop concludes immediately and the final
    reply is the first scripted reply (or "{}")."""

    def __init__(self, replies: dict[str, str] | None = None,
                 scripts: dict[str, list] | None = None):
        self._replies = replies or {}
        self._scripts = {k: list(v) for k, v in (scripts or {}).items()}
        self.calls: list[dict] = []
        self._env_seq = 0

    def _sme_of(self, system_instruction, input) -> str:
        text = (system_instruction or "") + " " + str(input or "")
        for sme in list(self._replies) + list(self._scripts):
            if sme in text:
                return sme
        return ""

    def create(self, *, agent=None, model=None, input=None, system_instruction=None,
               environment=None, tools=None, previous_interaction_id=None,
               response_mime_type=None, **kw):
        self.calls.append({
            "agent": agent, "model": model, "input": input,
            "system_instruction": system_instruction, "environment": environment,
            "tools": tools, "previous_interaction_id": previous_interaction_id,
            "response_mime_type": response_mime_type,
        })
        # provisioning create (agent + environment="remote") → new per-SME env id.
        if environment == "remote":
            self._env_seq += 1
            return _pytypes.SimpleNamespace(
                id=f"it-{self._env_seq}", environment_id=f"env-{self._env_seq}",
                output_text="ready", steps=[])

        # GATHER tool-loop turn: model-based interaction (no agent). Pop the next
        # scripted turn for this SME (default: conclude gathering). The opening turn
        # carries system_instruction; continuations don't, so carry the active sme.
        if model is not None:
            sme = self._sme_of(system_instruction, input)
            if sme == "" and previous_interaction_id is not None:
                sme = getattr(self, "_active_sme", "")
            else:
                self._active_sme = sme
            steps_script = self._scripts.get(sme)
            steps = steps_script.pop(0) if steps_script else None
            # when gathering concludes, output_text is the grounding summary.
            out = "" if steps else f"grounding for {sme}".strip()
            return _pytypes.SimpleNamespace(
                id=f"turn-{len(self.calls)}", output_text=out,
                steps=list(steps) if steps else [])

        # final REASON turn: agent in the warm env → the scripted SmeResponse JSON.
        sme = self._sme_of(system_instruction, input) or getattr(self, "_active_sme", "")
        reply = self._replies.get(sme) or next(iter(self._replies.values()), "{}")
        return _pytypes.SimpleNamespace(
            id="it-final", environment_id=environment, output_text=reply, steps=[])


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
    """The sandbox path is now the DEFAULT — we only need a key + genai present.
    (Flag left UNSET deliberately so these tests exercise the default.)"""
    monkeypatch.delenv("FORGE_SME_USE_SANDBOX", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
    monkeypatch.setattr(gs, "_sandbox_enabled", lambda: True)


# ───────────────────────── default path IS the sandbox ─────────────────────

def test_sandbox_is_the_default_path(monkeypatch):
    """With the flag UNSET, real_summon_one uses the per-SME managed-agent
    sandbox path (NOT the Flash loop)."""
    monkeypatch.delenv("FORGE_SME_USE_SANDBOX", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
    monkeypatch.setattr(gs, "_sandbox_enabled", lambda: True)
    assert gs._sme_sandbox_enabled() is True

    called = {"loop": 0, "sandbox": 0}

    def fake_loop(system, brief, siblings, knowledge, on_tool_call=None):
        called["loop"] += 1
        raise AssertionError("Flash loop taken when sandbox is the default")

    def fake_sandbox(sme_id, summon, system, brief, knowledge=None, on_tool_call=None):
        called["sandbox"] += 1
        return {"confidence": 0.8, "claim": "sandbox.", "rationale": "r"}

    monkeypatch.setattr(gs, "_run_sme_tool_loop", fake_loop)
    monkeypatch.setattr(gs, "_summon_via_sandbox", fake_sandbox)

    resp = gs.real_summon_one("@power", _summon(), knowledge=KnowledgeAdapter())
    assert resp.claim == "sandbox."
    assert called["sandbox"] == 1 and called["loop"] == 0


def test_flag_zero_is_the_escape_hatch_to_flash_loop(monkeypatch):
    """FORGE_SME_USE_SANDBOX=0 → the Flash tool-loop, sandbox NEVER touched."""
    monkeypatch.setenv("FORGE_SME_USE_SANDBOX", "0")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
    monkeypatch.setattr(gs, "_sandbox_enabled", lambda: True)
    assert gs._sme_sandbox_enabled() is False

    called = {"loop": 0, "sandbox": 0}

    def fake_loop(system, brief, siblings, knowledge, on_tool_call=None):
        called["loop"] += 1
        return {"confidence": 0.8, "claim": "loop.", "rationale": "r"}, []

    def fake_sandbox(*a, **k):
        called["sandbox"] += 1
        raise AssertionError("sandbox path taken when flag=0")

    monkeypatch.setattr(gs, "_run_sme_tool_loop", fake_loop)
    monkeypatch.setattr(gs, "_summon_via_sandbox", fake_sandbox)

    resp = gs.real_summon_one("@power", _summon(), knowledge=KnowledgeAdapter())
    assert resp.claim == "loop."
    assert called["loop"] == 1 and called["sandbox"] == 0


# ───────────────────────── tool-capable hybrid path ────────────────────────

def test_hybrid_gathers_with_model_tools_then_reasons_in_agent_sandbox(monkeypatch, _sandbox_on):
    """The GATHER step is a model-based interaction with the knowledge tools +
    run_analysis declared via tools=; the final REASON turn runs the per-SME
    Antigravity AGENT in the warm env (custom tools are NOT on the agent turn).
    Returns a real SmeResponse."""
    reply = json.dumps({"confidence": 0.9, "claim": "rail ok.",
                        "rationale": "Per board-doc J3 ≤ 30V."})
    fake = _FakeSmeInteractions(replies={"@power": reply})
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(fake))

    resp = gs.real_summon_one("@power", _summon(briefing="check the J3 rail"),
                              knowledge=KnowledgeAdapter())
    assert resp.smeId == "@power" and resp.claim == "rail ok."
    assert abs(resp.confidence - 0.9) < 1e-6

    # provisioning create (agent + environment="remote").
    remote = [c for c in fake.calls if c["environment"] == "remote"]
    assert len(remote) == 1 and remote[0]["agent"] == gs.ANTIGRAVITY_AGENT

    # GATHER turn: model-based interaction declaring the custom function tools.
    gather = [c for c in fake.calls if c["model"] is not None]
    assert gather, "the gather step must be a model-based interaction"
    opening = gather[0]
    assert opening["model"] == gs.FLASH_MODEL and opening["agent"] is None
    assert "check the J3 rail" in opening["input"]            # briefing is input
    assert "@power" in (opening["system_instruction"] or "")  # persona is system
    decl = {t["name"] for t in opening["tools"]}
    assert {"lookup_datasheet", "lookup_board_doc", "get_documented_limit",
            "run_analysis"} <= decl
    assert all(t["type"] == "function" for t in opening["tools"])

    # REASON turn: the per-SME AGENT in the warm env (no custom tools on it).
    reason = [c for c in fake.calls
              if c["agent"] == gs.ANTIGRAVITY_AGENT and c["environment"] != "remote"]
    assert reason and reason[-1]["tools"] is None     # agent rejects function tools
    assert reason[-1]["environment"] != "remote"      # ran in the warm per-SME env
    assert "@power" in (reason[-1]["system_instruction"] or "")  # persona is system


def test_hybrid_grounds_and_cites(monkeypatch, _sandbox_on):
    """The GATHER loop CALLS get_documented_limit (executed against the real
    adapter), the result is fed back + the interaction continued, the retrieved
    fact reaches the REASON turn's enriched briefing, and the citation on a
    proposed setpoint is the ORCHESTRATOR's documented limit — never the model's."""
    # gather turn 1: ask for the J3 net limit; turn 2: conclude gathering.
    scripts = {"@power": [[_fc_step("get_documented_limit", {"target": "J3", "kind": "net"})], None]}
    pa = {"tool": "set_psu", "args": {"target": "J3", "voltage_v": 30.0},
          "instruction": "Set PSU to 30 V across J3.", "risk": "HIGH",
          "documentedLimitRef": "model invented this"}
    reply = json.dumps({"confidence": 0.92, "claim": "Apply 30V to J3.",
                        "rationale": "Per board_profile J3<=30V (get_documented_limit).",
                        "proposedAction": pa})
    fake = _FakeSmeInteractions(replies={"@power": reply}, scripts=scripts)
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(fake))

    resp = gs.real_summon_one("@power", _summon(briefing="set J3", smes=("@power",)),
                              knowledge=KnowledgeAdapter())

    # the gather function-call was executed AND the result fed back on a model
    # continuation (previous_interaction_id set, model-based).
    continued = [c for c in fake.calls
                 if c["previous_interaction_id"] and c["model"] is not None]
    assert continued, "the gather interaction must be continued with the results"
    fed = [c for c in continued if isinstance(c["input"], list)]
    assert fed and fed[0]["input"][0]["type"] == "function_result"
    assert fed[0]["input"][0]["name"] == "get_documented_limit"

    # the retrieved fact (with its source) reached the REASON turn's briefing.
    reason = [c for c in fake.calls
              if c["agent"] == gs.ANTIGRAVITY_AGENT and c["environment"] != "remote"]
    assert reason and "board_profile.nets[J3]" in reason[-1]["input"]

    # the citation is the ORCHESTRATOR's, from the real board profile (not the model).
    assert len(resp.proposedActions) == 1
    a = resp.proposedActions[0]
    assert a.documentedLimitRef == "board_profile.nets[J3]"
    assert "model invented this" not in (a.documentedLimitRef or "")


def test_hybrid_streams_tool_calls_and_completion(monkeypatch, _sandbox_on):
    """on_tool_call fires for the opening managed_agent notice AND for each
    executed gather tool call (the same streaming-sink contract the Flash loop
    honors)."""
    scripts = {"@power": [[_fc_step("lookup_board_doc", {"query": "J3 rail"})], None]}
    reply = json.dumps({"confidence": 0.7, "claim": "ok.", "rationale": "r"})
    fake = _FakeSmeInteractions(replies={"@power": reply}, scripts=scripts)
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(fake))

    seen: list[dict] = []
    gs.real_summon_one("@power", _summon(smes=("@power",)), knowledge=KnowledgeAdapter(),
                       on_tool_call=seen.append)
    names = [c["name"] for c in seen]
    assert "managed_agent" in names                 # the opening turn notice
    assert "lookup_board_doc" in names              # the executed gather tool call
    looked = [c for c in seen if c["name"] == "lookup_board_doc"]
    assert looked[0]["args"] == {"query": "J3 rail"} and "result" in looked[0]


def test_gather_rounds_are_capped(monkeypatch, _sandbox_on):
    """The gather loop never runs more than SME_SANDBOX_MAX_TOOL_ROUNDS retrieval
    rounds before moving on to the final REASON turn."""
    monkeypatch.setattr(gs, "SME_SANDBOX_MAX_TOOL_ROUNDS", 2)
    # script more tool turns than the cap; the loop must stop + reason anyway.
    scripts = {"@power": [[_fc_step("lookup_board_doc", {"query": "x"})]] * 10}
    reply = json.dumps({"confidence": 0.5, "claim": "capped.", "rationale": "r"})
    fake = _FakeSmeInteractions(replies={"@power": reply}, scripts=scripts)
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(fake))

    seen: list[dict] = []
    resp = gs.real_summon_one("@power", _summon(smes=("@power",)),
                              knowledge=KnowledgeAdapter(), on_tool_call=seen.append)
    assert resp.claim == "capped."
    executed = [c for c in seen if c["name"] == "lookup_board_doc"]
    assert len(executed) <= 2  # capped


# ───────────────────────── warm per-SME env reuse ──────────────────────────

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

    remote_before = len([c for c in fake.calls if c["environment"] == "remote"])
    gs.real_summon_one("@power", _summon(), knowledge=KnowledgeAdapter())
    remote_after = len([c for c in fake.calls if c["environment"] == "remote"])
    assert remote_after == remote_before  # reused, not re-provisioned
    assert gs._sme_env["@power"] == env_after_first["@power"]


# ───────────────────────── graceful fallback ───────────────────────────────

def test_sandbox_failure_falls_back_to_flash_loop(monkeypatch, _sandbox_on):
    """A sandbox failure on the default path FALLS BACK to the Flash tool-loop
    (not straight to the stub) — graceful degradation (01 §7)."""
    class _Boom(_FakeSmeInteractions):
        def create(self, **kw):
            if kw.get("environment") == "remote":
                return super().create(**kw)  # provisioning succeeds
            raise RuntimeError("agent exploded")
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(_Boom()))

    flash = {"n": 0}

    def fake_loop(system, brief, siblings, knowledge, on_tool_call=None):
        flash["n"] += 1
        return {"confidence": 0.6, "claim": "flash fallback.", "rationale": "r"}, []

    monkeypatch.setattr(gs, "_run_sme_tool_loop", fake_loop)

    resp = gs.real_summon_one("@power", _summon(), knowledge=KnowledgeAdapter())
    assert resp.claim == "flash fallback."
    assert flash["n"] == 1  # the Flash loop ran after the sandbox blew up


def test_total_failure_degrades_to_stub(monkeypatch, _sandbox_on):
    """When BOTH the sandbox AND the Flash fallback fail, degrade to the stub —
    never raises (01 §7)."""
    class _Boom(_FakeSmeInteractions):
        def create(self, **kw):
            if kw.get("environment") == "remote":
                return super().create(**kw)
            raise RuntimeError("agent exploded")
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(_Boom()))

    def fake_loop(*a, **k):
        raise RuntimeError("flash also down")
    monkeypatch.setattr(gs, "_run_sme_tool_loop", fake_loop)

    resp = gs.real_summon_one("@power", _summon())
    assert "[stub]" in resp.claim  # never-fail-stop (01 §7)


# ───────────────────────── prewarm ─────────────────────────────────────────

def test_prewarm_provisions_one_env_per_roster_sme(monkeypatch, _sandbox_on):
    fake = _FakeSmeInteractions()
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(fake))

    envs = gs.prewarm_smes(["@power", "@signal", "@firmware"])
    assert set(envs) == {"@power", "@signal", "@firmware"}
    remote = [c for c in fake.calls if c["environment"] == "remote"]
    assert len(remote) == 3
    assert len(set(envs.values())) == 3

    # idempotent: a second prewarm does not re-provision.
    gs.prewarm_smes(["@power"])
    remote2 = [c for c in fake.calls if c["environment"] == "remote"]
    assert len(remote2) == 3


def test_prewarm_no_op_when_flag_off(monkeypatch):
    monkeypatch.setenv("FORGE_SME_USE_SANDBOX", "0")

    def _explode():
        raise AssertionError("_genai touched when sandbox path off")
    monkeypatch.setattr(gs, "_genai", _explode)
    assert gs.prewarm_smes(["@power"]) == {}
    assert gs._sme_env == {}


# ───────────────────────── per-SME keep-alive ──────────────────────────────

def test_keepwarm_sme_envs_pings_each_provisioned_env(monkeypatch, _sandbox_on):
    """keepwarm_sme_envs pings every provisioned per-SME env (one reuse
    interaction each) so an idle env never cold-starts."""
    fake = _FakeSmeInteractions()
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(fake))

    gs.prewarm_smes(["@power", "@signal"])
    base = len(fake.calls)
    pinged = gs.keepwarm_sme_envs()
    assert pinged == 2
    # two new reuse interactions, against the two provisioned (non-"remote") envs.
    new = fake.calls[base:]
    assert len(new) == 2
    assert all(c["environment"] != "remote" and c["input"] == "ping" for c in new)
    assert {c["environment"] for c in new} == set(gs._sme_env.values())


def test_keepwarm_sme_envs_no_op_when_off(monkeypatch):
    monkeypatch.setenv("FORGE_SME_USE_SANDBOX", "0")

    def _explode():
        raise AssertionError("_genai touched when sandbox path off")
    monkeypatch.setattr(gs, "_genai", _explode)
    assert gs.keepwarm_sme_envs() == 0


def test_keepwarm_sme_envs_robust_to_failure(monkeypatch, _sandbox_on):
    """A ping failure on one env is logged + skipped; never raises (01 §7)."""
    class _Boom(_FakeSmeInteractions):
        def create(self, **kw):
            if kw.get("environment") == "remote":
                return super().create(**kw)
            raise RuntimeError("network blip")
    fake = _Boom()
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(fake))

    gs.prewarm_smes(["@power"])      # provisioning succeeds
    assert gs.keepwarm_sme_envs() == 0  # the ping fails → 0, but never raises

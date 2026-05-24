"""Kept-warm Antigravity compute sandbox + run_analysis tool (deterministic).

Offline, no network: a fake `client.interactions` stands in for the real
google-genai Antigravity Interactions API. The fakes are shaped like the real
google-genai 2.6.0 events (an iterable Stream of objects with `.event_type` +
`.delta` / `.interaction`, matching
`google.genai._interactions.types.interaction_sse_event`). We assert:

  * the sandbox is CREATED ONCE (environment="remote") and REUSED by id
    (environment=<id>) on every later interaction — never re-created;
  * run_analysis returns the computed RESULT string the sandbox printed;
  * intermediate SSE steps (model text / code-exec / code result) are forwarded
    to the on_step sink IN ORDER, as they arrive;
  * the keep-warm ping reuses the same env and is robust to failures;
  * no GEMINI_API_KEY (or google-genai absent) → run_analysis is a no-op stub
    (returns None) and never creates a sandbox (offline boot);
  * a hung interaction is bounded by the timeout (→ None, SME continues);
  * run_analysis is wired into the SME tool-loop and its steps stream through
    the #12 on_tool_call sink as `run_analysis ▸ <step>`.
"""

from __future__ import annotations

import types as _pytypes

import pytest

from orchestrator import genai_seams as gs


# ───────────────────────── fake Antigravity Interactions API ───────────────
#
# Events mirror the real discriminated union (event_type + delta/interaction).

def _text_delta(text: str):
    return _pytypes.SimpleNamespace(
        event_type="step.delta",
        delta=_pytypes.SimpleNamespace(type="text", text=text),
    )


def _code_call_delta(code: str):
    return _pytypes.SimpleNamespace(
        event_type="step.delta",
        delta=_pytypes.SimpleNamespace(
            type="code_execution_call",
            arguments=_pytypes.SimpleNamespace(code=code, language="PYTHON"),
        ),
    )


def _code_result_delta(result: str):
    return _pytypes.SimpleNamespace(
        event_type="step.delta",
        delta=_pytypes.SimpleNamespace(type="code_execution_result", result=result),
    )


def _completed(output_text: str):
    interaction = _pytypes.SimpleNamespace(output_text=output_text, steps=[])
    return _pytypes.SimpleNamespace(
        event_type="interaction.completed", interaction=interaction
    )


class _FakeInteractions:
    """Records every create() call. A `create` with environment="remote" returns
    a freshly-provisioned interaction carrying a new environment_id; a `create`
    with a string env id reuses it. `stream=True` returns the scripted SSE list."""

    def __init__(self, env_id="env-abc", sse_events=None):
        self._env_id = env_id
        self._sse_events = sse_events or []
        self.calls: list[dict] = []
        self.create_count = 0
        self.stream_count = 0

    def create(self, *, agent, input, environment=None, stream=False,
               previous_interaction_id=None, **kw):
        self.create_count += 1
        self.calls.append({
            "agent": agent, "input": input, "environment": environment,
            "stream": stream, "previous_interaction_id": previous_interaction_id,
        })
        if stream:
            self.stream_count += 1
            return iter(list(self._sse_events))
        # non-stream: provisioning / keep-warm. environment="remote" → new env id.
        env_id = self._env_id if environment == "remote" else environment
        return _pytypes.SimpleNamespace(
            id=f"it-{self.create_count}", environment_id=env_id, steps=[],
            output_text="ready",
        )


class _FakeClient:
    def __init__(self, interactions):
        self.interactions = interactions


@pytest.fixture(autouse=True)
def _reset_sandbox():
    """Each test starts with no cached sandbox + keyed + genai-present so the
    sandbox path is exercised. Restores afterwards."""
    gs.reset_sandbox_for_tests()
    yield
    gs.reset_sandbox_for_tests()


@pytest.fixture
def _keyed(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
    monkeypatch.setattr(gs, "_sandbox_enabled", lambda: True)


# ───────────────────────── create-once + reuse ─────────────────────────────

def test_sandbox_created_once_and_reused(monkeypatch, _keyed):
    fake = _FakeInteractions(env_id="env-XYZ", sse_events=[_completed("RESULT: 0.45 A")])
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(fake))

    # first call: creates the env (environment="remote") then runs the analysis
    # against the new env id.
    out1 = gs.run_analysis("compute total current", on_step=None)
    assert out1 == "0.45 A"
    # the FIRST create is the provisioning create with environment="remote".
    assert fake.calls[0]["environment"] == "remote"
    assert fake.calls[0]["agent"] == gs.ANTIGRAVITY_AGENT
    # the analysis create reuses the returned env id (NOT "remote").
    analysis_calls = [c for c in fake.calls if c["stream"]]
    assert analysis_calls and analysis_calls[0]["environment"] == "env-XYZ"

    create_count_after_first = fake.create_count

    # second call: NO re-create — the cached env id is reused directly.
    out2 = gs.run_analysis("compute again", on_step=None)
    assert out2 == "0.45 A"
    remote_creates = [c for c in fake.calls if c["environment"] == "remote"]
    assert len(remote_creates) == 1, "sandbox must be created exactly once"
    # the 2nd analysis still streams against env-XYZ.
    assert fake.calls[-1]["environment"] == "env-XYZ"
    assert fake.create_count > create_count_after_first  # it did run, just reused


def test_get_sandbox_id_caches(monkeypatch, _keyed):
    fake = _FakeInteractions(env_id="env-CACHE")
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(fake))
    a = gs._get_sandbox_id()
    b = gs._get_sandbox_id()
    assert a == b == "env-CACHE"
    assert len([c for c in fake.calls if c["environment"] == "remote"]) == 1


# ───────────────────────── computed result extraction ──────────────────────

def test_run_analysis_returns_computed_result(monkeypatch, _keyed):
    sse = [
        _text_delta("Let me compute that.\n"),
        _code_call_delta("loads=[0.12,0.08,0.25]\nprint('RESULT:', sum(loads),'A')"),
        _code_result_delta("RESULT: 0.45 A"),
        _completed("The worst-case current is\nRESULT: 0.45 A"),
    ]
    fake = _FakeInteractions(sse_events=sse)
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(fake))

    assert gs.run_analysis("worst-case current on 3.3V rail") == "0.45 A"


def test_run_analysis_falls_back_to_last_line_without_result_prefix(monkeypatch, _keyed):
    sse = [_completed("the answer is 42 ohms")]
    fake = _FakeInteractions(sse_events=sse)
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(fake))
    assert gs.run_analysis("compute") == "the answer is 42 ohms"


# ───────────────────────── streaming intermediate steps ────────────────────

def test_intermediate_steps_stream_in_order(monkeypatch, _keyed):
    sse = [
        _text_delta("Computing rail budget."),
        _code_call_delta("i = 0.12 + 0.08 + 0.25\nprint('RESULT:', i)"),
        _code_result_delta("RESULT: 0.45"),
        _text_delta("Done."),
        _completed("RESULT: 0.45 A"),
    ]
    fake = _FakeInteractions(sse_events=sse)
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(fake))

    steps: list[str] = []
    out = gs.run_analysis("budget", on_step=steps.append)

    assert out == "0.45 A"
    # text → code → result → text, in arrival order; coarse phase events skipped.
    assert steps == [
        "Computing rail budget.",
        "running code: i = 0.12 + 0.08 + 0.25",
        "code result: RESULT: 0.45",
        "Done.",
    ]


def test_stream_step_cap_is_enforced(monkeypatch, _keyed):
    monkeypatch.setattr(gs, "SANDBOX_MAX_STREAM_STEPS", 3)
    sse = [_text_delta(f"line {i}") for i in range(10)] + [_completed("RESULT: ok")]
    fake = _FakeInteractions(sse_events=sse)
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(fake))

    steps: list[str] = []
    gs.run_analysis("x", on_step=steps.append)
    assert len(steps) == 3  # capped; the rest are consumed but not forwarded


def test_on_step_sink_raising_does_not_break_analysis(monkeypatch, _keyed):
    sse = [_text_delta("a"), _completed("RESULT: 7")]
    fake = _FakeInteractions(sse_events=sse)
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(fake))

    def boom(_line):
        raise RuntimeError("bad sink")

    assert gs.run_analysis("x", on_step=boom) == "7"  # completed despite bad sink


# ───────────────────────── keep-warm ───────────────────────────────────────

def test_keepwarm_ping_creates_then_reuses(monkeypatch, _keyed):
    fake = _FakeInteractions(env_id="env-WARM")
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(fake))

    assert gs.keepwarm_ping() is True   # first ping creates the env
    assert gs.keepwarm_ping() is True   # second ping reuses it
    remote = [c for c in fake.calls if c["environment"] == "remote"]
    assert len(remote) == 1
    reuse = [c for c in fake.calls if c["environment"] == "env-WARM"]
    assert len(reuse) >= 1


def test_keepwarm_ping_no_op_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(gs, "_sandbox_enabled", lambda: False)
    assert gs.keepwarm_ping() is False


def test_keepwarm_ping_robust_to_failure(monkeypatch, _keyed):
    class _Boom(_FakeInteractions):
        def create(self, **kw):
            if kw.get("environment") == "remote":
                return super().create(**kw)  # let creation succeed
            raise RuntimeError("network blip")

    fake = _Boom(env_id="env-W")
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(fake))
    # creation succeeds, the reuse ping fails → False, but never raises.
    assert gs.keepwarm_ping() is False


# ───────────────────────── no-key / unavailable → no-op stub ───────────────

def test_run_analysis_no_op_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(gs, "_sandbox_enabled", lambda: False)

    # _genai must NEVER be touched in the no-op path.
    def _explode():
        raise AssertionError("_genai called in no-op path")
    monkeypatch.setattr(gs, "_genai", _explode)

    steps: list[str] = []
    assert gs.run_analysis("compute", on_step=steps.append) is None
    assert steps == []
    assert gs._get_sandbox_id() is None


def test_run_analysis_no_op_when_create_returns_no_env(monkeypatch, _keyed):
    class _NoEnv(_FakeInteractions):
        def create(self, **kw):
            return _pytypes.SimpleNamespace(id="it", environment_id=None, steps=[])
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(_NoEnv()))
    assert gs.run_analysis("compute") is None


def test_run_analysis_returns_none_on_interaction_error(monkeypatch, _keyed):
    class _BoomStream(_FakeInteractions):
        def create(self, *, stream=False, environment=None, **kw):
            if environment == "remote":
                return super().create(stream=stream, environment=environment, **kw)
            raise RuntimeError("sandbox exploded")
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(_BoomStream(env_id="e")))
    assert gs.run_analysis("compute") is None  # SME continues without it


# ───────────────────────── timeout bound ───────────────────────────────────

def test_run_analysis_times_out(monkeypatch, _keyed):
    import time

    monkeypatch.setattr(gs, "SANDBOX_ANALYSIS_TIMEOUT_S", 0.2)

    class _Hang(_FakeInteractions):
        def create(self, *, stream=False, environment=None, **kw):
            if environment == "remote":
                return super().create(stream=stream, environment=environment, **kw)
            time.sleep(5)  # hang past the timeout
            return iter([])
    monkeypatch.setattr(gs, "_genai", lambda: _FakeClient(_Hang(env_id="e")))

    t0 = time.monotonic()
    assert gs.run_analysis("compute") is None  # timed out → None
    assert time.monotonic() - t0 < 3  # returned promptly, did not wait 5s


# ───────────────────────── tool-loop integration (#12 sink) ────────────────

class _FlashAndSandboxClient:
    """One fake client exposing BOTH the Flash `.models` API (for the SME
    tool-loop) AND the Antigravity `.interactions` API (for run_analysis) — the
    real `_genai()` returns one client serving both, so run_analysis flows
    through its true code path here (no monkeypatching of run_analysis)."""

    def __init__(self, *, tool_turns, final_json, sandbox):
        from orchestrator.tests.test_genai_seams import _FakeModels
        self.models = _FakeModels(tool_turns, final_json)
        self.interactions = sandbox


def test_run_analysis_wired_into_sme_tool_loop_and_streams(monkeypatch):
    """run_analysis is a real SME tool: the Flash SME calls it, the sandbox steps
    stream through the #12 on_tool_call sink as run_analysis 'step' calls, and the
    computed value reaches the model before the final SmeResponse. Exercises the
    REAL run_analysis path (no monkeypatch of run_analysis itself)."""
    pytest.importorskip("google.genai.types")
    import json as _json

    from orchestrator.knowledge import KnowledgeAdapter
    from orchestrator.proto.events import SummonGuild

    sandbox = _FakeInteractions(env_id="env-LOOP", sse_events=[
        _code_call_delta("print('RESULT:', 0.12+0.08+0.25, 'A')"),
        _code_result_delta("RESULT: 0.45 A"),
        _completed("RESULT: 0.45 A"),
    ])
    client = _FlashAndSandboxClient(
        tool_turns=[[("run_analysis", {"task": "sum loads 0.12,0.08,0.25 on 3.3V rail in A"})]],
        final_json=_json.dumps({"confidence": 0.9, "claim": "Worst-case 0.45 A on 3V3.",
                                "rationale": "Computed via run_analysis."}),
        sandbox=sandbox,
    )
    monkeypatch.setattr(gs, "_genai", lambda: client)
    monkeypatch.setattr(gs, "_sandbox_enabled", lambda: True)

    seen: list[dict] = []
    resp = gs.real_summon_one(
        "@power", SummonGuild(callId="c", topic="rail budget", smes=["@power"],
                              briefing="compute the 3V3 budget"),
        knowledge=KnowledgeAdapter(), on_tool_call=seen.append,
    )

    # the sandbox was created once (environment="remote") then the analysis ran
    # against the cached env id, streamed.
    assert [c["environment"] for c in sandbox.calls if c["environment"] == "remote"]
    assert any(c["stream"] and c["environment"] == "env-LOOP" for c in sandbox.calls)

    # the streamed run_analysis steps came through the on_tool_call sink as
    # {"name":"run_analysis","args":{"step": ...}} calls, in order...
    step_calls = [c for c in seen if c["name"] == "run_analysis" and "step" in c.get("args", {})]
    assert [c["args"]["step"] for c in step_calls] == [
        "running code: print('RESULT:', 0.12+0.08+0.25, 'A')",
        "code result: RESULT: 0.45 A",
    ]
    # ...and the completed run_analysis call (with the computed result) also fired.
    done = [c for c in seen if c["name"] == "run_analysis" and "task" in c.get("args", {})]
    assert done and done[0]["result"]["computed"] == "0.45 A"
    # the SME concluded using the computed value.
    assert resp.claim == "Worst-case 0.45 A on 3V3."


def test_format_sse_step_skips_coarse_events():
    skip = _pytypes.SimpleNamespace(event_type="step.start", step=None)
    assert gs._format_sse_step(skip) is None
    assert gs._format_sse_step(_pytypes.SimpleNamespace(event_type="interaction.created",
                                                        interaction=None)) is None
    err = _pytypes.SimpleNamespace(
        event_type="error",
        error=_pytypes.SimpleNamespace(message="boom", code="X"))
    assert gs._format_sse_step(err) == "error: boom"

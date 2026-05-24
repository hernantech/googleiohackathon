"""Deterministic distiller tests. The Gemini call is STUBBED — no network."""

from __future__ import annotations

from observer.distill import (
    LONG_PAUSE_MS,
    STUCK_CONFIRM_MS,
    compute_facts,
    distill_once,
    headline_for,
    heuristic_headline,
)
from observer.ingest import persist_event
from observer.store import Store, now_ms
from tests import synthetic as S


def _seed(store: Store, events, sid="op-bench-01"):
    for ev in events:
        persist_event(store, ev, default_session_id=sid)


def test_compute_facts_extracts_smes_and_task():
    store = Store(":memory:")
    _seed(store, S.scenario())
    events = store.recent_events(limit=100, session_id="op-bench-01")
    facts = compute_facts(events, session_id="op-bench-01")

    assert facts["board_task"] == "3V3-rail-rework"  # from SummonGuild topic
    smes = {s["sme"] for s in facts["smes_consulted"]}
    assert smes == {"@power", "@firmware"}
    assert facts["event_count"] >= 5
    # one confirmation pending, none resolved
    assert len(facts["pending_confirmations"]) == 1
    assert facts["pending_confirmations"][0]["risk"] == "HIGH"


def test_flag_safety_halt():
    store = Store(":memory:")
    _seed(store, [S.summon(), S.safety(severity="HALT")])
    events = store.recent_events(limit=100, session_id="op-bench-01")
    facts = compute_facts(events, session_id="op-bench-01")
    assert "safety_halt" in facts["flags"]


def test_flag_repeated_dissent():
    store = Store(":memory:")
    _seed(store, [S.summon(), S.dissent(), S.dissent(summary="still split")])
    events = store.recent_events(limit=100, session_id="op-bench-01")
    facts = compute_facts(events, session_id="op-bench-01")
    assert "repeated_dissent" in facts["flags"]


def test_flag_stuck_confirmation_uses_age():
    store = Store(":memory:")
    _seed(store, [S.summon(), S.confirmation_request(call_id="cZ")])
    events = store.recent_events(limit=100, session_id="op-bench-01")
    # pretend "now" is well past the stuck threshold
    future = now_ms() + STUCK_CONFIRM_MS + 5000
    facts = compute_facts(events, session_id="op-bench-01", now=future)
    assert "stuck_confirmation" in facts["flags"]
    assert facts["pending_confirmations"][0]["pending_ms"] >= STUCK_CONFIRM_MS


def test_flag_long_pause_only_with_open_step():
    store = Store(":memory:")
    _seed(store, [S.summon(), S.confirmation_request(call_id="cP")])
    events = store.recent_events(limit=100, session_id="op-bench-01")
    future = now_ms() + LONG_PAUSE_MS + 5000
    facts = compute_facts(events, session_id="op-bench-01", now=future)
    assert "long_pause" in facts["flags"]
    assert facts["active"] is False  # idle past threshold


def test_empty_session_facts_are_safe():
    facts = compute_facts([], session_id="nobody")
    assert facts["event_count"] == 0
    assert facts["flags"] == []
    assert facts["active"] is False


def test_headline_uses_stubbed_gemini():
    facts = compute_facts(
        [], session_id="x"
    )
    facts["event_count"] = 3  # force the gemini branch

    calls = {}

    def stub_model(prompt: str) -> str:
        calls["prompt"] = prompt
        return '{"headline": "Operator 12 min into a 3V3 rework; @power flags a short."}'

    headline, source = headline_for(facts, stub_model)
    assert source == "gemini"
    assert headline == "Operator 12 min into a 3V3 rework; @power flags a short."
    assert "SESSION FACTS" in calls["prompt"]  # the prompt embeds the facts


def test_headline_tolerates_bare_sentence_from_model():
    facts = {"event_count": 2}
    headline, source = headline_for(facts, lambda p: "Just a plain sentence.")
    assert source == "gemini"
    assert headline == "Just a plain sentence."


def test_headline_falls_back_when_model_raises():
    store = Store(":memory:")
    _seed(store, S.scenario())
    events = store.recent_events(limit=100, session_id="op-bench-01")
    facts = compute_facts(events, session_id="op-bench-01")

    def boom(prompt: str) -> str:
        raise RuntimeError("network down")

    headline, source = headline_for(facts, boom)
    assert source == "heuristic"
    assert "3V3-rail-rework" in headline  # heuristic mentions the task


def test_heuristic_headline_no_model():
    store = Store(":memory:")
    _seed(store, S.scenario())
    events = store.recent_events(limit=100, session_id="op-bench-01")
    facts = compute_facts(events, session_id="op-bench-01")
    h = heuristic_headline(facts)
    assert "Operator" in h and "3V3-rail-rework" in h


def test_distill_once_writes_status_row_with_stub():
    store = Store(":memory:")
    _seed(store, S.scenario())

    written = distill_once(
        store, window_s=3600, max_events=200,
        model_call=lambda p: '{"headline": "STUBBED HEADLINE"}',
    )
    assert written == 1

    rows = store.all_status()
    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == "op-bench-01"
    assert row["headline"] == "STUBBED HEADLINE"
    assert row["source"] == "gemini"
    # the full facts are stored for the dashboard
    assert row["detail"]["board_task"] == "3V3-rail-rework"


def test_distill_once_heuristic_when_no_model():
    store = Store(":memory:")
    _seed(store, S.scenario())
    written = distill_once(store, window_s=3600, max_events=200, model_call=None)
    assert written == 1
    assert store.all_status()[0]["source"] == "heuristic"


# ── managed-agent distiller (Antigravity Interactions API) ───────────────────

def test_headline_for_records_managed_source():
    """A successful call labelled model_source='managed' is recorded as such, so
    the dashboard honestly shows the headline came from a managed agent."""
    facts = {"event_count": 2}
    headline, source = headline_for(
        facts, lambda p: '{"headline": "X"}', model_source="managed"
    )
    assert source == "managed" and headline == "X"


def test_distill_once_records_managed_source():
    store = Store(":memory:")
    _seed(store, S.scenario())
    written = distill_once(
        store, window_s=3600, max_events=200,
        model_call=lambda p: '{"headline": "M"}', model_source="managed",
    )
    assert written == 1
    assert store.all_status()[0]["source"] == "managed"


def test_managed_agent_model_call_reuses_warm_env(monkeypatch):
    """managed_agent_model_call routes through interactions.create, provisions a
    remote env on the FIRST call, and REUSES the returned environment_id on every
    later call (warm — only the first pays the cold-start). Network is stubbed."""
    import sys
    import types as T
    from observer.distill import managed_agent_model_call

    calls: list[dict] = []

    class _Inter:
        def create(self, **kw):
            calls.append(kw)
            return T.SimpleNamespace(
                output_text='{"headline": "MANAGED HEADLINE"}',
                environment_id="env-1",
            )

    class _Client:
        def __init__(self, *a, **k):
            self.interactions = _Inter()

    fake_genai = T.SimpleNamespace(Client=_Client)
    fake_google = T.ModuleType("google")
    fake_google.genai = fake_genai  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)

    call = managed_agent_model_call("test-key", "antigravity-preview-05-2026")
    out1 = call("PROMPT-1")
    out2 = call("PROMPT-2")

    assert out1 == out2 == '{"headline": "MANAGED HEADLINE"}'
    assert calls[0]["agent"] == "antigravity-preview-05-2026"
    assert calls[0]["input"] == "PROMPT-1"
    assert calls[0]["environment"] == "remote"   # first call provisions
    assert calls[1]["environment"] == "env-1"     # second reuses the warm env

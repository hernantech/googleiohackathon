"""Deterministic: synthetic bus events → ingest path → persisted in SQLite →
read back. No socket, no network."""

from __future__ import annotations

import json

from observer.ingest import normalize, persist_event
from observer.store import Store
from tests import synthetic as S


def _store() -> Store:
    return Store(":memory:")


def test_normalize_flattens_chatmessage():
    row = normalize(S.chat("hello world", channel="#power", author="@power"))
    assert row["kind"] == "ChatMessage"
    assert row["channel_id"] == "#power"
    assert row["author_id"] == "@power"
    assert row["summary"] == "hello world"
    assert json.loads(row["raw_json"])["body"] == "hello world"
    # ns ts normalized to ms
    assert row["ts_ms"] < 10**14


def test_normalize_drops_audio_and_heartbeat():
    assert normalize(S.audio()) is None
    assert normalize(S.ping()) is None


def test_hello_sets_session_id_over_default():
    row = normalize(S.hello("op-7"), default_session_id="observer")
    assert row["session_id"] == "op-7"


def test_default_session_id_applied_when_absent():
    row = normalize(S.chat("hi"), default_session_id="observer-sub")
    assert row["session_id"] == "observer-sub"


def test_persist_event_round_trips_to_sqlite():
    store = _store()
    ids = []
    for ev in S.scenario():
        rid = persist_event(store, ev, default_session_id="op-bench-01")
        if rid is not None:
            ids.append(rid)
    assert ids, "expected at least one persisted event"

    rows = store.recent_events(limit=100)
    kinds = [r["kind"] for r in rows]
    assert "ChatMessage" in kinds
    assert "SmeResponse" in kinds
    assert "ConfirmationRequest" in kinds
    # audio/heartbeat never persisted
    assert "AudioChunk" not in kinds and "Ping" not in kinds


def test_persist_drops_noise_returns_none():
    store = _store()
    assert persist_event(store, S.audio()) is None
    assert store.event_count() == 0


def test_pending_confirmation_age_tracked():
    store = _store()
    persist_event(store, S.confirmation_request(call_id="c9", summary="probe net"))
    pend = store.pending_confirmations()
    assert len(pend) == 1
    assert pend[0]["call_id"] == "c9"
    assert pend[0]["pending_ms"] >= 0
    # resolving clears it
    persist_event(store, S.confirmation_response(call_id="c9", approved=True))
    assert store.pending_confirmations() == []

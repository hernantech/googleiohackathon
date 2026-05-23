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


def test_normalize_sets_dedup_key_only_for_replayable_kinds():
    # ChatMessage → keyed by messageId; ConfirmationRequest → keyed by callId.
    assert normalize(S.chat("hi", mid="abc"))["dedup_key"] == "ChatMessage:abc"
    assert (
        normalize(S.confirmation_request(call_id="cc"))["dedup_key"]
        == "ConfirmationRequest:cc"
    )
    # one-shot / non-replayed events carry no dedup key (NULL ⇒ never deduped).
    assert normalize(S.sme_response())["dedup_key"] is None
    assert normalize(S.safety())["dedup_key"] is None
    assert normalize(S.confirmation_response(call_id="cc"))["dedup_key"] is None


def test_duplicate_chatmessage_is_ignored_on_replay():
    store = _store()
    rid1 = persist_event(store, S.chat("rail droops", mid="m-dup"))
    rid2 = persist_event(store, S.chat("rail droops", mid="m-dup"))  # replayed copy
    assert rid1 is not None
    assert rid2 is None                       # OR IGNORE dropped the duplicate
    assert store.event_count() == 1


def test_duplicate_confirmation_request_is_ignored():
    store = _store()
    assert persist_event(store, S.confirmation_request(call_id="dup1")) is not None
    assert persist_event(store, S.confirmation_request(call_id="dup1")) is None
    assert store.event_count() == 1


def test_reconnect_replay_does_not_inflate_counts():
    """Simulate a WS reconnect. The orchestrator's replay re-sends only its
    buffered ChatMessages + pending ConfirmationRequests (see chat_bus/bus.py
    ``replay``). With stable ids those re-deliveries must be dropped, so the
    count is unchanged across a reconnect."""
    store = _store()
    for ev in S.scenario():
        persist_event(store, ev, default_session_id="op-bench-01")
    first = store.event_count()

    # what the bus actually replays on reconnect: the same ChatMessages (by
    # messageId) and the still-pending ConfirmationRequest (by callId).
    replayed = [e for e in S.scenario() if e["kind"] in ("ChatMessage", "ConfirmationRequest")]
    assert replayed, "scenario should contain replay-able events"
    for ev in replayed:
        persist_event(store, ev, default_session_id="op-bench-01")
    assert store.event_count() == first    # idempotent across reconnect


def test_non_replayable_events_are_not_deduped():
    """Distinct SmeResponses (no stable per-event id) must all persist — we must
    not over-dedup genuine separate events."""
    store = _store()
    persist_event(store, S.sme_response(sme="@power", claim="short near U4"))
    persist_event(store, S.sme_response(sme="@firmware", claim="baud mismatch"))
    rows = store.recent_events(limit=10, kinds=("SmeResponse",))
    assert len(rows) == 2

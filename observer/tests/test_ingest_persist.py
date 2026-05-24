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


# ── coverage for kinds that previously had no summary / were invisible ─────────

def test_tool_result_gets_a_summary_and_persists():
    row = normalize(S.tool_result(result='{"datasheet":"ok"}'))
    assert row is not None and row["kind"] == "ToolResult"
    assert row["summary"] and "datasheet" in row["summary"]


def test_channel_update_persists_with_snippet():
    row = normalize(S.channel_update(delta="hello", done=True))
    assert row is not None and row["kind"] == "ChannelUpdate"
    assert "hello" in row["summary"]


def test_snapshot_analysis_chatmessage_surfaces_analysis_text():
    row = normalize(S.snapshot_chat(analysis="Solder bridge on U4 pin 12."))
    assert row["kind"] == "ChatMessage"
    # the manager-relevant analysis prose, not an opaque JSON blob
    assert "snapshot:" in row["summary"]
    assert "Solder bridge" in row["summary"]


def test_goodbye_and_checkpoint_and_transcript_persist():
    assert normalize(S.goodbye())["kind"] == "Goodbye"
    assert normalize(S.checkpoint())["kind"] == "CheckpointMarker"
    t = normalize(S.transcript(text="probe net 3V3", partial=False))
    assert t["summary"] == "probe net 3V3"


def test_replay_envelopes_and_pong_are_dropped():
    # the per-reconnect replay envelopes have no stable id ⇒ never persisted
    for k in ("ChannelList", "ReplayDone", "BackpressureNotice"):
        assert normalize(S.replay_envelope(k)) is None
    assert normalize(S.pong()) is None


def test_presence_event_attributed_to_its_session():
    """Forward-hook (see ATTRIBUTION.md): a Presence event is persisted keyed by
    its own sessionId, overriding the observer's single-bucket default."""
    presence = {"kind": "Presence", "sessionId": "op-9", "client": "phone",
                "state": "online", "ts": 1}
    row = normalize(presence, default_session_id="observer-dashboard")
    assert row is not None
    assert row["session_id"] == "op-9"
    assert "online" in row["summary"]


def test_tagged_event_attributed_to_origin_over_default():
    """Forward-hook: once fan-out events carry a real sessionId, the observer
    keys them to that operator instead of the single observer bucket."""
    tagged = S.sme_response()
    tagged["sessionId"] = "op-bench-07"
    row = normalize(tagged, default_session_id="observer-dashboard")
    assert row["session_id"] == "op-bench-07"


def test_manager_relevant_kinds_are_never_dropped():
    """Guard against a regression that silently drops a manager-relevant kind."""
    keep = [
        S.sme_response(), S.summon(), S.confirmation_request(),
        S.confirmation_response(), S.safety(), S.dissent(), S.tool_call(),
        S.checkpoint(), S.transcript(), S.goodbye(), S.hello(),
        S.tool_result(), S.channel_update(),
    ]
    for ev in keep:
        assert normalize(ev) is not None, f"{ev['kind']} must be persisted"

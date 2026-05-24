"""Deterministic tests for the new persistence/display surface in the store:
keyset pagination, full per-session history, last-activity, kind counts."""

from __future__ import annotations

from observer.ingest import persist_event
from observer.store import Store, now_ms
from tests import synthetic as S


def _store() -> Store:
    return Store(":memory:")


def test_events_page_keyset_pagination_walks_whole_db():
    store = _store()
    # 25 distinct chat messages (distinct messageId ⇒ none deduped).
    for i in range(25):
        persist_event(store, S.chat(f"msg {i}", mid=f"m{i}"), default_session_id="op")
    seen_ids = []
    before = None
    while True:
        page = store.events_page(limit=10, before_id=before)
        if not page:
            break
        seen_ids.extend(r["id"] for r in page)
        before = page[-1]["id"]
        if len(page) < 10:
            break
    # every persisted row reachable via pagination, no dupes, strictly descending
    assert len(seen_ids) == 25
    assert len(set(seen_ids)) == 25
    assert seen_ids == sorted(seen_ids, reverse=True)


def test_events_page_text_search_matches_summary_and_raw():
    store = _store()
    persist_event(store, S.chat("rail droops near U4", mid="a"), default_session_id="op")
    persist_event(store, S.chat("totally unrelated", mid="b"), default_session_id="op")
    hits = store.events_page(text="U4")
    assert len(hits) == 1
    assert hits[0]["summary"] == "rail droops near U4"


def test_events_page_filters_by_kind_and_session():
    store = _store()
    persist_event(store, S.chat("hi", mid="c1"), default_session_id="opA")
    persist_event(store, S.summon(), default_session_id="opA")
    persist_event(store, S.chat("hey", mid="c2"), default_session_id="opB")
    assert all(r["kind"] == "ChatMessage" for r in store.events_page(kinds=("ChatMessage",)))
    assert all(r["session_id"] == "opB" for r in store.events_page(session_id="opB"))


def test_session_events_returns_full_history_all_kinds():
    store = _store()
    for ev in S.scenario():
        persist_event(store, ev, default_session_id="op-bench-01")
    rows = store.session_events("op-bench-01")
    kinds = {r["kind"] for r in rows}
    # full history includes kinds the compact timeline filters out (Hello/Chat)
    assert "Hello" in kinds and "ChatMessage" in kinds and "SmeResponse" in kinds


def test_session_last_activity_includes_stale_sessions():
    store = _store()
    persist_event(store, S.chat("recent", mid="r"), default_session_id="fresh")
    persist_event(store, S.chat("old", mid="o"), default_session_id="stale")
    la = store.session_last_activity()
    assert set(la) == {"fresh", "stale"}
    assert all(isinstance(v, int) for v in la.values())


def test_session_event_count():
    store = _store()
    # use chats only (no embedded Hello.sessionId) so all land under "op"
    for i in range(4):
        persist_event(store, S.chat(f"m{i}", mid=f"c{i}"), default_session_id="op")
    assert store.session_event_count("op") == 4
    assert store.session_event_count("op") == store.event_count()
    assert store.session_event_count("nobody") == 0


def test_kind_counts_and_event_kinds():
    store = _store()
    persist_event(store, S.chat("a", mid="x"), default_session_id="op")
    persist_event(store, S.chat("b", mid="y"), default_session_id="op")
    persist_event(store, S.summon(), default_session_id="op")
    counts = store.kind_counts()
    assert counts["ChatMessage"] == 2
    assert counts["SummonGuild"] == 1
    assert "ChatMessage" in store.event_kinds()

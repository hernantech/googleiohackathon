"""Deterministic: persist synthetic events + a distilled status, then assert the
dashboard read endpoints return them. Uses FastAPI TestClient (no network)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from observer.distill import distill_once
from observer.ingest import persist_event
from observer.store import Store, now_ms
from observer.web import build_app
from tests import synthetic as S


def _client_with_data():
    store = Store(":memory:")
    for ev in S.scenario():
        persist_event(store, ev, default_session_id="op-bench-01")
    distill_once(
        store, window_s=3600, max_events=200,
        model_call=lambda p: '{"headline": "Operator on the 3V3 rework."}',
    )
    return TestClient(build_app(store)), store


def test_healthz():
    client, _ = _client_with_data()
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["events"] >= 5


def test_overview_returns_operator_with_distilled_headline():
    client, _ = _client_with_data()
    r = client.get("/api/overview")
    assert r.status_code == 200
    data = r.json()
    assert data["totals"]["operators"] == 1
    op = data["operators"][0]
    assert op["session_id"] == "op-bench-01"
    assert op["headline"] == "Operator on the 3V3 rework."
    assert op["headline_source"] == "gemini"
    assert op["board_task"] == "3V3-rail-rework"
    # SMEs surfaced
    assert {s["sme"] for s in op["smes_consulted"]} == {"@power", "@firmware"}
    # pending confirmation surfaced globally too
    assert len(data["pending_confirmations"]) == 1
    assert data["pending_confirmations"][0]["risk"] == "HIGH"


def test_session_detail_returns_timeline():
    client, _ = _client_with_data()
    r = client.get("/api/session/op-bench-01")
    assert r.status_code == 200
    facts = r.json()["facts"]
    kinds = {t["kind"] for t in facts["timeline"]}
    assert "SummonGuild" in kinds and "SmeResponse" in kinds


def test_events_endpoint_returns_raw():
    client, _ = _client_with_data()
    r = client.get("/api/events?limit=50")
    assert r.status_code == 200
    body = r.json()
    evs = body["events"]
    assert any(e["kind"] == "ChatMessage" for e in evs)
    # each carries the parsed raw payload for drill-down
    assert all("raw" in e for e in evs)
    # pagination cursor present
    assert "next_before_id" in body


def test_events_endpoint_paginates_with_before_id():
    store = Store(":memory:")
    for i in range(15):
        persist_event(store, S.chat(f"m{i}", mid=f"id{i}"), default_session_id="op")
    client = TestClient(build_app(store))
    p1 = client.get("/api/events?limit=10").json()
    assert len(p1["events"]) == 10
    p2 = client.get(f"/api/events?limit=10&before_id={p1['next_before_id']}").json()
    assert len(p2["events"]) == 5
    ids1 = {e["id"] for e in p1["events"]}
    ids2 = {e["id"] for e in p2["events"]}
    assert ids1.isdisjoint(ids2)  # no overlap across pages


def test_events_endpoint_filters_by_kind_session_and_text():
    store = Store(":memory:")
    persist_event(store, S.chat("short near U4", mid="x"), default_session_id="opA")
    persist_event(store, S.summon(), default_session_id="opA")
    persist_event(store, S.chat("baud mismatch", mid="y"), default_session_id="opB")
    client = TestClient(build_app(store))
    by_kind = client.get("/api/events?kind=SummonGuild").json()["events"]
    assert by_kind and all(e["kind"] == "SummonGuild" for e in by_kind)
    by_sid = client.get("/api/events?session_id=opB").json()["events"]
    assert by_sid and all(e["session_id"] == "opB" for e in by_sid)
    by_text = client.get("/api/events?q=U4").json()["events"]
    assert len(by_text) == 1 and "U4" in by_text[0]["summary"]


def test_kinds_endpoint_lists_kinds_and_counts():
    client, _ = _client_with_data()
    d = client.get("/api/kinds").json()
    assert "ChatMessage" in d["kinds"]
    assert d["counts"]["ChatMessage"] >= 1


def test_session_full_history_returns_all_kinds_paginated():
    client, _ = _client_with_data()
    d = client.get("/api/session/op-bench-01?full=true&limit=100").json()
    assert "events" in d
    kinds = {e["kind"] for e in d["events"]}
    # full history surfaces kinds the compact timeline filters out (Hello/Chat)
    assert "Hello" in kinds and "ChatMessage" in kinds
    assert all("raw" in e for e in d["events"])
    assert d["total_events"] >= 5


def test_offline_operator_still_shown_after_recent_window():
    """A session whose newest event is >1h old has a persisted status row that
    must still appear — marked offline, never silently dropped."""
    store = Store(":memory:")
    # seed + distill so a status row exists (chats only — no embedded Hello
    # sessionId, so everything stays under the one "op-old" bucket)
    persist_event(store, S.chat("starting rework", mid="o1"), default_session_id="op-old")
    persist_event(store, S.summon(), default_session_id="op-old")
    persist_event(store, S.sme_response(), default_session_id="op-old")
    distill_once(store, window_s=3600, max_events=200, model_call=None)
    # backdate every event well past the 1h recent window
    old = now_ms() - 3 * 60 * 60 * 1000
    store._conn.execute("UPDATE events SET received_ms = ?, ts_ms = ?", (old, old))
    store._conn.commit()

    client = TestClient(build_app(store))
    data = client.get("/api/overview").json()
    op = next(o for o in data["operators"] if o["session_id"] == "op-old")
    assert op["online"] is False          # stale ⇒ offline
    assert op["active"] is False
    # but still present + still carries its distilled headline
    assert op["headline"]
    assert data["totals"]["operators"] == 1
    assert data["totals"]["online"] == 0


def test_overview_lists_session_without_status_row():
    """A freshly-seen operator (distiller hasn't run) is still visible."""
    store = Store(":memory:")
    persist_event(store, S.chat("just started"), default_session_id="op-new")
    client = TestClient(build_app(store))
    data = client.get("/api/overview").json()
    sids = [o["session_id"] for o in data["operators"]]
    assert "op-new" in sids
    op = next(o for o in data["operators"] if o["session_id"] == "op-new")
    assert op["headline_source"] == "pending"

"""Deterministic: persist synthetic events + a distilled status, then assert the
dashboard read endpoints return them. Uses FastAPI TestClient (no network)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from observer.distill import distill_once
from observer.ingest import persist_event
from observer.store import Store
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
    evs = r.json()["events"]
    assert any(e["kind"] == "ChatMessage" for e in evs)
    # each carries the parsed raw payload for drill-down
    assert all("raw" in e for e in evs)


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

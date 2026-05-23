"""§3.5b on-demand snapshot path + BK-11 grounded cite.

The strong model is faked (no network). Asserts: store wraps with FRAM, the
analyzer grounds a datasheet cite, the endpoint posts a SnapshotAnalysis card to
#live-feed, and the model resolves with graceful offline fallback.
"""

from __future__ import annotations

import json

from orchestrator.knowledge import KnowledgeAdapter
from orchestrator.proto.events import ChatMessage, SnapshotAnalysis
from orchestrator.snapshot.analyzer import analyze_snapshot, resolve_snapshot_model
from orchestrator.snapshot.endpoint import handle_snapshot
from orchestrator.storage.frame_store import InMemoryFrameStore, has_fram_header

_JPEG = b"\xff\xd8\xff\xe0fake-jpeg-bytes\xff\xd9"


def _fake_model(_jpeg: bytes, _ctx: str, _model: str) -> str:
    return "Only the VIO header is connected; the cell-stack lead at J3 is unplugged."


class _FakeBus:
    def __init__(self):
        self.published: list = []

    def publish(self, event) -> None:
        self.published.append(event)


def test_store_wraps_fram_and_recovers_jpeg():
    store = InMemoryFrameStore()
    ref = store.put(_JPEG, 1920, 1080, ts=123)
    blob = store.get(ref.uri)
    assert has_fram_header(blob)
    assert store.get_jpeg(ref.uri) == _JPEG
    assert ref.width == 1920 and ref.sourceSeq == 1


def test_bk11_analyze_snapshot_grounded():
    snap = analyze_snapshot(
        jpeg_bytes=_JPEG, width=1920, height=1080,
        context="bq79616 power-up wiring",
        knowledge=KnowledgeAdapter(), model_call=_fake_model,
        store=InMemoryFrameStore(),
    )
    assert isinstance(snap, SnapshotAnalysis)
    assert snap.analysis.startswith("Only the VIO")
    assert snap.cites, "snapshot analysis must be grounded with a citation (BK-11)"
    assert snap.cites[0].kind == "datasheet"
    assert "bq79616" in snap.cites[0].note.lower()  # grounded to the right part
    assert snap.frame.uri.startswith("mem:")


def test_endpoint_posts_snapshot_card_to_live_feed():
    bus = _FakeBus()
    snap = handle_snapshot(
        session_id="01HSESS", jpeg_bytes=_JPEG, width=1920, height=1080,
        note="bq79616 wiring", knowledge=KnowledgeAdapter(), model_call=_fake_model,
        store=InMemoryFrameStore(), bus=bus,
    )
    assert len(bus.published) == 1
    msg = bus.published[0]
    assert isinstance(msg, ChatMessage)
    assert msg.channelId == "#live-feed"
    assert msg.bodyContentType == "application/json"
    # the body round-trips back to a SnapshotAnalysis (CB-11 contract)
    reparsed = SnapshotAnalysis.model_validate(json.loads(msg.body))
    assert reparsed.frame.uri == snap.frame.uri


def test_model_resolution_offline_fallback(monkeypatch):
    monkeypatch.delenv("GEMINI_SNAPSHOT_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_SME_MODEL", raising=False)
    assert resolve_snapshot_model() == "stub-snapshot"
    monkeypatch.setenv("GEMINI_SME_MODEL", "gemini-2.5-pro")
    assert resolve_snapshot_model() == "gemini-2.5-pro"
    monkeypatch.setenv("GEMINI_SNAPSHOT_MODEL", "gemini-3-pro")
    assert resolve_snapshot_model() == "gemini-3-pro"
    assert resolve_snapshot_model("explicit") == "explicit"


def test_no_transcode_symbol_in_module():
    import orchestrator.snapshot.analyzer as a
    for name in dir(a):
        assert not any(s in name.lower() for s in ("decode", "transcode"))

"""POST /v2/snapshot handler (00 §4.2).

`handle_snapshot` is transport-agnostic: it stores + analyzes the JPEG, posts a
`SnapshotAnalysis` card to #live-feed via the chat bus (duck-typed `.publish`),
and returns the analysis (whose `frame` the graph sets as `latestFrame`). A thin
FastAPI route can wrap this; the logic here is what the tests drive.
"""

from __future__ import annotations

from typing import Callable, Protocol

from orchestrator.knowledge import KnowledgeAdapter
from orchestrator.proto.events import ChatMessage, SnapshotAnalysis, new_ulid, now_ns
from orchestrator.snapshot.analyzer import ModelCall, analyze_snapshot
from orchestrator.storage.frame_store import InMemoryFrameStore


class _Bus(Protocol):
    def publish(self, event) -> None: ...


def handle_snapshot(
    *,
    session_id: str,
    jpeg_bytes: bytes,
    width: int,
    height: int,
    note: str | None,
    knowledge: KnowledgeAdapter,
    model_call: ModelCall,
    store: InMemoryFrameStore,
    bus: _Bus | None = None,
    model_name: str | None = None,
    part_hint: str | None = None,
    now: Callable[[], int] = now_ns,
) -> SnapshotAnalysis:
    snap = analyze_snapshot(
        jpeg_bytes=jpeg_bytes,
        width=width,
        height=height,
        context=note or "",
        knowledge=knowledge,
        model_call=model_call,
        store=store,
        model_name=model_name,
        part_hint=part_hint,
        now=now,
    )
    if bus is not None:
        bus.publish(ChatMessage(
            channelId="#live-feed",
            authorId="@forge",
            authorKind="system",
            body=snap.model_dump_json(),
            bodyContentType="application/json",
            messageId=new_ulid(),
            ts=snap.ts,
        ))
    return snap

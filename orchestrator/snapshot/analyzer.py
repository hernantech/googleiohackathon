"""analyze_snapshot() — run a stronger model on one hi-res still and ground the
result against board knowledge (00 §4.2, 05 §3.4).

The model call is INJECTED (`ModelCall`) so this is pure and testable with no
network; production wires it to Gemini 3.x/4.x `generateContent`. There is no
video decode here — the snapshot is an already-encoded JPEG.
"""

from __future__ import annotations

import os
from typing import Callable

from orchestrator.knowledge import KnowledgeAdapter
from orchestrator.proto.events import EvidenceRef, FrameRef, SnapshotAnalysis, new_ulid, now_ns
from orchestrator.storage.frame_store import InMemoryFrameStore

#: (jpeg_bytes, context_text, model_name) -> analysis markdown.
ModelCall = Callable[[bytes, str, str], str]


def resolve_snapshot_model(model_name: str | None = None) -> str:
    """GEMINI_SNAPSHOT_MODEL > GEMINI_SME_MODEL > 'stub-snapshot' (07 §2.1)."""
    return (
        model_name
        or os.environ.get("GEMINI_SNAPSHOT_MODEL")
        or os.environ.get("GEMINI_SME_MODEL")
        or "stub-snapshot"
    )


def _ground(knowledge: KnowledgeAdapter, context: str, part_hint: str | None) -> list[EvidenceRef]:
    """Attach a grounded datasheet citation so the analysis isn't a free-form
    vision guess (05 §3.4, BK-11)."""
    candidates: list[str] = []
    if part_hint:
        candidates.append(part_hint)
    ctx = (context or "").lower()
    for p in knowledge.board_profile.parts:
        if p.datasheet and (p.datasheet.lower() in ctx or p.part.lower() in ctx):
            candidates.append(p.datasheet)
    if not candidates:
        candidates = [p.datasheet for p in knowledge.board_profile.parts if p.datasheet]

    cites: list[EvidenceRef] = []
    if candidates:
        ds = candidates[0]
        r = knowledge.lookup_datasheet(ds, context or "analysis")
        uri = r.passages[0].sourceUri if r.passages else f"datasheet://{ds}"
        cites.append(EvidenceRef(kind="datasheet", uri=uri, note=r.cite))
    return cites


def analyze_snapshot(
    *,
    jpeg_bytes: bytes,
    width: int,
    height: int,
    context: str,
    knowledge: KnowledgeAdapter,
    model_call: ModelCall,
    store: InMemoryFrameStore,
    model_name: str | None = None,
    part_hint: str | None = None,
    now: Callable[[], int] = now_ns,
) -> SnapshotAnalysis:
    ts = now()
    frame: FrameRef = store.put(jpeg_bytes, width, height, ts)
    model = resolve_snapshot_model(model_name)
    analysis = model_call(jpeg_bytes, context, model)
    cites = _ground(knowledge, context, part_hint)
    return SnapshotAnalysis(
        jobId=new_ulid(),
        frame=frame,
        model=model,
        analysis=analysis,
        cites=cites,
        ts=ts,
    )

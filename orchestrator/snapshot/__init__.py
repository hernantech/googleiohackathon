"""On-demand snapshot path (P4) — `specs/00_wire_protocol.md` §4.2.

The operator taps 📷; the client POSTs one hi-res JPEG to /v2/snapshot; the
SnapshotAnalyzer stores it, runs a *stronger* model (injectable), grounds the
result against the KnowledgeAdapter, and returns a `SnapshotAnalysis` that the
endpoint posts to #live-feed and sets as `latestFrame`. One-shot request/
response — no persistent socket, no video decode.
"""

from orchestrator.snapshot.analyzer import (  # noqa: F401
    ModelCall,
    analyze_snapshot,
    resolve_snapshot_model,
)
from orchestrator.snapshot.endpoint import handle_snapshot  # noqa: F401

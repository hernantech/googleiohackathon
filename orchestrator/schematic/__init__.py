"""Schematic → JSON capability (spec 09).

A vision-first parser that turns a schematic photo / PDF page into a validated,
provenance-stamped `SchematicJSON` (components / refdes / values / nets / pins,
with `confidence` + `warnings`). It reuses the injected-`ModelCall` pattern from
`orchestrator/snapshot/analyzer.py` so the parser is pure + testable with no
network, and the real model call reuses `genai_seams._genai()` + the snapshot
vision model (gemini-3-pro-preview) with `response_mime_type=application/json`.

The result is **advisory context, never a limit source** (09 §4, 03 §3.3.6): a
parsed `nominalVGuess`/`classGuess` is never promoted to a setpoint —
`get_documented_limit` remains the only source of documented limits.
"""

from __future__ import annotations

from orchestrator.schematic.schema import (
    SchComponent,
    SchematicJSON,
    SchematicSource,
    SchNet,
    SchNetNode,
    SchPin,
)

__all__ = [
    "SchematicJSON",
    "SchComponent",
    "SchNet",
    "SchNetNode",
    "SchPin",
    "SchematicSource",
]

"""Model-seam implementations + GraphDeps assembly (HANDOFF §2.B).

`GraphEngine` takes a `GraphDeps` whose four model-ish steps are injected
callables. The tests inject deterministic doubles; this module provides the
production wiring point. Today the seams are STUBS so the service boots and runs
end-to-end with zero env vars (07 §2.4). Real google-genai / Antigravity wiring
(ROADMAP Phase 3) replaces the bodies below — the signatures are frozen by
`graph/state.py`, so swapping is local.

Antigravity (Managed Agents) note: authenticates with the same GEMINI_API_KEY;
the real summon_one call is
  client.interactions.create(agent="antigravity-preview-05-2026",
                             input=<prompt>, environment=<env_id>,
                             previous_interaction_id=<prev_id>)
then orchestrator.managed_agents.read_sme_response(...) parses the SmeResponse.
"""

from __future__ import annotations

import logging
import os
import re

from orchestrator.graph.state import DissentResult, GraphDeps, RouteDecision
from orchestrator.knowledge import KnowledgeAdapter
from orchestrator.proto.events import SmeResponse, SummonGuild, new_ulid, now_ns
from orchestrator.safety.gate import SafetyGate

_MENTION = re.compile(r"@([a-z0-9\-]+)", re.I)


def stub_classify(transcript: str, recent: list[str]) -> RouteDecision | None:
    """Route to explicitly @-mentioned SMEs; otherwise direct reply (no guild).
    Lets the guild path demo in stub mode when the operator @-mentions someone."""
    mentions = [f"@{m}" for m in _MENTION.findall(transcript or "")]
    if mentions:
        return RouteDecision(needs_guild=True, smes=mentions, topic=transcript[:80])
    return RouteDecision(needs_guild=False)


def stub_summon_one(sme_id: str, summon: SummonGuild) -> SmeResponse:
    """Canned SME response. Phase 3 replaces with an Antigravity interaction."""
    call_id = getattr(summon, "callId", None) or new_ulid()
    return SmeResponse(
        smeId=sme_id,
        callId=call_id,
        confidence=0.7,
        claim=f"[stub] {sme_id} has no live model wired yet.",
        rationale="Stubbed seam — see ROADMAP Phase 3.",
        ts=now_ns(),
    )


def stub_merge_fn(kept: list[SmeResponse]) -> tuple[str, list[str]]:
    headline = "; ".join(r.claim for r in kept) or "No guild input (stub)."
    return headline, [r.smeId for r in kept]


def stub_dissent_fn(responses: list[SmeResponse], cross_exam_round: int) -> DissentResult:
    return DissentResult(pairwise=[], convergence="converged")


def stub_snapshot_model_call(jpeg_bytes: bytes, context: str, model_name: str) -> str:
    """ModelCall stub for analyze_snapshot. Phase 3 wires Gemini vision."""
    return (
        f"[stub vision · {model_name}] {len(jpeg_bytes)} bytes received. "
        "No live model wired; see ROADMAP Phase 3."
    )


def stub_schematic_model_call(
    image_bytes: bytes, mime_type: str, hint, model_name: str
) -> str:
    """SchematicModelCall stub for parse_schematic. Returns a minimal valid
    SchematicJSON string (empty topology, a warning) so the parser produces a
    well-formed low-confidence result with no model wired — zero-config boot
    (07 §2.4). Phase 3 wires Gemini vision (genai_seams.real_parse_schematic)."""
    import json

    return json.dumps({
        "source": {"kind": "pdf" if mime_type == "application/pdf" else "image",
                   "uri": None, "model": model_name},
        "confidence": 0.0,
        "components": [],
        "nets": [],
        "sheetCount": 1,
        "warnings": [
            f"[stub vision · {model_name}] {len(image_bytes)} bytes received; "
            "no live model wired (ROADMAP Phase 3)."
        ],
        "cite": f"schematic (stub) · {model_name}",
    })


def build_parse_schematic():
    """Select the schematic vision model_call: real Gemini vision (forced JSON +
    response_schema on SNAPSHOT_MODEL) when GEMINI_API_KEY is set and google-genai
    is importable, else the stub. Mirrors build_snapshot_model_call's selection."""
    log = logging.getLogger("forge.seams")
    if os.getenv("GEMINI_API_KEY"):
        try:
            from orchestrator.genai_seams import real_parse_schematic

            log.info("real schematic vision model_call active (gemini vision)")
            return real_parse_schematic
        except Exception as e:  # noqa: BLE001  google-genai missing or import error
            log.warning("real parse_schematic model_call unavailable (%s); using stub", e)
    return stub_schematic_model_call


def build_snapshot_model_call():
    """Select the snapshot (📷) vision model_call: real Gemini vision when
    GEMINI_API_KEY is set and google-genai is importable, else the stub.

    Mirrors build_graph_deps' real-vs-stub selection so /v2/snapshot uses the
    same model wiring as chat/live instead of always returning the stub vision
    string. Zero-config boot (no key) still falls back to the stub (07 §2.4)."""
    log = logging.getLogger("forge.seams")
    if os.getenv("GEMINI_API_KEY"):
        try:
            from orchestrator.genai_seams import real_snapshot_model_call

            log.info("real snapshot vision model_call active (gemini vision)")
            return real_snapshot_model_call
        except Exception as e:  # noqa: BLE001  google-genai missing or import error
            log.warning("real snapshot model_call unavailable (%s); using stub", e)
    return stub_snapshot_model_call


def build_graph_deps(knowledge: KnowledgeAdapter) -> GraphDeps:
    """Assemble GraphDeps. Uses real Gemini/Antigravity seams when GEMINI_API_KEY
    is set and google-genai is importable; otherwise stubs (zero-config boot,
    07 §2.4). SafetyGate + KnowledgeAdapter are always real."""
    log = logging.getLogger("forge.seams")
    if os.getenv("GEMINI_API_KEY"):
        try:
            from orchestrator.genai_seams import build_real_deps

            deps = build_real_deps(knowledge)
            log.info("real model seams active (gemini-3.5-flash + antigravity)")
            return deps
        except Exception as e:  # noqa: BLE001  google-genai missing or import error
            log.warning("real seams unavailable (%s); falling back to stubs", e)
    return GraphDeps(
        gate=SafetyGate(knowledge),
        knowledge=knowledge,
        classify=stub_classify,
        summon_one=stub_summon_one,
        merge_fn=stub_merge_fn,
        dissent_fn=stub_dissent_fn,
    )

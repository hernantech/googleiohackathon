"""Real model-seam wiring (ROADMAP Phase 3, HANDOFF §2.B–D).

Replaces the stub callables with live Google calls:
- classify / merge / dissent  → Gemini Flash (GEMINI_SME_MODEL, default gemini-3.5-flash)
- summon_one                  → Antigravity Interactions API (same GEMINI_API_KEY)
- snapshot model_call         → Gemini vision (GEMINI_SNAPSHOT_MODEL, default gemini-3-pro-preview)

`google-genai` is imported lazily so this module loads even when the lib is
absent (the package is an optional [live] extra). Every seam falls back to its
stub (orchestrator.seams) on any error, matching the graph's never-fail-stop
contract (01 §7). Selected by orchestrator.seams.build_graph_deps when
GEMINI_API_KEY is set.
"""

from __future__ import annotations

import json
import logging
import os

from orchestrator.graph.state import DissentResult, GraphDeps, RouteDecision
from orchestrator.knowledge import KnowledgeAdapter
from orchestrator.proto.events import DissentPair, SmeResponse, SummonGuild, now_ns
from orchestrator.safety.gate import SafetyGate
from orchestrator import seams as _stub

log = logging.getLogger("forge.genai_seams")

FLASH_MODEL = os.getenv("GEMINI_SME_MODEL", "gemini-3.5-flash")
SNAPSHOT_MODEL = os.getenv("GEMINI_SNAPSHOT_MODEL", "gemini-3-pro-preview")
ANTIGRAVITY_AGENT = os.getenv("ANTIGRAVITY_AGENT", "antigravity-preview-05-2026")
SME_ROSTER = "@power @signal @firmware @layout @librarian @sourcing @reverse @sentinel @scribe @tutor"

_client = None


def _genai():
    """Lazily build a cached google-genai client (reads GEMINI_API_KEY)."""
    global _client
    if _client is None:
        from google import genai  # optional [live] dep

        _client = genai.Client()
    return _client


def _flash_json(prompt: str) -> dict:
    """One Flash call constrained to JSON; returns the parsed object."""
    r = _genai().models.generate_content(
        model=FLASH_MODEL,
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    return json.loads(r.text)


def _interaction_text(it: object) -> str:
    parts: list[str] = []
    for step in getattr(it, "steps", None) or []:
        for c in getattr(step, "content", None) or []:
            t = getattr(c, "text", None)
            if t:
                parts.append(t)
    return "\n".join(parts)


# ── seams ──────────────────────────────────────────────────────────────────
def real_classify(transcript: str, recent: list[str]) -> RouteDecision | None:
    try:
        d = _flash_json(
            "You are SupervisorRouter (spec 01 §3.2). Decide whether to summon the "
            "SME guild for the operator's request and which SMEs.\n"
            f"Available SMEs: {SME_ROSTER}.\n"
            'Output ONLY JSON: {"needs_guild": bool, "smes": ["@power", ...], "topic": "short"}.\n'
            f"Recent context: {recent[-5:]}\nTranscript: {transcript!r}"
        )
        return RouteDecision(
            needs_guild=bool(d.get("needs_guild")),
            smes=[s for s in d.get("smes", []) if isinstance(s, str)],
            topic=str(d.get("topic", ""))[:120],
        )
    except Exception as e:  # noqa: BLE001
        log.warning("real_classify failed (%s); using stub", e)
        return _stub.stub_classify(transcript, recent)


def real_merge_fn(kept: list[SmeResponse]) -> tuple[str, list[str]]:
    try:
        payload = [{"smeId": r.smeId, "claim": r.claim, "confidence": r.confidence} for r in kept]
        d = _flash_json(
            "You are MergeOpinion (spec 01 §3.5). Synthesize ONE operator-facing "
            "headline from these SME responses and list the SMEs that support it.\n"
            'Output ONLY JSON: {"headline": "...", "supportingSmes": ["@power", ...]}.\n'
            f"Responses: {json.dumps(payload)}"
        )
        supporting = [s for s in d.get("supportingSmes", []) if isinstance(s, str)]
        return str(d.get("headline", "")) or "No consensus.", supporting or [r.smeId for r in kept]
    except Exception as e:  # noqa: BLE001
        log.warning("real_merge_fn failed (%s); using stub", e)
        return _stub.stub_merge_fn(kept)


def real_dissent_fn(responses: list[SmeResponse], cross_exam_round: int) -> DissentResult:
    try:
        payload = [{"smeId": r.smeId, "claim": r.claim, "rationale": r.rationale} for r in responses]
        d = _flash_json(
            "You are DissentDetector (spec 01 §3.6). Find pairwise disagreements among "
            "these SME responses and decide if they've converged.\n"
            'Output ONLY JSON: {"convergence": "converged"|"needs_more_rounds", '
            '"crossExamPrompt": "..."|null, '
            '"pairwise": [{"a":"@x","b":"@y","aClaim":"...","bClaim":"...","crux":"..."}]}.\n'
            f"Cross-exam round: {cross_exam_round}\nResponses: {json.dumps(payload)}"
        )
        pairwise = [
            DissentPair(a=p["a"], b=p["b"], aClaim=p.get("aClaim", ""),
                        bClaim=p.get("bClaim", ""), crux=p.get("crux", ""))
            for p in d.get("pairwise", [])
            if isinstance(p, dict) and p.get("a") and p.get("b")
        ]
        conv = d.get("convergence")
        return DissentResult(
            pairwise=pairwise,
            convergence="needs_more_rounds" if conv == "needs_more_rounds" else "converged",
            crossExamPrompt=d.get("crossExamPrompt"),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("real_dissent_fn failed (%s); using stub", e)
        return _stub.stub_dissent_fn(responses, cross_exam_round)


def real_summon_one(sme_id: str, summon: SummonGuild) -> SmeResponse:
    try:
        # SMEs run as fast gemini-3.5-flash model calls (no Antigravity sandbox —
        # the sandbox path is ~70s cold per SME, too slow for live deliberation;
        # see ROADMAP). The model returns only the substantive fields; we build
        # the SmeResponse envelope ourselves (reliable — avoids the model having
        # to emit a fully-valid nested ProposedAction). proposedActions are left
        # empty for now; a richer action schema can come later.
        prompt = (
            f"You are {sme_id}, a specialist SME advising a human operator at an "
            "electronics bench (spec 02). Forge actuates nothing; give cited, "
            "safety-aware advice.\n"
            f"Topic: {summon.topic}\nContext refs: {list(summon.contextRefs)}\n"
            'Respond with a single JSON object: {"confidence": <number 0-1>, '
            '"claim": "<one-sentence headline answer>", '
            '"rationale": "<2-3 sentence justification, cite the board doc/datasheet>"}.'
        )
        r = _genai().models.generate_content(
            model=FLASH_MODEL,
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        d = json.loads(r.text)
        conf = float(d.get("confidence", 0.5))
        return SmeResponse(
            smeId=sme_id,
            callId=summon.callId,
            confidence=min(max(conf, 0.0), 1.0),
            claim=str(d.get("claim", "")).strip() or f"{sme_id}: (no claim)",
            rationale=str(d.get("rationale", "")).strip(),
            ts=now_ns(),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("real_summon_one(%s) failed (%s); using stub", sme_id, e)
        return _stub.stub_summon_one(sme_id, summon)


def real_snapshot_model_call(jpeg_bytes: bytes, context: str, model_name: str) -> str:
    try:
        from google.genai import types  # optional [live] dep

        model = model_name or SNAPSHOT_MODEL
        r = _genai().models.generate_content(
            model=model,
            contents=[
                types.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg"),
                f"You are Forge's vision analyst. Describe what is on the bench and any "
                f"connection/orientation issues relevant to the operator.\nContext: {context}",
            ],
        )
        return r.text or ""
    except Exception as e:  # noqa: BLE001
        log.warning("real_snapshot_model_call failed (%s); using stub", e)
        return _stub.stub_snapshot_model_call(jpeg_bytes, context, model_name)


def build_real_deps(knowledge: KnowledgeAdapter) -> GraphDeps:
    """GraphDeps with real Gemini/Antigravity seams. Raises if google-genai is
    unimportable (caller falls back to stubs)."""
    from google import genai  # noqa: F401  fail fast if [live] extra missing

    return GraphDeps(
        gate=SafetyGate(knowledge),
        knowledge=knowledge,
        classify=real_classify,
        summon_one=real_summon_one,
        merge_fn=real_merge_fn,
        dissent_fn=real_dissent_fn,
    )

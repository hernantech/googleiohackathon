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

import functools
import json
import logging
import os
from typing import Callable

from orchestrator.graph.state import DissentResult, GraphDeps, RouteDecision
from orchestrator.knowledge import KnowledgeAdapter
from orchestrator.proto.events import DissentPair, ProposedAction, SmeResponse, SummonGuild, now_ns
from orchestrator.safety.gate import SafetyGate
from orchestrator import seams as _stub

log = logging.getLogger("forge.genai_seams")

FLASH_MODEL = os.getenv("GEMINI_SME_MODEL", "gemini-3.5-flash")
SNAPSHOT_MODEL = os.getenv("GEMINI_SNAPSHOT_MODEL", "gemini-3-pro-preview")
ANTIGRAVITY_AGENT = os.getenv("ANTIGRAVITY_AGENT", "antigravity-preview-05-2026")
SME_ROSTER = "@power @signal @firmware @layout @librarian @sourcing @reverse @sentinel @scribe @tutor"

#: Persona / lane per SME (spec 02). Used as the system-instruction so each SME
#: stays in its lane — the Managed-Agents-faithful equivalent of its AGENTS.md
#: "Role" section. (We run SMEs as fast Flash calls, not the ~70s-cold
#: Antigravity sandbox; structuring system-vs-input mirrors interactions.create
#: so the path can be swapped to the Antigravity API later — see build_real_deps.)
SME_ROLES = {
    "@power": "Power Engineer — rails, regulators, decoupling, transient response, thermal headroom",
    "@signal": "Signal-Integrity Engineer — buses, termination, scope/logic captures, comm integrity",
    "@firmware": "Firmware Engineer — MCU init, serial/console, register sequences, flashing",
    "@layout": "PCB Layout Engineer — placement, routing, parasitics",
    "@librarian": "Datasheet Librarian — cites parts, datasheet pages, app notes",
    "@sourcing": "Sourcing Engineer — part substitutes, BOM, availability",
    "@reverse": "Reverse-Engineering Tech — reads chip markings / board topology from images",
    "@sentinel": "Bench Safety Officer — flags hazards only; never diagnoses",
    "@scribe": "Session Scribe — keeps the report; never gates anything",
    "@tutor": "Tutor — explains the concept simply",
}

_client = None


def _genai():
    """Lazily build a cached google-genai client (reads GEMINI_API_KEY)."""
    global _client
    if _client is None:
        from google import genai  # optional [live] dep

        _client = genai.Client()
    return _client


#: Cap on tool-calling rounds per SME turn (retrieve -> reason -> retrieve).
#: Keeps latency bounded; the SME must conclude within this many lookups.
SME_MAX_TOOL_ROUNDS = int(os.getenv("GEMINI_SME_MAX_TOOL_ROUNDS", "5"))


def _flash_json(prompt: str) -> dict:
    """One Flash call constrained to JSON; returns the parsed object."""
    r = _genai().models.generate_content(
        model=FLASH_MODEL,
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    return _loads(r.text)


def _loads(text: str | None) -> dict:
    """Robust JSON parse: tolerate ```json fences / surrounding prose, else {}.

    The forced-JSON path returns clean JSON, but the tool-loop's final call may
    occasionally wrap it; never raise on a stray fence (01 §7 never-fail-stop)."""
    if not text:
        return {}
    s = text.strip()
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        # strip a leading/trailing fence and retry on the largest {...} span
        if s.startswith("```"):
            s = s.split("```", 2)[-2] if s.count("```") >= 2 else s.strip("`")
            s = s[s.find("{"):] if "{" in s else s
        lo, hi = s.find("{"), s.rfind("}")
        if 0 <= lo < hi:
            try:
                return json.loads(s[lo : hi + 1])
            except Exception:  # noqa: BLE001
                return {}
        return {}


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


def _wrap_action(pa: object, claim: str, knowledge: KnowledgeAdapter | None) -> list[ProposedAction]:
    """Turn the model's optional proposedAction into a ProposedAction, with the
    orchestrator attaching the documented-limit citation (never the model — keeps
    'never invent a setpoint' honest, 03 §3.3.6). Robust: bad shape → no action."""
    if not isinstance(pa, dict) or not pa.get("tool"):
        return []
    tool = str(pa["tool"]).strip()
    args = pa.get("args") if isinstance(pa.get("args"), dict) else {}
    risk = pa.get("risk") if pa.get("risk") in ("LOW", "MEDIUM", "HIGH") else "MEDIUM"
    ref: str | None = None
    if knowledge is not None and tool == "set_psu":
        target = args.get("target") or args.get("net")
        if target:
            try:
                lim = knowledge.get_documented_limit(target, "net")
                if lim.found:
                    ref = lim.source
            except Exception:  # noqa: BLE001
                pass
    return [ProposedAction(
        actor="operator", tool=tool, argsJson=json.dumps(args),
        rationale=claim, risk=risk,
        instruction=str(pa.get("instruction") or "").strip() or None,
        documentedLimitRef=ref)]


# ── SME knowledge tools (bound to the per-session KnowledgeAdapter) ──────────
#
# The SME PULLS information instead of answering one-shot: we expose the three
# read-only KnowledgeAdapter lookups (05 §3) to gemini-3.5-flash as
# function-calling tools, run a bounded retrieve->reason->retrieve loop, then
# force a JSON SmeResponse. We declare the schemas explicitly (rather than
# google-genai's automatic-function-calling on raw callables) because:
#   * AFC cannot be combined with response_mime_type=application/json — the
#     final answer MUST be forced JSON (the SmeResponse contract);
#   * a manual loop lets us cap rounds AND capture the exact tool calls made
#     (for the audit trail / smoke report);
#   * it binds cleanly to the per-session adapter via a closure.
# The model never invents a limit: get_documented_limit returns the cited value
# and the orchestrator attaches the citation in _wrap_action (03 §3.3.6).

_TOOL_SCHEMAS = [
    {
        "name": "lookup_datasheet",
        "description": (
            "Retrieve datasheet passages for a board part, matched to a query. "
            "Use when you need a part's behavior, electrical spec, or power-up "
            "requirement (e.g. BQ79616 wake/VIO, ESP32 UART/IO levels). Returns "
            "page-cited passages. part may be a part number (BQ79616), a "
            "datasheet slug (bq79616), or a board ref (U2)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "part": {"type": "STRING", "description": "part number, datasheet slug, or board ref"},
                "query": {"type": "STRING", "description": "what you want to know"},
            },
            "required": ["part", "query"],
        },
    },
    {
        "name": "lookup_board_doc",
        "description": (
            "Search this board's documentation and structured profile (parts, "
            "rails, nets, test points, preconditions, bring-up procedures). Use "
            "for board-level facts: which rail powers what, net/test-point names, "
            "the documented bring-up order."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "board-level question"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_documented_limit",
        "description": (
            "Return the deterministic, cited documented limit (max voltage / "
            "current) for a net, rail, or part. ALWAYS use this before proposing "
            "any concrete setpoint — never invent a voltage or current. "
            "kind is one of: net, rail, part."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "target": {"type": "STRING", "description": "net id (J3), rail id (3V3), or part (U2/BQ79616)"},
                "kind": {"type": "STRING", "enum": ["net", "rail", "part"]},
            },
            "required": ["target", "kind"],
        },
    },
]


def _dispatch_tool(name: str, args: dict, knowledge: KnowledgeAdapter) -> dict:
    """Execute a declared tool against the bound adapter; return a JSON-able dict.
    Read-only — nothing here actuates hardware (BK-10)."""
    if name == "lookup_datasheet":
        res = knowledge.lookup_datasheet(
            str(args.get("part", "")), str(args.get("query", "")),
        )
        return res.model_dump()
    if name == "lookup_board_doc":
        res = knowledge.lookup_board_doc(str(args.get("query", "")))
        return res.model_dump()
    if name == "get_documented_limit":
        res = knowledge.get_documented_limit(
            str(args.get("target", "")), str(args.get("kind", "")),
        )
        return res.model_dump()
    return {"error": f"unknown tool {name!r}"}


def _run_sme_tool_loop(
    system: str, brief: str, siblings: list[str], knowledge: KnowledgeAdapter | None,
    on_tool_call: "Callable[[dict], None] | None" = None,
) -> tuple[dict, list[dict]]:
    """Bounded function-calling loop on gemini-3.5-flash, returning the parsed
    final SmeResponse dict and the list of tool calls made (for audit/report).

    Round structure: generate_content with the tool declarations; if the model
    emits functionCall parts, execute them against the per-session adapter,
    append the functionResponse turn, and loop (capped at SME_MAX_TOOL_ROUNDS).
    Once the model stops calling tools (or the cap is hit) we make one final
    forced-JSON call to extract the SmeResponse — keeping the structured-output
    contract that AFC-with-JSON cannot satisfy in a single call.

    `on_tool_call`, when given, is invoked with each {name, args, result} dict
    the moment the call executes — so the graph can stream retrieval activity to
    the SME's chat channel live (engine._parallel_summon)."""
    from google.genai import types  # optional [live] dep

    final_instructions = (
        "You are out of tool calls. Conclude NOW with what you have retrieved — "
        "do NOT say you still need to look something up. Reply with ONE JSON object: "
        '{"confidence": <0-1>, "claim": "<one-sentence answer>", '
        '"rationale": "<2-3 sentences; CITE the specific datasheet page / board-doc '
        'section / documented limit you retrieved>", '
        '"proposedAction": null OR {"tool": "set_psu|probe_net|serial_send|flash_mcu|inspect_closeup", '
        '"args": {"target":"<net>","voltage_v":<n>,...}, '
        '"instruction": "<imperative step for the operator>", "risk": "LOW|MEDIUM|HIGH"}}. '
        "Use proposedAction only when a concrete operator step is warranted; else null. "
        "Do NOT invent any voltage/current setpoint — only cite values you obtained "
        "from get_documented_limit."
    )
    base = (
        f"=== Guild brief ===\n{brief}\n\n"
        f"Other SMEs consulted in parallel: {siblings or 'none'}\n\n"
        "You have tools to PULL board knowledge: lookup_datasheet, "
        "lookup_board_doc, get_documented_limit. Call them as needed to ground "
        "your answer BEFORE concluding; you may call several in sequence. When you "
        "have enough, stop calling tools and you will be asked for the final JSON."
    )

    client = _genai()
    tools = [types.Tool(function_declarations=_TOOL_SCHEMAS)]
    cfg = types.GenerateContentConfig(
        system_instruction=system,
        tools=tools,
        # we drive the loop ourselves; disable the SDK's automatic execution so
        # we can capture each call and bind it to the session adapter.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        tool_config=types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(mode="AUTO")
        ),
    )

    contents: list = [types.Content(role="user", parts=[types.Part(text=base)])]
    tool_calls: list[dict] = []

    for _ in range(max(1, SME_MAX_TOOL_ROUNDS)):
        r = client.models.generate_content(
            model=FLASH_MODEL, contents=contents, config=cfg,
        )
        calls = list(getattr(r, "function_calls", None) or [])
        if not calls:
            break  # model is ready to conclude

        # echo the model's tool-call turn, then answer each call.
        cand = (getattr(r, "candidates", None) or [None])[0]
        model_content = getattr(cand, "content", None) if cand else None
        if model_content is not None:
            contents.append(model_content)

        resp_parts = []
        for fc in calls:
            args = dict(fc.args or {})
            knowledge_for_call = knowledge or KnowledgeAdapter()
            result = _dispatch_tool(fc.name, args, knowledge_for_call)
            call = {"name": fc.name, "args": args, "result": result}
            tool_calls.append(call)
            # Surface this retrieval to the SME's channel the moment it runs
            # (streaming); a raising sink must not break the loop (01 §7).
            if on_tool_call is not None:
                try:
                    on_tool_call(call)
                except Exception as e:  # noqa: BLE001
                    log.warning("on_tool_call sink raised (%s); continuing", e)
            resp_parts.append(
                types.Part.from_function_response(name=fc.name, response={"result": result})
            )
        contents.append(types.Content(role="user", parts=resp_parts))

    # final forced-JSON answer (no tools on this call so JSON mode is allowed).
    final = client.models.generate_content(
        model=FLASH_MODEL,
        contents=contents + [types.Content(role="user", parts=[types.Part(text=final_instructions)])],
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
        ),
    )
    return _loads(getattr(final, "text", None)), tool_calls


def real_summon_one(
    sme_id: str,
    summon: SummonGuild,
    knowledge: KnowledgeAdapter | None = None,
    on_tool_call: "Callable[[dict], None] | None" = None,
) -> SmeResponse:
    """Summon one SME as a bounded tool-calling agent on gemini-3.5-flash (not
    the ~70s-cold Antigravity sandbox; see ROADMAP). The persona is the system
    instruction and the orchestrator-assembled `summon.briefing` (question +
    board facts + limits + snapshot, see GraphEngine._build_briefing) is the
    grounded starting context. The SME may PULL more via three read-only tools
    (lookup_datasheet / lookup_board_doc / get_documented_limit) bound to the
    per-session KnowledgeAdapter, reasoning over what it retrieves before
    concluding with a forced-JSON SmeResponse. We build the SmeResponse envelope
    ourselves and the orchestrator attaches the documented-limit citation to any
    proposed step — the model never invents a setpoint (03 §3.3.6)."""
    try:
        role = SME_ROLES.get(sme_id, "a specialist SME")
        siblings = [s for s in summon.smes if s != sme_id]
        system = (
            f"You are {sme_id}, {role}. You are on Forge's guild advising a HUMAN "
            "operator at an electronics bench. Forge actuates nothing — you only "
            "recommend steps the operator performs by hand. Be terse, ground every "
            "claim in the board doc/datasheet (use your tools to retrieve them), and "
            "stay strictly in your lane."
        )
        brief = summon.briefing or f"Topic: {summon.topic}"
        d, _tool_calls = _run_sme_tool_loop(system, brief, siblings, knowledge, on_tool_call)
        conf = min(max(float(d.get("confidence", 0.5)), 0.0), 1.0)
        claim = str(d.get("claim", "")).strip() or f"{sme_id}: (no claim)"
        return SmeResponse(
            smeId=sme_id,
            callId=summon.callId,
            confidence=conf,
            claim=claim,
            rationale=str(d.get("rationale", "")).strip(),
            proposedActions=_wrap_action(d.get("proposedAction"), claim, knowledge),
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
        # bind the KnowledgeAdapter so summon_one can cite documented limits on
        # any proposed step; the engine still calls summon_one(sme, summon).
        summon_one=functools.partial(real_summon_one, knowledge=knowledge),
        merge_fn=real_merge_fn,
        dissent_fn=real_dissent_fn,
    )

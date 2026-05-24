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
import threading
from pathlib import Path
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

#: Wall-clock cap on ONE run_analysis sandbox interaction (model + code-exec
#: rounds). A hung sandbox must never wedge a summon — on timeout we abandon the
#: interaction and the SME concludes WITHOUT the computed value (graceful
#: degradation, 01 §7). Tunable via env for slow/cold environments.
SANDBOX_ANALYSIS_TIMEOUT_S = float(os.getenv("FORGE_SANDBOX_TIMEOUT_S", "90"))
#: Cap on streamed intermediate steps forwarded per interaction (defensive bound
#: against a chatty/runaway stream flooding the chat channel).
SANDBOX_MAX_STREAM_STEPS = int(os.getenv("FORGE_SANDBOX_MAX_STREAM_STEPS", "60"))
#: Keep-warm ping cadence: idle Antigravity envs snapshot at ~15 min, so a cheap
#: interaction every ~240 s keeps the single shared sandbox hot (no cold-start).
SANDBOX_KEEPWARM_INTERVAL_S = float(os.getenv("FORGE_SANDBOX_KEEPWARM_S", "240"))

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

# ── SME persona/skill packs (smes/<id>/AGENTS.md + SKILL.md, spec 02) ────────
#
# When a pack exists on disk it provides the RICH persona (the AGENTS.md "Role"
# + lane + defer-to + never-invent rule) as the SME's system instruction, with
# the SKILL.md tool-usage contract appended (bounded). When absent, we fall back
# to the inline SME_ROLES one-liner — so zero-config boot still works and the
# packs are a purely additive upgrade (they ship in the image; see Dockerfile +
# pyproject packaging). Loaded + cached ONCE, lazily, module-level.

#: smeId ("@power") -> assembled persona text (None = no pack on disk).
_sme_packs: "dict[str, str | None]" = {}
_sme_packs_lock = threading.Lock()

#: Cap on SKILL.md text folded into the persona (defensive bound on prompt size).
SME_SKILL_MAX_CHARS = int(os.getenv("FORGE_SME_SKILL_MAX_CHARS", "2500"))


def _smes_dir() -> Path:
    """Resolve the repo's `smes/` directory robustly.

    `FORGE_SMES_DIR` wins if set (lets the container point at the shipped packs).
    Otherwise we walk up from this file looking for a `smes/` dir — works from a
    source checkout AND from the installed package in the image (the packs are
    COPYed alongside the repo root). Returns the first match, else a best-effort
    repo-root/smes path (which simply won't exist → graceful one-liner fallback)."""
    env = os.getenv("FORGE_SMES_DIR")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "smes"
        if cand.is_dir():
            return cand
    # repo root is two up from orchestrator/genai_seams.py in a checkout.
    return here.parents[1] / "smes"


def _build_persona(sme_id: str) -> "str | None":
    """Assemble the system instruction for `sme_id` from its on-disk pack, or
    None when no AGENTS.md exists. The AGENTS.md is the persona; SKILL.md (the
    tool-usage + output contract) is appended, bounded, so the loop is grounded
    in the SME's real specialty. Never raises — any read error → None (one-liner
    fallback, 01 §7 never-fail-stop)."""
    name = sme_id.lstrip("@#")
    base = _smes_dir() / name
    try:
        agents = base / "AGENTS.md"
        if not agents.is_file():
            return None
        persona = agents.read_text(encoding="utf-8").strip()
        if not persona:
            return None
        skill = base / "SKILL.md"
        if skill.is_file():
            skill_text = skill.read_text(encoding="utf-8").strip()
            if skill_text:
                persona += (
                    "\n\n=== Skill pack (tools + when to use them) ===\n"
                    + skill_text[:SME_SKILL_MAX_CHARS]
                )
        return persona
    except Exception as e:  # noqa: BLE001 — a bad/missing pack must not fail-stop
        log.warning("loading SME pack for %s failed (%s); using one-liner", sme_id, e)
        return None


def _sme_persona(sme_id: str) -> "str | None":
    """Return the cached assembled persona for `sme_id` (None if no pack). Loads
    + caches once per SME, thread-safe (the guild fans out concurrently)."""
    if sme_id in _sme_packs:
        return _sme_packs[sme_id]
    with _sme_packs_lock:
        if sme_id not in _sme_packs:  # re-check after acquiring the lock
            _sme_packs[sme_id] = _build_persona(sme_id)
        return _sme_packs[sme_id]


def reset_sme_packs_for_tests() -> None:
    """Test hook: drop the cached personas so a test can re-exercise loading
    (e.g. after pointing FORGE_SMES_DIR at a fixture). Not used in production."""
    _sme_packs.clear()


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


# ── Kept-warm Antigravity compute sandbox (ROADMAP "Managed Agents") ─────────
#
# A SINGLE process-wide Antigravity sandbox, created lazily on first use and
# REUSED for every run_analysis call by passing `environment=<environment_id>`.
# Antigravity sandboxes are Linux + Python 3.12 with the `code_execution` tool —
# we use them as a "compute tool" that runs REAL code (the SME asks it to
# compute a number and print it). The same GEMINI_API_KEY authenticates it.
#
# Lifecycle:
#   _get_sandbox_id()  → create-once (environment="remote"), cache the
#                        environment_id; all subsequent calls reuse it.
#   run_analysis(...)  → interactions.create(environment=<id>, stream=True);
#                        streams SSE intermediate steps out via a callback,
#                        returns the final computed result string.
#   keepwarm_ping()    → cheap reuse interaction so the env never cold-starts;
#                        driven by an asyncio task started at orchestrator
#                        startup (main.py) and cancelled on shutdown.
#
# Key-gated + lazy: with no GEMINI_API_KEY / google-genai absent there is NO
# sandbox and run_analysis is a no-op stub (boot offline, tests pass).

#: Cached environment_id of the one shared warm sandbox (None until created).
_sandbox_env_id: str | None = None
#: Serializes the create-once race (the keep-warm task + a first summon could
#: both try to create the sandbox simultaneously); cheap, only contended at
#: cold start. interactions.create itself is left un-locked (concurrent reuse of
#: a warm env is fine and is what keeps SMEs parallel).
_sandbox_lock = threading.Lock()


def _sandbox_enabled() -> bool:
    """True only when a sandbox can exist: a key is present AND google-genai is
    importable. Everything sandbox-related no-ops otherwise (offline boot)."""
    if not os.getenv("GEMINI_API_KEY"):
        return False
    try:
        import google.genai  # noqa: F401  optional [live] dep
    except Exception:  # noqa: BLE001
        return False
    return True


def _get_sandbox_id() -> str | None:
    """Create-once + cache the shared Antigravity sandbox; return its
    environment_id (or None if sandboxing is unavailable / creation failed).

    The FIRST call creates a fresh remote environment (`environment="remote"`)
    with a trivial input and records the returned `environment_id`. Every later
    call returns the cached id so the SAME warm sandbox is reused (no re-create,
    no cold-start). Failure to create degrades to None — run_analysis then
    no-ops and the SME answers without the computed value."""
    global _sandbox_env_id
    if _sandbox_env_id is not None:
        return _sandbox_env_id
    if not _sandbox_enabled():
        return None
    with _sandbox_lock:
        if _sandbox_env_id is not None:  # won the race after acquiring the lock
            return _sandbox_env_id
        try:
            it = _genai().interactions.create(
                agent=ANTIGRAVITY_AGENT,
                input="ready",  # trivial provisioning turn; NOT model=+agent= both
                environment="remote",
            )
            env_id = getattr(it, "environment_id", None)
            if not env_id:
                log.warning("sandbox create returned no environment_id; disabling")
                return None
            _sandbox_env_id = env_id
            log.info("antigravity sandbox created + cached env_id=%s", env_id)
            return _sandbox_env_id
        except Exception as e:  # noqa: BLE001 — preview/allowlist/network
            log.warning("sandbox create failed (%s); run_analysis will no-op", e)
            return None


def _format_sse_step(event: object) -> str | None:
    """Turn one InteractionSSEEvent into a SHORT human line for the chat channel,
    or None to skip (events that aren't operator-interesting).

    Handles the google-genai 2.6.0 event union (discriminated on `event_type`):
      step.delta → text fragment / code being executed / code-exec result
      step.start/stop, interaction.* → coarse phase markers (mostly skipped)
      error      → surfaced so a sandbox failure is visible
    Defensive: any unknown shape degrades to None rather than raising."""
    et = getattr(event, "event_type", None)
    if et == "step.delta":
        delta = getattr(event, "delta", None)
        dt = getattr(delta, "type", None)
        if dt == "text":
            txt = (getattr(delta, "text", "") or "").strip()
            return txt[:200] if txt else None
        if dt == "code_execution_call":
            args = getattr(delta, "arguments", None)
            code = (getattr(args, "code", "") or "").strip()
            if code:
                first = code.splitlines()[0][:160]
                return f"running code: {first}"
            return "running code"
        if dt == "code_execution_result":
            res = (getattr(delta, "result", "") or "").strip()
            return f"code result: {res[:160]}" if res else None
        return None
    if et == "error":
        err = getattr(event, "error", None)
        msg = getattr(err, "message", None) or "sandbox error"
        return f"error: {str(msg)[:160]}"
    # step.start / step.stop / interaction.created / interaction.status_update /
    # interaction.completed carry no operator-interesting delta → skip.
    return None


def keepwarm_ping() -> bool:
    """Run ONE cheap reuse interaction against the warm sandbox so an idle env
    never snapshots/cold-starts (~15 min idle window). Creates the sandbox on the
    first ping if needed. Returns True on success, False otherwise. Never raises —
    the caller is a background loop that must keep going (01 §7)."""
    env_id = _get_sandbox_id()
    if env_id is None:
        return False
    try:
        _genai().interactions.create(
            agent=ANTIGRAVITY_AGENT,
            input="ping",
            environment=env_id,  # reuse the warm env; NOT environment_id=
        )
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("sandbox keep-warm ping failed (%s)", e)
        return False


def run_analysis(task: str, on_step: "Callable[[str], None] | None" = None) -> str | None:
    """Run `task` as REAL code in the kept-warm Antigravity sandbox and return the
    computed result string (or None if the sandbox is unavailable / it failed /
    it timed out — the SME then concludes WITHOUT the computed value).

    This is the SME tool-loop's occasional "do real math" escape hatch: the
    sandbox agent (Gemini 3.5 Flash + code_execution) is told to COMPUTE the
    answer in Python and PRINT it. We stream via `stream=True` and forward each
    intermediate SSE step (model output / code being run / code result) to
    `on_step` the moment it arrives, so the operator can watch the computation
    unfold; then we return the final printed/answered result.

    Bounded: a wall-clock timeout (SANDBOX_ANALYSIS_TIMEOUT_S) and a streamed-step
    cap keep a hung or chatty sandbox from wedging a summon — on timeout we
    abandon and return None."""
    env_id = _get_sandbox_id()
    if env_id is None:
        return None  # no-op stub: sandbox unavailable

    instructions = (
        "You are a compute tool on an electronics workbench. COMPUTE the answer to "
        "the task below by WRITING AND RUNNING Python code (use the code execution "
        "tool — do not just reason in prose). Show the computation, then on the LAST "
        "line print the final numeric/short result clearly prefixed with 'RESULT: '. "
        "Be exact; state units.\n\n=== TASK ===\n" + (task or "")
    )

    import concurrent.futures as _cf

    def _drive() -> str | None:
        stream = _genai().interactions.create(
            agent=ANTIGRAVITY_AGENT,
            input=instructions,
            environment=env_id,  # reuse the warm sandbox; NOT environment_id=
            stream=True,
        )
        texts: list[str] = []
        steps = 0
        final_it = None
        for event in stream:
            # the completed event carries the full interaction → use it for the
            # authoritative final result text.
            if getattr(event, "event_type", None) in (
                "interaction.completed", "interaction.created"
            ):
                final_it = getattr(event, "interaction", None) or final_it
            line = _format_sse_step(event)
            if line is None:
                continue
            # collect text deltas for the final-result fallback.
            et = getattr(event, "event_type", None)
            if et == "step.delta" and getattr(getattr(event, "delta", None), "type", None) == "text":
                texts.append(getattr(event.delta, "text", "") or "")
            if on_step is not None and steps < SANDBOX_MAX_STREAM_STEPS:
                steps += 1
                try:
                    on_step(line)
                except Exception as e:  # noqa: BLE001 — a bad sink must not fail-stop
                    log.warning("run_analysis on_step sink raised (%s); continuing", e)
        # Prefer the completed interaction's output_text; fall back to the
        # concatenated streamed text. Extract the RESULT: line when present.
        full = ""
        if final_it is not None:
            full = (getattr(final_it, "output_text", "") or _interaction_text(final_it))
        if not full:
            full = "".join(texts)
        return _extract_result(full)

    # Run the (blocking) SSE drive on a worker thread bounded by a wall-clock
    # timeout. On timeout we do NOT join the worker (shutdown wait=False) so a
    # hung sandbox can't wedge the summon — the worker is abandoned and the SME
    # concludes without the computed value.
    ex = _cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="run_analysis")
    fut = ex.submit(_drive)
    try:
        result = fut.result(timeout=SANDBOX_ANALYSIS_TIMEOUT_S)
        ex.shutdown(wait=False)
        return result
    except _cf.TimeoutError:
        log.warning("run_analysis timed out after %ss; SME continues without it",
                    SANDBOX_ANALYSIS_TIMEOUT_S)
        ex.shutdown(wait=False)  # do NOT block on the hung worker
        return None
    except Exception as e:  # noqa: BLE001 — never wedge a summon
        log.warning("run_analysis failed (%s); SME continues without it", e)
        ex.shutdown(wait=False)
        return None


def _extract_result(text: str) -> str | None:
    """Pull the SME-facing answer out of the sandbox's final output: prefer the
    last `RESULT: ...` line we asked it to print; else the last non-empty line;
    else the trimmed whole. None when there is nothing usable."""
    if not text or not text.strip():
        return None
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    for ln in reversed(lines):
        low = ln.lower()
        if low.startswith("result:"):
            return ln[len("result:"):].strip() or ln
    return (lines[-1] if lines else text.strip())[:500]


def reset_sandbox_for_tests() -> None:
    """Test hook: drop the cached environment_id so a fresh test can re-exercise
    create-once. Not used in production."""
    global _sandbox_env_id
    _sandbox_env_id = None


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
    {
        "name": "run_analysis",
        "description": (
            "Run a quantitative analysis as REAL Python code in a compute "
            "sandbox and get back the computed result. Use ONLY when you need to "
            "actually CALCULATE a number from values you already have (e.g. a "
            "worst-case rail current from a list of loads, a thermal/power "
            "budget, an RC time constant, a divider). Do NOT use it to look up "
            "facts (use the lookup tools) or to invent inputs. Pass the full "
            "computation as `task`, INCLUDING the concrete input numbers and the "
            "units you want; the sandbox computes and returns the result string."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "task": {
                    "type": "STRING",
                    "description": (
                        "Self-contained computation to perform, with concrete "
                        "input values and desired units, e.g. 'On a 3.3V rail "
                        "with loads 120mA, 80mA, 250mA, compute worst-case total "
                        "current in A.'"
                    ),
                },
            },
            "required": ["task"],
        },
    },
]


def _dispatch_tool(
    name: str, args: dict, knowledge: KnowledgeAdapter,
    on_step: "Callable[[str], None] | None" = None,
) -> dict:
    """Execute a declared tool against the bound adapter; return a JSON-able dict.
    Read-only knowledge lookups — nothing here actuates hardware (BK-10).

    `run_analysis` is the one exception to "read-only adapter": it runs REAL code
    in the kept-warm Antigravity sandbox (still no hardware — pure compute). Its
    intermediate SSE steps are forwarded to `on_step` so they stream live; the
    returned dict carries the computed result (or a no-op marker when the sandbox
    is unavailable / timed out, so the SME concludes without it)."""
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
    if name == "run_analysis":
        computed = run_analysis(str(args.get("task", "")), on_step=on_step)
        if computed is None:
            return {"computed": None,
                    "note": "compute sandbox unavailable; reason from documented values instead"}
        return {"computed": computed}
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
        "your answer BEFORE concluding; you may call several in sequence. You ALSO "
        "have run_analysis, which runs REAL Python in a compute sandbox — use it "
        "ONLY to actually CALCULATE a number from inputs you already have (e.g. a "
        "worst-case rail current or thermal/power budget), passing the concrete "
        "values + units. When you have enough, stop calling tools and you will be "
        "asked for the final JSON."
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

            # For run_analysis, forward each sandbox SSE step (model output /
            # code-exec / result) to the SME's channel AS IT ARRIVES, so the
            # operator watches the computation unfold (#12 emit sink). Each step
            # rides the same on_tool_call sink as a run_analysis "step" call.
            on_step = None
            if fc.name == "run_analysis" and on_tool_call is not None:
                def on_step(line: str) -> None:  # noqa: B023 — fc is per-iter, intended
                    try:
                        on_tool_call({"name": "run_analysis",
                                      "args": {"step": line}, "result": None})
                    except Exception as e:  # noqa: BLE001
                        log.warning("on_tool_call (step) sink raised (%s); continuing", e)

            result = _dispatch_tool(fc.name, args, knowledge_for_call, on_step)
            call = {"name": fc.name, "args": args, "result": result}
            tool_calls.append(call)
            # Surface this completed tool call to the SME's channel (streaming);
            # a raising sink must not break the loop (01 §7).
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
        siblings = [s for s in summon.smes if s != sme_id]
        # Prefer the rich on-disk persona pack (smes/<id>/AGENTS.md + SKILL.md);
        # fall back to the inline SME_ROLES one-liner when no pack ships (keeps
        # zero-config boot working — the packs are an additive upgrade).
        pack = _sme_persona(sme_id)
        if pack is not None:
            system = (
                pack
                + "\n\n=== Standing instructions ===\n"
                "You are on Forge's guild advising a HUMAN operator at an "
                "electronics workbench. Forge actuates nothing — you only "
                "recommend steps the operator performs by hand. Be terse, ground "
                "every claim by retrieving it with your tools, and stay strictly "
                "in your lane."
            )
        else:
            role = SME_ROLES.get(sme_id, "a specialist SME")
            system = (
                f"You are {sme_id}, {role}. You are on Forge's guild advising a HUMAN "
                "operator at an electronics workbench. Forge actuates nothing — you "
                "only recommend steps the operator performs by hand. Be terse, ground "
                "every claim in the board doc/datasheet (use your tools to retrieve "
                "them), and stay strictly in your lane."
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

"""Distiller — the "managed agent" that turns raw event chatter into a
manager-readable STATUS row per session.

Split into two layers on purpose:

  1. ``compute_facts`` — DETERMINISTIC. Derives the structured fields a manager
     needs straight from the events: active duration, current board/task guess,
     SMEs consulted + their key claims, timeline, pending confirmations + ages,
     and the attention flags (long pause / repeated dissent / safety halt /
     stuck confirmation). These are reliable and need no LLM — so the dashboard
     is useful even with no Gemini key and even if the model hallucinates.

  2. ``headline_for`` — the ONE-LINE natural-language "what they're doing right
     now". Uses Gemini when keyed; falls back to a templated heuristic from the
     same facts otherwise. The Gemini call is injected (``model_call``) so tests
     stub it with zero network.

The distiller writes ``headline`` + the full ``facts`` dict to ``status`` so the
dashboard renders both the gist and the structured detail.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from typing import Any, Callable, Optional

from observer.store import Store, now_ms

log = logging.getLogger("observer.distill")

# Thresholds for attention flags (ms).
LONG_PAUSE_MS = 3 * 60 * 1000          # no activity for >3 min while a step is open
STUCK_CONFIRM_MS = 2 * 60 * 1000       # a confirmation pending >2 min ⇒ "operator stuck?"
REPEATED_DISSENT_N = 2                 # ≥2 dissent reports ⇒ unresolved disagreement

ModelCall = Callable[[str], str]  # prompt -> one-line headline


# ── deterministic fact extraction ────────────────────────────────────────────

def _board_task_hint(events: list[dict[str, Any]]) -> Optional[str]:
    """Best-effort guess at the board/task from SummonGuild topics + chat.

    The bus doesn't carry an explicit "board/task" field, so we mine topics and
    rail/net mentions (e.g. "3V3", "BQ79616") from recent text. Honest heuristic.
    """
    # Prefer the most recent SummonGuild topic — it's the orchestrator's own
    # framing of the current question.
    for ev in events:  # events are newest-first
        if ev["kind"] == "SummonGuild":
            raw = json.loads(ev["raw_json"])
            topic = raw.get("topic")
            if topic:
                return topic
    # Otherwise sniff a rail/part token from any recent text.
    pat = re.compile(r"\b(\d+V\d+|\d+\.\d+\s?V|BQ\d+|ESP32|[A-Z]{2,}\d{3,})\b", re.IGNORECASE)
    for ev in events:
        for field in (ev.get("summary"), ev.get("channel_id")):
            if field:
                m = pat.search(field)
                if m:
                    return m.group(1)
    return None


def _smes_consulted(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each SME that responded + its highest-confidence claim (key finding)."""
    by_sme: dict[str, dict[str, Any]] = {}
    for ev in events:
        if ev["kind"] != "SmeResponse":
            continue
        raw = json.loads(ev["raw_json"])
        sme = raw.get("smeId") or ev.get("author_id") or "?"
        conf = raw.get("confidence")
        claim = raw.get("claim") or ""
        prev = by_sme.get(sme)
        if prev is None or (conf is not None and conf > prev.get("confidence", -1)):
            by_sme[sme] = {"sme": sme, "confidence": conf, "claim": claim}
    return list(by_sme.values())


def _timeline(events: list[dict[str, Any]], *, limit: int = 25) -> list[dict[str, Any]]:
    """Compact, manager-facing step/consult timeline (newest-first → returned
    oldest-first for natural reading)."""
    interesting = {
        "SummonGuild", "SmeResponse", "DissentReport", "ConfirmationRequest",
        "ConfirmationResponse", "SafetyInterrupt", "CheckpointMarker", "ToolCall",
    }
    rows = []
    for ev in events:
        if ev["kind"] not in interesting:
            continue
        rows.append(
            {
                "ts_ms": ev["ts_ms"],
                "kind": ev["kind"],
                "author": ev.get("author_id"),
                "summary": ev.get("summary"),
            }
        )
        if len(rows) >= limit:
            break
    return list(reversed(rows))


def compute_facts(
    events: list[dict[str, Any]],
    *,
    session_id: str,
    now: Optional[int] = None,
) -> dict[str, Any]:
    """Derive the structured manager view from a session's recent events.

    ``events`` MUST be newest-first (as ``Store.recent_events`` returns them).
    Pure + deterministic — the test asserts exact fields.
    """
    now = now if now is not None else now_ms()
    flags: list[str] = []

    if not events:
        return {
            "session_id": session_id,
            "active": False,
            "active_for_ms": 0,
            "last_activity_ms": None,
            "idle_ms": None,
            "board_task": None,
            "event_count": 0,
            "smes_consulted": [],
            "timeline": [],
            "pending_confirmations": [],
            "flags": [],
        }

    newest = events[0]
    oldest = events[-1]
    last_activity = newest["received_ms"]
    first_activity = oldest["received_ms"]
    idle_ms = max(0, now - last_activity)
    active_for_ms = max(0, last_activity - first_activity)

    # Pending confirmations within this batch (by call_id, req w/o response).
    resolved = {
        json.loads(e["raw_json"]).get("callId") or e.get("call_id")
        for e in events
        if e["kind"] == "ConfirmationResponse"
    }
    pending = []
    seen: set[str] = set()
    for e in events:
        if e["kind"] != "ConfirmationRequest":
            continue
        raw = json.loads(e["raw_json"])
        cid = raw.get("callId") or e.get("call_id")
        if cid in resolved or cid in seen:
            continue
        seen.add(cid)
        age = max(0, now - e["received_ms"])
        pending.append(
            {
                "call_id": cid,
                "summary": e.get("summary"),
                "risk": raw.get("risk"),
                "invoker": raw.get("invokerSmeId"),
                "pending_ms": age,
            }
        )
    pending.sort(key=lambda x: x["pending_ms"], reverse=True)

    # Attention flags — deterministic + reliable.
    kinds = Counter(e["kind"] for e in events)
    has_open_step = bool(pending)
    if kinds.get("SafetyInterrupt"):
        # Surface HALT distinctly from WARN.
        severities = {
            json.loads(e["raw_json"]).get("severity")
            for e in events
            if e["kind"] == "SafetyInterrupt"
        }
        flags.append("safety_halt" if "HALT" in severities else "safety_warn")
    if kinds.get("DissentReport", 0) >= REPEATED_DISSENT_N:
        flags.append("repeated_dissent")
    if has_open_step and any(p["pending_ms"] >= STUCK_CONFIRM_MS for p in pending):
        flags.append("stuck_confirmation")
    if has_open_step and idle_ms >= LONG_PAUSE_MS:
        flags.append("long_pause")

    return {
        "session_id": session_id,
        "active": idle_ms < LONG_PAUSE_MS,
        "active_for_ms": active_for_ms,
        "last_activity_ms": last_activity,
        "idle_ms": idle_ms,
        "board_task": _board_task_hint(events),
        "event_count": len(events),
        "smes_consulted": _smes_consulted(events),
        "timeline": _timeline(events),
        "pending_confirmations": pending,
        "flags": flags,
    }


# ── one-line headline (Gemini, or heuristic fallback) ─────────────────────────

def _humanize_ms(ms: Optional[int]) -> str:
    if not ms:
        return "0 min"
    mins = ms // 60000
    if mins < 1:
        return f"{ms // 1000}s"
    return f"{mins} min"


def heuristic_headline(facts: dict[str, Any]) -> str:
    """Deterministic templated headline from facts — used when Gemini is off or
    fails. Mirrors the structure the prompt asks Gemini for, so the dashboard
    reads the same regardless of source."""
    parts: list[str] = []
    task = facts.get("board_task")
    dur = _humanize_ms(facts.get("active_for_ms"))
    if task:
        parts.append(f"Operator {dur} into '{task}'")
    elif facts.get("event_count"):
        parts.append(f"Operator active {dur}")
    else:
        return "No recent activity"

    smes = facts.get("smes_consulted") or []
    if smes:
        top = max(smes, key=lambda s: (s.get("confidence") or 0))
        claim = (top.get("claim") or "").rstrip(".")
        if claim:
            parts.append(f"{top['sme']} says {claim}")

    pend = facts.get("pending_confirmations") or []
    if pend:
        oldest = pend[0]
        parts.append(
            f"{len(pend)} confirmation(s) pending, oldest {_humanize_ms(oldest['pending_ms'])}"
        )

    if "safety_halt" in facts.get("flags", []):
        parts.append("SAFETY HALT active")
    return "; ".join(parts) + "."


_PROMPT_TEMPLATE = """You are a shift supervisor's assistant watching one bench operator who is \
doing an electronics rework with an AI advisor ("Forge"). Below is a JSON \
summary of what just happened on their session. Write ONE concise sentence (max \
~30 words) a busy manager can read at a glance: what the operator is doing right \
now, how long they've been at it, any SME flag, and any pending safety \
confirmation and how long it's been waiting. No preamble, no markdown.

Respond ONLY as JSON: {{"headline": "<the one sentence>"}}

SESSION FACTS:
{facts}
"""


def build_prompt(facts: dict[str, Any]) -> str:
    # Trim the timeline in the prompt to keep tokens bounded.
    compact = dict(facts)
    compact["timeline"] = facts.get("timeline", [])[-10:]
    return _PROMPT_TEMPLATE.format(facts=json.dumps(compact, indent=2))


def _parse_headline(text: str) -> str:
    """Extract the one-line headline from a model response. The prompt asks for
    JSON ``{"headline": "..."}`` (response_mime_type=application/json), but we
    tolerate a bare sentence too — robustness over strictness."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty model response")
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and obj.get("headline"):
            return " ".join(str(obj["headline"]).split())
    except (json.JSONDecodeError, TypeError):
        pass
    return " ".join(text.split())


def headline_for(facts: dict[str, Any], model_call: Optional[ModelCall]) -> tuple[str, str]:
    """Return ``(headline, source)``. ``source`` ∈ {'gemini','heuristic'}.

    ``model_call`` maps a prompt → the raw model text (JSON per the prompt).
    ``None`` (or any error) falls back to the deterministic heuristic — the
    dashboard never goes blank.
    """
    if model_call is None or not facts.get("event_count"):
        return heuristic_headline(facts), "heuristic"
    try:
        headline = _parse_headline(model_call(build_prompt(facts)))
        if not headline:
            raise ValueError("empty headline")
        return headline, "gemini"
    except Exception:  # noqa: BLE001
        log.exception("distill: model_call failed; using heuristic")
        return heuristic_headline(facts), "heuristic"


def gemini_model_call(api_key: str, model: str) -> ModelCall:
    """Build a ModelCall backed by google-genai — mirrors orchestrator
    genai_seams._flash_json: lazy ``genai.Client()`` (reads GEMINI_API_KEY from
    env automatically), a plain ``generate_content`` constrained to JSON. NOT
    the Antigravity sandbox — distillation is fast text summarization, so a
    plain gemini-3.5-flash call is the right tool. Imported lazily so tests
    never touch the network or the optional dep.

    ``api_key`` is accepted for explicitness/testability; an empty string lets
    the client read GEMINI_API_KEY from the environment (the deployed path)."""
    from google import genai  # optional dep

    client = genai.Client(api_key=api_key) if api_key else genai.Client()

    def _call(prompt: str) -> str:
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        return getattr(resp, "text", "") or ""

    return _call


def distill_once(
    store: Store,
    *,
    window_s: float,
    max_events: int,
    model_call: Optional[ModelCall],
    now: Optional[int] = None,
) -> int:
    """Run one distill cycle across all sessions seen in the window. Writes a
    status row per session. Returns the number of status rows written.

    This is the seam the test drives: insert events, run ``distill_once`` with a
    stub ``model_call``, assert a status row appears.
    """
    now = now if now is not None else now_ms()
    since = now - int(window_s * 1000)
    sessions = store.session_ids(since_ms=since)
    written = 0
    for sid in sessions:
        events = store.recent_events(limit=max_events, since_ms=since, session_id=sid)
        if not events:
            continue
        facts = compute_facts(events, session_id=sid, now=now)
        headline, source = headline_for(facts, model_call)
        store.upsert_status(sid, headline, facts, source)
        written += 1
    return written

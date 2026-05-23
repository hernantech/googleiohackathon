"""SME structured-output reader (P6) — strategy + fallback chain.

Direct implementation of `specs/02_sme_persona_format.md` §4 ("hackathon plan:
implement (b) AND (c) simultaneously … prefer (b), fall back to (c)") and the
SME-4 contract (§11). The reader turns a Managed-Agents SME turn's output into
a validated `SmeResponse` (frozen at `orchestrator/proto/events.py`).

Transport candidates (02 §4 / 00 §6):
  (a) `response_schema` on the model — NOT implemented this spike.
  (b) `/workspace/output.json` — a JSON string the SME wrote at end of turn.
  (c) a fenced ```json block in the SME's free-text turn, Pydantic-validated,
      with ONE one-shot retry on validation failure.

Preference: (b) when present and valid, else (c). If neither yields a valid
envelope, the reader returns a low-confidence stub `SmeResponse` (confidence
0.0) rather than raising — it NEVER raises on bad input.

Design decisions resolving spec ambiguity:
- 02 §4 leaves the retry to candidate (c) only ("Orchestrator regex-extracts,
  validates against Pydantic; on failure, sends a one-shot retry prompt").
  Strategy (b) reads a file the SME already committed; there is nobody to
  re-prompt for it, so (b) has NO retry. The single retry lives in (c). This is
  consistent with SME-4 case (iv) which describes a *fenced* block that is
  malformed-then-valid-on-retry.
- The reader is pure / injectable: `retry` is a `Callable[[str], str]` so tests
  run with no network. If `retry` is None, (c) does not retry; it just fails
  over to the stub.
- `smeId`/`callId` from the parsed payload are AUTHORITATIVE when present and
  non-empty; otherwise we backfill from the caller-supplied identity so the
  returned envelope always ties back to the summon (00 §9). The retry prompt
  text matches the spec wording.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from pydantic import ValidationError

from orchestrator.proto.events import SmeResponse, now_ns

__all__ = ["read_sme_response", "RETRY_PROMPT_TEMPLATE"]

# 02 §4 candidate (c): "your JSON failed validation: <error>. Resend ONLY the JSON."
RETRY_PROMPT_TEMPLATE = (
    "Your JSON failed validation: {error}. Resend ONLY the JSON, in a single "
    "```json fenced block, matching the SmeResponse schema."
)

# Matches a ```json … ``` fenced block (case-insensitive language tag, optional).
# Non-greedy body; we collect every match and prefer the LAST one (02 §4: the
# SME "ends every turn with a ```json fenced block").
_FENCE_RE = re.compile(
    r"```[ \t]*(?:json)?[ \t]*\r?\n(?P<body>.*?)\r?\n?```",
    re.DOTALL | re.IGNORECASE,
)


def read_sme_response(
    *,
    smeId: str,
    callId: str,
    output_json: str | None = None,
    free_text: str | None = None,
    retry: Callable[[str], str] | None = None,
) -> SmeResponse:
    """Read a validated `SmeResponse` from an SME turn's output.

    Args:
        smeId: identity of the responding SME (e.g. "@power"). Backfilled into
            the envelope when the parsed payload omits it.
        callId: the summon `callId` this response ties back to (00 §9).
        output_json: strategy (b) — the raw content of `/workspace/output.json`
            (a JSON string), or None/empty if the file was missing/empty.
        free_text: strategy (c) — the SME's free-text turn, expected to contain
            a trailing ```json fenced block.
        retry: optional one-shot retry callback for strategy (c). Given the
            retry prompt, returns fresh free text. Injected for testability.

    Returns:
        A valid `SmeResponse`. Prefers (b); falls back to (c) (with one retry);
        degrades to a low-confidence stub if neither yields a valid envelope.
        Never raises on bad input.
    """
    # ── Strategy (b): /workspace/output.json ──────────────────────────────
    if output_json and output_json.strip():
        resp = _validate_payload(output_json, smeId=smeId, callId=callId)
        if resp is not None:
            return resp
        # (b) present but invalid → fall through to (c). No retry for (b):
        # there is no live turn to re-prompt for an already-committed file.

    # ── Strategy (c): fenced ```json block in free text (+ one retry) ─────
    if free_text and free_text.strip():
        block = _extract_json_block(free_text)
        if block is not None:
            resp = _validate_payload(block, smeId=smeId, callId=callId)
            if resp is not None:
                return resp
            # Validation failed → ONE one-shot retry (02 §4 candidate c).
            resp = _retry_once(block, smeId=smeId, callId=callId, retry=retry)
            if resp is not None:
                return resp
        elif retry is not None:
            # No parseable block at all — still spend the one allowed retry,
            # asking for ONLY the JSON.
            resp = _retry_once(
                free_text, smeId=smeId, callId=callId, retry=retry,
                error="no ```json fenced block found",
            )
            if resp is not None:
                return resp

    # ── Neither (b) nor (c) yielded a valid envelope → low-confidence stub ─
    return _stub(smeId=smeId, callId=callId)


def _retry_once(
    bad_payload: str,
    *,
    smeId: str,
    callId: str,
    retry: Callable[[str], str] | None,
    error: str | None = None,
) -> SmeResponse | None:
    """Issue ONE retry via the callback and validate the result.

    Returns a valid `SmeResponse`, or None if there is no callback, the
    callback misbehaves, or the retried output still fails to validate.
    """
    if retry is None:
        return None
    if error is None:
        # Re-run validation just to capture the error string for the prompt.
        error = _validation_error_str(bad_payload)
    prompt = RETRY_PROMPT_TEMPLATE.format(error=error)
    try:
        retried = retry(prompt)
    except Exception:
        # A misbehaving retry callback must not break the reader.
        return None
    if not retried or not str(retried).strip():
        return None
    # The retry may itself come back fenced or as bare JSON.
    block = _extract_json_block(retried) or retried
    return _validate_payload(block, smeId=smeId, callId=callId)


def _extract_json_block(text: str) -> str | None:
    """Return the body of the LAST ```json fenced block, or None."""
    matches = _FENCE_RE.findall(text)
    if not matches:
        return None
    return matches[-1].strip() or None


def _validate_payload(
    raw: str, *, smeId: str, callId: str
) -> SmeResponse | None:
    """Parse + validate a JSON payload into a `SmeResponse`.

    Backfills `kind`, `smeId`, `callId`, and `ts` so a payload that is correct
    on the substantive fields but light on envelope plumbing still validates and
    ties back to the summon. Returns None on any parse/validation failure.
    """
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    data = _backfill_envelope(data, smeId=smeId, callId=callId)
    try:
        return SmeResponse.model_validate(data)
    except ValidationError:
        return None


def _backfill_envelope(
    data: dict[str, Any], *, smeId: str, callId: str
) -> dict[str, Any]:
    """Fill required envelope fields the SME may have omitted.

    Parsed payload values win when present and non-empty; otherwise we use the
    caller-supplied identity. `ts` defaults to now if absent.
    """
    out = dict(data)
    out.setdefault("kind", "SmeResponse")
    if not out.get("smeId"):
        out["smeId"] = smeId
    if not out.get("callId"):
        out["callId"] = callId
    if "ts" not in out or out.get("ts") is None:
        out["ts"] = now_ns()
    return out


def _validation_error_str(raw: str) -> str:
    """Best-effort one-line error description for the retry prompt."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        return f"invalid JSON: {exc}"
    if not isinstance(data, dict):
        return "JSON is not an object"
    try:
        SmeResponse.model_validate(data)
    except ValidationError as exc:
        return "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
            for e in exc.errors()
        ) or str(exc)
    return "unknown validation error"


def _stub(*, smeId: str, callId: str) -> SmeResponse:
    """A safe, valid, low-confidence fallback (02 §4 / SME-4 neither-case)."""
    return SmeResponse(
        smeId=smeId,
        callId=callId,
        confidence=0.0,
        claim="<no parseable output>",
        rationale=(
            "No valid SmeResponse could be read from this SME's turn: "
            "neither /workspace/output.json (strategy b) nor a fenced ```json "
            "block (strategy c) yielded a schema-valid envelope."
        ),
        ts=now_ns(),
    )

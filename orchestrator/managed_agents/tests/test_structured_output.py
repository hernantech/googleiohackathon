"""SME-4 — structured-output reader: strategy (b) + (c) fallback chain.

specs/02_sme_persona_format.md §11 SME-4:
  given an output.json (strategy b) -> parses to SmeResponse;
  given only a fenced ```json block (strategy c) -> parses;
  given both -> prefers (b);
  given neither -> one retry then low-confidence stub
  (fallback chain holds).

Run: PYTHONPATH=. .venv/bin/pytest orchestrator/managed_agents/tests/ -q
"""

from __future__ import annotations

import json

from orchestrator.managed_agents import read_sme_response
from orchestrator.proto.events import SmeResponse


# ───────────────────────── fixtures / helpers ──────────────────────────

SME_ID = "@power"
CALL_ID = "01CALLIDULIDXXXXXXXXXXXXXX"


def _payload(claim: str, *, confidence: float = 0.8, **extra: object) -> dict:
    """A substantively-complete SmeResponse payload (envelope may be light)."""
    base: dict[str, object] = {
        "smeId": SME_ID,
        "callId": CALL_ID,
        "confidence": confidence,
        "claim": claim,
        "rationale": "Because the datasheet says so.",
    }
    base.update(extra)
    return base


def _json_str(claim: str, **kw: object) -> str:
    return json.dumps(_payload(claim, **kw))


def _fenced(claim: str, *, lang: str = "json", **kw: object) -> str:
    body = json.dumps(_payload(claim, **kw), indent=2)
    return (
        "Here is my reasoning. The 3.3V rail is sagging.\n\n"
        f"```{lang}\n{body}\n```\n"
    )


# ───────────────────── SME-4 (i): only output.json → (b) ────────────────

def test_case_i_only_output_json_parses_via_b():
    resp = read_sme_response(
        smeId=SME_ID,
        callId=CALL_ID,
        output_json=_json_str("rail droop from undersized bulk cap"),
        free_text=None,
    )
    assert isinstance(resp, SmeResponse)
    assert resp.claim == "rail droop from undersized bulk cap"
    assert resp.smeId == SME_ID
    assert resp.callId == CALL_ID
    # Validates against the frozen proto contract (round-trips).
    assert SmeResponse.model_validate_json(resp.model_dump_json()) == resp


# ───────────── SME-4 (ii): only a fenced ```json block → (c) ────────────

def test_case_ii_only_fenced_block_parses_via_c():
    resp = read_sme_response(
        smeId=SME_ID,
        callId=CALL_ID,
        output_json=None,
        free_text=_fenced("EMI from the switcher, add input filtering"),
    )
    assert isinstance(resp, SmeResponse)
    assert resp.claim == "EMI from the switcher, add input filtering"
    assert SmeResponse.model_validate_json(resp.model_dump_json()) == resp


def test_case_ii_bare_json_language_tag_omitted_still_parses():
    # Tolerate a fence with no explicit `json` language hint.
    resp = read_sme_response(
        smeId=SME_ID,
        callId=CALL_ID,
        output_json=None,
        free_text=_fenced("loose fence still parses", lang=""),
    )
    assert resp.claim == "loose fence still parses"


def test_case_ii_last_fenced_block_wins():
    # SMEs "end every turn with a ```json fenced block" — prefer the LAST.
    text = (
        _fenced("an earlier draft block")
        + "\nOn reflection, my final answer:\n"
        + _fenced("final block at end of turn")
    )
    resp = read_sme_response(
        smeId=SME_ID, callId=CALL_ID, output_json=None, free_text=text
    )
    assert resp.claim == "final block at end of turn"


# ──────────────── SME-4 (iii): both present → prefers (b) ───────────────

def test_case_iii_both_present_prefers_b():
    resp = read_sme_response(
        smeId=SME_ID,
        callId=CALL_ID,
        output_json=_json_str("FROM FILE B — authoritative"),
        free_text=_fenced("FROM FENCE C — should be ignored"),
    )
    assert isinstance(resp, SmeResponse)
    assert resp.claim == "FROM FILE B — authoritative"


def test_case_iii_invalid_b_falls_back_to_c():
    # (b) preference is conditional on (b) being VALID; an invalid file must
    # not shadow a good fenced block.
    resp = read_sme_response(
        smeId=SME_ID,
        callId=CALL_ID,
        output_json="{ this is : not valid json ]",
        free_text=_fenced("rescued from fence C"),
    )
    assert resp.claim == "rescued from fence C"


# ──── SME-4 (iv): malformed fenced json → ONE retry → valid-on-retry ────

def test_case_iv_malformed_fence_then_valid_on_retry():
    calls: list[str] = []

    def retry(prompt: str) -> str:
        calls.append(prompt)
        # Second attempt comes back valid (fenced).
        return _fenced("valid after one retry")

    # Free text has a fenced block whose JSON is malformed (trailing comma /
    # unterminated) so Pydantic/json rejects it, triggering the retry.
    malformed = "Here goes:\n```json\n{ \"claim\": \"oops\", , }\n```\n"
    resp = read_sme_response(
        smeId=SME_ID,
        callId=CALL_ID,
        output_json=None,
        free_text=malformed,
        retry=retry,
    )
    assert isinstance(resp, SmeResponse)
    assert resp.claim == "valid after one retry"
    # The retry callback was invoked exactly ONCE.
    assert len(calls) == 1
    assert "Resend ONLY the JSON" in calls[0]
    assert SmeResponse.model_validate_json(resp.model_dump_json()) == resp


def test_case_iv_retry_invoked_at_most_once_even_if_still_bad():
    calls: list[str] = []

    def retry(prompt: str) -> str:
        calls.append(prompt)
        return "```json\n{ still: broken }\n```"  # still invalid

    resp = read_sme_response(
        smeId=SME_ID,
        callId=CALL_ID,
        output_json=None,
        free_text="```json\n{ broken\n```",
        retry=retry,
    )
    # Exactly one retry, then stub (no retry loop).
    assert len(calls) == 1
    assert resp.confidence == 0.0
    assert resp.claim == "<no parseable output>"


def test_case_iv_retry_can_return_bare_json():
    def retry(prompt: str) -> str:
        return _json_str("bare json on retry")  # no fence

    resp = read_sme_response(
        smeId=SME_ID,
        callId=CALL_ID,
        output_json=None,
        free_text="```json\n{ broken\n```",
        retry=retry,
    )
    assert resp.claim == "bare json on retry"


# ──────────────── neither present → low-confidence stub ─────────────────

def test_neither_present_returns_low_confidence_stub():
    resp = read_sme_response(
        smeId=SME_ID, callId=CALL_ID, output_json=None, free_text=None
    )
    assert isinstance(resp, SmeResponse)
    assert resp.confidence == 0.0
    assert resp.claim == "<no parseable output>"
    assert resp.smeId == SME_ID
    assert resp.callId == CALL_ID
    assert SmeResponse.model_validate_json(resp.model_dump_json()) == resp


def test_empty_strings_treated_as_absent():
    resp = read_sme_response(
        smeId=SME_ID, callId=CALL_ID, output_json="   ", free_text="\n\t "
    )
    assert resp.confidence == 0.0
    assert resp.claim == "<no parseable output>"


def test_no_retry_callback_degrades_to_stub_not_raise():
    # Malformed fence, no retry provided → stub, never raises.
    resp = read_sme_response(
        smeId=SME_ID,
        callId=CALL_ID,
        output_json=None,
        free_text="```json\n{ broken\n```",
        retry=None,
    )
    assert resp.confidence == 0.0
    assert resp.claim == "<no parseable output>"


# ─────────────────── never-raises + envelope plumbing ───────────────────

def test_misbehaving_retry_callback_does_not_raise():
    def retry(prompt: str) -> str:
        raise RuntimeError("network down")

    resp = read_sme_response(
        smeId=SME_ID,
        callId=CALL_ID,
        output_json=None,
        free_text="```json\n{ broken\n```",
        retry=retry,
    )
    assert resp.confidence == 0.0  # degraded to stub, no exception


def test_evidence_and_actions_round_trip_through_reader():
    payload = _payload(
        "needs decoupling cap",
        confidence=0.7,
        evidence=[{"kind": "datasheet", "uri": "ds://lm2596#p7"}],
        proposedActions=[
            {
                "actor": "operator",
                "tool": "set_psu",
                "argsJson": "{\"v\": 3.3, \"i\": 0.5}",
                "rationale": "bring rail to nominal",
                "risk": "LOW",
                "documentedLimitRef": "board:rail.3v3#max",
            }
        ],
        dissentsWith=["@signal"],
    )
    resp = read_sme_response(
        smeId=SME_ID,
        callId=CALL_ID,
        output_json=json.dumps(payload),
        free_text=None,
    )
    assert resp.evidence[0].kind == "datasheet"
    assert resp.proposedActions[0].tool == "set_psu"
    assert resp.dissentsWith == ["@signal"]
    assert SmeResponse.model_validate_json(resp.model_dump_json()) == resp


def test_envelope_identity_backfilled_when_payload_omits_it():
    # Payload missing smeId/callId/ts: backfilled from caller identity.
    bare = json.dumps(
        {"confidence": 0.6, "claim": "light payload", "rationale": "terse"}
    )
    resp = read_sme_response(
        smeId=SME_ID, callId=CALL_ID, output_json=bare, free_text=None
    )
    assert resp.smeId == SME_ID
    assert resp.callId == CALL_ID
    assert resp.ts > 0
    assert resp.claim == "light payload"

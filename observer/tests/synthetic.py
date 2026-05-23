"""Synthetic bus events mirroring orchestrator/proto/examples.py shapes.

Used by the deterministic tests AND by the local replay tool so we can exercise
the full pipeline with zero orchestrator dependency / no network.
"""

from __future__ import annotations

import time


def _ns(offset_s: float = 0.0) -> int:
    return int((time.time() + offset_s) * 1_000_000_000)


def hello(session_id: str = "op-bench-01") -> dict:
    return {"kind": "Hello", "client": "phone", "sessionId": session_id, "protocolVersion": "2.0"}


def chat(body: str, *, channel="#general", author="user", kind_author="user", mid="m1") -> dict:
    return {
        "kind": "ChatMessage", "channelId": channel, "authorId": author,
        "authorKind": kind_author, "body": body, "bodyContentType": "text/markdown",
        "messageId": mid, "ts": _ns(),
    }


def summon(call_id="c1", topic="3V3-rail-rework", smes=("@power", "@signal")) -> dict:
    return {"kind": "SummonGuild", "callId": call_id, "topic": topic, "smes": list(smes),
            "contextRefs": [], "deadlineMs": 30000}


def sme_response(sme="@power", call_id="c1", confidence=0.91,
                 claim="Possible short on the 3V3 rail near U4.") -> dict:
    return {"kind": "SmeResponse", "smeId": sme, "callId": call_id, "confidence": confidence,
            "claim": claim, "rationale": "Rail droops under load per the capture.",
            "evidence": [], "proposedActions": [], "dissentsWith": [], "ts": _ns()}


def dissent(call_id="c1", axis="root_cause", summary="Power vs comm bus.") -> dict:
    return {"kind": "DissentReport", "callId": call_id, "parties": ["@power", "@firmware"],
            "axis": axis, "summary": summary, "pairwise": [], "ts": _ns()}


def confirmation_request(call_id="c1", summary="Set PSU to 3.3 V across J3", risk="HIGH",
                         invoker="@power") -> dict:
    return {"kind": "ConfirmationRequest", "callId": call_id, "summary": summary,
            "risk": risk, "invokerSmeId": invoker, "actionCardJson": None}


def confirmation_response(call_id="c1", approved=True) -> dict:
    return {"kind": "ConfirmationResponse", "callId": call_id, "approved": approved,
            "approverChannel": "voice"}


def safety(severity="HALT", reason="Hot iron near a powered board.") -> dict:
    return {"kind": "SafetyInterrupt", "severity": severity, "reason": reason,
            "suggestedRecoverActions": [], "ts": _ns()}


def audio() -> dict:
    return {"kind": "AudioChunk", "pcmBase64": "AAAA", "ts": _ns()}


def ping() -> dict:
    return {"kind": "Ping", "nonce": "x"}


def scenario() -> list[dict]:
    """A coherent rework session: summon → SME findings → safety warn → a
    pending confirmation. Returns events oldest-first."""
    return [
        hello("op-bench-01"),
        chat("My ESP32 can't read the BQ79616.", mid="m1"),
        summon(),
        sme_response(sme="@power", confidence=0.91,
                     claim="Possible short on the 3V3 rail near U4."),
        sme_response(sme="@firmware", confidence=0.55,
                     claim="Could be a baud mismatch on the comm bus."),
        safety(severity="WARN", reason="Verify PSU is off before probing."),
        confirmation_request(summary="Set PSU to 3.3 V across J3", risk="HIGH"),
    ]

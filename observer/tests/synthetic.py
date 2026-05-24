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


def goodbye(reason="operator closed the app") -> dict:
    return {"kind": "Goodbye", "reason": reason}


def transcript(text="set the PSU to 3.3 volts", partial=False, speaker="user") -> dict:
    return {"kind": "Transcript", "text": text, "partial": partial,
            "speaker": speaker, "ts": _ns()}


def tool_call(name="lookup_datasheet", call_id="t1", args="{}") -> dict:
    return {"kind": "ToolCall", "name": name, "argsJson": args, "callId": call_id}


def tool_result(call_id="t1", result='{"ok":true}', deferred=False) -> dict:
    return {"kind": "ToolResult", "callId": call_id, "resultJson": result,
            "deferred": deferred}


def channel_update(message_id="m1", delta="tok", done=False) -> dict:
    return {"kind": "ChannelUpdate", "messageId": message_id, "deltaText": delta,
            "done": done, "ts": _ns()}


def checkpoint(checkpoint_id="ck1", node="diagnose") -> dict:
    return {"kind": "CheckpointMarker", "checkpointId": checkpoint_id,
            "graphNodeName": node, "ts": _ns()}


def snapshot_chat(analysis="Solder bridge between pins 11 and 12 of U4.", mid="snap1") -> dict:
    """A SnapshotAnalysis card carried inside a ChatMessage (application/json)."""
    import json as _json
    payload = {
        "kind": "SnapshotAnalysis", "jobId": "j1",
        "frame": {"kind": "FrameRef", "uri": "blob://x", "width": 640,
                  "height": 480, "ts": _ns(), "sourceSeq": 1},
        "model": "gemini-vision", "analysis": analysis, "cites": [], "ts": _ns(),
    }
    return {"kind": "ChatMessage", "channelId": "#actions", "authorId": "system",
            "authorKind": "system", "body": _json.dumps(payload),
            "bodyContentType": "application/json", "messageId": mid, "ts": _ns()}


def replay_envelope(kind="ChannelList") -> dict:
    """A per-reconnect replay envelope the bus re-sends (no stable id) — dropped."""
    return {"kind": kind}


def audio() -> dict:
    return {"kind": "AudioChunk", "pcmBase64": "AAAA", "ts": _ns()}


def ping() -> dict:
    return {"kind": "Ping", "nonce": "x"}


def pong() -> dict:
    return {"kind": "Pong", "nonce": "x"}


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

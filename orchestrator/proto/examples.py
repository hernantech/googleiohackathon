"""Canonical instances of every wire type — the single source of truth for the
golden corpus (`testdata/wire/*.json`) and the contract tests (WP-6, 08 §3.1).

Both `testdata/wire/_generate.py` and `orchestrator/proto/tests/test_events.py`
import `canonical()`; the generator dumps them to JSON, the tests round-trip
them. Keep `ts` values fixed so the golden JSON is deterministic."""

from __future__ import annotations

from pydantic import BaseModel

from orchestrator.proto.events import (
    ActionCard,
    AudioChunk,
    ChannelUpdate,
    ChatMessage,
    CheckpointMarker,
    ConfirmationRequest,
    ConfirmationResponse,
    DissentPair,
    DissentReport,
    EvidenceRef,
    FrameRef,
    Goodbye,
    Hello,
    ProposedAction,
    SafetyInterrupt,
    SmeResponse,
    SnapshotAnalysis,
    SummonGuild,
    ToolCall,
    ToolResult,
    Transcript,
)

_TS = 1_716_500_000_000_000_000  # fixed ns timestamp for determinism

_FRAME = FrameRef(uri="gs://forge/frame-00412.jpg", width=1920, height=1080, ts=_TS, sourceSeq=1)
_EVIDENCE = EvidenceRef(kind="datasheet", uri="gs://forge/bq79616.pdf", note="§7 power-up")
_OPERATOR_STEP = ProposedAction(
    actor="operator",
    tool="set_psu",
    argsJson='{"channel":1,"voltage_v":30.0,"current_limit_a":0.5,"target":"J3"}',
    rationale="Apply the emulated cell stack so the AFE wakes.",
    risk="HIGH",
    instruction="Set bench PSU CH1 to 30.0 V, 0.5 A limit, across the cell-sim ladder (J3).",
    documentedLimitRef="board_doc p.4 / board_profile.nets[J3]",
)
_LOOKUP_STEP = ProposedAction(
    actor="guild",
    tool="lookup_datasheet",
    argsJson='{"part":"bq79616","query":"power-up"}',
    rationale="Confirm the wake sequence requires a cell stack.",
    risk="LOW",
)
_ACTION_CARD = ActionCard(
    title="@power asks you to:",
    bodyMarkdown="Set bench PSU CH1 to **30.0 V**, 0.5 A limit, across the cell-sim ladder (J3).",
    diffMarkdown="| | Now | Set |\n|---|---|---|\n| CH1 | off | 30.0 V / 0.5 A |",
    risk="HIGH",
    documentedLimit="board doc max: 30 V",
)


def canonical() -> dict[str, BaseModel]:
    """name → canonical instance. Names become `testdata/wire/<name>.json`."""
    return {
        # ── AgentEvent union (15) ──
        "Hello": Hello(client="phone", sessionId="01HSESSION", protocolVersion="2.0"),
        "Goodbye": Goodbye(reason="protocol_mismatch"),
        "Transcript": Transcript(text="ESP32 can't read the BQ79616.", partial=False, ts=_TS),
        "ToolCall": ToolCall(name="summon_guild", argsJson='{"topic":"comm-timeout"}', callId="01HCALL"),
        "ToolResult": ToolResult(callId="01HCALL", resultJson='{"jobId":"01HJOB"}', deferred=True),
        "ConfirmationRequest": ConfirmationRequest(
            callId="01HCALL", summary="Set PSU to 30 V across J3", risk="HIGH",
            invokerSmeId="@power", actionCardJson=_ACTION_CARD.model_dump_json(),
        ),
        "ConfirmationResponse": ConfirmationResponse(callId="01HCALL", approved=True, approverChannel="voice"),
        "AudioChunk": AudioChunk(pcmBase64="AAAA", ts=_TS),
        "ChatMessage": ChatMessage(
            channelId="#power", authorId="@power", authorKind="sme",
            body="Rail looks fine; the stack isn't applied.", messageId="01HMSG", ts=_TS,
            mentions=["@signal"],
        ),
        "SummonGuild": SummonGuild(
            callId="01HCALL", topic="bq79616-comm-timeout",
            smes=["@firmware", "@signal", "@power"], contextRefs=["gs://forge/frame-00412.jpg"],
        ),
        "SmeResponse": SmeResponse(
            smeId="@power", callId="01HCALL", confidence=0.92,
            claim="Missing cell stack; comm timeout is a symptom.",
            rationale="Only VIO is wired; per datasheet §7 the AFE needs the stack to wake.",
            evidence=[_EVIDENCE], proposedActions=[_OPERATOR_STEP, _LOOKUP_STEP],
            dissentsWith=["@firmware"], ts=_TS,
        ),
        "DissentReport": DissentReport(
            callId="01HCALL", parties=["@power", "@firmware"], axis="root_cause",
            summary="Power vs comm bus.",
            pairwise=[DissentPair(
                a="@power", b="@firmware",
                aClaim="No stack → AFE never wakes.", bClaim="Comm/baud init is wrong.",
                crux="is the comm bus the root cause",
            )], ts=_TS,
        ),
        "ChannelUpdate": ChannelUpdate(messageId="01HMSG", deltaText="...the stack", done=False, ts=_TS),
        "SafetyInterrupt": SafetyInterrupt(
            severity="WARN", reason="Hot iron near a powered board.",
            suggestedRecoverActions=[ProposedAction(
                actor="operator", tool="disable_psu_output", argsJson='{"channel":1}',
                rationale="Power down before rework.", risk="LOW",
                instruction="Turn the PSU output OFF now.",
            )], ts=_TS,
        ),
        "CheckpointMarker": CheckpointMarker(checkpointId="01HCKPT", graphNodeName="SafetyGate", ts=_TS),
        # ── card / payload types (not in the union) ──
        "FrameRef": _FRAME,
        "SnapshotAnalysis": SnapshotAnalysis(
            jobId="01HJOB", frame=_FRAME, model="gemini-3-pro",
            analysis="Only the VIO header is connected; the cell-stack lead at J3 is unplugged.",
            cites=[_EVIDENCE], ts=_TS,
        ),
        "ActionCard": _ACTION_CARD,
    }


#: Names that are members of the AgentEvent discriminated union.
UNION_MEMBER_NAMES = (
    "Hello", "Goodbye", "Transcript", "ToolCall", "ToolResult",
    "ConfirmationRequest", "ConfirmationResponse", "AudioChunk",
    "ChatMessage", "SummonGuild", "SmeResponse", "DissentReport",
    "ChannelUpdate", "SafetyInterrupt", "CheckpointMarker",
)

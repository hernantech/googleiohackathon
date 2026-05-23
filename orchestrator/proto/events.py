"""Forge wire protocol — frozen contracts.

Direct implementation of `specs/00_wire_protocol.md` §2 (AgentEvent union),
§4 (FrameRef / SnapshotAnalysis), and the user-visible tool registry (§10).

Design notes that resolve spec/test tensions:
- `Hello.protocolVersion` is REQUIRED (no default). The §2.1 snippet shows a
  default, but §7 states it "is now required" and WP-9 asserts a Hello without
  it is rejected. We honor the stated intent + the test.
- Extra fields are ignored (pydantic v2 default) → forward-compatible (WP-3).
- The discriminated union keys on `kind`; only the 15 event types carry a
  type-name `kind`. ActionCard / FrameRef / SnapshotAnalysis are *card/payload*
  types (carried inside events), not union members.
"""

from __future__ import annotations

import os
import time
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter

PROTOCOL_VERSION = "2.0"


# ───────────────────────── helpers ──────────────────────────

def now_ns() -> int:
    """Nanoseconds since epoch (the `ts` field unit everywhere)."""
    return time.time_ns()


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid() -> str:
    """A lexicographically-sortable ULID (48-bit ms time + 80-bit random).

    Client-stable IDs (§9). Implemented inline to avoid a runtime dependency.
    """
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ms << 80) | rand
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


# ───────────────────── supporting / card types ──────────────────────
# Not in the AgentEvent union — carried inside events or written to state.

class EvidenceRef(BaseModel):
    kind: Literal["frame", "scope_capture", "datasheet", "url", "file"]
    uri: str = Field(min_length=1)
    note: str | None = None


class ProposedAction(BaseModel):
    """A unit of recommended work. Forge never executes hardware actions.
    `actor` says who does it: "operator" (human performs a manual step) or
    "guild" (a read-only knowledge lookup the orchestrator runs)."""
    actor: Literal["operator", "guild"] = "operator"
    tool: str
    argsJson: str
    rationale: str
    risk: Literal["LOW", "MEDIUM", "HIGH"]
    instruction: str | None = None
    documentedLimitRef: str | None = None


class DissentPair(BaseModel):
    a: str
    b: str
    aClaim: str
    bClaim: str
    crux: str


class ActionCard(BaseModel):
    """Operator-instruction card; carried inside ConfirmationRequest.actionCardJson."""
    kind: Literal["ActionCard"] = "ActionCard"
    title: str
    bodyMarkdown: str
    diffMarkdown: str | None = None
    risk: Literal["LOW", "MEDIUM", "HIGH"]
    documentedLimit: str | None = None
    affirmLabel: str = "I did it"
    denyLabel: str = "Skip"


class FrameRef(BaseModel):
    """A stored image artifact (the latest on-demand snapshot, §4.2)."""
    kind: Literal["FrameRef"] = "FrameRef"
    uri: str = Field(min_length=1)
    width: int
    height: int
    ts: int
    sourceSeq: int


class SnapshotAnalysis(BaseModel):
    """Result of analyze_snapshot() (§4.2). Carried inside a ChatMessage
    (bodyContentType=application/json) AND sets state.latestFrame.
    Not an AgentEvent — like ActionCard, it is a card payload."""
    kind: Literal["SnapshotAnalysis"] = "SnapshotAnalysis"
    jobId: str
    frame: FrameRef
    model: str
    analysis: str
    cites: list[EvidenceRef] = Field(default_factory=list)
    ts: int


# ─────────────────── AgentEvent union (v1 carryover) ───────────────────

class Hello(BaseModel):
    kind: Literal["Hello"] = "Hello"
    client: str                                    # "phone" | "quest" | "test"
    sessionId: str
    protocolVersion: str                           # REQUIRED in v2 (WP-9, §7)


class Goodbye(BaseModel):
    kind: Literal["Goodbye"] = "Goodbye"
    reason: str


class Transcript(BaseModel):
    kind: Literal["Transcript"] = "Transcript"
    text: str
    partial: bool
    ts: int
    speaker: Literal["user", "live", "sme"] = "user"
    smeId: str | None = None


class ToolCall(BaseModel):
    kind: Literal["ToolCall"] = "ToolCall"
    name: str
    argsJson: str
    callId: str


class ToolResult(BaseModel):
    kind: Literal["ToolResult"] = "ToolResult"
    callId: str
    resultJson: str
    deferred: bool = False


class ConfirmationRequest(BaseModel):
    kind: Literal["ConfirmationRequest"] = "ConfirmationRequest"
    callId: str
    summary: str
    risk: Literal["LOW", "MEDIUM", "HIGH"]
    invokerSmeId: str | None = None
    actionCardJson: str | None = None


class ConfirmationResponse(BaseModel):
    kind: Literal["ConfirmationResponse"] = "ConfirmationResponse"
    callId: str
    approved: bool                                 # operator step: True == "I did it"
    approverChannel: Literal["voice", "chat"] = "voice"


class AudioChunk(BaseModel):
    kind: Literal["AudioChunk"] = "AudioChunk"
    pcmBase64: str
    ts: int


# ─────────────────── AgentEvent union (v2 additions) ───────────────────

class ChatMessage(BaseModel):
    kind: Literal["ChatMessage"] = "ChatMessage"
    channelId: str
    authorId: str
    authorKind: Literal["user", "live", "sme", "system"]
    body: str
    bodyContentType: Literal["text/markdown", "application/json", "text/code"] = "text/markdown"
    mentions: list[str] = Field(default_factory=list)
    replyToId: str | None = None
    messageId: str
    ts: int
    streaming: bool = False


class SummonGuild(BaseModel):
    kind: Literal["SummonGuild"] = "SummonGuild"
    callId: str
    topic: str
    smes: list[str]
    contextRefs: list[str] = Field(default_factory=list)
    deadlineMs: int = 30_000
    briefing: str | None = None                    # NEW; orchestrator-assembled grounding
                                                   #   (question + board facts + limits +
                                                   #   latest snapshot) handed to each SME.
                                                   #   Additive/optional → forward-compatible.


class SmeResponse(BaseModel):
    kind: Literal["SmeResponse"] = "SmeResponse"
    smeId: str
    callId: str
    confidence: float
    claim: str
    rationale: str
    evidence: list[EvidenceRef] = Field(default_factory=list)
    proposedActions: list[ProposedAction] = Field(default_factory=list)
    dissentsWith: list[str] = Field(default_factory=list)
    ts: int


class DissentReport(BaseModel):
    kind: Literal["DissentReport"] = "DissentReport"
    callId: str
    parties: list[str]
    axis: str
    summary: str
    pairwise: list[DissentPair]
    ts: int


class ChannelUpdate(BaseModel):
    kind: Literal["ChannelUpdate"] = "ChannelUpdate"
    messageId: str
    deltaText: str
    done: bool = False
    ts: int


class SafetyInterrupt(BaseModel):
    kind: Literal["SafetyInterrupt"] = "SafetyInterrupt"
    severity: Literal["WARN", "HALT"]
    reason: str
    suggestedRecoverActions: list[ProposedAction] = Field(default_factory=list)
    ts: int


class CheckpointMarker(BaseModel):
    kind: Literal["CheckpointMarker"] = "CheckpointMarker"
    checkpointId: str
    graphNodeName: str
    ts: int


AgentEvent = Annotated[
    Union[
        Hello, Goodbye, Transcript, ToolCall, ToolResult,
        ConfirmationRequest, ConfirmationResponse, AudioChunk,
        ChatMessage, SummonGuild, SmeResponse, DissentReport,
        ChannelUpdate, SafetyInterrupt, CheckpointMarker,
    ],
    Field(discriminator="kind"),
]

AGENT_EVENT_ADAPTER: TypeAdapter[AgentEvent] = TypeAdapter(AgentEvent)

#: The 15 concrete event classes, in declaration order (WP-1 iterates these).
AGENT_EVENT_TYPES = (
    Hello, Goodbye, Transcript, ToolCall, ToolResult,
    ConfirmationRequest, ConfirmationResponse, AudioChunk,
    ChatMessage, SummonGuild, SmeResponse, DissentReport,
    ChannelUpdate, SafetyInterrupt, CheckpointMarker,
)


def parse_agent_event(data: str | bytes | dict) -> AgentEvent:
    """Parse a JSON string/bytes or a dict into the right AgentEvent variant."""
    if isinstance(data, dict):
        return AGENT_EVENT_ADAPTER.validate_python(data)
    return AGENT_EVENT_ADAPTER.validate_json(data)


# ───────────────────────── tool registry (§10) ──────────────────────────
# The only tools that surface to clients as ToolCalls. None actuate hardware.

USER_VISIBLE_TOOLS: frozenset[str] = frozenset({
    "summon_guild",
    "confirm_step",
    "analyze_snapshot",
    "lookup_datasheet",
    "lookup_board_doc",
    "get_documented_limit",
})

#: Operator-step verbs (05 §5). These are *labels* on ProposedAction.tool —
#: the human performs them. They are deliberately NOT in USER_VISIBLE_TOOLS and
#: there is no executable behind any of them (WP-10).
OPERATOR_STEP_TOOLS: frozenset[str] = frozenset({
    "set_psu",
    "enable_psu_output",
    "disable_psu_output",
    "probe_net",
    "serial_send",
    "flash_mcu",
    "reflow_pin",
    "inspect_closeup",
})

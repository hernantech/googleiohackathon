"""Chat-bus-only envelopes — the wire types defined in `04` that are NOT
members of the sealed `AgentEvent` union in `orchestrator/proto/events.py`.

The client distinguishes union events from these by the `kind` discriminator.
These travel only on the chat bus (channel A); they are never produced by the
LangGraph and never appear in the golden corpus.

Spec sources:
- §2  ChannelList / ChannelInfo
- §5  Ping / Pong / BackpressureNotice
- §6  ReplayDone
- §7  Subscribe / Unsubscribe
- §8  ErrorEvent
- §10 ChannelHint
- observer/ATTRIBUTION.md  Presence (additive per-operator attribution hook)
"""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field


# ───────────────────────── §2 channel roster ──────────────────────────

class ChannelInfo(BaseModel):
    id: str                                       # "#power"
    title: str                                    # "Power"
    smeId: str | None = None                      # "@power" if an SME channel
    icon: str | None = None                       # emoji or short code
    alwaysVisible: bool = False                   # if true, can't be muted/collapsed
    unreadHint: int = 0                           # backfill count at replay


class ChannelList(BaseModel):
    kind: Literal["ChannelList"] = "ChannelList"
    channels: list[ChannelInfo]


# ───────────────────────── §5 streaming / liveness ──────────────────────────

class Ping(BaseModel):
    kind: Literal["Ping"] = "Ping"
    nonce: str


class Pong(BaseModel):
    kind: Literal["Pong"] = "Pong"
    nonce: str


class BackpressureNotice(BaseModel):
    kind: Literal["BackpressureNotice"] = "BackpressureNotice"
    dropped: int
    sinceTs: int


# ───────────────────────── §6 reconnect / replay ──────────────────────────

class ReplayDone(BaseModel):
    kind: Literal["ReplayDone"] = "ReplayDone"
    resumeTs: int
    checkpointId: str | None = None


# ───────────────────────── §7 client → server ──────────────────────────

class Subscribe(BaseModel):
    kind: Literal["Subscribe"] = "Subscribe"
    channelId: str


class Unsubscribe(BaseModel):
    kind: Literal["Unsubscribe"] = "Unsubscribe"
    channelId: str


# ───────────────────────── §8 error envelopes ──────────────────────────

class ErrorEvent(BaseModel):
    kind: Literal["ErrorEvent"] = "ErrorEvent"
    code: Literal[
        "invalid_event", "unknown_channel", "auth_failed",
        "rate_limited", "protocol_mismatch", "internal_error",
    ]
    message: str
    causedByMessageId: str | None = None
    ts: int


# ───────────────────────── §10 UI rendering hints ──────────────────────────

class ChannelHint(BaseModel):
    kind: Literal["ChannelHint"] = "ChannelHint"
    channelId: str
    hint: Literal["focus", "flash", "demote", "collapse"]
    reason: str


# ──────────── per-operator attribution (observer/ATTRIBUTION.md) ────────────

class Presence(BaseModel):
    """Lightweight connect/disconnect signal so the observer dashboard can show
    each operator as connected-vs-idle (observer/ATTRIBUTION.md §2).

    A NEW additive kind that is intentionally NOT a member of the frozen
    `AgentEvent` union in `orchestrator/proto/events.py` — like every other
    envelope here it rides only on the chat bus, so existing clients ignore the
    unknown `kind` (WP-3) while the observer reads it (keyed by `sessionId`).
    """

    kind: Literal["Presence"] = "Presence"
    sessionId: str
    client: str                                    # "phone" | "quest" | ...
    state: Literal["online", "offline"]
    ts: int = Field(default_factory=time.time_ns)  # ns, like every other event

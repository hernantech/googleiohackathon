# 04 — Chat Bus Protocol

> WebSocket protocol between the orchestrator and the phone/Quest client for the Discord-style multi-channel UI.
> This is channel (A) in `00_wire_protocol.md` §1.
> Cross-refs: `00_wire_protocol.md` §2 (event types), `01_langgraph_state_machine.md` (event producers), `03_safety_gate_matrix.md` §4 (ActionCard rendering).

---

## 1. Connection

```
GET wss://orchestrator.forge.ai/v2/chat
    ?sessionId=<ulid>
    &client=phone|quest|web
    &replayFrom=<checkpointId>?       ; optional
Sec-WebSocket-Protocol: forge.chat.v2, bearer.<token>
```

- `sessionId` is client-stable. Reconnecting with the same `sessionId` triggers replay.
- `replayFrom` is optional; if absent, server replays the most recent N=200 chat messages.
- Auth token rides in `Sec-WebSocket-Protocol` (see `00_wire_protocol.md` §8). Server-selected subprotocol is `forge.chat.v2`.

Inside the WS, only the JSON channel is used. H.264 video + audio ride on the separate Gemini Live WS (channel B); the on-demand snapshot is a separate `POST /v2/snapshot` (`00 §4.2`). The chat bus carries no binary media. The snapshot result comes back over the chat bus as a `SnapshotAnalysis` card (a `ChatMessage` with `bodyContentType=application/json`); camera imagery reaches the UI only as `FrameRef` thumbnails/links inside those cards.

---

## 2. Channels

Channels are server-defined. The client cannot create channels; it can only subscribe / mute. Channel naming:

| Channel | Author kinds allowed | Purpose | Mutable by user? |
|---|---|---|---|
| `#live-feed` | live, system | What Gemini Live is saying, plus aggregated SME headlines | mute only |
| `#user` | user | User's typed messages (voice transcripts also mirrored here) | always on |
| `#actions` | system, sme | Operator instructions, confirmations ("I did it"/"Skip"), outcomes | mute only |
| `#dissent` | system | DissentReport messages | mute only |
| `#scribe` | sme(@scribe), system | Continuous session report excerpts | mute only |
| `#sentinel` | sme(@sentinel), system | Sentinel observations (non-HALT) | mute only |
| `#<sme-id>` | sme(@`<sme-id>`), user | per-SME deliberation channel | mute only |
| `#general` | any | Catch-all for messages outside a deliberation | always on |

`<sme-id>` mirrors the roster: `#power`, `#signal`, `#firmware`, `#layout`, `#librarian`, `#sourcing`, `#reverse`, `#sentinel`, `#scribe`, `#tutor`. (No `#bench-tech` — that SME is removed; nothing actuates the bench.)

The orchestrator emits a `ChannelList` message at connection time so the client knows what to render:

```python
class ChannelList(BaseModel):
    kind: Literal["ChannelList"] = "ChannelList"
    channels: list[ChannelInfo]

class ChannelInfo(BaseModel):
    id: str                                       # "#power"
    title: str                                    # "Power"
    smeId: str | None = None                      # "@power" if SME channel
    icon: str | None = None                       # emoji or short code
    alwaysVisible: bool = False                   # if true, can't be muted/collapsed
    unreadHint: int = 0                           # backfill at replay
```

`ChannelList` is NOT in the sealed `AgentEvent` union from `00_wire_protocol.md` §2 — it's a chat-bus-only envelope. The client distinguishes by `kind`.

---

## 3. Message kinds (renderer matrix)

Every chat message arrives as a `ChatMessage` (`00 §2.1`). The renderer dispatches on `bodyContentType` AND on `body`'s parsed kind when JSON.

| `bodyContentType` | `body` shape | Renderer |
|---|---|---|
| `text/markdown` | markdown string | inline markdown view with code-fence support |
| `text/code` | code with first line `lang: <name>` | monospace block, syntax highlight if lang known |
| `application/json` | JSON object with `kind` discriminator | typed-card renderer (table below) |

### 3.1 Typed cards (when `bodyContentType == application/json`)

| `kind` | Card | Where it lands |
|---|---|---|
| `SmeResponse` | SME response card: confidence chip, claim headline, expandable rationale, evidence chips, proposed-action chips | `#<sme-id>` (primary), mirror to `#live-feed` (collapsed) |
| `DissentReport` | Side-by-side pair view; one card per `DissentPair` plus a summary banner | `#dissent` |
| `ActionCard` | Operator InstructionCard with "I did it" / "Skip" (see `03 §4`); shows the documented-limit citation; carried inside a ConfirmationRequest's `actionCardJson` field but ALSO mirrored into `#actions` for context | `#actions` |
| `ToolResult` (for user-visible tools) | result snippet with the tool name and a copy-to-clipboard JSON link | `#actions` |
| `SafetyInterrupt` (WARN tier — HALT is a takeover, not a card) | yellow banner with reason + suggested recover actions chips | top of every channel (sticky 10s) |
| `EvidenceRef` (rare standalone) | thumbnail or external-link chip | wherever requested |
| `MergedOpinion` (from `MergeOpinion` node) | headline + supportingSmes chips + openQuestions list | `#actions` |
| `SnapshotAnalysis` (from `SnapshotAnalyzer`, `00 §4.2`) | hi-res frame thumbnail + strong-model analysis (markdown) + citation chips; tapping the thumbnail opens the full image | `#live-feed` (mirror to the relevant SME channel if a summon used it) |

Unknown `kind` → render as collapsed JSON with a "rendering not supported" notice. Forward-compatible.

### 3.2 Streaming deltas

Messages with `streaming=true` are placeholders. The body field starts with whatever content is available at emission time (often empty). Subsequent `ChannelUpdate(messageId, deltaText, done)` events append to the body until `done=true`.

Renderer expectations:
- Live token append, no flicker.
- A subtle "typing" indicator near the author until `done=true`.
- If `done` never arrives within 60s, mark the message as "truncated" with a small icon; do not append further.

---

## 4. @-mention parsing

Server-side, but documented here so the client renders mentions consistently.

Tokens recognized:
- `@<sme-id>` for any SME in the roster
- `@user`
- `@live`
- `#<channel-id>` for cross-references
- `>>msgId` for inline reply quoting (already covered by `replyToId` field, but inline forms exist in user text)

Parsing rules:
- Strict prefix match against the known SME roster and channel list at parse time.
- Unmatched `@foo` is treated as literal text, NOT a mention.
- Mentions in user-authored ChatMessages are extracted by the orchestrator and copied into `ChatMessage.mentions` for downstream routing (SupervisorRouter uses these as hard hints).

Renderer:
- @-mention tokens render as colored chips with the SME's avatar / icon.
- Tapping a mention opens that SME's channel.
- Tapping `#channel` switches to that channel.

---

## 5. Real-time streaming semantics

Sequencing guarantees:
- Per-channel, messages are delivered in monotonic `ts` order.
- Across channels, no global ordering — clients may render cross-channel out of order, but within a channel, ordering is preserved.
- `ChannelUpdate(messageId)` is delivered after the originating `ChatMessage(messageId)`. If a client receives an update for an unknown messageId (e.g. reconnect mid-stream), it buffers up to 2 seconds awaiting the parent then drops.

Backpressure:
- Server-side bounded queue per WS at 256 events. On overflow, server drops `ChannelUpdate`s preferentially over `ChatMessage`s and emits a `BackpressureNotice` event so the client can show a degraded-stream banner.

Idempotency:
- All ChatMessage `messageId`s are ULIDs. The client deduplicates by `messageId` (important during replay).

Heartbeat:
- Server sends a `Ping` envelope every 20s. Client replies with `Pong`. Missing 2 pings → client treats connection as dead, schedules reconnect with exponential backoff.

```python
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
```

---

## 6. Reconnect, replay, backfill

On reconnect with the same `sessionId`:

1. Client sends `Hello(sessionId, protocolVersion="2.0")`.
2. If `replayFrom=<checkpointId>` was in the URL query, server starts emitting from that checkpoint forward.
3. Otherwise, server emits:
   - `ChannelList` (current)
   - last N=200 `ChatMessage`s across all channels, in `ts` order
   - any active `pendingConfirmations` as fresh `ConfirmationRequest` events (so the user can still approve/deny)
4. Server emits `ReplayDone(ts=<resumeTs>)`; client renders normally afterward.

```python
class ReplayDone(BaseModel):
    kind: Literal["ReplayDone"] = "ReplayDone"
    resumeTs: int
    checkpointId: str | None = None
```

During replay, the client suppresses notifications (sound/vibrate). After `ReplayDone`, new events trigger normal notifications.

---

## 7. Client → server events

Client may send:
- `Hello` (once at open)
- `Goodbye` (once at close)
- `ChatMessage(authorKind=USER)` — typed user input
- `ConfirmationResponse` — user tapping "I did it" / "Skip" on an InstructionCard
- `Subscribe(channelId)` / `Unsubscribe(channelId)` — for muting (server still ships ALL messages; mute is a client preference but server logs it for ranking)
- `Pong`

```python
class Subscribe(BaseModel):
    kind: Literal["Subscribe"] = "Subscribe"
    channelId: str
class Unsubscribe(BaseModel):
    kind: Literal["Unsubscribe"] = "Unsubscribe"
    channelId: str
```

User-authored `ChatMessage`s:
- `messageId` MUST be a client-generated ULID.
- `channelId` defaults to `#general` if omitted.
- @-mentions are parsed server-side and re-populated in `mentions` before broadcast (client's `mentions` is treated as advisory).
- The server echoes the message back so all clients (multi-device) stay in sync; the originating client must dedupe by `messageId`.

---

## 8. Error envelopes

Server-side validation failures or routing problems:

```python
class ErrorEvent(BaseModel):
    kind: Literal["ErrorEvent"] = "ErrorEvent"
    code: Literal[
        "invalid_event", "unknown_channel", "auth_failed",
        "rate_limited", "protocol_mismatch", "internal_error"
    ]
    message: str
    causedByMessageId: str | None = None
    ts: int
```

`auth_failed` and `protocol_mismatch` are followed immediately by `Goodbye` and connection close.
Rate limits: 30 user messages/minute, 5 reconnect attempts/minute. Excess triggers `rate_limited` and a 30s cooldown.

---

## 9. Bandwidth profile

Empirically expected steady-state for a 5-SME deliberation:
- ~20 `ChannelUpdate` events/sec at peak streaming
- ~1–2 `ChatMessage` events/sec (final messages)
- ~50–200 bytes/event on the wire (JSON, compressed via WS permessage-deflate)

Target: < 50 kB/s sustained per session. Well within phone-LTE budget.

---

## 10. UI rendering hints (non-normative)

The server emits these to nudge the client; clients MAY ignore.

```python
class ChannelHint(BaseModel):
    kind: Literal["ChannelHint"] = "ChannelHint"
    channelId: str
    hint: Literal["focus", "flash", "demote", "collapse"]
    reason: str
```

Examples:
- `#dissent` hint=`focus` when a new DissentReport lands (so the user knows to look).
- `#power` hint=`flash` when @power emits a HIGH-confidence claim.
- A muted channel hint=`demote` after 30s of activity (so client knows it's not just unread, it's intentionally low-priority).

Out of scope for v2: emoji reactions, file uploads from client, voice notes (Live is already the voice channel).

---

## 11. Versioning & extension

- The chat bus speaks `forge.chat.v2`. v1 had no chat bus, so this is a new protocol; no backcompat required.
- Adding new `ChatMessage` `bodyContentType`s is a minor bump (clients fall back to JSON view).
- Adding new top-level kinds (e.g. `Ping`, `ChannelHint`) is a minor bump.
- Removing or renaming any of the kinds in this doc is a major bump.

---

## 12. Open questions for lead engineers

- Should `#dissent` and `#actions` be merged into a single `#deliberation` channel? Argument for: less channel-switching during a 3-minute demo. Argument against: dissent is the load-bearing showpiece for the prize criterion.
- Should the client get a per-message "react" affordance (👍/👎) that feeds back into MergeOpinion's confidence? Punted — v2.5.
- Replay window of N=200: enough for a 3-minute demo, maybe not for a 30-minute session. Consider checkpoint-anchored replay (which we have) as the primary path and `N=200` as the fallback.

---

## 13. Test cases (component-level — chat-bus framing)

Run: `pytest orchestrator/chat_bus/tests/`. Driven with an in-memory WS pair (no network); the server side is the real `ws.py`, the client side is a test harness that records events.

**Design patterns under test:** pub/sub fan-out with bounded queues, idempotent ULID dedup, streaming-delta assembly, forward-compatible rendering.

| ID | Test | Pass criterion |
|---|---|---|
| CB-1 | On connect, server emits `ChannelList` whose `#<sme>` channels equal the roster in `02`/`07` (and contain no `#bench-tech`) | sets equal |
| CB-2 | Streaming: `ChatMessage(streaming=true)` then 5 `ChannelUpdate`s then `done=true` → client reconstructs the full body in order | body matches |
| CB-3 | `ChannelUpdate` for an unknown messageId → buffered ≤2 s then dropped, no crash | buffered/dropped |
| CB-4 | Idempotency: replay re-sends a message with the same ULID → client renders once | dedup holds |
| CB-5 | Backpressure: flood >256 events → server drops `ChannelUpdate` before `ChatMessage` and emits `BackpressureNotice` | priority preserved |
| CB-6 | InstructionCard round-trip: a `ConfirmationRequest` with `actionCardJson` → client parses `ActionCard`, renders "I did it"/"Skip", sends `ConfirmationResponse(approved=True)` on affirm | labels + response correct |
| CB-7 | Reconnect with same `sessionId` → `ChannelList` + last N=200 messages + any pending `ConfirmationRequest` re-emitted + `ReplayDone` | replay contract holds |
| CB-8 | Unknown `kind` in an `application/json` body → renders collapsed-JSON fallback, no exception | forward-compatible |
| CB-9 | Auth/version: `Hello` with mismatched major `protocolVersion` → `ErrorEvent("protocol_mismatch")` then `Goodbye` + close | rejected cleanly |
| CB-10 | Heartbeat: server `Ping` every 20 s; missing 2 `Pong`s → server marks WS dead | liveness works |
| CB-11 | `SnapshotAnalysis` card: a `ChatMessage(application/json, body=SnapshotAnalysis)` → client parses it, renders the `FrameRef` thumbnail + analysis + cites; lands in `#live-feed` | card renders, no binary on the bus |

CB-6, CB-7, and CB-11 are the seams reused by the system-level UI-contract + snapshot tests (`08 §3.3`, `08 §3.5`).

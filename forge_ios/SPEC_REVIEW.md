# Forge iOS — Spec Cross-Check Review

> Reviewer: senior cross-check against the canonical v2 specs in `../specs/` and `../ARCHITECTURE.md`.
> Subject: the native SwiftUI/ARKit client under `forge_ios/Forge/`, built to its OWN spec (`forge_ios/IMPLEMENTATION.md` + `README.md`), which mirrors the **v1** wire protocol (`forge_orchestrator/proto/events.py`).
> Scope: review only. No Swift files were modified.

---

## 1. Summary

The iOS app is a clean, internally-consistent implementation of the **v1** Forge wire protocol. It compiles, follows its own frozen `AgentProto.swift` faithfully, and its decoder is genuinely forward-compatible (it drops unknown `kind`s instead of crashing). However, measured against the **v2** specs it is a different protocol generation: it is missing all seven v2 `AgentEvent` additions, the entire multi-channel chat-bus surface (channels, `ChatMessage`, streaming deltas, dissent, SME responses), the safety-gate richness (risk-tier UX, ActionCard, `SafetyInterrupt`, `approverChannel`), and two hard v2 connection requirements: `Hello.protocolVersion` (REQUIRED; not sent) and subprotocol-header auth (`Sec-WebSocket-Protocol: forge.chat.v2, bearer.<token>` — the client instead uses an `Authorization: Bearer` header on a `/v1/session` URL). **As built it would fail to connect to a conformant v2 orchestrator** (the orchestrator rejects clients with no/mismatched `protocolVersion` via `Goodbye("protocol_mismatch")`, and reads the token from the subprotocol). Almost every gap is "v1-by-design" rather than a coding bug — but the headline consequence is that this client cannot drive the v2 demo in `06_demo_script.md` (no parallel-channel deliberation, no dissent moment, no real ActionCard) without a protocol uplift.

---

## 2. Severity-ranked findings table

| # | Finding | Spec ref | Code ref | Severity | v1-vs-v2 by design? |
|---|---|---|---|---|---|
| F1 | `Hello.protocolVersion` not sent (REQUIRED in v2; orchestrator rejects mismatch) | `00 §2.1`, `00 §7`, `04 §6.1` | `AgentProto.swift:19`, `AgentProtoCoding.swift:84-88,124-127`, `OrchestratorSocket.swift:120` | Critical | No (omission, breaks connect) |
| F2 | Auth uses `Authorization: Bearer` header, not the WS `Sec-WebSocket-Protocol` subprotocol | `00 §8`, `04 §1`, `05 §1` | `OrchestratorSocket.swift:109-111` | Critical | No (breaks v2 auth) |
| F3 | Connect URL is `/v1/session`, not `/v2/chat`; no `sessionId`/`client` query params | `04 §1` | `ConfigStore.swift:17,30`, `README.md:48,52` | High | Partial (v1 endpoint by design; query-param shape is new) |
| F4 | Entire v2 chat-bus model absent: channels, `ChannelList`, `ChatMessage` wire type, `authorKind`, `mentions`, `replyToId`, `bodyContentType`, typed JSON cards | `04 §2-§4`, `00 §2.1` | `ChatStore.swift:17-30`, `ExpertChatPanel.swift`, `AgentProto.swift:12-21` | High | Yes (v1 had no chat bus) |
| F5 | Streaming deltas (`ChannelUpdate` append-to-message, "typing", 60s truncation) unmodeled | `04 §3.2`, `00 §2.1` | (absent) `AgentProto.swift`, `ChatStore.swift` | High | Yes |
| F6 | `SafetyInterrupt` (WARN/HALT, sentinel pre-empt, full-screen takeover) not handled at all | `00 §2.1`, `03 §5`, `03 §4`, `01 §3.8`, `ARCHITECTURE.md §7` | (absent) | High | Yes |
| F7 | Confirmation flow missing `ActionCard` (title/bodyMarkdown/diffMarkdown/affirm-deny labels), `invokerSmeId`, risk-tier UX | `00 §2.1`, `03 §1-§4` | `ConfirmationSheet.swift:1-108`, `AgentProto.swift:16` | High | Yes |
| F8 | `ConfirmationResponse.approverChannel` ("voice"/"chat") not sent | `00 §2.1`, `03 §2` | `AgentProto.swift:17`, `AgentProtoCoding.swift:74-78,116-119`, `SessionViewModel.swift:351-359` | Medium | No (field omission in a shared event) |
| F9 | HIGH-risk UX rule (3s affirm-disabled countdown) not implemented | `03 §1`, `03 §4` | `ConfirmationSheet.swift:63-87` | Medium | Yes |
| F10 | `Transcript.speaker` / `.smeId` not modeled; all transcripts attributed to one author "forge" | `00 §2.1`, `01 §3.8` | `AgentProto.swift:13`, `AgentProtoCoding.swift:51-56`, `SessionViewModel.swift:227-229` | Medium | Yes |
| F11 | `ToolResult.deferred` (async function-call hedge) not modeled | `00 §2.1`, `00 §3` | `AgentProto.swift:15`, `AgentProtoCoding.swift:63-67` | Medium | Yes |
| F12 | `SmeResponse` / `DissentReport` / `MergedOpinion` types + cards absent — no dissent surface (the prize showpiece) | `00 §2.1`, `04 §3.1`, `01 §3.5-3.6`, `06 §3` | (absent) | High | Yes |
| F13 | `SummonGuild` / `CheckpointMarker` not modeled | `00 §2.1`, `01 §3.1,3.3` | (absent) | Low | Yes |
| F14 | Reconnect/replay: no `replayFrom`, `ReplayDone`, dedup-by-`messageId`, pending-confirmation re-surface; `ReplayBuffer` is local-only | `04 §5-§6`, `01 §5`, `06 §2 step 8`, `06 §5.4` | `ReplayBuffer.swift` (recorded but never replayed), `OrchestratorSocket.swift:103-137` | Medium | Partial (v1 replay differs; v2 shape new) |
| F15 | Heartbeat `Ping`/`Pong` + `BackpressureNotice` not handled (2-missed-ping reconnect) | `04 §5` | `OrchestratorSocket.swift:140-165` | Medium | Yes |
| F16 | `ErrorEvent` (`auth_failed`, `protocol_mismatch`, `rate_limited`, …) not handled | `04 §8` | (absent) | Medium | Yes |
| F17 | Client→server `ChatMessage` (typed user input), `Subscribe`/`Unsubscribe` (mute) not sendable | `04 §7` | `AgentProto.swift`, `SessionViewModel.swift:331-364` | Medium | Yes |
| F18 | `META` binary-frame magic (frame-aligned annotations, e.g. tap-to-zoom) not produced; only `FRAM`/`AUDI` | `00 §4` | `OrchestratorSocket.swift:77-99,170-197` | Low | Yes (META is new v2) |
| F19 | Discriminator value casing is PascalCase ("Transcript") — CORRECT, matches v2 `Literal` values | `00 §2.1` | `AgentProtoCoding.swift:19-28` | — (aligned) | — |

---

## 3. Detailed findings

### F1 — `Hello.protocolVersion` is REQUIRED in v2 and is never sent (Critical)

Spec: `00 §2.1` defines `Hello.protocolVersion: str = "2.0"` with the comment "**NEW in v2: clients MUST send**". `00 §7`: "`protocolVersion` in `Hello` is the source of truth. Orchestrator rejects (`Goodbye("protocol_mismatch")`) any client whose major version differs." `04 §6.1` reconnect: "Client sends `Hello(sessionId, protocolVersion="2.0")`."

Code: the Swift `Hello` case carries only `client` and `sessionId`:
- `AgentProto.swift:19` — `case hello(client: String, sessionId: String)`
- `AgentProtoCoding.swift:124-127` encodes only `kind`, `client`, `sessionId`.
- `OrchestratorSocket.swift:120` — `let hello = AgentEvent.hello(client: "ios", sessionId: sessionId)`.

Impact: a conformant v2 orchestrator sees no `protocolVersion`, treats it as a major mismatch, and closes the connection with `Goodbye("protocol_mismatch")`. This is the single most consequential gap because it blocks the connection before anything else can be demoed.

Recommendation: add `protocolVersion` to the `hello` case (default `"2.0"`), encode it, and set it in `OrchestratorSocket`. This is a small, surgical change despite the file being marked frozen.

### F2 — Auth token in the wrong place (Critical)

Spec: `00 §8`: "`Hello` carries no token; tokens travel in the WS `Sec-WebSocket-Protocol` subprotocol header." `04 §1`: `Sec-WebSocket-Protocol: forge.chat.v2, bearer.<token>` and "Server-selected subprotocol is `forge.chat.v2`."

Code: `OrchestratorSocket.swift:109-111`:
```swift
request.setValue("Bearer \(authToken)", forHTTPHeaderField: "Authorization")
request.setValue(sessionId, forHTTPHeaderField: "X-Session-Id")
```
The token rides an `Authorization` header and the session id rides a bespoke `X-Session-Id` header — neither is the v2 contract.

Impact: a v2 orchestrator looks for the bearer token in the subprotocol list and selects `forge.chat.v2`. With the token absent there, the handshake yields `auth_failed` → `Goodbye` (`04 §8`). Note this matches the app's own `IMPLEMENTATION.md` (shared-secret + Bearer is the v1 plan), so it is faithful to v1 but wrong for v2.

Recommendation: set the WS subprotocols to `["forge.chat.v2", "bearer.\(authToken)"]` (URLSession supports `URLSessionWebSocketTask` subprotocols via the request or the `Sec-WebSocket-Protocol` header), move `sessionId` into the URL query, and drop the `Authorization`/`X-Session-Id` headers.

### F3 — Endpoint path and query shape (High)

Spec: `04 §1`: `GET wss://orchestrator.forge.ai/v2/chat ?sessionId=<ulid> &client=phone|quest|web &replayFrom=<checkpointId>?`.

Code: `ConfigStore.swift:17,30` and `README.md:48,52` hardcode `…/v1/session`. No `sessionId`/`client`/`replayFrom` query params are appended anywhere; `sessionId` is sent as a header (see F2).

Impact: wrong route and missing connection metadata for v2. The `/v1/session` choice is consistent with the app's v1 design, but v2 split the chat bus (channel A, `/v2/chat`) from the Live audio/video channel (channel B), which this single-socket design does not reflect (see also F4 architecture note).

Recommendation: when uplifting, point at `/v2/chat`, add the query params, and decide whether to model the separate Live channel (B) or keep audio/frames multiplexed (the v2 chat bus "carries no binary frames" per `04 §1`, so the current single-socket binary `FRAM`/`AUDI` design conflicts with the v2 channel split).

### F4 — The entire v2 chat-bus model is absent (High, v1-by-design)

Spec: `04` defines a Discord-style multi-channel bus: server-defined channels (`#live-feed`, `#user`, `#actions`, `#dissent`, `#scribe`, `#sentinel`, `#<sme-id>`, `#general`), a `ChannelList`/`ChannelInfo` envelope at connect (`04 §2`), the `ChatMessage` wire type with `channelId`, `authorId`, `authorKind` (user/live/sme/system), `body`, `bodyContentType` (markdown/json/code), `mentions`, `replyToId`, `messageId` (ULID), `streaming` (`00 §2.1`), and a typed-card renderer matrix (`04 §3.1`). `ARCHITECTURE.md §5` shows the wireframe with a per-channel left rail.

Code: the client models a fundamentally different, simpler thing — a per-component thread map:
- `ChatStore.swift:17-30` — `var threads: [String: [ChatMessage]]` keyed by component id ("" = global).
- `ChatMessage` (`ChatStore.swift:4-9`) is a LOCAL UI struct: `{id: UUID, author: ChatAuthor (.user/.system/.agent(name)), text, ts}` — it is NOT the wire `ChatMessage` and has no `channelId`, `authorKind`, `mentions`, `messageId`, `bodyContentType`.
- `ExpertChatPanel.swift` renders sections keyed by component id (`accentForKey` switches on "U"/"R"/"C"…), i.e. per-PCB-component threads, not per-SME/system channels.
- The only thing populating chat is `SessionViewModel.swift:227-229`, which turns every `Transcript` into a single `.agent(name: "forge")` message on the global thread.

Impact: the v2 "money shot" (parallel SME channels streaming token deltas, `06 §3` at 0:00–0:45) cannot render. There is no concept of channels at all.

Recommendation: this is the largest single uplift. Add the wire `ChatMessage`/`ChannelList`/`ChannelInfo`/`ChannelUpdate` types, re-key `ChatStore` by `channelId`, and rebuild `ExpertChatPanel` around channels with the typed-card renderer. The app's own `IMPLEMENTATION.md §"Open questions" #5` already anticipates this ("Requires a wire-protocol bump (Phase 0.5)").

### F5 — Streaming deltas unmodeled (High, v1-by-design)

Spec: `04 §3.2`: messages with `streaming=true` are placeholders; subsequent `ChannelUpdate(messageId, deltaText, done)` append until `done`; renderer must token-append without flicker, show a typing indicator, and mark "truncated" if `done` never arrives in 60s. `01 §3.3-3.4` emits these as the primary live deliberation surface.

Code: no `ChannelUpdate` case in `AgentEvent` (`AgentProto.swift:12-21`); `ChatStore.append` only ever appends whole messages. There is no message-id keyed buffer to append deltas into.

Recommendation: add `ChannelUpdate` to the union and a `messageId`-indexed mutable store; implement the 2s orphan-buffer (`04 §5`) and 60s truncation.

### F6 — `SafetyInterrupt` not handled at all (High, v1-by-design)

Spec: `00 §2.1` `SafetyInterrupt{severity: WARN|HALT, reason, suggestedRecoverActions, ts}`. `03 §5`: sentinel pre-empts the voice channel; HALT is a full-screen takeover (`03 §4`, `ARCHITECTURE.md §7`); WARN is a sticky 10s top-of-channel banner (`04 §3.1`). `06 §2.30` (`@sentinel` cameo) and `06 §5.6` (magic smoke) depend on it.

Code: no `SafetyInterrupt` case anywhere; the decoder drops it as an unknown kind (`OrchestratorSocket.swift:150-152`). There is a `DegradedStatusPanel` for connection loss, but nothing for safety interrupts.

Impact: the always-on safety surface — a load-bearing demo beat and a judge Q&A talking point (`06 §6`) — is invisible. The HALT full-screen takeover does not exist.

Recommendation: add `SafetyInterrupt`; render WARN as a sticky banner and HALT as a full-screen modal that blocks pending action cards until acknowledged.

### F7 — Confirmation flow lacks the v2 ActionCard richness (High, v1-by-design)

Spec: `00 §2.1` `ConfirmationRequest` adds `invokerSmeId` and `actionCardJson` (an `ActionCard{title, bodyMarkdown, diffMarkdown, risk, affirmLabel, denyLabel}`). `03 §4`: render `diffMarkdown` as a 2-column Current/Proposed table; show the invoker's avatar prominently ("users should never wonder which SME asked for this"); risk pill colors LOW/MEDIUM/HIGH/HALT.

Code: `AgentProto.swift:16` — `confirmationRequest(callId, summary, risk)` only. `ConfirmationSheet.swift` renders a plain `summary` string with a risk label/icon and Approve/Deny — no card, no diff table, no invoker identity, no custom affirm/deny labels.

Recommendation: extend `ConfirmationRequest` with `invokerSmeId`/`actionCardJson`, add an `ActionCard` payload struct (parallel to `ToolResults.swift`), and render the diff table + invoker avatar.

### F8 — `ConfirmationResponse.approverChannel` not sent (Medium, true omission)

Spec: `00 §2.1` `ConfirmationResponse` gains `approverChannel: "voice"|"chat" = "voice"`. `03 §2`: "Whichever channel (voice or chat) responds first wins. The losing channel's UI updates to 'decided via <channel>'."

Code: `AgentProto.swift:17` and `AgentProtoCoding.swift:74-78,116-119` model only `{callId, approved}`. `SessionViewModel.swift:351-359` sends `confirmationResponse(callId:approved:)` from the tap (which is the "chat" path) with no channel tag.

Impact: even within v1 framing this drops information the v2 orchestrator audit record expects (`03 §7` logs `approverChannel`). Since the iOS approve button is the chat path, it should send `approverChannel="chat"`.

Recommendation: add `approverChannel` to the case and default the tap path to `.chat`.

### F9 — HIGH-risk 3s affirm-delay missing (Medium, v1-by-design)

Spec: `03 §1` HIGH: "3s delay before button enables"; `03 §4`: "affirm button DISABLED for 3 seconds (countdown shown). Forces user to read."

Code: `ConfirmationSheet.swift:63-87` — both buttons are always enabled regardless of `risk`. No countdown.

Recommendation: when `risk == .high`, disable Approve for 3s with a visible countdown.

### F10 — `Transcript.speaker`/`.smeId` unmodeled; everything is "forge" (Medium, v1-by-design)

Spec: `00 §2.1` `Transcript` adds `speaker: "user"|"live"|"sme"` and `smeId`. `01 §3.8`: LiveSpeaker mirrors spoken text as `Transcript(speaker="live", partial=False)`.

Code: `AgentProto.swift:13` / `AgentProtoCoding.swift:51-56` model `{text, partial, ts}`. `SessionViewModel.swift:227-229` attributes every transcript to `.agent(name: "forge")` on the global thread — so user vs Live vs SME speech is indistinguishable.

Recommendation: add `speaker`/`smeId`; route by speaker (user→`#user`, live→`#live-feed`, sme→`#<smeId>`).

### F11 — `ToolResult.deferred` unmodeled (Medium, v1-by-design)

Spec: `00 §2.1` adds `deferred: bool = False`; `00 §3` explains the async-function-call hedge ("client renderers don't have to switch"). A deferred `ToolResult` carries `{"jobId": ...}` and the real result lands later.

Code: `AgentProto.swift:15` / `AgentProtoCoding.swift:63-67` model `{callId, resultJson}` only. `SessionViewModel.swift:223-225` assumes every `ToolResult` is a `LookAtBenchResult` and silently returns if it does not parse — a deferred placeholder (`{"jobId":...}`) is dropped with no "consulting…" affordance.

Recommendation: add `deferred`; when true, render a pending/"consulting the guild" state keyed by `callId`.

### F12 — No SmeResponse / DissentReport / MergedOpinion surface (High, v1-by-design)

Spec: `00 §2.1` defines `SmeResponse` (confidence, claim, rationale, evidence, proposedActions, dissentsWith), `DissentReport` (parties, axis, pairwise `DissentPair`s), and `04 §3.1` maps them to typed cards in `#<sme-id>`/`#dissent`/`#actions`. `MergedOpinion` (`01 §3.5`, `04 §3.1`) lands in `#actions`. The dissent moment is explicitly the prize showpiece (`04 §12`, `06 §3` at 0:15–1:15, `06 §6`).

Code: none of these types exist; no `#dissent` view; `ARCHITECTURE.md §5`'s split dissent view (the side-by-side `@power` vs `@librarian` card) is unbuilt.

Recommendation: add the types and the dissent split-card renderer; this is the highest-value demo feature after the chat bus itself.

### F13 — `SummonGuild` / `CheckpointMarker` unmodeled (Low, v1-by-design)

Spec: `SummonGuild` (`00 §2.1`, routing key, smes, deadline) and `CheckpointMarker` (`01 §3.1`) are emitted to the client. The client mostly observes these for affordances ("Consulting the guild on <topic>…", `06 §3` at 0:00) and replay anchoring.

Code: absent; dropped as unknown kinds. Low because they are informational; the "consulting" string could also be synthesized from a deferred `ToolResult` (F11).

### F14 — Reconnect/replay is local-only; v2 replay protocol unimplemented (Medium)

Spec: `04 §5-§6` and `01 §5`: reconnect with the same `sessionId` triggers server replay — `ChannelList`, last N=200 `ChatMessage`s in `ts` order, pending `ConfirmationRequest`s re-emitted, then a `ReplayDone(resumeTs, checkpointId)`; notifications suppressed during replay; client dedups by `messageId`. `06 §2 step 8` and `06 §5.4` make replay a demoed feature.

Code: `OrchestratorSocket.swift:103-137` reconnects the transport with backoff but sends a fresh `hello` with no `replayFrom`/replay handling; there is no `ReplayDone` case and no `messageId` dedup. `ReplayBuffer.swift` records frames/events locally (`SessionViewModel.swift:161,221`) but `snapshot()` is never consumed — it is dead state with respect to server replay.

Recommendation: on reconnect, pass `replayFrom`/same `sessionId`, suppress notifications until `ReplayDone`, and dedup by `messageId`.

### F15 — No heartbeat (Ping/Pong) or BackpressureNotice (Medium, v1-by-design)

Spec: `04 §5`: server `Ping` every 20s, client `Pong`; 2 missed pings → treat connection dead and reconnect with backoff. `BackpressureNotice{dropped, sinceTs}` → degraded-stream banner.

Code: `OrchestratorSocket.swift:140-165` has no ping/pong; liveness relies solely on `receive()` throwing. `BackpressureNotice` is dropped as unknown.

Recommendation: handle `Ping`→`Pong`, add a missed-ping watchdog, and surface `BackpressureNotice` in `DegradedStatusPanel`.

### F16 — `ErrorEvent` unhandled (Medium, v1-by-design)

Spec: `04 §8`: `ErrorEvent{code, message, causedByMessageId, ts}` with codes incl. `auth_failed`, `protocol_mismatch`, `rate_limited`; `auth_failed`/`protocol_mismatch` are followed by `Goodbye` + close.

Code: no `ErrorEvent` case; dropped as unknown kind. So an auth/protocol failure (which is exactly what F1/F2 would trigger) produces no user-visible diagnostic — the app just silently degrades into stub mode (`SessionViewModel.swift:206-218`).

Recommendation: add `ErrorEvent`; surface `auth_failed`/`protocol_mismatch` as explicit error banners rather than generic "orchestrator unreachable".

### F17 — Client cannot send typed chat or mute channels (Medium, v1-by-design)

Spec: `04 §7`: client may send `ChatMessage(authorKind=USER)` (typed input, client-ULID `messageId`, defaults `channelId=#general`), `Subscribe`/`Unsubscribe` (mute), `Pong`, `Hello`, `Goodbye`, `ConfirmationResponse`.

Code: the client can only send the v1 set (`hello`, `goodbye`, `toolCall`, `confirmationResponse`, plus binary frames). `SessionViewModel.swift:331-364` shows the only client-originated events; there is no text-input path (`06 §5.2` fallback "typed input via `#general`" is impossible) and no mute.

Recommendation: add a client `ChatMessage` send path and `Subscribe`/`Unsubscribe`.

### F18 — `META` binary frame magic not produced (Low, v1-by-design)

Spec: `00 §4` adds a third binary magic `META` (UTF-8 JSON sidecar, first key `kind`, ≤8KB) "used by clients that want to send frame-aligned annotations (e.g. tap-to-zoom-here coordinates)."

Code: `OrchestratorSocket.swift:77-99,170-197` builds only `FRAM`/`AUDI`. The data-flow step in `IMPLEMENTATION.md §"Data flow per frame" #6` (tap → `expert_chat.focus` toolCall) is implemented via a JSON `ToolCall` instead, which is a reasonable substitute but not the v2 `META` mechanism.

Recommendation: optional — add `META` if frame-aligned tap coordinates are desired; otherwise the toolCall path is acceptable.

### F19 — Discriminator casing is CORRECT (aligned)

`AgentProtoCoding.swift:19-28` uses PascalCase kind values (`"Transcript"`, `"ToolCall"`, `"ConfirmationRequest"`, …) keyed under `"kind"`, exactly matching the v2 `Literal["Transcript"]` discriminators in `00 §2.1`. Field names are camelCase, also matching. No casing bug.

---

## 4. What's correctly aligned (credit where due)

- **Forward-compatible decoder (genuinely correct).** `OrchestratorSocket.swift:147-153` catches `AgentEventDecodingError` and *drops* unknown kinds instead of crashing; `AgentProtoCoding.swift:42-49` throws cleanly on unknown/`missing` kind. This satisfies the v2 forward-compat intent (`04 §3.1` "Unknown kind → render as collapsed JSON… Forward-compatible"; `00 §7` additive evolution). The app will not crash when a v2 orchestrator sends `ChatMessage`/`SummonGuild`/etc. — it just ignores them. (Caveat: "drop silently" is safer than crashing but less than the spec's "collapsed JSON with a notice"; for a chat client, silent drop means invisible content.)
- **Discriminator key + casing** match v2 exactly (F19).
- **All v1 carryover events are field-accurate.** `Transcript`, `ToolCall`, `ToolResult`, `ConfirmationRequest`, `ConfirmationResponse`, `AudioChunk`, `Hello`, `Goodbye` carry exactly their v1 fields (`00 §2.1` confirms v1 fields are preserved by name). The v2 deltas are purely additive, so the existing fields are correct as far as they go.
- **Binary framing header** (`OrchestratorSocket.swift:170-197`) matches `00 §4` byte layout: 4-byte ASCII magic, Int32-LE width, Int32-LE height, Int64-LE timestamp ns, then payload. `FRAM`/`AUDI` and the 16kHz-in/24kHz-out PCM contract (`IMPLEMENTATION.md` Task C) match `00 §4`.
- **Tolerant tool-result decoding.** `ToolResults.swift` and `DetectedComponent` (`AgentProto.swift:97-105`) use `decodeIfPresent` with sane defaults, which is robust to additive server changes.
- **Stub mode** (`SessionViewModel.swift:368-445`) mirrors the orchestrator's zero-config stub philosophy (`07 §2.4`, `06 §5.1`) — the demo loop survives an orchestrator outage, which is in the spirit of `06 §5`.
- **Risk enum casing** (`AgentProto.swift:23-27`) uses `"LOW"/"MEDIUM"/"HIGH"`, matching the wire `Literal` values.

---

## 5. Recommended next steps (ordered)

1. **Send `protocolVersion="2.0"` in `Hello` (F1).** Without this, nothing else matters — the v2 orchestrator closes the connection. Smallest change, highest leverage.
2. **Move auth into the WS subprotocol and switch the endpoint (F2, F3).** Set subprotocols `["forge.chat.v2", "bearer.<token>"]`, point at `/v2/chat?sessionId=…&client=phone`, drop the `Authorization`/`X-Session-Id` headers. After steps 1-2 the client can actually connect to v2.
3. **Handle `ErrorEvent` + `Goodbye("protocol_mismatch")/("auth_failed")` (F16).** So that, while uplifting, failures are diagnosable instead of silently degrading to stub mode.
4. **Add the v2 chat-bus core: `ChatMessage` (wire), `ChannelList`/`ChannelInfo`, `ChannelUpdate` (F4, F5).** Re-key `ChatStore` by `channelId`; rebuild `ExpertChatPanel` as channels with the typed-card renderer and streaming-delta append. This unlocks the `06` demo's parallel-deliberation money shot.
5. **Add `SmeResponse` + `DissentReport` typed cards and the `#dissent` split view (F12).** The dissent moment is the prize showpiece.
6. **Add `SafetyInterrupt` (WARN banner + HALT full-screen takeover) (F6).** Restores the sentinel demo beat and safety story.
7. **Enrich the confirmation flow: `ActionCard` (diff table + invoker avatar), `invokerSmeId`, HIGH-risk 3s countdown, and send `approverChannel="chat"` (F7, F8, F9).**
8. **Add `Transcript.speaker/smeId` and `ToolResult.deferred` (F10, F11)** so transcripts route to the right channel and deferred guild calls render a "consulting…" state.
9. **Implement v2 reconnect/replay + heartbeat (F14, F15):** `replayFrom`, `ReplayDone`, `messageId` dedup, notification suppression during replay, `Ping`/`Pong` watchdog, `BackpressureNotice` banner.
10. **Add client→server typed `ChatMessage` and `Subscribe`/`Unsubscribe` (F17);** optionally `META` frames (F18).

> Steps 1-3 are surgical and unblock connectivity. Steps 4-7 are the substantive v2 uplift and where the demo value lives. The app's own `IMPLEMENTATION.md` already flags this as a needed "wire-protocol bump (Phase 0.5)" — this review quantifies exactly which types and fields that bump must add.

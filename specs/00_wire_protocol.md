# 00 — Wire Protocol (frozen contracts)

> Frozen vocabulary for every cross-process message in Forge.
> Lineage: extends `forge_orchestrator/proto/events.py` and `forge_quest/proto/AgentProto.kt` from v1. New event types are additive — every v1 event still exists and still carries the same fields.
> Cross-refs: `01_langgraph_state_machine.md` (consumers), `04_chat_bus_protocol.md` (client surface), `05_bench_daemon_api.md` (RPC subset).

---

## 1. Process map and channels

```
                ┌───────────────────────┐
                │   Phone / Quest UI    │      Discord-style chat client
                │   (Kotlin / Compose)  │
                └─────────┬─────────────┘
                          │ (A) ChatBus WS — JSON only — see 04
                          │ (B) Live WS    — bidi audio + video — Gemini Live framing
                          ▼
                ┌───────────────────────┐
                │   Orchestrator        │      LangGraph; one graph instance per session
                │   (Python 3.12)       │
                └──┬───────┬────────┬───┘
                   │       │        │
                   │       │        │ (C) Managed-Agents REST/SSE — see Spike 1/4
                   │       │        ▼
                   │       │   ┌──────────────────────────┐
                   │       │   │  SME Sandbox × N         │   one env per SME persona
                   │       │   │  (Antigravity preview)   │
                   │       │   └──────────────────────────┘
                   │       │
                   │       │ (D) BenchDaemon JSON-RPC over WS — see 05
                   │       ▼
                   │   ┌──────────────────────────┐
                   │   │  Bench Daemon            │   local Linux box at the bench
                   │   │  (PSU, scope, MCU, …)    │
                   │   └──────────────────────────┘
                   │
                   │ (E) Gemini Live WS — Google's bidi protocol — opaque to us
                   ▼
              Gemini Live
```

Channels A, D are defined here. B is defined by Google. C is defined by Google (we wrap with envelopes — see §6). E is Google's Live protocol verbatim.

---

## 2. AgentEvent envelope (sealed union)

All JSON messages on channels A and (where Live's function-calling shape allows) wrapped function returns on E share the `AgentEvent` discriminated union. New v2 types are appended; v1 types are preserved by name and field.

### 2.1 Pydantic (orchestrator side)

```python
# proto/events.py — additive to v1
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field

# ─── v1 carryover (unchanged) ──────────────────────────────────────────────
class Hello(BaseModel):
    kind: Literal["Hello"] = "Hello"
    client: str                                    # "phone" | "quest" | "test"
    sessionId: str
    protocolVersion: str = "2.0"                   # NEW in v2: clients MUST send

class Goodbye(BaseModel):
    kind: Literal["Goodbye"] = "Goodbye"
    reason: str

class Transcript(BaseModel):
    kind: Literal["Transcript"] = "Transcript"
    text: str
    partial: bool
    ts: int                                        # ns since epoch
    speaker: Literal["user", "live", "sme"] = "user"   # NEW
    smeId: str | None = None                       # NEW; set when speaker == "sme"

class ToolCall(BaseModel):
    kind: Literal["ToolCall"] = "ToolCall"
    name: str
    argsJson: str
    callId: str

class ToolResult(BaseModel):
    kind: Literal["ToolResult"] = "ToolResult"
    callId: str
    resultJson: str
    deferred: bool = False                         # NEW; see §3 async semantics

class ConfirmationRequest(BaseModel):
    kind: Literal["ConfirmationRequest"] = "ConfirmationRequest"
    callId: str
    summary: str
    risk: Literal["LOW", "MEDIUM", "HIGH"]
    invokerSmeId: str | None = None                # NEW
    actionCardJson: str | None = None              # NEW; rich payload — see 04 §3

class ConfirmationResponse(BaseModel):
    kind: Literal["ConfirmationResponse"] = "ConfirmationResponse"
    callId: str
    approved: bool
    approverChannel: Literal["voice", "chat"] = "voice"   # NEW

class AudioChunk(BaseModel):
    kind: Literal["AudioChunk"] = "AudioChunk"
    pcmBase64: str
    ts: int

# ─── v2 additions ──────────────────────────────────────────────────────────
class ChatMessage(BaseModel):
    kind: Literal["ChatMessage"] = "ChatMessage"
    channelId: str                                 # "#power" | "#dissent" | …
    authorId: str                                  # "@power" | "@user" | "@live"
    authorKind: Literal["user", "live", "sme", "system"]
    body: str                                      # markdown
    bodyContentType: Literal["text/markdown", "application/json", "text/code"] = "text/markdown"
    mentions: list[str] = Field(default_factory=list)     # ["@signal", "@firmware"]
    replyToId: str | None = None
    messageId: str                                 # ULID, client-stable
    ts: int
    streaming: bool = False                        # NEW deltas land via ChannelUpdate

class SummonGuild(BaseModel):
    kind: Literal["SummonGuild"] = "SummonGuild"
    callId: str                                    # ties to async function-call ID
    topic: str                                     # routing key
    smes: list[str]                                # ["@power", "@signal"]
    contextRefs: list[str] = Field(default_factory=list)  # frame URIs, prior msgIds
    deadlineMs: int = 30_000

class SmeResponse(BaseModel):
    """Structured envelope every SME emits at end of turn — see 02 §4."""
    kind: Literal["SmeResponse"] = "SmeResponse"
    smeId: str
    callId: str                                    # ties back to SummonGuild
    confidence: float                              # 0.0–1.0
    claim: str                                     # 1-sentence headline
    rationale: str                                 # markdown
    evidence: list[EvidenceRef] = Field(default_factory=list)
    proposedActions: list[ProposedAction] = Field(default_factory=list)
    dissentsWith: list[str] = Field(default_factory=list)   # other smeIds
    ts: int

class EvidenceRef(BaseModel):
    kind: Literal["frame", "scope_capture", "datasheet", "url", "file"]
    uri: str
    note: str | None = None

class ProposedAction(BaseModel):
    tool: str                                      # bench-daemon method or other tool name
    argsJson: str
    rationale: str
    risk: Literal["LOW", "MEDIUM", "HIGH"]

class DissentReport(BaseModel):
    kind: Literal["DissentReport"] = "DissentReport"
    callId: str
    parties: list[str]                             # smeIds that disagree
    axis: str                                      # short label: "root_cause" | "next_action" | …
    summary: str                                   # markdown rendered into #dissent
    pairwise: list[DissentPair]
    ts: int

class DissentPair(BaseModel):
    a: str; b: str                                 # smeIds
    aClaim: str; bClaim: str
    crux: str                                      # the specific point of disagreement

class ChannelUpdate(BaseModel):
    """Streaming delta into an existing ChatMessage (token-by-token)."""
    kind: Literal["ChannelUpdate"] = "ChannelUpdate"
    messageId: str
    deltaText: str
    done: bool = False
    ts: int

class ActionCard(BaseModel):
    """Renderable confirmation card; carried inside ConfirmationRequest.actionCardJson."""
    kind: Literal["ActionCard"] = "ActionCard"
    title: str
    bodyMarkdown: str
    diffMarkdown: str | None = None                # before/after table for set_psu etc.
    risk: Literal["LOW", "MEDIUM", "HIGH"]
    affirmLabel: str = "Approve"
    denyLabel: str = "Hold"

class SafetyInterrupt(BaseModel):
    """@sentinel only. Pre-empts the voice channel — see 03 §5."""
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

AgentEvent = (
    Hello | Goodbye | Transcript | ToolCall | ToolResult
    | ConfirmationRequest | ConfirmationResponse | AudioChunk
    | ChatMessage | SummonGuild | SmeResponse | DissentReport
    | ChannelUpdate | SafetyInterrupt | CheckpointMarker
)
```

### 2.2 Kotlin (client side)

```kotlin
@Serializable
sealed class AgentEvent {
    @Serializable data class Hello(val client: String, val sessionId: String, val protocolVersion: String = "2.0") : AgentEvent()
    @Serializable data class Goodbye(val reason: String) : AgentEvent()
    @Serializable data class Transcript(
        val text: String, val partial: Boolean, val ts: Long,
        val speaker: Speaker = Speaker.USER, val smeId: String? = null
    ) : AgentEvent()
    @Serializable data class ToolCall(val name: String, val argsJson: String, val callId: String) : AgentEvent()
    @Serializable data class ToolResult(val callId: String, val resultJson: String, val deferred: Boolean = false) : AgentEvent()
    @Serializable data class ConfirmationRequest(
        val callId: String, val summary: String, val risk: Risk,
        val invokerSmeId: String? = null, val actionCardJson: String? = null
    ) : AgentEvent()
    @Serializable data class ConfirmationResponse(
        val callId: String, val approved: Boolean,
        val approverChannel: ApproverChannel = ApproverChannel.VOICE
    ) : AgentEvent()
    @Serializable data class AudioChunk(val pcmBase64: String, val ts: Long) : AgentEvent()

    @Serializable data class ChatMessage(
        val channelId: String, val authorId: String, val authorKind: AuthorKind,
        val body: String, val bodyContentType: BodyContentType = BodyContentType.MARKDOWN,
        val mentions: List<String> = emptyList(),
        val replyToId: String? = null, val messageId: String, val ts: Long,
        val streaming: Boolean = false
    ) : AgentEvent()

    @Serializable data class SummonGuild(
        val callId: String, val topic: String, val smes: List<String>,
        val contextRefs: List<String> = emptyList(), val deadlineMs: Int = 30_000
    ) : AgentEvent()

    @Serializable data class SmeResponse(
        val smeId: String, val callId: String, val confidence: Float,
        val claim: String, val rationale: String,
        val evidence: List<EvidenceRef> = emptyList(),
        val proposedActions: List<ProposedAction> = emptyList(),
        val dissentsWith: List<String> = emptyList(), val ts: Long
    ) : AgentEvent()

    @Serializable data class DissentReport(
        val callId: String, val parties: List<String>, val axis: String,
        val summary: String, val pairwise: List<DissentPair>, val ts: Long
    ) : AgentEvent()

    @Serializable data class ChannelUpdate(
        val messageId: String, val deltaText: String, val done: Boolean = false, val ts: Long
    ) : AgentEvent()

    @Serializable data class SafetyInterrupt(
        val severity: Severity, val reason: String,
        val suggestedRecoverActions: List<ProposedAction> = emptyList(), val ts: Long
    ) : AgentEvent()

    @Serializable data class CheckpointMarker(
        val checkpointId: String, val graphNodeName: String, val ts: Long
    ) : AgentEvent()
}

@Serializable enum class Risk { LOW, MEDIUM, HIGH }
@Serializable enum class Severity { WARN, HALT }
@Serializable enum class Speaker { USER, LIVE, SME }
@Serializable enum class AuthorKind { USER, LIVE, SME, SYSTEM }
@Serializable enum class BodyContentType { @SerialName("text/markdown") MARKDOWN, @SerialName("application/json") JSON, @SerialName("text/code") CODE }
@Serializable enum class ApproverChannel { VOICE, CHAT }
```

---

## 3. Async function-call semantics

**DEPENDS ON SPIKE 1.** Gemini Live's function-calling loop is synchronous in the SDK as documented; we need confirmation that returning `{deferred: true, jobId}` immediately and pushing the real `functionResponse` later (via the same Live session handle) keeps the conversation alive.

### Branch A — async injection supported
- `live_function_call(name=summon_guild, args=…)` returns immediately with `ToolResult(deferred=true, resultJson='{"jobId":"<ulid>"}')`.
- Orchestrator runs the LangGraph subgraph; on completion, calls `live.send_tool_response(call_id=<original>, result=<final SmeResponse[]>)`.
- Live voice loop remains open the whole time. `@scribe` and chat continue streaming in parallel.

### Branch B — async injection NOT supported (fallback)
- The Live tool returns a synthetic "I'm consulting the guild" placeholder synchronously and the orchestrator stops Live from speaking until the graph completes; Live then receives a follow-up `user`-role message containing the structured guild result and is instructed to summarise.
- Trade-off: voice latency is bounded by the slowest SME (Spike 3 numbers). Mitigation: enforce `deadlineMs` in `SummonGuild` aggressively.

The `deferred` boolean in `ToolResult` exists in both branches so client renderers don't have to switch.

---

## 4. Binary frame format

Identical to v1 (forge_orchestrator/IMPLEMENTATION.md §"Binary channel"). Reproduced for completeness so this spec is self-contained.

| Offset | Size | Field |
|---|---|---|
| 0 | 4 | Magic ASCII: `FRAM` (jpeg) \| `AUDI` (pcm) \| `META` (json sidecar — NEW v2) |
| 4 | 4 | Width uint32 LE (0 for AUDI/META) |
| 8 | 4 | Height uint32 LE (0 for AUDI/META) |
| 12 | 8 | Timestamp ns uint64 LE |
| 20 | … | Payload |

`META` payload is a UTF-8 JSON blob whose first key is `kind` — used by clients that want to send frame-aligned annotations (e.g. tap-to-zoom-here coordinates) without breaking JPEG framing.

Payload encodings:
- `FRAM`: JPEG, quality ≥ 70, max dimension ≤ 1920 px.
- `AUDI`: PCM 16-bit LE, mono, 16 kHz (mic → orchestrator) or 24 kHz (orchestrator → speaker — same as v1).
- `META`: UTF-8 JSON, ≤ 8 KB.

---

## 5. Chat-bus message types (summary)

Full definitions live in `04_chat_bus_protocol.md` §3. The wire shape is `ChatMessage` (above) with `bodyContentType` discriminating:

| `bodyContentType` | Renderer | Notes |
|---|---|---|
| `text/markdown` | inline markdown | default |
| `application/json` | typed-card renderer | use for `SmeResponse`, `DissentPair`, `ActionCard` |
| `text/code` | monospace block | language hint via fenced markdown header |

Server-side composite messages (`SmeResponse`, `DissentReport`, `SafetyInterrupt`) are emitted as standalone events AND mirrored into the appropriate channel as `ChatMessage(bodyContentType="application/json", body=<envelope JSON>)` so reconnecting clients can replay history with one query. **DEPENDS ON SPIKE 4** — final envelope-vs-flat decision waits on Spike 4 outcome.

---

## 6. Managed-Agents wrapper (channel C)

Each SME has one persistent Managed-Agent `environment_id`. Orchestrator → SME communication is via `interactions.create(environment_id, prompt, …)`. Outbound prompts wrap user/live messages plus orchestrator context; inbound responses are parsed against the SmeResponse schema (see Spike 4).

**DEPENDS ON SPIKE 2.** If concurrent `interactions.create` on the same environment is rejected:
- Branch A (concurrent OK): single env per SME, fan out in parallel from `ParallelSummonSMEs` (see `01_langgraph_state_machine.md` §3).
- Branch B (serialized): pre-warm a small pool (`@power#a`, `@power#b`) per SME, round-robin. Pool size = 2 for hackathon scope; document tradeoff (warm-state divergence between pool members) in `02_sme_persona_format.md` §6.

**DEPENDS ON SPIKE 4.** Structured output envelope (`SmeResponse`):
- Candidate (a) `response_schema` on underlying Gemini model: cleanest but unclear support inside Managed-Agents sandbox.
- Candidate (b) write `/workspace/output.json` + orchestrator polls `files/environment-{id}:download`: deterministic, slower, requires file-poll loop.
- Candidate (c) regex-extract a JSON fenced block from free text + Pydantic validate + 1 retry.
Until decided, the SmeResponse schema in §2.1 is the canonical shape; only the transport varies.

---

## 7. Versioning

- `protocolVersion` in `Hello` is the source of truth. Orchestrator rejects (`Goodbye("protocol_mismatch")`) any client whose major version differs.
- v1 → v2 break points: `protocolVersion` is now required (v1 clients sent no version); `Transcript.speaker` and `.smeId` fields are new but optional in JSON. Old v1 events without these fields parse cleanly because the new fields are defaulted.
- New event types appended to the sealed union are minor bumps; removals are major bumps.
- Reserved discriminator strings (do not reuse): every `kind` listed in §2.1, plus `LogLine`, `Metric`, `TraceSpan` (reserved for future observability piping).

---

## 8. Authentication

`Hello` carries no token; tokens travel in the WS `Sec-WebSocket-Protocol` subprotocol header for both ChatBus and Live channels.

**DEPENDS ON SPIKE 5 (and ops decisions).** Two candidates:
- (a) Firebase ID token, verified by `FirebaseAuth` (carryover from v1).
- (b) Shared secret per device, set via `AUTH_TOKEN` env on client and `ALLOWED_DEV_TOKENS` on orchestrator.

Either way, the orchestrator passes the verified `uid` to LangGraph state under `state.userId`. The `@sentinel` SME may consult this to gate dangerous actions for unknown / non-owner users (`03_safety_gate_matrix.md` §4).

---

## 9. ULID / ID conventions

- `sessionId`: client-generated ULID at connect time.
- `messageId`: client-generated ULID for user messages; server-generated for SME/Live messages. Stable across reconnect so dedup works.
- `callId`: orchestrator-generated ULID for every tool call AND every guild summon. One `callId` may map to many `SmeResponse`s (one per SME consulted).
- `checkpointId`: LangGraph checkpoint ID, opaque string.

---

## 10. Out of scope (not on the wire)

- Internal LangGraph state graph — never serialised to a client; replay uses checkpoint storage + the ChatMessage history.
- SME-to-SME chat — modeled as orchestrator-mediated messages in `#dissent` / `#actions`; SMEs do not have a direct WS to each other.
- Per-SME tool calls from inside the sandbox (file writes, web fetches, code exec) — those are internal to Managed Agents and never surface as `ToolCall` events. Only the orchestrator's user-visible tools (`summon_guild`, `bench.*`, `confirm`) become `ToolCall`s.

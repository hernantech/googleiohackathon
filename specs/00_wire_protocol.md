# 00 — Wire Protocol (frozen contracts)

> Frozen vocabulary for every cross-process message in Forge.
> Lineage: extends `forge_orchestrator/proto/events.py` and `forge_quest/proto/AgentProto.kt` from v1. New event types are additive — every v1 event still exists and still carries the same fields.
> Cross-refs: `01_langgraph_state_machine.md` (consumers), `04_chat_bus_protocol.md` (client surface), `05_board_knowledge_api.md` (knowledge-lookup + operator-step contract).
> Model: Forge advises a human operator. No process actuates hardware — "actions" are operator instructions and read-only knowledge lookups. There is no bench daemon. Camera frames are not a client channel; they are tapped server-side from the Live video stream (§4).

---

## 1. Process map and channels

```
                ┌───────────────────────┐
                │   Phone / Quest UI    │      Discord-style chat client
                │   (Kotlin / Compose)  │      camera+mic capture; speaker out
                └─────────┬─────────────┘
                          │ (A) ChatBus WS — JSON only — see 04
                          │ (B) Live WS    — bidi audio + VIDEO — Gemini Live framing
                          │     (no separate frame channel; FrameTap samples B — §4)
                          ▼
                ┌───────────────────────┐
                │   Orchestrator        │      LangGraph; one graph instance per session
                │   (Python 3.12)       │      GeminiLiveBridge owns the FrameTap
                └──┬───────┬────────────┘
                   │       │
                   │       │ (C) Managed-Agents REST/SSE — see Spike 1/4
                   │       ▼
                   │   ┌──────────────────────────┐
                   │   │  SME Sandbox × N         │   one env per SME persona
                   │   │  (Antigravity preview)   │   SMEs propose operator steps +
                   │   └──────────────────────────┘   request knowledge lookups
                   │
                   │ (E) Gemini Live WS — Google's bidi protocol — opaque to us
                   ▼
              Gemini Live
```

The human at the bench performs every physical step Forge recommends; no channel reaches an instrument. Knowledge lookups (board doc, datasheets, documented limits — `05`) are in-process via the KnowledgeAdapter, not a network channel.

Channel A is defined here. B is defined by Google (the FrameTap consumes its video; §4). C is defined by Google (we wrap with envelopes — see §6). E is Google's Live protocol verbatim. (The former channel D — BenchDaemon JSON-RPC — is removed.)

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
    approved: bool                                 # for operator steps: True == "I did it",
                                                   #   False == "skipped"
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
    """A unit of recommended work. Forge never executes hardware actions.
    `actor` says who does it:
      - "operator": a manual step the human performs at the bench (set PSU, probe,
        solder, flash). Forge phrases it as an instruction and gates it (03).
      - "guild": a read-only knowledge lookup the orchestrator/SME runs itself
        (lookup_datasheet, lookup_board_doc, get_documented_limit — see 05).
    The name `ProposedAction` is retained from v1 for contract stability."""
    actor: Literal["operator", "guild"] = "operator"   # NEW
    tool: str                                      # operator-step verb (e.g. "set_psu",
                                                   #   "probe_net", "reflow_pin", "flash_mcu")
                                                   #   or a knowledge-lookup tool name
    argsJson: str                                  # structured params (target net, value, unit…)
    rationale: str
    risk: Literal["LOW", "MEDIUM", "HIGH"]
    instruction: str | None = None                 # NEW; human-readable step text for the card
    documentedLimitRef: str | None = None          # NEW; citation for the value (05 §4)

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
    """Renderable operator-instruction card; carried inside
    ConfirmationRequest.actionCardJson. Tells the human what to do BY HAND and
    collects 'I did it' / 'Skip'. (Name retained from v1 for contract stability.)"""
    kind: Literal["ActionCard"] = "ActionCard"
    title: str
    bodyMarkdown: str                              # the step, spelled out
    diffMarkdown: str | None = None                # e.g. "PSU now: off  →  set: 30.0 V / 0.5 A"
    risk: Literal["LOW", "MEDIUM", "HIGH"]
    documentedLimit: str | None = None             # NEW; shown so the human can sanity-check
                                                   #   the value against the board's own docs
    affirmLabel: str = "I did it"                  # CHANGED (was "Approve")
    denyLabel: str = "Skip"                        # CHANGED (was "Hold")

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
@Serializable enum class Actor { @SerialName("operator") OPERATOR, @SerialName("guild") GUILD }

// Supporting types carried inside events (parity with Pydantic §2.1).
@Serializable data class EvidenceRef(val kind: String, val uri: String, val note: String? = null)
@Serializable data class ProposedAction(
    val actor: Actor = Actor.OPERATOR,
    val tool: String, val argsJson: String, val rationale: String, val risk: Risk,
    val instruction: String? = null, val documentedLimitRef: String? = null
)
@Serializable data class DissentPair(
    val a: String, val b: String, val aClaim: String, val bClaim: String, val crux: String
)
// Rendered inside ConfirmationRequest.actionCardJson — the operator instruction card.
@Serializable data class ActionCard(
    val title: String, val bodyMarkdown: String, val diffMarkdown: String? = null,
    val risk: Risk, val documentedLimit: String? = null,
    val affirmLabel: String = "I did it", val denyLabel: String = "Skip"
)
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

## 4. Frame source and internal frame format (FrameTap)

**Frames are NOT a client channel.** The client sends one Live media stream (channel B: audio + video, Gemini Live framing). The orchestrator's **FrameTap** (a tee + sampler living in `GeminiLiveBridge`, ARCHITECTURE §2) subscribes to the *same* video frames being forwarded to Gemini Live, throttles to ≈2–5 fps, JPEG-encodes, and publishes a `FrameRef` into `ForgeState.latestFrame`, the `FrameStore`, and `@sentinel`'s vision feed. This guarantees the SMEs analyze exactly the pixels Live saw, with no second uplink and no cadence drift.

`FrameRef` (what flows through state / to SMEs / to the chat bus as a contextRef):

```python
class FrameRef(BaseModel):
    kind: Literal["FrameRef"] = "FrameRef"
    uri: str                                       # gs://… (FrameStore) or in-mem id
    width: int
    height: int
    ts: int                                        # ns; the Live frame's capture ts
    sourceSeq: int                                 # monotonic FrameTap sample index
```

The on-disk / FrameStore byte format the FrameTap *produces* (and that internal consumers may pass around) keeps the v1 binary header for tooling compatibility:

| Offset | Size | Field |
|---|---|---|
| 0 | 4 | Magic ASCII: `FRAM` (jpeg) \| `META` (json sidecar) |
| 4 | 4 | Width uint32 LE (0 for META) |
| 8 | 4 | Height uint32 LE (0 for META) |
| 12 | 8 | Timestamp ns uint64 LE |
| 20 | … | Payload |

Payload encodings:
- `FRAM`: JPEG, quality ≥ 70, max dimension ≤ 1920 px (FrameTap enforces).
- `META`: UTF-8 JSON, ≤ 8 KB — frame-aligned annotations (e.g. a tap-to-zoom-here coordinate the client sends over channel A as a `ChatMessage` `META` body, resolved against `FrameRef.sourceSeq`).

`AUDI` (raw PCM) is **removed from this spec**: audio rides inside the Live channel (B) in Google's framing; the orchestrator never re-frames it. Mic capture is 16 kHz mono; speaker out is 24 kHz — these are Live-session settings, not a Forge binary format.

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
- Per-SME tool calls from inside the sandbox (file writes, web fetches, code exec) — those are internal to Managed Agents and never surface as `ToolCall` events. Only the orchestrator's user-visible tools (`summon_guild`, `confirm_step`, and knowledge lookups `lookup_datasheet` / `lookup_board_doc` / `get_documented_limit` — `05`) become `ToolCall`s. There are no hardware-actuation tools at all.

---

## 11. Test cases (component-level — the contract)

These are the unit/contract tests that gate any change to this file. They run with no external services (pure (de)serialization). Run: `pytest orchestrator/proto/tests/`.

**Design pattern under test:** the discriminated union + idempotent ULIDs + graceful, forward-compatible parsing.

| ID | Test | Pass criterion |
|---|---|---|
| WP-1 | Round-trip every `AgentEvent` variant: `model_validate_json(e.model_dump_json()) == e` | exact equality for all 15 variants |
| WP-2 | Discriminator dispatch: a JSON blob with `kind:"ChatMessage"` parses to `ChatMessage`, never another variant | type is exact |
| WP-3 | Forward-compat: an event JSON with an unknown extra field parses without error (ignored) | no exception |
| WP-4 | v1→v2 default-fill: a `Transcript` JSON lacking `speaker`/`smeId` parses with `speaker="user"`, `smeId=None` | defaults applied |
| WP-5 | `ProposedAction.actor` defaults to `"operator"` when absent; `"guild"` lookups validate | both parse |
| WP-6 | Kotlin↔Pydantic parity: a golden corpus of one JSON per variant (`testdata/wire/*.json`) parses cleanly in BOTH the Python and Kotlin (`AgentProto`) deserializers | both succeed; field values match the golden table |
| WP-7 | `ActionCard` defaults: omitting `affirmLabel`/`denyLabel` yields `"I did it"` / `"Skip"` | defaults applied |
| WP-8 | `FrameRef` validates with a `gs://` and an in-mem `mem:` URI; rejects empty `uri` | accept/accept/reject |
| WP-9 | Protocol gate: a `Hello` without `protocolVersion` is rejected by the v2 validator (required field) | `ValidationError` |
| WP-10 | No actuation surface: assert the tool-name registry exposed to clients contains none of `set_psu`/`flash_mcu` as *executable* tools — only as `ProposedAction.tool` step labels | registry assertion holds |

The golden corpus in WP-6 is the single source of truth for cross-language parity and is reused by the system-level contract test `08 §3.1`.

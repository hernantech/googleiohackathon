# 01 — LangGraph State Machine

> Node-by-node spec for the Forge orchestrator graph.
> Cross-refs: `00_wire_protocol.md` (event types), `02_sme_persona_format.md` (SmeResponse shape), `03_safety_gate_matrix.md` (SafetyGate behavior), `04_chat_bus_protocol.md` (client emission), `05_board_knowledge_api.md` (knowledge-lookup + operator-step surface).
> Model: the graph produces **operator instructions** and **knowledge lookups**, never hardware actuation. `proposedActions` with `actor="operator"` are steps the human performs; `actor="guild"` are read-only lookups the orchestrator runs. SafetyGate gates the instruction; the human reports "I did it" / "skipped".

---

## 1. Graph state

```python
# orchestrator/state.py
from typing import Annotated
from langgraph.graph.message import add_messages

class ForgeState(TypedDict):
    # ── identity ──
    sessionId: str
    userId: str | None

    # ── perception ──
    latestFrame: FrameRef | None                  # the latest on-demand snapshot (00 §4.2);
                                                   #   None until the operator taps 📷. Continuous
                                                   #   vision lives with Gemini Live, not here.
    latestSnapshot: SnapshotAnalysis | None        # strong-model analysis of latestFrame, if any
    latestTranscriptPartial: str | None
    latestTranscriptFinal: str | None
    boardProfileId: str | None                    # see 05 §2 board profile (the board under test)

    # ── routing ──
    pendingSummon: SummonGuild | None             # input to ParallelSummonSMEs
    activeSmes: list[str]                         # SMEs we're awaiting
    smeResponses: dict[str, SmeResponse]          # smeId -> response

    # ── decision ──
    mergedOpinion: MergedOpinion | None
    dissentReport: DissentReport | None
    proposedActions: list[ProposedAction]
    safetyDecisions: dict[str, GateDecision]      # callId -> decision
    pendingConfirmations: dict[str, ConfirmationRequest]
    approvedActions: list[ProposedAction]

    # ── output ──
    outboundEvents: Annotated[list[AgentEvent], add_messages]   # reducer appends
    liveSpeakerScript: str | None                 # text Live should say next

    # ── runtime ──
    checkpointId: str | None
    errors: list[str]
```

`outboundEvents` uses an append-only reducer. A dispatcher coroutine drains it and emits to both the ChatBus WS and (where relevant) the Live function-response channel.

---

## 2. Node topology

```
                       ┌─────────────────────┐
   user voice / video  │   PerceptionGate    │  (always-on; entrypoint for every tick)
   bench events  ─────▶│                     │
                       └────────┬────────────┘
                                │ transcript_final | frame_update | sentinel_alarm
                                ▼
                       ┌─────────────────────┐
                       │  SupervisorRouter   │  picks SMEs OR skips guild
                       └────┬────────┬───────┘
                            │        │
                no_guild    │        │ guild_needed
                 ┌──────────┘        └──────────────┐
                 ▼                                  ▼
        ┌────────────────┐                ┌────────────────────────┐
        │  LiveSpeaker   │                │ ParallelSummonSMEs     │
        │  (chitchat)    │                │ fan-out to N SMEs      │
        └────────────────┘                └──────┬─────────────────┘
                                                 │ streaming
                                                 ▼
                                       ┌──────────────────────┐
                                       │ StreamingAggregator  │ mirrors deltas
                                       │ → #live-feed         │ into ChatBus
                                       └──────┬───────────────┘
                                              │ all_done | deadline
                                              ▼
                                       ┌──────────────────────┐
                                       │   MergeOpinion       │
                                       └──────┬───────────────┘
                                              │
                                              ▼
                                       ┌──────────────────────┐
                                       │  DissentDetector     │ if disagreement,
                                       └──────┬───────────────┘ emits DissentReport
                                              │
                                              │ converged | needs_more_rounds
                                ┌─────────────┴──────────────┐
                                │                            │
                                ▼                            ▼
                       ┌────────────────┐        (loop back to ParallelSummonSMEs
                       │  SafetyGate    │         with the cross-examination prompt)
                       └────┬────┬──────┘
                            │    │ needs_user_confirm
                            │    └─────────────┐
                            │                  ▼
                            │            ┌──────────────────┐
                            │            │  interrupt → user│  (HITL)
                            │            └─────┬────────────┘
                            │                  │ approved | denied
                            ▼                  ▼
                       ┌────────────────┐
                       │  LiveSpeaker   │  voices the final answer/action
                       └────┬───────────┘
                            │
                            ▼
                          END (tick)

   ─── always-on side channels (run as separate subgraphs / cron edges) ───
   @sentinel → SafetyInterrupt → preempts ANY node (priority bus)
   @scribe   → continuous SmeResponse stream into #scribe (no merge)
   @librarian → cached lookups, fires on @-mention or vocabulary trigger
```

ASCII edge labels match the conditional-edge function return values.

---

## 3. Node specs

### 3.1 PerceptionGate

**Purpose**: entrypoint for every external event tick. Normalizes inputs before routing.

**Input state delta**: one of
- `Transcript(partial=False)` from Live
- `SnapshotAnalysis` from the `SnapshotAnalyzer` (`00 §4.2`) — the operator tapped 📷; updates `latestFrame` + `latestSnapshot` and posts the analysis to `#live-feed`, so the next `summon_guild` carries it as evidence. (There is no continuous frame feed; Live owns continuous vision.)
- `SafetyInterrupt` from `@sentinel` (bypasses to LiveSpeaker via priority bus)
- `ChatMessage(authorKind=USER)` from chat (typed input)

**Output state delta**:
- updates `latestFrame` / `latestTranscriptFinal` / `latestTranscriptPartial`
- emits `CheckpointMarker` to `outboundEvents`

**Prompt template**: none — pure routing node.

**Error handling**: dropped frames are logged and ignored; malformed transcripts log and emit `Goodbye("perception_invalid")` on the affected channel only.

**Streaming**: synchronous, sub-ms.

**Checkpointing**: writes a checkpoint at every final transcript (so we can replay from "what the user just said").

---

### 3.2 SupervisorRouter

**Purpose**: decide whether the user's utterance needs the guild, and if so which SMEs.

**Input**: `latestTranscriptFinal` plus the rolling N=20-message conversation window.

**Output**: sets `pendingSummon = SummonGuild(topic=…, smes=[…], deadlineMs=…)` OR routes to `LiveSpeaker` direct.

**Prompt template**:
```
You are the Supervisor for Forge. Decide:
1. Does this user message need consultation from one or more SMEs?
2. If yes, which SMEs from the roster?
3. What's the deadline (5s / 15s / 30s) given urgency?

Roster (always-on agents @librarian, @sentinel, @scribe run independently;
do NOT include them in `smes` unless their primary opinion is required):
{roster_table}

Recent conversation:
{conversation_window}

User just said:
{transcript_final}

Respond with strict JSON:
{
  "needs_guild": bool,
  "smes": ["@power", ...],
  "topic": "<≤8 words>",
  "deadline_ms": int,
  "reason": "<≤1 sentence>"
}
```

**Error handling**: model returns malformed JSON → retry once with `response_schema`; second failure → default `needs_guild=false`, route to LiveSpeaker with the original transcript verbatim, log a `routing_failed` error.

**Streaming**: not streamed; one-shot ≤300ms.

**Checkpointing**: pre-decision.

**DEPENDS ON SPIKE 5** — if google-genai + LangGraph have a published adapter, we use it here; else we wrap `genai.GenerativeModel.generate_content_async(...)` inside a custom `RunnableLambda`.

---

### 3.3 ParallelSummonSMEs

**Purpose**: fan out the summon to N SMEs concurrently and collect streamed responses.

**Input**: `pendingSummon`.

**Output**:
- per-SME side-effect: opens `interactions.create(environment_id=<sme_env>, …)` SSE stream
- mirrors each delta into `outboundEvents` as `ChannelUpdate(messageId=<sme_msg>, deltaText=…)` (channel `#<sme>`)
- on per-SME completion, parses final `SmeResponse` and writes to `state.smeResponses[smeId]`

**Prompt template** (per SME):
```
{sme_agents_md_persona_preamble}      # the SME's AGENTS.md system content

Session context:
{rolling_summary}

Latest user utterance:
{transcript_final}

Latest visual evidence:
{frame_caption_or_uri}                # @librarian provides captions if frame present

Other SMEs consulted in parallel: {sibling_smes}

Standing instructions:
- Reason out loud (you are speaking into your own channel; the user can see it).
- End with the SmeResponse JSON in a fenced ```json block.
- If you disagree with another SME visibly in their channel (you will see deltas),
  you MAY @-mention them in your rationale.
- Time budget: {deadline_ms} ms.
```

**Error handling**:
- per-SME timeout (deadline reached) → record `SmeResponse(confidence=0.0, claim="<timeout>", rationale="…")` and continue.
- per-SME tool error inside sandbox → captured in rationale; downgrades confidence by 0.3.
- environment cold-start failure → mark SME as unavailable, continue with the rest, log.

**Streaming**: yes — central to the demo (chat channels render tokens live).

**Checkpointing**: writes a checkpoint at fan-out (so we can replay deliberation deterministically).

**DEPENDS ON SPIKE 2** — concurrency model affects implementation:
- Branch A: single `environment_id` per SME, parallel `interactions.create`. Implementation: `asyncio.gather(...)`.
- Branch B: per-SME pool of 2 envs, round-robin via a `asyncio.Queue`. Same fan-out signature.

**DEPENDS ON SPIKE 3** — pre-warming policy:
- If cold p95 > 8s, run a `KeepWarmTask` that pings every SME env every 4 min with a no-op.
- If warm-after-5min p95 < 1s, no pre-warm needed.

---

### 3.4 StreamingAggregator

**Purpose**: not a true LangGraph node — a coroutine that subscribes to `outboundEvents` from `ParallelSummonSMEs` and mirrors deltas into the `#live-feed` channel as a rolling consolidated view.

**Input**: `ChannelUpdate` events on per-SME channels.

**Output**: emits a single `ChatMessage(channelId="#live-feed", streaming=True)` initially and a stream of `ChannelUpdate`s tagged with the SME's headline so the user can skim the swarm.

**Prompt template**: none — text-only.

**Error handling**: backpressure via bounded queue (size 64 deltas); on overflow, coalesce to most-recent.

**Streaming**: yes.

**Checkpointing**: none (consumer-only).

---

### 3.5 MergeOpinion

**Purpose**: synthesize the `SmeResponse[]` into a single recommendation.

**Input**: `state.smeResponses` (fully populated or partially populated at deadline).

**Output**: `state.mergedOpinion = MergedOpinion(headline=…, supportingSmes=[…], openQuestions=[…], proposedActions=[…])` and one `ChatMessage(channelId="#actions", bodyContentType="application/json")`.

**Prompt template**:
```
You are MergeOpinion. Synthesize the SME responses below into a single
recommendation for the user. Prefer the highest-confidence agreed-on claim;
do NOT average claims; preserve dissent as DissentReport (the next node handles
that). If proposedActions overlap, deduplicate by (tool, args) tuple.

SME responses:
{sme_responses_json}

Output strict JSON:
{
  "headline": "<≤2 sentences>",
  "supportingSmes": [...],
  "openQuestions": [...],
  "proposedActions": [<ProposedAction>...]
}
```

**Error handling**: any SME with `confidence < 0.2` is excluded from the merge but kept in `openQuestions`. If 0 SMEs above the threshold, set `mergedOpinion.headline = "Inconclusive; need more evidence."`.

**Streaming**: no — one-shot.

**Checkpointing**: post-merge.

---

### 3.6 DissentDetector

**Purpose**: pairwise comparison of `SmeResponse[]`, emit `DissentReport` if disagreement detected.

**Input**: `state.smeResponses`.

**Output**: `state.dissentReport`, ChatMessage to `#dissent`, conditional edge:
- `converged` → SafetyGate
- `needs_more_rounds` → ParallelSummonSMEs with cross-examination prompt (one bounce only — counter incremented in state; cap = 2)

**Prompt template**:
```
You are DissentDetector. Compare these SME responses pairwise. For each pair,
state whether they agree, disagree, or are orthogonal. For disagreements,
identify the crux (the specific claim or value they conflict on).

Trigger another round of consultation ONLY IF:
- ≥ 2 SMEs disagree on a load-bearing claim (one that gates a proposedAction)
- AND the cross-examination round counter < 2

SME responses:
{sme_responses_json}

Cross-exam round counter: {round}

Output strict JSON:
{
  "pairwise": [<DissentPair>...],
  "convergence": "converged" | "needs_more_rounds",
  "crossExamPrompt": "<prompt sent to next round if needed>"
}
```

**Error handling**: malformed output → assume `converged`, log.

**Streaming**: no.

**Checkpointing**: post-decision.

---

### 3.7 SafetyGate

**Purpose**: enforce the safety matrix (see `03_safety_gate_matrix.md`) on each `ProposedAction`. The gate governs **what Forge instructs the human to do** — it never executes anything.

**Input**: `state.mergedOpinion.proposedActions`.

**Behavior** (table-driven; `03 §3`):
- `actor="guild"` lookups (read-only knowledge fetches) → always `allow`; run in-process via the KnowledgeAdapter, no card.
- `actor="operator"` steps → split into `auto_allowed` (LOW: just shown in `#actions`) and `needs_confirm` (MEDIUM/HIGH).
- For `needs_confirm`: before surfacing, **validate the step's values against the documented board limits** (`05 §4`, `get_documented_limit`). If a value exceeds the cited limit → `DENY`, emit `SafetyInterrupt(WARN)` naming the limit, do NOT show the card.
- Otherwise emit `ConfirmationRequest` (with an `ActionCard` carrying the looked-up `documentedLimit`) to chat AND to Live voice via `LiveSpeaker`; set `state.pendingConfirmations[callId] = …`.
- Wait (LangGraph interrupt) until `ConfirmationResponse` lands (HITL).
- `approved=True` ("I did it") → append to `state.approvedActions`, record `operatorOutcome="done"` in the audit record.
- `approved=False` ("skip") → drop the step, record `operatorOutcome="skipped"`, emit `ChatMessage(#actions, "operator skipped <summary>")`.

**Prompt template**: none — table-driven (see 03 §3).

**Error handling**:
- no response within `SAFETY_CONFIRM_TIMEOUT_S` (60s) → re-prompt once via voice ("did you do that step?"); +60s → record `operatorOutcome="timeout"` and continue (do not block the session).
- multiple pending confirmations: queued, surfaced one at a time in the UI to avoid card spam (cap 3, `03 §3.3`).

**Streaming**: no.

**Checkpointing**: per-confirmation — replay must reproduce the exact instruction card the human saw.

---

### 3.8 LiveSpeaker

**Purpose**: drive the Gemini Live voice channel.

**Input**: `state.liveSpeakerScript` (text to speak) OR a direct "chitchat" path from SupervisorRouter (in which case Live decides what to say from its own context).

**Output**: audio frames to the client via Live's outbound channel; mirror the spoken text as `Transcript(speaker="live", partial=False)` to chat.

**Prompt template** (system level, fixed for the Live model):
```
You are the voice of Forge. You DO NOT make decisions; the LangGraph
orchestrator does. Your job:
- Acknowledge the user warmly.
- When `summon_guild` is invoked, say "Consulting the guild on <topic>…"
  and stay quiet until the result lands (Spike 1 dependent).
- When a ConfirmationRequest is surfaced, read the summary clearly and pause.
- When @sentinel emits a SafetyInterrupt, interrupt yourself and read it.
- Never invent measurements. Never invent SME conclusions.
```

**Error handling**:
- Live API outage → fall back to text-only operation; emit `SafetyInterrupt(WARN, "voice down, text only")`.

**Streaming**: yes (audio).

**Checkpointing**: none (Live owns its own state).

**DEPENDS ON SPIKE 1** — function-call return shape determines whether LiveSpeaker can stay quiet during deferred guild work or must filibuster.

---

## 4. Always-on subgraphs

### 4.1 SentinelSubgraph

Runs in parallel as an `asyncio.Task`. **Its continuous eyes are Gemini Live**, not a server-side frame feed: Live watches the always-on H.264 (`00 §4.1`) and the bridge surfaces vision cues (smoke, a hot iron over a live board) plus the voice transcript; `@sentinel` also sees any on-demand `SnapshotAnalysis` and `latestTranscriptFinal`. On hazard detection it emits a `SafetyInterrupt` event onto a priority bus that the LiveSpeaker AND the SafetyGate both subscribe to. (Continuous hazard vision is therefore best-effort via Live — there is no dedicated frame grab; an autonomous-snapshot watcher is explicitly out of scope, `00 §4.2`.)

Authority to pre-empt voice: yes (see `03_safety_gate_matrix.md` §5). **Forge cannot power anything down** — there is no actuator. A `HALT` is a full-screen "POWER DOWN NOW" takeover plus a spoken command instructing the *human* to kill the PSU by hand, and it blocks all pending instruction cards until the human acks the hazard is cleared. A `WARN` is a sticky banner plus a spoken caution; the session continues.

Cap on interruptions: one HALT per 60s; subsequent HALTs are coalesced to a single emission.

### 4.2 ScribeSubgraph

Subscribes to every state delta; appends to a running session report kept in Firestore at `sessions/{sessionId}/report`. Emits a `ChatMessage(#scribe, …)` every 30s or every 5 major state transitions.

Never emits actions. Never gates anything.

### 4.3 LibrarianSubgraph

Triggered by:
- `@librarian` @-mention in chat
- vocabulary triggers in `latestTranscriptFinal` (regex on part numbers, signal names like "I²C", etc.)
- explicit query from any other SME via the `query_datasheet` adapter

Returns `EvidenceRef[]` into the requesting context (channel or SME's working memory).

---

## 5. Checkpointing & replay

LangGraph checkpoints are stored in:
- in-memory `MemorySaver` when no GCP set (dev mode)
- Firestore-backed checkpointer when `GCP_PROJECT_ID` set (collection `sessions/{sessionId}/checkpoints/`)

Replay: a client reconnect with the same `sessionId` triggers `replay_from(checkpointId)`. The orchestrator re-emits the `ChatMessage` history (last N=200) AND resumes any pending `SafetyGate` interrupt.

**DEPENDS ON SPIKE 5** — exact checkpointer plumbing depends on the LangGraph × google-genai integration story.

---

## 6. Conditional edges (machine-readable)

```python
graph.add_conditional_edges(
    "SupervisorRouter",
    lambda s: "guild_needed" if s["pendingSummon"] else "no_guild",
    {"guild_needed": "ParallelSummonSMEs", "no_guild": "LiveSpeaker"},
)
graph.add_conditional_edges(
    "DissentDetector",
    lambda s: s["dissentReport"].convergence if s["dissentReport"] else "converged",
    {"converged": "SafetyGate", "needs_more_rounds": "ParallelSummonSMEs"},
)
graph.add_conditional_edges(
    "SafetyGate",
    lambda s: "needs_confirm" if s["pendingConfirmations"] else "go",
    {"needs_confirm": END_with_interrupt, "go": "LiveSpeaker"},
)
```

Cross-exam loop cap: `state.crossExamRound < 2`; otherwise force `converged`.

---

## 7. Error envelope

Every node wraps its body in:
```python
try:
    return await node_body(state)
except Exception as e:
    log.exception(node_name, error=str(e))
    return {"errors": state["errors"] + [f"{node_name}: {e!r}"],
            "outboundEvents": [SafetyInterrupt(severity="WARN",
                reason=f"internal error in {node_name}", ts=now_ns())]}
```

The graph does NOT fail-stop — it surfaces errors via SafetyInterrupt and continues, because losing the guild mid-demo is worse than telling the user "consult failed, please retry."

---

## 8. Test cases (component-level — per node)

Run: `pytest orchestrator/graph/tests/`. SMEs and Live are faked with deterministic doubles (canned `SmeResponse`s, a scripted transcript); checkpointer is `MemorySaver`. No network. Each node is exercised in isolation by constructing a `ForgeState` and asserting the returned state delta + emitted `outboundEvents`.

**Design patterns under test:** single-writer reducer state, table-driven gate, HITL-interrupt resume, bounded retry, never-fail-stop error envelope.

| ID | Node / edge | Test | Pass criterion |
|---|---|---|---|
| GR-1 | PerceptionGate | feed a `SnapshotAnalysis` (from a fake SnapshotAnalyzer) → state updates `latestFrame` + `latestSnapshot`; analysis posted to `#live-feed`; emits a `CheckpointMarker` | frame+snapshot set; posted; one marker |
| GR-1b | PerceptionGate→Summon | take a snapshot, then `summon_guild` → the `SummonGuild.contextRefs` includes the snapshot `FrameRef` (evidence reaches SMEs) | evidence threaded in |
| GR-2 | PerceptionGate | malformed transcript → logs + `Goodbye("perception_invalid")` only on that channel, graph survives | no exception escapes |
| GR-3 | SupervisorRouter | utterance with `@power` mention → `pendingSummon.smes` contains `@power` (hard hint honored) | mention forced in |
| GR-4 | SupervisorRouter | model returns bad JSON twice → falls back to `needs_guild=false`, routes to LiveSpeaker, logs `routing_failed` | fallback edge taken |
| GR-5 | ParallelSummonSMEs | 3 SMEs, one exceeds `deadlineMs` → that SME recorded `confidence=0.0, claim="<timeout>"`, others complete | partial result, no hang |
| GR-6 | StreamingAggregator | overflow the 64-delta bounded queue → deltas coalesce, no exception, `#live-feed` still advances | backpressure handled |
| GR-7 | MergeOpinion | two SMEs, one `confidence<0.2` → excluded from merge, surfaced in `openQuestions` | exclusion holds |
| GR-8 | DissentDetector | two SMEs disagree on a load-bearing claim → emits `DissentReport`, edge=`needs_more_rounds` once, then forced `converged` at round 2 | loop cap = 2 |
| GR-9 | SafetyGate | `actor="guild"` lookup → auto-allow, no card, KnowledgeAdapter called | no `pendingConfirmations` entry |
| GR-10 | SafetyGate | `actor="operator"` step value > documented limit → DENY + `SafetyInterrupt(WARN)`, no card shown | denial path; limit cited |
| GR-11 | SafetyGate | HIGH operator step within limit → emits `ConfirmationRequest` w/ `ActionCard.documentedLimit`; interrupt; resume `approved=True` → `approvedActions` appended, audit `operatorOutcome="done"` | full HITL round-trip |
| GR-12 | SafetyGate | resume `approved=False` → step dropped, audit `operatorOutcome="skipped"` | skip path |
| GR-13 | SentinelSubgraph | inject a hazard cue via the fake Live vision feed (not a frame grab) → `SafetyInterrupt(HALT)`; assert NO actuation call exists anywhere, only the takeover + power-down instruction events; assert no continuous frame-grab task is spawned | no actuator, no frame-grab |
| GR-14 | Error envelope | force an exception inside MergeOpinion → graph emits `SafetyInterrupt(WARN, "internal error…")` and continues to LiveSpeaker | no fail-stop |
| GR-15 | Checkpoint/replay | run to a SafetyGate interrupt, drop + reconnect same `sessionId` → pending `ConfirmationRequest` re-emitted, resume completes | replay reproduces the card |

GR-11 and GR-15 are the seams that feed the system-level HITL test `08 §3.4`.

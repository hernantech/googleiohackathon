# 01 — LangGraph State Machine

> Node-by-node spec for the Forge orchestrator graph.
> Cross-refs: `00_wire_protocol.md` (event types), `02_sme_persona_format.md` (SmeResponse shape), `03_safety_gate_matrix.md` (SafetyGate behavior), `04_chat_bus_protocol.md` (client emission), `05_bench_daemon_api.md` (bench tool surface).

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
    latestFrame: FrameRef | None                  # gs:// or in-mem id; updated by PerceptionGate
    latestTranscriptPartial: str | None
    latestTranscriptFinal: str | None
    benchProfileId: str | None                    # see 05 §2 device profile

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
- `FrameChunk` from Live binary channel
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

**Purpose**: enforce the safety matrix (see `03_safety_gate_matrix.md`) on each `ProposedAction`.

**Input**: `state.mergedOpinion.proposedActions`.

**Output**:
- splits actions into `auto_allowed` and `needs_confirm`
- for `needs_confirm`: emits `ConfirmationRequest` to chat AND to Live voice via `LiveSpeaker`; sets `state.pendingConfirmations[callId] = …`
- waits (LangGraph interrupt) until `ConfirmationResponse` lands (HITL)
- on `approved` → appends to `state.approvedActions`
- on `denied` → drops action, emits `ChatMessage(#actions, "user denied <summary>")`

**Prompt template**: none — table-driven (see 03 §3).

**Error handling**:
- no response within 60s → re-prompt user once via voice ("did you mean to approve?"); 60s further → auto-deny + log.
- multiple pending confirmations: queued, surfaced one at a time in the UI to avoid action-card spam.

**Streaming**: no.

**Checkpointing**: per-confirmation — replay must reproduce the exact prompt the user saw.

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

Runs in parallel as an `asyncio.Task` consuming the same `latestFrame` and `latestTranscriptFinal` from `ForgeState`. On hazard detection (smoke, sparks, alarming voltage trend, panicked user voice), emits a `SafetyInterrupt` event onto a priority bus that the LiveSpeaker AND the SafetyGate both subscribe to.

Authority to pre-empt voice: yes (see `03_safety_gate_matrix.md` §5).

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

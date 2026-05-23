# Forge — Architecture Diagrams

> Companion to the spec files in `specs/`. The diagrams here are the shared mental model; every spec file refers back to them by number.

Sections:

1. System topology — the boxes
2. Orchestrator internals — zoomed in
3. LangGraph state machine — the choreographer
4. Sequence diagram — one full request, voice → guided fix
5. Discord UI wireframe — phone, portrait
6. SME sandbox folder structure
7. Safety gate decision tree
8. Design patterns (the load-bearing ones)
9. Testing strategy (where the test cases live)

---

## 0. What Forge is (and is NOT)

Forge is a **voice + vision multi-agent advisor for a human at an electronics bench**. The human is the operator: they hold the probes, turn the PSU knob, and wield the soldering iron. Forge watches through the phone/Quest camera, listens, summons a guild of specialist SME agents that deliberate **visibly and in parallel**, surfaces their disagreements, and hands the operator **precise, safety-gated, step-by-step instructions** — backed by tool calls that look up the right values (e.g. "what voltage does the board doc say to apply to the cell-sim ladder?") against board documentation and datasheets.

Forge **does NOT** actuate any instrument. There is no bench daemon, no JSON-RPC to a PSU, no automated flashing. Every physical action is performed by the human and confirmed back ("I did it" / "skipped"). This makes the system simpler, removes a whole class of safety risk, and is honest about where the authority lives: with the operator.

Two changes from the earlier draft drive these diagrams:

- **One video, one source of truth.** The client sends a single Live media stream (audio + video). Frames for the SMEs and `@sentinel` are *tapped server-side* from that same stream by a **FrameTap** — they are no longer a second binary channel uploaded by the client. See §2 and the note at the end of §1.
- **No bench daemon.** The "actions" the guild proposes are *operator instructions* and *knowledge lookups*, not RPCs. `@bench-tech` (the only SME that could actuate) is removed.

---

## 1. System topology (the boxes)

```
                                ┌─────────────────────────────────────────┐
                                │       THE BENCH  (human-operated)        │
                                │                                          │
                                │   PCB: ESP32  +  BQ79616 (cell monitor)  │
                                │   bench PSU · multimeter · solder station│
                                │                                          │
                                │   ▲  the human turns every knob,         │
                                │   │  probes every pad, holds the iron    │
                                └───┼──────────────────────────────────────┘
                                    │  the human reads Forge's instructions
                                    │  and performs the steps by hand
        ┌───────────────────────┐  │
        │  Phone or Quest 3     │  │
        │                       │  │
        │  ┌─────────────────┐  │  │ camera ▶  (sees the bench + hands)
        │  │ Live media       │ │  │ mic    ▶  (operator's voice)
        │  │  capture (A/V)   │◀─┼──┘ voice  ◀  (Forge speaks instructions)
        │  └────────┬─────────┘  │
        │           │            │
        │  ┌────────┴─────────┐  │
        │  │ Discord-UI       │  │
        │  │ ───────────────  │  │
        │  │ #live-feed       │  │
        │  │ #power           │  │
        │  │ #signal          │  │
        │  │ #firmware        │  │
        │  │ #librarian       │  │
        │  │ #sentinel  (!)   │  │
        │  │ #scribe          │  │
        │  │ #dissent         │  │
        │  │ #actions (steps) │  │
        │  └──────────────────┘  │
        └──────────┬─────────────┘
                   │  client → orchestrator over WSS:
                   │   (A) ChatBus WS — JSON events
                   │   (B) Live WS    — bidi audio + VIDEO (Gemini Live framing)
                   │   ── NO separate jpeg frame channel (frames are tapped, see §2) ──
                   ▼
        ┌─────────────────────────────────────────────────────────────────────────┐
        │                                                                         │
        │                  FORGE ORCHESTRATOR  (FastAPI · Cloud Run / laptop)     │
        │                                                                         │
        │   ┌─────────────────┐         ┌─────────────────────────────┐           │
        │   │ GeminiLiveBridge│ ◄─────► │       LangGraph Engine      │           │
        │   │  - bidi audio   │         │  PerceptionGate              │          │
        │   │  - bidi VIDEO   │         │   ↓                          │          │
        │   │  - func calls   │         │  SupervisorRouter            │          │
        │   │  ┌───────────┐  │         │   ↓                          │          │
        │   │  │ FrameTap  │──┼───────► │  ParallelSummonSMEs ──┐      │          │
        │   │  │ tee+sample│  │         │   ↓                   │      │          │
        │   │  └───────────┘  │         │  StreamingAggregator  │      │          │
        │   └─────────────────┘         │   ↓                   │      │          │
        │                               │  MergeOpinion ────────┤      │          │
        │   ┌─────────────────┐         │   ↓                   │      │          │
        │   │  Channel Bus    │ ◄─────► │  DissentDetector ◄────┘      │          │
        │   │  - per-channel  │         │   ↓                          │          │
        │   │    fan-out      │         │  SafetyGate (HITL interrupt) │          │
        │   │  - replay       │         │   ↓                          │          │
        │   └─────────────────┘         │  LiveSpeaker                 │          │
        │                               └─────────────┬───────────────┘           │
        │   ┌─────────────────┐                       │                           │
        │   │  Audit Writer   │ ◄─────                 │                           │
        │   │  (Firestore)    │         ┌──────────────▼────────────────┐          │
        │   └─────────────────┘         │  ManagedAgentDispatcher       │         │
        │                               │  - env registry (per SME)     │         │
        │   ┌─────────────────┐         │  - SSE → channel-bus mapper   │         │
        │   │  KnowledgeAdapter│◄──────►│  - always-on heartbeat        │         │
        │   │  - board profile │        └─────────────┬────────────────┘          │
        │   │  - datasheet RAG │                      │                           │
        │   │  - documented    │                      │  google-genai             │
        │   │    limits        │                      ▼                           │
        │   └─────────────────┘                                                   │
        └─────────────────────────────────────────────┼───────────────────────────┘
                                                      │
                                                      ▼
        ┌─────────────────────────────────────────────────────────────────────────┐
        │              MANAGED  AGENTS  API   (Antigravity preview)               │
        │                                                                         │
        │   On-demand (summoned per question):                                    │
        │   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐      │
        │   │ @power   │ │ @signal  │ │@firmware │ │ @layout  │ │@sourcing │      │
        │   └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘      │
        │   ┌──────────┐ ┌──────────┐                                             │
        │   │@reverse  │ │ @tutor   │                                             │
        │   └──────────┘ └──────────┘                                             │
        │                                                                         │
        │   Always-on  (long-lived, listen to all channels):                      │
        │   ┌──────────┐ ┌──────────┐ ┌──────────┐                                │
        │   │@librarian│ │@sentinel │ │ @scribe  │                                │
        │   └──────────┘ └──────────┘ └──────────┘                                │
        │                                                                         │
        │   Each agent = AGENTS.md persona + SKILL.md pack +                      │
        │                persistent sandbox per (user, project).                  │
        │   SMEs PROPOSE operator steps + REQUEST knowledge lookups.              │
        │   No SME actuates hardware (there is nothing to actuate).               │
        └─────────────────────────────────────────────────────────────────────────┘
```

**Why there is no separate frame channel (answering the design question directly).**
The video already flows to the orchestrator inside the Live media stream (channel B), on its way to Gemini Live. Having the client *also* push a parallel JPEG stream meant: (a) two encodings of the same pixels on the uplink, doubling client work and bandwidth; (b) two cadences that drift, so "what Live saw" and "what the SME analyzed" could disagree; and (c) two contracts the client had to keep in sync. The single benefit of a separate channel — letting SME vision run at a different resolution/cadence than Live — is preserved more cheaply by a **server-side FrameTap** that *tees* the Live video and *samples* it (≈2–5 fps, JPEG q≥70, ≤1920 px). One source of truth, less client code, no desync. The on-the-wire JPEG/`FrameRef` artifact still exists — it is now *produced by the orchestrator*, not uploaded by the client (`specs/00_wire_protocol.md` §4).

---

## 2. Orchestrator internals (zoomed in)

```
                                ▼ WSS from client (2 channels, not 3)
        ┌──────────────────────────────────────────────────────────────────┐
        │                                                                  │
        │   ┌──────────────────────────────────────────────────────────┐   │
        │   │           Connection Layer  (FastAPI /v2/session)        │   │
        │   │  AuthMiddleware → SessionFactory.open(client_jwt)        │   │
        │   └──────────────────────────────────────────────────────────┘   │
        │                              │                                   │
        │                  ┌───────────┴────────────┐                      │
        │                  ▼                         ▼                      │
        │       ┌────────────────────┐   ┌────────────────────┐            │
        │       │ Live Channel        │   │ Chat Channel       │            │
        │       │ (audio + VIDEO)     │   │ (json events)      │            │
        │       └─────────┬──────────┘   └─────────┬──────────┘            │
        │                 │                        │                       │
        │                 ▼                        │                       │
        │   ┌────────────────────────────────────┐ │                       │
        │   │           GeminiLiveBridge          │ │                       │
        │   │  - manages 1 Live session per WS    │ │                       │
        │   │  - forwards user audio/video up     │ │                       │
        │   │  - receives transcripts/tool-calls  │ │                       │
        │   │  - inject_function_response(...)    │ │                       │
        │   │                                     │ │                       │
        │   │  ┌───────────────────────────────┐  │ │                       │
        │   │  │ FrameTap  (tee + sampler)     │  │ │                       │
        │   │  │  - subscribes to forwarded    │  │ │                       │
        │   │  │    video frames               │  │ │                       │
        │   │  │  - throttles to ≈2–5 fps      │  │ │                       │
        │   │  │  - JPEG-encodes (q≥70)        │  │ │                       │
        │   │  │  - publishes FrameRef →       │  │ │                       │
        │   │  │      state.latestFrame        │  │ │                       │
        │   │  │      FrameStore               │  │ │                       │
        │   │  │      @sentinel vision feed    │  │ │                       │
        │   │  └───────────────────────────────┘  │ │                       │
        │   └────────────────────┬────────────────┘ │                       │
        │                        │                   │                       │
        │                        ▼                   ▼                       │
        │   ┌────────────────────────────────────────────────────────┐     │
        │   │                LangGraph Engine                        │     │
        │   │  state: ForgeState (transcript, latestFrame,           │     │
        │   │    summoned SMEs, sme_responses, dissent,              │     │
        │   │    proposedSteps, chat_log, boardProfileId)            │     │
        │   │  checkpointer: Firestore (for replay scrubber)         │     │
        │   │  nodes: see Diagram 3                                  │     │
        │   └──────┬──────────────────────┬──────────────────────────┘     │
        │          │                      │                                │
        │          ▼                      ▼                                │
        │   ┌──────────────────┐  ┌─────────────────────────┐              │
        │   │ ManagedAgent     │  │ Channel Bus             │              │
        │   │ Dispatcher       │  │ - in: chat msgs from    │              │
        │   │ - resolves env   │  │      LangGraph nodes    │              │
        │   │   for (user,     │  │ - out: WS broadcasts    │              │
        │   │    project, SME) │  │      per channel        │              │
        │   │ - interactions.  │  │ - replay buffer 30 min  │              │
        │   │   create(stream) │  │ - backfill on reconnect │              │
        │   │ - SSE → channel  │  └─────────────────────────┘              │
        │   │   bus            │                                           │
        │   └──────┬───────────┘  ┌─────────────────────────┐              │
        │          │              │ KnowledgeAdapter        │              │
        │          │              │ - board profile (YAML)  │              │
        │          │              │ - datasheet RAG         │              │
        │          │              │ - get_documented_limit  │              │
        │          │              │   (read-only lookups;   │              │
        │          │              │    serves SME tool      │              │
        │          │              │    calls + SafetyGate)  │              │
        │          ▼              └──────────┬──────────────┘              │
        │   to Managed Agents API            │                            │
        │                                    ▼                            │
        │   ┌────────────────────────────────────────────────────────┐     │
        │   │            Shared Infrastructure                       │     │
        │   │  - AuditWriter (Firestore: every state transition)     │     │
        │   │  - FrameStore  (GCS: jpeg bytes + signed URLs)         │     │
        │   │  - SafetyGateMatrix (lookup: step → gate decision)     │     │
        │   │  - EnvRegistry (managed-agent env IDs per session/SME) │     │
        │   └────────────────────────────────────────────────────────┘     │
        └──────────────────────────────────────────────────────────────────┘
```

The **BenchDaemon Adapter is gone**. In its place: the **KnowledgeAdapter** (read-only board-doc / datasheet / documented-limit lookups, `specs/05`). Nothing here opens a socket to an instrument.

---

## 3. LangGraph state machine (the choreographer)

```
                  ┌─────────────────────────────────────┐
                  │  ENTRY: ToolCallReceived from Live  │
                  │  (e.g. summon_guild(...))           │
                  └────────────────┬────────────────────┘
                                   ▼
                  ┌─────────────────────────────────────┐
                  │  PerceptionGate                     │
                  │  - normalize Live event             │
                  │  - attach latest FrameTap frame     │
                  │  - append to state.live_transcript  │
                  └────────────────┬────────────────────┘
                                   ▼
                  ┌─────────────────────────────────────┐
                  │  SupervisorRouter                   │
                  │  - small Flash call                 │
                  │  - reads transcript + frame caption │
                  │  - emits state.summoned_smes        │
                  │  - heuristics:                      │
                  │    · explicit @-mention → force     │
                  │    · safety keyword → @sentinel     │
                  │    · idle → []                      │
                  └────────────────┬────────────────────┘
                                   │
                       ┌───────────┴───────────┐
                summoned=[]            summoned=[smes…]
                       │                       │
                       ▼                       ▼
              ┌─────────────────┐    ┌─────────────────────────┐
              │ LiveSpeaker     │    │ ParallelSummonSMEs      │
              │ (direct reply,  │    │  asyncio.gather(N tasks)│
              │  no guild)      │    │  each → managed-agents  │
              └────────┬────────┘    │  interaction.create(    │
                       │             │    stream=True,         │
                       │             │    environment=env_for( │
                       │             │      user, project, sme)│
                       │             └────────────┬────────────┘
                       │                          ▼
                       │             ┌─────────────────────────┐
                       │             │ StreamingAggregator     │
                       │             │  for each SSE delta:    │
                       │             │   - emit ChatMessage to │
                       │             │     channel #<sme>      │
                       │             │   - accumulate response │
                       │             │  await all complete OR  │
                       │             │  timeout (deadline)     │
                       │             └────────────┬────────────┘
                       │                          ▼
                       │             ┌─────────────────────────┐
                       │             │ MergeOpinion            │
                       │             │  - small Flash call     │
                       │             │  - inputs: SME outputs  │
                       │             │  - outputs:             │
                       │             │     consensus: str      │
                       │             │     positions: [...]    │
                       │             │     proposedSteps:      │
                       │             │       [operator steps]  │
                       │             │     disagreements: [...]│
                       │             └────────────┬────────────┘
                       │                          │
                       │              ┌───────────┴────────────┐
                       │       disagreements=[]      disagreements!=[]
                       │              │                        │
                       │              │                        ▼
                       │              │            ┌─────────────────────────┐
                       │              │            │ DissentDetector         │
                       │              │            │  - emit DissentReport   │
                       │              │            │    to #dissent          │
                       │              │            │  - if user (or auto-)   │
                       │              │            │    requests cross-exam: │
                       │              │            │    LOOP back to         │
                       │              │            │    ParallelSummonSMEs   │
                       │              │            │    with "rebut" prompt  │
                       │              │            └───────────┬─────────────┘
                       │              │                        │ (loop bound:│
                       │              │                        │  ≤ 2 rounds)│
                       │              ▼                        ▼             │
                       │       ┌──────────────────────────────────────┐      │
                       │       │ proposedSteps == []  ────────────────┼──┐   │
                       │       │ proposedSteps != []                  │  │   │
                       │       └─────────────────────┬────────────────┘  │   │
                       │                             ▼                   │   │
                       │                  ┌─────────────────────────┐    │   │
                       │                  │ SafetyGate              │    │   │
                       │                  │  - lookup gate matrix   │    │   │
                       │                  │  - check step against   │    │   │
                       │                  │    documented limits    │    │   │
                       │                  │  - if gated:            │    │   │
                       │                  │     emit Instruction-   │    │   │
                       │                  │     Card to #actions    │    │   │
                       │                  │     CHECKPOINT (HITL    │    │   │
                       │                  │     interrupt)          │    │   │
                       │                  │  - on resume w/ "I did  │    │   │
                       │                  │    it": record outcome  │    │   │
                       │                  └────────────┬────────────┘    │   │
                       │                               │                 │   │
                       │                               ▼                 │   │
                       │                  ┌────────────────────────────┐ │   │
                       │                  │ LiveSpeaker                │◄┘   │
                       │                  │  - voice the step / answer │     │
                       │                  │  - GeminiLiveBridge.       │◄────┘
                       │                  │    inject_function_response│
                       │                  │    (callId, payload)       │
                       │                  │  - post summary to #live-  │
                       │                  │    feed                    │
                       │                  └────────────┬───────────────┘
                       │                               │
                       └───────────────────────────────┴──► END
```

`proposedSteps` is the diagram's friendly name for `state.proposedActions` (`list[ProposedAction]`, `00 §2.1` / `01 §1`). Each is either an **operator instruction** (`actor="operator"`, e.g. "set the bench PSU to 30.0 V across the cell-sim ladder at J3") or a **knowledge lookup** (`actor="guild"`, e.g. "look up the BQ79616 wake-tone timing"), never an RPC. SafetyGate confirms the risky operator steps with the human; on resume the human reports "I did it" / "skipped" — there is no daemon dispatch.

**Always-on side-loop** (parallel to the main graph):

```
        ┌────────────────────────────────────────────────────┐
        │  AlwaysOnSupervisor (runs per session, not per call)│
        │                                                    │
        │   on every ChatMessage published to any channel:   │
        │     ▶ forward to @sentinel's open interaction      │
        │       (input as "observation": ...)                │
        │     ▶ forward to @scribe's open interaction        │
        │       (input as "log_entry": ...)                  │
        │     ▶ forward chip-name mentions to @librarian     │
        │                                                    │
        │   @sentinel may emit SafetyInterrupt →             │
        │     ▶ force-mute Live audio out                    │
        │     ▶ inject sentinel voice line via Live's TTS    │
        │     ▶ HALT: full-screen "POWER DOWN NOW" takeover  │
        │       + instruct operator to kill the PSU by hand  │
        │       (Forge cannot kill it — the human must)      │
        └────────────────────────────────────────────────────┘
```

---

## 4. Sequence diagram (one full request, voice → guided fix)

Scenario: BQ79616 bring-up. The ESP32 host reports a comm timeout reading cell voltages.

```
Operator   Live          Bridge        LangGraph    Dispatcher    Managed-Agents API   Knowledge
(human)    (+FrameTap)
 │           │             │              │              │              │                │
 │ "ESP32    │             │              │              │              │                │
 │  can't    │             │              │              │              │                │
 │  read the │             │              │              │              │                │
 │  BQ79616  │             │              │              │              │                │
 │  — comm   │             │              │              │              │                │
 │  timeout."│             │              │              │              │                │
 │──A/V─────►│             │              │              │              │                │
 │           │ FrameTap samples a frame → state.latestFrame                              │
 │           │──transcript→│              │              │              │                │
 │           │──funcall───►│              │              │              │                │
 │           │ consult_    │──ToolCall───►│              │              │                │
 │           │ guild()     │              │              │              │                │
 │           │             │              │ PerceptionGate (attaches frame)              │
 │           │             │              │ SupervisorRouter                              │
 │           │             │              │  → summon: [@firmware,@signal,@power]        │
 │           │             │              │──summon────► │              │                │
 │           │             │              │              │──N parallel─►│ @firmware start│
 │           │             │              │              │  interactions│ @signal start  │
 │           │             │              │              │              │ @power start   │
 │  see chat │             │              │  ┌── SSE deltas streaming back ──┐           │
 │  panes    │◄─ChannelMsg─┤◄─chat-bus────┤◄─aggregator──┤              │                │
 │  fill up  │             │              │              │              │                │
 │           │             │              │              │  @power: "BQ79616 needs a     │
 │           │             │              │              │   valid cell-stack present at │
 │           │             │              │              │   power-up." asks Knowledge ──┼──► lookup
 │           │             │              │              │              │ datasheet:     │ datasheet
 │           │             │              │              │              │ wake/stack req │◄──page
 │           │             │              │ MergeOpinion: consensus + disagreement       │
 │           │             │              │ DissentDetector: @firmware/@signal (comm)    │
 │           │             │              │   vs @power (missing stack voltage)          │
 │   "they   │             │              │                                              │
 │   disagree│◄────────────┼──────────────┤  emit DissentReport → #dissent              │
 │   — root  │             │              │                                              │
 │   cause:  │             │              │                                              │
 │   bus or  │             │              │                                              │
 │   power?  │             │              │                                              │
 │   cross-  │             │              │                                              │
 │   examine?│             │              │                                              │
 │"          │             │              │                                              │
 │ "yes"     │──funcall───►│──resume─────►│ (re-enters ParallelSummonSMEs w/ rebut)      │
 │           │             │              │ ... second round ...                         │
 │           │             │              │ MergeOpinion (now converged on @power)       │
 │           │             │              │ proposedSteps:                               │
 │           │             │              │   [set bench PSU to <V from board doc>       │
 │           │             │              │    across cell-sim ladder J3]                │
 │           │             │              │ SafetyGate: HIGH (>5 V to a live board) →    │
 │           │             │              │   look up documented max for J3 ─────────────┼──► get_documented_limit
 │           │             │              │ ◄────── CHECKPOINT (HITL) ──────             │◄──30 V max
 │ Instr.    │             │              │                                              │
 │ card +    │◄────────────┼──────────────┤  ConfirmationRequest → #actions + voice      │
 │ voice:    │             │              │                                              │
 │ "set PSU  │             │              │                                              │
 │  to 30 V, │             │              │                                              │
 │  0.5 A    │             │              │                                              │
 │  limit,   │             │              │                                              │
 │  then say │             │              │                                              │
 │  done."   │             │              │                                              │
 │           │             │              │                                              │
 │ *human    │             │              │                                              │
 │  turns the│             │              │                                              │
 │  PSU knob*│             │              │                                              │
 │ "done"    │──funcall───►│──resume─────►│ (records operator outcome=done)              │
 │           │             │              │ LiveSpeaker: "Good. Now re-run the ESP32     │
 │           │             │              │   read and tell me what you see."            │
 │ ◄──TTS────┤             │              │                                              │
 │           │             │              │                                              │
 │ "all 16   │──A/V───────►│──ToolCall───►│ PerceptionGate → verify path                 │
 │  cells    │             │              │ @firmware confirms valid reads               │
 │  reading  │             │              │ @scribe (always-on) writes session summary   │
 │  now"     │             │              │ @sentinel (always-on) logs "within limits"   │
```

The only difference from a "real" actuating system: the rows where a daemon would have driven the PSU are replaced by **the human doing it and saying "done."** Every value Forge tells the human (30 V, 0.5 A) came from a **Knowledge lookup against the board doc / datasheet**, not from a guess.

---

## 5. Discord UI wireframe (phone, portrait)

```
┌──────────────────────────────────────┐
│  ☰  Forge · bq79616-bringup-may23    │
├────────┬─────────────────────────────┤
│        │                             │
│ #live- │   #live-feed                │
│  feed  │  ╶──────────────────────╴   │
│  • 12  │  10:42 USER 🎤             │
│        │  ▸ "ESP32 can't read the    │
│ #power │     BQ79616 — comm timeout" │
│  ●  3  │                             │
│        │  10:42 FORGE 🔊             │
│ #signal│  ▸ "Asking the guild..."    │
│  ●  2  │                             │
│        │  10:42 SYSTEM               │
│#firmwar│  📢 summoned: @firmware,    │
│  ●  4  │      @signal, @power        │
│        │                             │
│ #libra │  10:43 FORGE 🔊             │
│  ●  1  │  ▸ "Firmware suspects the   │
│        │     comm bus; Power says    │
│ #sentin│     the cell stack isn't    │
│  ⚠  !  │     powered."               │
│        │                             │
│#scribe │  10:43 ⚠ DISSENT           │
│  ●  6  │  @power vs @firmware/@signal │
│        │  [tap to see]               │
│#dissent│                             │
│  ●  1  │  10:43 USER 🎤             │
│        │  ▸ "have them cross-examine"│
│#actions│                             │
│  ●  1  │  10:44 FORGE 🔊             │
│        │  ▸ "Resolved. Power is      │
│        │     right: no stack voltage,│
│        │     so the AFE never wakes."│
│        │                             │
│        │  10:44 🪛 DO THIS STEP      │
│        │  @power asks you to:        │
│        │  Set bench PSU CH1 →        │
│        │   30.0 V, 0.5 A limit,      │
│        │   across cell-sim ladder J3 │
│        │  (board doc max: 30 V)      │
│        │  risk: HIGH                 │
│        │  [I DID IT]   [SKIP]        │
│        │                             │
├────────┴─────────────────────────────┤
│ [Hold to talk]    [Tap to type]   ⚙ │
└──────────────────────────────────────┘
```

Channel pane states (left rail):

- `●` — recent activity, unread badge
- `⚠` — dissent or warning, attention required
- `!` — sentinel alert, demands immediate attention
- gray dot — idle / no recent activity

The action card is now an **operator Instruction card** — it tells the human what to do by hand and they acknowledge `[I DID IT]` / `[SKIP]` (not Approve/Deny of a machine action). Risky steps show the documented limit Forge looked up, so the human can sanity-check the instruction against the board's own paperwork.

Tap on `#dissent` opens split view:

```
┌──────────────────────────────────────┐
│  ← #dissent · BQ79616 comm-timeout   │
├──────────────────────────────────────┤
│  ┌─────────────────┬───────────────┐ │
│  │ @firmware/@signal│ @power        │ │
│  │ ─────────────── │ ─────────────│ │
│  │ "Comm timeout → │ "AFE has no   │ │
│  │  daisy-chain or │  cell stack   │ │
│  │  init/baud is   │  applied, so  │ │
│  │  the root cause"│  it never     │ │
│  │                 │  wakes → comm │ │
│  │ confidence 0.71 │  is a symptom"│ │
│  │ ev: ESP32 log   │               │ │
│  │                 │ confidence    │ │
│  │                 │  0.92         │ │
│  │                 │ ev: datasheet │ │
│  │                 │  §7 power-up  │ │
│  └─────────────────┴───────────────┘ │
│                                      │
│  Auto-detected disagreement on:      │
│  "is the comm bus the root cause?"   │
│                                      │
│  [👤 ask user]  [🔀 cross-examine]  │
│  [🗳 vote: ___]                      │
└──────────────────────────────────────┘
```

---

## 6. SME sandbox folder structure

```
/workspace/                              ← managed-agent persistent env
│
├── .agents/                             ← auto-loaded by Antigravity harness
│   ├── AGENTS.md                        ← persona + standing instructions
│   └── skills/
│       ├── rail-budget/
│       │   ├── SKILL.md                 ← description (YAML frontmatter)
│       │   └── compute.py               ← optional implementation
│       ├── bq79616-bringup/
│       │   ├── SKILL.md
│       │   └── wake_sequence.md
│       └── thermal-derate/
│           └── SKILL.md
│
├── state/                               ← persists across sessions
│   ├── projects/
│   │   └── bq79616-bringup-may23/
│   │       ├── bom.json                 ← user-edited BOM
│   │       ├── board_doc.pdf            ← uploaded board documentation
│   │       ├── session_log.md           ← @scribe maintains
│   │       └── learned_quirks.md        ← "PSU CH1 reads 0.02 V high"
│   └── shared/
│       └── glossary.md                  ← cross-project knowledge
│
├── inbox/                               ← per-invocation, fresh
│   ├── context.json                     ← orchestrator's question + state
│   ├── latest_frame.jpg                 ← written by FrameTap before invocation
│   └── recent_transcript.txt
│
└── output.json                          ← THE structured answer
                                         ← orchestrator downloads via
                                         ←   files/environment-{id}:download
                                         ← shape: SmeResponse (per 02_sme_persona_format.md)
                                         ←
                                         ← (this convention is needed because
                                         ←  Managed Agents don't support
                                         ←  function_calling yet — DEPENDS ON SPIKE 4)
```

`latest_frame.jpg` is the FrameTap output (§2) — the exact pixels Gemini Live saw, not a separately-uploaded image.

---

## 7. Safety gate decision tree (high level)

The gate now governs **what Forge instructs the human to do**, and the second independent safety layer is **documented board limits + `@sentinel`'s live hazard watch**, not a daemon.

```
                    proposed operator Step
                              │
                              ▼
                   ┌─────────────────────┐
                   │ invoker on the      │
                   │ step's "Invokable   │
                   │ by" list?           │
                   └──────┬──────────────┘
                          │
                no ───────┴─────── yes
                 │                  │
                 ▼                  ▼
        ┌────────────────┐  ┌─────────────────────┐
        │ REJECT         │  │ look up Step.kind   │
        │ (out of scope  │  │ in gate matrix      │
        │  for invoker;  │  └─────────┬───────────┘
        │  emit WARN)    │            │
        └────────────────┘            │
                            ┌─────────┼─────────┬─────────┐
                            ▼         ▼         ▼         ▼
                         allow     confirm-  confirm-   deny
                         (just     LOW       HIGH      (unsafe /
                          show                          forbidden
                          the step)                     advice)
                            │         │         │
                            │         ▼         ▼
                            │   ┌──────────────────────────┐
                            │   │ check step values against│
                            │   │ DOCUMENTED board limits   │
                            │   │ (KnowledgeAdapter lookup) │
                            │   │  e.g. proposed V ≤ doc max│
                            │   └──────┬───────────────────┘
                            │          │
                            │   pass───┴───fail
                            │     │         │
                            │     ▼         ▼
                            │  emit Instr- REJECT
                            │  Card to     (exceeds documented
                            │  #actions    limit; emit WARN with
                            │  CHECKPOINT   the cited limit)
                            │     │
                            │  human does it / skips
                            │     │
                            │  ┌──┴──┐
                            │  ▼     ▼
                            │ did   skipped
                            │ it     │
                            ▼  │     │
                          shown│     │
                            │  │     │
                            └──┴─────┴───► AuditWriter logs decision +
                                           operator outcome + frame-ref
```

`@sentinel` interrupt path bypasses normal flow:

```
   @sentinel observation (smoke, hot iron near live board, panic in voice)
       │
       ▼
   SafetyInterrupt (severity = HALT | WARN)
       │
       ▼
   force-mute Live TTS
       │
       ▼
   inject sentinel voice line via Live
       │
       ▼
   HALT → full-screen "POWER DOWN NOW" takeover; instruct the human
          to kill the PSU by hand; block all pending instruction cards
          until the human acks the hazard is cleared.
   WARN → sticky banner + spoken caution; instructions continue.
```

Forge has no kill switch of its own — the *human* is the actuator, including for emergency power-down. `@sentinel`'s authority is to **command attention** (pre-empt voice, take over the screen), not to flip a relay.

---

## 8. Design patterns (the load-bearing ones)

Named so the implementation stays simple and consistent. Each is referenced by the spec that owns it.

| Pattern | Where | Why |
|---|---|---|
| **Tee + Sampler** (FrameTap) | §2, `00 §4`, `01 §3.1` | One video source of truth; SME vision cadence decoupled from Live cadence without a second client channel. |
| **Single-writer state + reducer** | `01 §1` | `ForgeState.outboundEvents` is append-only via a reducer; one dispatcher drains it. No races over who emits. |
| **Table-driven policy** | `03 §3` | The safety matrix is *data*, not branching code. `max(table_default, sme_declared)` for risk. Easy to audit and test. |
| **HITL interrupt (checkpoint)** | `01 §3.7`, `03 §2` | LangGraph checkpoint = the human-confirmation pause. Replay reproduces the exact prompt the human saw. |
| **Strategy + fallback chain** | `00 §6`, `02 §4` | Structured-output reader tries (a) schema → (b) file → (c) fenced-JSON. Each external dep degrades to a stub (null-object). |
| **Pub/Sub fan-out with bounded queues** | `04 §5` | Channel Bus drops `ChannelUpdate` before `ChatMessage` under backpressure; emits a `BackpressureNotice`. Never blocks the graph. |
| **Idempotency via ULID + dedup** | `00 §9`, `04 §5` | Every message carries a stable ULID; clients dedup on reconnect/replay. |
| **Two independent safety layers** | `03 §6` | Layer 1: SafetyGate (gates the *advice*). Layer 2: documented board limits + `@sentinel` live watch. Neither trusts the other. |
| **Bounded retry / circuit-break** | `01 §3.3`, `01 §3.6` | Per-SME `deadlineMs`; dissent cross-exam capped at 2 rounds; nodes never fail-stop (`01 §7`). |
| **Graceful degradation contract** | `07 §2.4` | The system MUST boot with zero env vars set; every external service has a stub. |

---

## 9. Testing strategy (where the test cases live)

Two layers, mirroring the user-facing requirement that components work *and* integrate cleanly:

- **Component-level tests** live inside each spec, in a `## Test cases` section: contract round-trips (`00`), per-node behavior (`01`), persona output validation (`02`), gate-matrix truth table (`03`), chat-bus framing (`04`), knowledge-lookup contracts (`05`).
- **System-level / integration tests** live in `specs/08_test_plan.md`: cross-process flows that prove the contracts and endpoints align end-to-end (Live → FrameTap → graph → SMEs → chat bus → operator and back), plus the build-order execution gates and the demo flow run as a single integration test.

Run order and CI gates: `08 §2`.

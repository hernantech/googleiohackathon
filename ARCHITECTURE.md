# Forge — Architecture Diagrams

> Companion to the spec files in `specs/`. The diagrams here are the shared mental model; every spec file refers back to them by number.

Sections:

1. System topology — the boxes
2. Orchestrator internals — zoomed in
3. LangGraph state machine — the choreographer
4. Sequence diagram — one full request, voice → fix
5. Discord UI wireframe — phone, portrait
6. SME sandbox folder structure
7. Safety gate decision tree

---

## 1. System topology (the boxes)

```
                                ┌─────────────────────────────┐
                                │       THE BENCH             │
                                │                             │
                                │   PCB · PSU · scope · MCU   │
                                │   serial · meter · cam      │
                                │                             │
                                └─────────────┬───────────────┘
                                              │  physical
                                              │  USB / serial
                                              │
        ┌──────────────────────┐              │              ┌───────────────────────┐
        │  Phone or Quest 3    │              │              │  Bench Daemon         │
        │                      │              │              │  (lab Linux box)      │
        │  ┌────────────────┐  │ camera ▶     │              │                       │
        │  │ Gemini Live    │  │ mic    ▶     │              │  - PSU adapter        │
        │  │  (voice + vid) │  │ voice  ◀     │              │  - sigrok logic       │
        │  └───────┬────────┘  │              │              │  - serial bridge      │
        │          │           │              │◄────USB─────►│  - meter cam OCR      │
        │          │           │              │              │  - chip-close-up cam  │
        │  ┌───────┴────────┐  │              │              │  - device profiles    │
        │  │ Discord-UI     │  │              │              │  - local hard limits  │
        │  │ ──────────────  │  │              │              │    (defense in depth)│
        │  │ #live-feed     │  │              │              │                       │
        │  │ #power         │  │              │              └──────────┬────────────┘
        │  │ #signal        │  │              │                         │
        │  │ #firmware      │  │              │                         │  WSS
        │  │ #librarian     │  │              │                         │  JSON-RPC
        │  │ #sentinel  (!) │  │              │                         │
        │  │ #scribe        │  │              │                         │
        │  │ #dissent       │  │              │                         │
        │  │ #actions       │  │              │                         │
        │  └────────────────┘  │              │                         │
        └──────────┬───────────┘              │                         │
                   │  WSS                     │                         │
                   │  - audio in/out          │                         │
                   │  - jpeg frames           │                         │
                   │  - chat-bus events       │                         │
                   │                          │                         │
                   ▼                          │                         ▼
        ┌─────────────────────────────────────────────────────────────────────────┐
        │                                                                         │
        │                  FORGE ORCHESTRATOR  (FastAPI · Cloud Run)              │
        │                                                                         │
        │   ┌─────────────────┐         ┌─────────────────────────────┐           │
        │   │ GeminiLiveBridge│ ◄─────► │       LangGraph Engine      │           │
        │   │  - bidi audio   │         │  PerceptionGate              │          │
        │   │  - bidi video   │         │   ↓                          │          │
        │   │  - func calls   │         │  SupervisorRouter            │          │
        │   └─────────────────┘         │   ↓                          │          │
        │                               │  ParallelSummonSMEs ──┐      │          │
        │   ┌─────────────────┐         │   ↓                   │      │          │
        │   │  Channel Bus    │ ◄─────► │  StreamingAggregator  │      │          │
        │   │  - per-channel  │         │   ↓                   │      │          │
        │   │    fan-out      │         │  MergeOpinion ────────┤      │          │
        │   │  - replay       │         │   ↓                   │      │          │
        │   └─────────────────┘         │  DissentDetector ◄────┘      │          │
        │                               │   ↓                          │          │
        │   ┌─────────────────┐         │  SafetyGate (HITL interrupt) │          │
        │   │  Audit Writer   │ ◄─────  │   ↓                          │          │
        │   │  (Firestore)    │         │  LiveSpeaker                 │          │
        │   └─────────────────┘         └─────────────┬───────────────┘           │
        │                                             │                           │
        │                               ┌─────────────▼────────────────┐          │
        │                               │  ManagedAgentDispatcher       │         │
        │                               │  - env registry (per SME)     │         │
        │                               │  - SSE → channel-bus mapper   │         │
        │                               │  - always-on heartbeat        │         │
        │                               └─────────────┬────────────────┘          │
        └─────────────────────────────────────────────┼───────────────────────────┘
                                                      │  google-genai
                                                      ▼
        ┌─────────────────────────────────────────────────────────────────────────┐
        │              MANAGED  AGENTS  API   (Antigravity preview)               │
        │                                                                         │
        │   On-demand (summoned per question):                                    │
        │   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐      │
        │   │ @power   │ │ @signal  │ │@firmware │ │ @layout  │ │@sourcing │      │
        │   └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘      │
        │   ┌──────────┐ ┌──────────┐ ┌────────────┐                              │
        │   │@reverse  │ │ @tutor   │ │@bench-tech │  (gated: only one allowed   │
        │   └──────────┘ └──────────┘ └────────────┘   to call bench daemon)     │
        │                                                                         │
        │   Always-on  (long-lived interactions, listen to all channels):         │
        │   ┌──────────┐ ┌──────────┐ ┌──────────┐                                │
        │   │@librarian│ │@sentinel │ │ @scribe  │                                │
        │   └──────────┘ └──────────┘ └──────────┘                                │
        │                                                                         │
        │   Each agent  =  AGENTS.md persona  +  SKILL.md pack  +                 │
        │                  persistent sandbox per (user, project)                 │
        └─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Orchestrator internals (zoomed in)

```
                                ▼ WSS from client
        ┌──────────────────────────────────────────────────────────────────┐
        │                                                                  │
        │   ┌──────────────────────────────────────────────────────────┐   │
        │   │           Connection Layer  (FastAPI /v1/session)        │   │
        │   │  AuthMiddleware → SessionFactory.open(client_jwt)        │   │
        │   └──────────────────────────────────────────────────────────┘   │
        │                              │                                   │
        │              ┌───────────────┼────────────────┐                  │
        │              ▼               ▼                ▼                  │
        │   ┌────────────────┐ ┌────────────────┐ ┌────────────────┐       │
        │   │ Live Channel   │ │ Chat Channel   │ │ Frame Channel  │       │
        │   │ (audio/video)  │ │ (json events)  │ │ (binary jpeg)  │       │
        │   └───────┬────────┘ └───────┬────────┘ └───────┬────────┘       │
        │           │                  │                  │                │
        │           ▼                  ▼                  ▼                │
        │   ┌────────────────────────────────────────────────────────┐     │
        │   │              GeminiLiveBridge                          │     │
        │   │  - manages 1 Live session per WS                       │     │
        │   │  - forwards user audio / frames upstream               │     │
        │   │  - receives transcripts / tool-calls from Live         │     │
        │   │  - emits ToolCallReceived → LangGraph entry            │     │
        │   │  - inject_function_response(callId, payload)           │     │
        │   │    ← async; called by LangGraph LiveSpeaker            │     │
        │   └────────────────────┬───────────────────────────────────┘     │
        │                        │                                         │
        │                        ▼                                         │
        │   ┌────────────────────────────────────────────────────────┐     │
        │   │                LangGraph Engine                        │     │
        │   │                                                        │     │
        │   │  state: ForgeState (Live transcript, latest frame,     │     │
        │   │    summoned SMEs, sme_responses, dissent, actions,     │     │
        │   │    chat_log)                                           │     │
        │   │                                                        │     │
        │   │  checkpointer: Firestore (for replay scrubber)         │     │
        │   │                                                        │     │
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
        │   │ - calls          │  │ - replay buffer 30 min  │              │
        │   │   interactions.  │  │ - backfill on reconnect │              │
        │   │   create(stream) │  └─────────────────────────┘              │
        │   │ - SSE → channel  │                                           │
        │   │   bus            │  ┌─────────────────────────┐              │
        │   └──────┬───────────┘  │ BenchDaemon Adapter     │              │
        │          │              │ - WS client → daemon    │              │
        │          │              │ - JSON-RPC dispatch     │              │
        │          │              │ - retries / fallback    │              │
        │          │              │   to stub mode          │              │
        │          │              └──────────┬──────────────┘              │
        │          │                         │                             │
        │          ▼                         ▼                             │
        │   to Managed Agents API     to Bench Daemon WS                   │
        │                                                                  │
        │   ┌────────────────────────────────────────────────────────┐     │
        │   │            Shared Infrastructure                       │     │
        │   │  - AuditWriter (Firestore: every state transition)     │     │
        │   │  - FrameStore  (GCS: jpeg bytes + signed URLs)         │     │
        │   │  - SafetyGateMatrix (lookup: action → gate decision)   │     │
        │   │  - EnvRegistry (managed-agent env IDs per session/SME) │     │
        │   └────────────────────────────────────────────────────────┘     │
        └──────────────────────────────────────────────────────────────────┘
```

---

## 3. LangGraph state machine (the choreographer)

```
                  ┌─────────────────────────────────────┐
                  │  ENTRY: ToolCallReceived from Live  │
                  │  (e.g. consult_guild(...))          │
                  └────────────────┬────────────────────┘
                                   ▼
                  ┌─────────────────────────────────────┐
                  │  PerceptionGate                     │
                  │  - normalize Live event             │
                  │  - attach latest frame caption      │
                  │  - append to state.live_transcript  │
                  └────────────────┬────────────────────┘
                                   ▼
                  ┌─────────────────────────────────────┐
                  │  SupervisorRouter                   │
                  │  - small Flash call                 │
                  │  - reads transcript + frame caption │
                  │  - emits state.summoned_smes:       │
                  │      list[str]                      │
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
                       │             │  timeout (30 s)         │
                       │             └────────────┬────────────┘
                       │                          ▼
                       │             ┌─────────────────────────┐
                       │             │ MergeOpinion            │
                       │             │  - small Flash call     │
                       │             │  - inputs: all SME      │
                       │             │    structured outputs   │
                       │             │  - outputs:             │
                       │             │     consensus: str      │
                       │             │     positions: [(sme,   │
                       │             │       claim, evidence)] │
                       │             │     disagreements:      │
                       │             │       list[(sme, sme,   │
                       │             │       topic)]           │
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
                       │              │            │  - if user(or auto-)    │
                       │              │            │    requests cross-exam: │
                       │              │            │    LOOP back to         │
                       │              │            │    ParallelSummonSMEs   │
                       │              │            │    with "rebut" prompt  │
                       │              │            └───────────┬─────────────┘
                       │              │                        │ (loop bound:│
                       │              │                        │  ≤ 2 rounds)│
                       │              │                        │             │
                       │              ▼                        ▼             │
                       │       ┌──────────────────────────────────────┐      │
                       │       │ proposed_actions == []  ─────────────┼──┐   │
                       │       │ proposed_actions != []               │  │   │
                       │       └─────────────────────┬────────────────┘  │   │
                       │                             ▼                   │   │
                       │                  ┌─────────────────────────┐    │   │
                       │                  │ SafetyGate              │    │   │
                       │                  │  - lookup gate matrix   │    │   │
                       │                  │  - if gated:            │    │   │
                       │                  │     emit Confirmation-  │    │   │
                       │                  │     Request to #actions │    │   │
                       │                  │     CHECKPOINT (HITL    │    │   │
                       │                  │     interrupt)          │    │   │
                       │                  │  - on resume w/         │    │   │
                       │                  │    approve: dispatch    │    │   │
                       │                  │    via @bench-tech      │    │   │
                       │                  └────────────┬────────────┘    │   │
                       │                               │                 │   │
                       │                               ▼                 │   │
                       │                  ┌────────────────────────────┐ │   │
                       │                  │ LiveSpeaker                │◄┘   │
                       │                  │  - synthesize final text   │     │
                       │                  │  - GeminiLiveBridge.       │◄────┘
                       │                  │    inject_function_response│
                       │                  │    (callId, payload)       │
                       │                  │  - post summary to #live-  │
                       │                  │    feed                    │
                       │                  └────────────┬───────────────┘
                       │                               │
                       └───────────────────────────────┴──► END
```

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
        │   @sentinel may emit InterruptIntent →             │
        │     ▶ SafetyGate (priority lane)                   │
        │     ▶ force-mute Live audio out                    │
        │     ▶ inject sentinel voice via Live's TTS         │
        └────────────────────────────────────────────────────┘
```

---

## 4. Sequence diagram (one full request, voice → fix)

```
User       Live          Bridge       LangGraph    Dispatcher    Managed-Agents API    Bench
 │           │             │              │              │              │                │
 │ "what's   │             │              │              │              │                │
 │  wrong    │             │              │              │              │                │
 │  with     │             │              │              │              │                │
 │  U2?"     │             │              │              │              │                │
 │──audio───►│             │              │              │              │                │
 │           │──transcript→│              │              │              │                │
 │           │──funcall───►│              │              │              │                │
 │           │ consult_    │──ToolCall───►│              │              │                │
 │           │ guild()     │              │              │              │                │
 │           │             │              │              │              │                │
 │           │             │              │ PerceptionGate                                │
 │           │             │              │ SupervisorRouter                              │
 │           │             │              │  → summon: [@power,@signal,@firmware]        │
 │           │             │              │              │              │                │
 │           │             │              │──summon────► │              │                │
 │           │             │              │              │──N parallel─►│ @power start   │
 │           │             │              │              │  interactions│ @signal start  │
 │           │             │              │              │              │ @firmware start│
 │           │             │              │              │              │                │
 │           │             │              │  ┌── SSE deltas streaming back ──┐           │
 │           │ ...meanwhile, Live's session is held open; user can keep talking          │
 │           │             │              │              │              │                │
 │  see chat │             │              │              │              │                │
 │  panes    │             │              │              │              │                │
 │  fill up  │◄─ChannelMsg─┤◄─chat-bus────┤◄─aggregator──┤              │                │
 │           │             │              │              │              │                │
 │           │             │              │              │              │ @power done    │
 │           │             │              │              │              │  → JSON output │
 │           │             │              │              │              │ @signal done   │
 │           │             │              │              │              │ @firmware done │
 │           │             │              │              │              │                │
 │           │             │              │ MergeOpinion: consensus + disagreement       │
 │           │             │              │ DissentDetector: @power vs @librarian        │
 │           │             │              │                                              │
 │   "they   │             │              │                                              │
 │   debated;│◄────────────┼──────────────┤  emit DissentReport → #dissent              │
 │   want me │             │              │                                              │
 │   to ask  │             │              │                                              │
 │   them to │             │              │                                              │
 │   cross-  │             │              │                                              │
 │   examine?│             │              │                                              │
 │"          │             │              │                                              │
 │           │             │              │                                              │
 │ "yes"     │             │              │                                              │
 │──audio───►│──funcall───►│              │                                              │
 │           │ cross_examine│──resume────►│ (graph re-enters ParallelSummonSMEs w/       │
 │           │              │              │  "rebut the other's claim" prompt)          │
 │           │              │              │ ... second round of fan-out ...             │
 │           │              │              │                                              │
 │           │              │              │ MergeOpinion (now no dissent)               │
 │           │              │              │ proposed_actions: [set_psu(5.0V)]           │
 │           │              │              │ SafetyGate: requires confirmation           │
 │           │              │              │ ◄────── CHECKPOINT ──────                   │
 │           │              │              │                                              │
 │ ConfReq   │              │              │                                              │
 │ in chat   │◄─────────────┼──────────────┤                                              │
 │ + voice   │              │              │                                              │
 │           │              │              │                                              │
 │ "approve" │              │              │                                              │
 │──audio───►│──funcall────►│──resume─────►│ (graph resumes; calls @bench-tech)          │
 │           │              │              │              │              │                │
 │           │              │              │              │  @bench-tech │                │
 │           │              │              │              │ ◄──summon───►│                │
 │           │              │              │              │              │  set_psu via JSON-RPC
 │           │              │              │              │              │ ─────────────► PSU=5V
 │           │              │              │              │              │ ◄─────ack─────│
 │           │              │              │              │              │                │
 │           │              │              │ LiveSpeaker                                  │
 │           │              │              │ inject_function_response(callId, "done")    │
 │           │ "PSU set to  │◄─────────────┤              │              │                │
 │           │  5V. Reading │              │              │              │                │
 │           │  resumed."   │              │              │              │                │
 │ ◄──TTS────┤              │              │              │              │                │
 │           │              │              │              │              │                │
 │           │              │              │ @scribe (always-on) writes session summary  │
 │           │              │              │ @sentinel (always-on) logs "within limits"  │
```

---

## 5. Discord UI wireframe (phone, portrait)

```
┌──────────────────────────────────────┐
│  ☰  Forge · breadboard-debug-may23   │
├────────┬─────────────────────────────┤
│        │                             │
│ #live- │   #live-feed                │
│  feed  │  ╶──────────────────────╴   │
│  • 12  │  10:42 USER 🎤             │
│        │  ▸ "what's wrong with U2?"  │
│ #power │                             │
│  ●  3  │  10:42 FORGE 🔊             │
│        │  ▸ "Asking the guild..."    │
│ #signal│                             │
│  ●  2  │  10:42 SYSTEM               │
│        │  📢 summoned: @power,       │
│#firmwar│      @signal, @firmware     │
│  ●  4  │                             │
│        │  10:42 FORGE 🔊             │
│ #libra │  ▸ "Power and Firmware     │
│  ●  1  │     agree: 3V3 rail is     │
│        │     sagging to 1.8V."       │
│ #sentin│                             │
│  ⚠  !  │  10:43 ⚠ DISSENT           │
│        │  @power vs @librarian       │
│#scribe │  [tap to see]               │
│  ●  6  │                             │
│        │  10:43 USER 🎤             │
│#dissent│  ▸ "have them cross-exam"   │
│  ●  1  │                             │
│        │  10:43 SYSTEM               │
│#actions│  📢 round 2 of fan-out      │
│  ●  1  │                             │
│        │  10:44 FORGE 🔊             │
│        │  ▸ "Resolved. Power says   │
│        │     LDO is current-        │
│        │     limiting."              │
│        │                             │
│        │  10:44 ⚡ ACTION REQUEST    │
│        │  @bench-tech proposes:      │
│        │  set_psu(CH1, 5.0V)         │
│        │  risk: MEDIUM               │
│        │  [APPROVE] [DENY]           │
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

Tap on `#dissent` opens split view:

```
┌──────────────────────────────────────┐
│  ← #dissent · BME280 rail debate     │
├──────────────────────────────────────┤
│  ┌─────────────────┬───────────────┐ │
│  │ @power          │ @librarian    │ │
│  │ ─────────────── │ ─────────────│ │
│  │ "3V3 rail is at │ "BME280 VDD   │ │
│  │  1.8V → root    │  range is     │ │
│  │  cause"         │  1.71-3.6V,   │ │
│  │                 │  so 1.8V is   │ │
│  │ confidence 0.93 │  IN SPEC."    │ │
│  │ ev: rail-trace  │               │ │
│  │     .csv        │ confidence    │ │
│  │                 │  0.99         │ │
│  │                 │ ev: datasheet │ │
│  │                 │  p.27         │ │
│  └─────────────────┴───────────────┘ │
│                                      │
│  Auto-detected disagreement on:      │
│  "is the rail voltage the root cause"│
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
│       ├── regulator-select/
│       │   ├── SKILL.md
│       │   └── decision_tree.json
│       └── thermal-derate/
│           └── SKILL.md
│
├── state/                               ← persists across sessions
│   ├── projects/
│   │   └── breadboard-debug-may23/
│   │       ├── bom.json                 ← user-edited BOM
│   │       ├── schematic.pdf            ← uploaded
│   │       ├── session_log.md           ← @scribe maintains
│   │       └── learned_quirks.md        ← "PSU CH1 reads 0.02V high"
│   └── shared/
│       └── glossary.md                  ← cross-project knowledge
│
├── input/                               ← per-invocation, fresh
│   ├── context.json                     ← orchestrator's question + state
│   ├── latest_frame.jpg                 ← optional, written before invocation
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

---

## 7. Safety gate decision tree (high level)

```
                       proposed Action
                              │
                              ▼
                   ┌─────────────────────┐
                   │  invoker == @bench- │
                   │  tech ?             │
                   └──────┬──────────────┘
                          │
                no ───────┴─────── yes
                 │                  │
                 ▼                  ▼
        ┌────────────────┐  ┌─────────────────────┐
        │ REJECT         │  │ look up Action.kind │
        │ (only @bench-  │  │ in gate matrix      │
        │  tech may      │  └─────────┬───────────┘
        │  invoke phys)  │            │
        └────────────────┘            │
                            ┌─────────┼─────────┬─────────┐
                            ▼         ▼         ▼         ▼
                         allow     confirm-  confirm-   deny
                         (read-     LOW       HIGH      (forbidden
                          only;                          combo)
                          gated:0)
                            │         │         │
                            │         │         │
                            │         ▼         ▼
                            │   ┌──────────────────────┐
                            │   │ check args against   │
                            │   │ daemon-local hard    │
                            │   │ limits (defense in   │
                            │   │ depth)               │
                            │   └──────┬───────────────┘
                            │          │
                            │   pass───┴───fail
                            │     │         │
                            │     ▼         ▼
                            │  emit Conf-  REJECT
                            │  Request     (local hard
                            │  to #actions  limit veto)
                            │  CHECKPOINT
                            │     │
                            │  user approves/denies
                            │     │
                            │  ┌──┴──┐
                            │  ▼     ▼
                            │ exec  cancel
                            ▼  │     │
                          exec │     │
                            │  │     │
                            └──┴─────┴───► AuditWriter logs decision +
                                           outcome + frame-ref
```

`@sentinel` interrupt path bypasses normal flow:

```
   @sentinel observation
       │
       ▼
   InterruptIntent (risk=HIGH)
       │
       ▼
   force-mute Live TTS
       │
       ▼
   inject sentinel voice line via Live
       │
       ▼
   block all pending Actions until user acks
```

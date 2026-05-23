# 08 — Test Plan & Execution Gates

> Component-level tests live inside each spec (`00 §11`, `01 §8`, `02 §11`, `03 §10`, `04 §13`, `05 §8`). This file owns the **system level**: the build order and CI gates (the execution plan), the **cross-process integration tests** that prove the contracts and endpoints actually line up end-to-end, and the contract-alignment matrix that ties it all together.
> Why this needs global context: every test here spans ≥2 components and asserts that what one side *emits* the other side *accepts and renders* — wire events, the FrameTap → state → SME path, the SafetyGate ↔ KnowledgeAdapter limit contract, the chat-bus ↔ client InstructionCard round trip, and the demo flow as one story.
> Cross-refs: every other spec.

---

## 1. Test pyramid & principles

```
                 ┌───────────────────────────────────┐
                 │  §3.6  Demo flow (one big story)   │  1 scenario test
                 ├───────────────────────────────────┤
                 │  §3.1–§3.5, §3.7–§3.8              │  ~12 integration tests
                 │  cross-process: contracts align    │  (this file)
                 ├───────────────────────────────────┤
                 │  WP-*/GR-*/SME-*/SG-*/CB-*/BK-*    │  ~65 component tests
                 │  one component each (the specs)    │  (the other specs)
                 └───────────────────────────────────┘
```

Principles:
1. **Contracts are tested at the seam, not just the unit.** A component test proves `ActionCard` serializes; the integration test proves the graph's `ActionCard` is the one the client renders and responds to (`§3.3`).
2. **Determinism by default.** SMEs, Live, and the network are faked with doubles unless a test is explicitly tagged `@live` (`§5`). CI runs the deterministic set; `@live` runs nightly + pre-demo.
3. **Zero-config must pass.** The whole pyramid runs with no env vars set (`§3.7`); that is the dev-loop contract from `07 §2.4`.
4. **No hardware in the loop, ever.** Forge actuates nothing, so there is no instrument to mock and no HIL rig. The "operator" is a scripted double that reports "I did it" (`§5`).
5. **Patterns under test are named** so a failing test points at a design decision, not just a line (ARCHITECTURE §8).

---

## 2. Build order & CI gates (the execution plan)

Implement bottom-up; each phase's gate is the set of tests that MUST be green before the next phase starts. This ordering means contracts are frozen before the components that depend on them are built.

| Phase | Build | Gate (must pass) | Rationale |
|---|---|---|---|
| **P0 — Contracts** | `proto/events.py` + Kotlin `AgentProto` + golden corpus | `WP-1…WP-10` + `§3.1` | Freeze the wire vocabulary first; everything downstream imports it. |
| **P1 — Knowledge** | `KnowledgeAdapter` (board profile, lookups, limits) | `BK-1…BK-10` | SafetyGate and SMEs both depend on `get_documented_limit`; build it before either. |
| **P2 — Safety** | `safety/matrix.py` + `gate.py` (table-driven) | `SG-1…SG-12` (uses P1 limits) | Gate is pure/table-driven; testable without the graph. |
| **P3 — Chat bus** | `chat_bus/ws.py` + channels + renderer | `CB-1…CB-10` | Client surface; decoupled from the graph via the Channel Bus. |
| **P4 — Live + FrameTap** | `live/bridge.py` + `frame_tap.py` | `§3.5` (frame pipeline) | Proves the unified frame source before the graph consumes frames. |
| **P5 — Graph** | nodes + subgraphs + checkpointer | `GR-1…GR-15` | Orchestration; depends on P0–P4 contracts. |
| **P6 — Managed agents** | `client.py` + `pool.py` + `structured_output.py` | `SME-1…SME-8` + `§3.2` | SME round-trip + structured-output strategy chain. |
| **P7 — Integration** | wire everything in `main.py` | `§3.1…§3.8` | Cross-process flows. |
| **P8 — Demo** | scenario fixtures + `prewarm` | `§3.6` + `07 §5` dry-run | The 3-minute story runs green, doubles only. |

CI (`pytest -m "not live"`) runs P0–P7 on every push; the gate for merge to `main` is all of P0–P7 green. The `@live` suite (`§5`) + `§3.6` run pre-demo (`06 §2`).

---

## 3. System-level integration tests

Location: `tests_integration/`. Each test below names the **components it spans**, the **contract it proves aligned**, and the **component-test seams** it builds on.

### 3.1 Wire-contract alignment (Python ↔ Kotlin, producer ↔ consumer)

- **Spans**: `proto/events.py`, Kotlin `AgentProto`, every node/chat-bus producer.
- **Proves**: every `AgentEvent` variant a producer can emit is parseable by the client deserializer, and vice versa — no orphan field, no enum drift.
- **Method**: load the golden corpus (`testdata/wire/*.json`, owned by `WP-6`); assert each parses in both Python and Kotlin to equal field values. Then statically scan the graph + chat-bus code for every `emit(<Event>)` call site and assert each emitted `kind` appears in the client's `when(kind)` render switch (no unhandled kind) and in the chat-bus renderer matrix (`04 §3.1`). Reverse-scan: every client→server event (`04 §7`) has a server handler.
- **Pass**: corpus round-trips both languages; zero unhandled `kind`s in either direction.
- **Builds on**: WP-6.

### 3.2 Managed-agents round-trip (orchestrator ↔ SME sandbox)

- **Spans**: `managed_agents/client.py`, `structured_output.py`, a live (or faked) SME env, `02` persona files.
- **Proves**: a `SummonGuild` produces a valid `SmeResponse` back through whichever structured-output strategy is available (a/b/c, `02 §4`), and the streamed deltas map onto `ChannelUpdate`s for `#<sme>`.
- **Method**: drive `ParallelSummonSMEs` against a stub env that returns (i) only `output.json`, (ii) only a fenced ```json block, (iii) both, (iv) malformed-then-valid-on-retry. Assert the reader prefers (b), falls back to (c), retries once, and that the final `SmeResponse` validates against `proto/events.py`. `@live` variant: run against one real `@power` sandbox.
- **Pass**: all four strategy cases yield a valid `SmeResponse`; deltas arrive as well-formed `ChannelUpdate`s.
- **Builds on**: SME-4, WP-1.

### 3.3 Chat-bus end-to-end + InstructionCard round trip (graph → bus → client → graph)

- **Spans**: graph emit → Channel Bus → `ws.py` → client harness → `ConfirmationResponse` → SafetyGate resume.
- **Proves**: the `ActionCard` the graph builds is the one the client renders with "I did it"/"Skip", and the client's response resumes the exact interrupted graph.
- **Method**: run the graph to a HIGH `set_psu` SafetyGate interrupt; capture the `ConfirmationRequest` over the in-memory WS; assert the client harness parses `ActionCard` (labels "I did it"/"Skip", `documentedLimit` present); send `ConfirmationResponse(approved=True)`; assert SafetyGate resumes and audits `operatorOutcome="done"`.
- **Pass**: card fields match graph intent; resume completes; audit written.
- **Builds on**: CB-6, GR-11, SG-2.

### 3.4 Safety end-to-end (SME → merge → gate → KnowledgeAdapter → operator → audit)

- **Spans**: SME proposal, `MergeOpinion`, `SafetyGate`, `KnowledgeAdapter.get_documented_limit`, chat bus, audit.
- **Proves**: the gate's documented-limit check uses the *real* KnowledgeAdapter contract and the three branches behave: ALLOW-within-limit (confirm), DENY-over-limit, and HALT-bypass.
- **Method**: three sub-cases against the fixture `board.yaml` (J3 max = 30 V):
  - (a) `@power` proposes `set_psu(30 V)` → HIGH confirm, card cites "board doc max: 30 V", approve → audited done.
  - (b) `@power` proposes `set_psu(35 V)` → DENY + `SafetyInterrupt(WARN)` citing the 30 V limit, no card.
  - (c) `@sentinel` emits `SafetyInterrupt(HALT)` + `disable_psu_output` → full-screen takeover event, bypasses confirm, rate-limited; assert **no actuation symbol is ever invoked** (only events emitted).
- **Pass**: all three branches; (c) proves Forge never executes.
- **Builds on**: SG-2, SG-3, SG-6, BK-2, GR-10/GR-13.

### 3.5 Frame pipeline (Live video → FrameTap → state → SME → @sentinel)

- **Spans**: `live/bridge.py`, `frame_tap.py`, `ForgeState`, SME `inbox/frame.jpg`, `SentinelSubgraph`.
- **Proves**: the unified frame source works — frames reach the SMEs and sentinel from the *Live stream only*, and there is no separate client frame channel anywhere in the path.
- **Method**: feed a synthetic Live video stream (30 frames) into the bridge; assert the FrameTap publishes `FrameRef`s at ≈`FRAME_TAP_FPS` (not 30), each a valid `FRAM`-header JPEG q≥70 ≤1920px; assert `state.latestFrame` updates; assert a summoned SME's `inbox/frame.jpg` equals the most-recent tapped frame; assert `@sentinel` receives it. Negative assertion: grep the client protocol + server connection layer for any frame-upload endpoint → none exists.
- **Pass**: sampling rate correct; same bytes reach SME + sentinel; no second channel.
- **Builds on**: WP-8, GR-1, SME-8.

### 3.6 The demo flow as one integration test (`06` scripted)

- **Spans**: everything, doubles only.
- **Proves**: the BQ79616 scenario runs start-to-finish and every contract holds in sequence.
- **Method**: `test_demo_flow.py` scripts the `06 §3` beats with a transcript double and a scripted "operator": comm-timeout utterance → summon `[@firmware,@signal,@power]` → first-round deltas → `DissentReport` (@power vs @firmware/@signal) → cross-exam → converge on @power → `probe_net` (operator reports "3.28 V") → HIGH `set_psu(30 V)` InstructionCard (cites J3=30 V) → operator "done" → `serial_send("read_cells")` → operator reports 16 valid reads → `@sentinel` WARN on hot-iron frame → `publish_report` stub URL. Assert the ordered event stream matches a golden transcript (modulo timestamps/ULIDs).
- **Pass**: ordered events match the golden; no `SafetyInterrupt(WARN, "internal error…")` anywhere; total wall-clock < 30 s with doubles.
- **Builds on**: §3.1–§3.5.

### 3.7 Graceful degradation / zero-config boot

- **Spans**: `config.py`, every adapter's stub path.
- **Proves**: the system boots and the full advisory loop runs with **no env vars set** (`07 §2.4`, `05 §6`).
- **Method**: launch with an empty environment; assert healthz ok; run a trimmed `§3.6`; assert SMEs run from `GEMINI_SME_MODEL` stubs / canned responses, `lookup_*` return canned data, `get_documented_limit` → `found=false` so the `set_psu(30 V)` step is **DENIED by the conservative default** (12 V) — and that this denial is surfaced cleanly (not a crash). Then load the fixture `board.yaml` and assert the same step now confirms. This double-checks the fail-safe-absence rule (`03 §6`).
- **Pass**: boots clean; canned flow runs; absent-limit denies safely; present-limit confirms.
- **Builds on**: BK-7, SG-9.

### 3.8 Replay / HITL across reconnect

- **Spans**: checkpointer, chat bus replay, SafetyGate interrupt.
- **Proves**: a session interrupted at a pending InstructionCard survives a client reconnect and the operator can still complete the step.
- **Method**: run to a SafetyGate interrupt; drop the WS; reconnect with the same `sessionId`; assert `ChannelList` + last-200 messages + the pending `ConfirmationRequest` re-emit + `ReplayDone`; send `approved=True`; assert the graph resumes from the checkpoint and audits done.
- **Pass**: pending card reproduced; resume completes from checkpoint.
- **Builds on**: CB-7, GR-15.

---

## 4. Contract-alignment matrix

The concrete "endpoints and contracts line up" check. Every cross-process contract → who emits it → who consumes it → the test that proves they agree. A change to any row that breaks alignment fails the named test.

| # | Contract | Producer | Consumer | Proven by |
|---|---|---|---|---|
| C1 | `AgentEvent` union (JSON shape) | graph nodes, chat bus | client renderer (`04 §3`) | §3.1, WP-6 |
| C2 | `SmeResponse` envelope | SME sandbox (`02 §4`) | `MergeOpinion`, `DissentDetector` | §3.2, SME-4 |
| C3 | `ChannelUpdate` streaming deltas | `StreamingAggregator` | client delta assembler (`04 §3.2`) | §3.3, CB-2 |
| C4 | `ActionCard` / `ConfirmationRequest` ↔ `ConfirmationResponse` | SafetyGate | client InstructionCard + back | §3.3, CB-6, GR-11 |
| C5 | `get_documented_limit(target,kind)` result | KnowledgeAdapter (`05 §3.3`) | SafetyGate (`03 §6`) | §3.4, SG-2/3, BK-2 |
| C6 | operator-step `tool` verbs (`05 §5`) | SME `proposedActions` | matrix rows (`03 §3`) + client renderer | §3.4, BK-8 |
| C7 | `FrameRef` from FrameTap | `live/frame_tap.py` (`00 §4`) | `PerceptionGate`, SME `inbox`, `@sentinel` | §3.5 |
| C8 | `ChannelList` roster | chat bus (`04 §2`) | client + SME registry (`02`, `07`) | §3.6, CB-1, SME-3 |
| C9 | checkpoint + replay (`01 §5`, `04 §6`) | checkpointer | chat-bus reconnect | §3.8, CB-7, GR-15 |
| C10 | stub/fallback contracts (`05 §6`, `07 §2.4`) | each adapter | whole system | §3.7, BK-7 |

If a row has no green test, the contract is unverified — treat it as a release blocker for the demo.

---

## 5. Test doubles & fixtures

- **SME double**: a stub Managed-Agents env that returns a pre-registered `SmeResponse` (per `(sme, scenario)` key) and optionally streams it token-by-token. Covers the four structured-output cases (`§3.2`).
- **Live double**: replays a scripted transcript + a synthetic video stream (a folder of JPEGs at a fixed cadence) into `GeminiLiveBridge`, so the FrameTap has real frames to sample (`§3.5`).
- **Operator double**: a scripted responder that, given a `ConfirmationRequest`, replies `approved=True/False` after a configurable delay and optionally injects a spoken reading ("VIO is 3.28 volts") back into the transcript.
- **KnowledgeAdapter fixture**: `tests_integration/fixtures/board.yaml` = the `bq79616-bringup-2026-05` profile (J3 max 30 V, preconditions) + a canned datasheet table.
- **Golden corpus**: `testdata/wire/*.json` (one per `AgentEvent` variant) — the single source of truth for `WP-6` and `§3.1`.
- **Golden transcript**: `testdata/demo/bq79616_golden.jsonl` — the expected ordered event stream for `§3.6`.

`@live` marker: tests that hit real Gemini Live / real Managed Agents / real Vertex Search. Excluded from CI; run nightly and in the `06 §2` pre-demo checklist.

---

## 6. Non-functional gates

| Gate | Target | Test |
|---|---|---|
| Warm single-round deliberation latency | p95 < 5 s | `§3.6` with `@live` SMEs, timed (Spike 3) |
| Chat-bus backpressure | drops `ChannelUpdate` before `ChatMessage`; never blocks the graph | CB-5 + a flood variant of `§3.3` |
| FrameTap overhead | sampling adds < 50 ms p95 to the Live forward path; no frame backlog | `§3.5` timed |
| Zero-config boot time | < 5 s to healthz | `§3.7` |
| Demo API spend | < $50 for a full rehearsal day | cost counter in metrics (`07 §9`) |

---

## 7. Explicitly NOT tested (and why)

- **Hardware-in-the-loop.** There is no hardware to drive; the operator is a human (a double in tests). The closest analog is the operator-double reporting outcomes (`§5`).
- **Instrument drivers.** Deleted with the bench daemon.
- **A second frame-upload path.** It does not exist by design; `§3.5` asserts its *absence* rather than testing it.
- **SME internal tool calls** (sandbox file IO, code exec) — internal to Managed Agents, never on our wire (`00 §10`); we test only the `SmeResponse` they produce.

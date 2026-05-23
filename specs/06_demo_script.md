# 06 — Demo Script

> Minute-by-minute 3-minute live demo. Backup paths. 60-second cut. Q&A prep.
> Cross-refs: `02_sme_persona_format.md` (SME behavior), `03_safety_gate_matrix.md` (UI cards), `05_bench_daemon_api.md` §8 (mock-bench fallback), `07_environment_setup.md` §5 (pre-warm).

---

## 1. The bug we demo

**Primary**: a BME280 environmental sensor on a custom breakout board failing intermittently when an adjacent ESP32 turns on its WiFi radio. Root cause: the 3.3 V rail droops below the BME280's brown-out threshold during ESP32 TX bursts because the on-board AMS1117 LDO can't source the transient current and the bulk cap (a single 10 µF X5R 0603) is undersized.

Why this bug:
- Carryover from Forge v1's reference bug, so we already have the PCB.
- Shows off ≥4 SMEs (`@power`, `@signal`, `@firmware`, `@librarian`) plus `@sentinel` opportunistic interjection.
- Resolution requires the guild to disagree visibly (`@signal` will initially blame I²C bus integrity; `@power` will spot the rail droop).
- Has a satisfying physical "before" (sensor reports NaN) and "after" (we add a bulk cap or current-limit ESP32 TX and the readings stabilise).

**Backup**: an Arduino Uno R3 with a misconfigured serial baud rate (9600 vs 115200) on a freshly-flashed sketch. Trivial bug but it exercises `@firmware`, `@librarian`, voice transcript with garbled characters, and a single bench action (`bench.serial_send` to verify after fix). Use this if the primary bug stages but the BME280 won't cooperate.

---

## 2. Pre-demo checklist (executed 10 minutes before)

| # | Step | Owner | Verify |
|---|---|---|---|
| 1 | `forge-orchestrator` running on `localhost:8080` | eng A | `curl localhost:8080/healthz` returns `{"ok":true}` |
| 2 | Bench daemon running on `localhost:9090`, real-mode | eng B | `forge-bench --selftest` exits 0; PSU shows 0V/0A enabled=false |
| 3 | All SME envs pre-warmed (07 §5) | eng A | `forge-orchestrator-cli sme status` shows all "warm" |
| 4 | Phone client connected, all channels visible | eng A | `#power`, `#signal`, `#firmware`, `#sentinel`, `#dissent`, `#actions`, `#live-feed` visible |
| 5 | Gemini Live session up, voice tested ("hello") | eng A | hear voice response within 2 s |
| 6 | Bug present on PCB: ESP32 firmware in the WiFi-loop sketch loaded; BME280 producing NaN | eng B | scope confirms 3.3V droop during TX |
| 7 | Mock bench ready as fallback (`forge-bench --mock` on port 9091) | eng B | port responds |
| 8 | Chat bus replay tested by reconnecting client | eng A | last 200 messages reload, action cards reappear if pending |
| 9 | Confirm projector / Zoom mirrors the phone screen | eng A | demo audience sees chat UI clearly |
| 10 | Sentinel false-positive guard: cover bench camera lens, verify no spurious HALT in 30s | eng B | `#sentinel` quiet |

If any check fails, see §5 (backup paths).

---

## 3. Three-minute live script

Timing assumes 5s for any voice prompt to complete and ~3s of guild deliberation per round (Spike 3 dependent; if cold-start p95 exceeds 8s we use the warm-pool §6).

### 0:00 — 0:15 — Opening

User picks up phone, points camera at the PCB.

**User says**: "Forge, my BME280 keeps returning NaN every few seconds. What's going on?"

**On screen**:
- `#live-feed` shows the transcript and "Consulting the guild on bme280-nan-intermittent…"
- The other channels (`#power`, `#signal`, `#firmware`, `#librarian`) begin streaming token deltas.

This is the money shot for the prize: multiple agents reasoning in parallel, visibly.

### 0:15 — 0:45 — First-round deliberation

**`@firmware`** posts first (most likely): "Reading the serial log via `@user`'s frame; saw I²C reads timing out periodically. Suspect bus contention."

**`@signal`** posts: "If the BME280's data line is glitching, I'd capture I²C during a failure window."

**`@power`** posts (key moment): "I see a 10 µF bulk cap and an AMS1117. Datasheet says 1.1 V dropout at 0.5 A. ESP32 WiFi TX peaks ~500 mA. I think the rail droops, not the bus."

**`@librarian`** (always-on, passive): pins the BME280 datasheet page showing the 1.71 V brown-out spec.

`#dissent` channel auto-emits a DissentReport:
- `@signal` vs `@power` on root cause (bus vs rail)
- `@firmware` adjacent (no strong claim either way yet)

### 0:45 — 1:15 — Cross-examination round

Orchestrator's `DissentDetector` triggers a second round with a cross-exam prompt.

`@signal` proposes: "Capture rail AND I²C simultaneously to settle this." Posts `ProposedAction` for `bench.capture_logic` with both channels.

`@power` agrees: "Yes, run the capture during a known-fail window. Trigger on ESP32 GPIO that goes high during TX."

SafetyGate sees `bench.capture_logic` with `duration_ms=2000` → LOW risk, no confirmation. Auto-dispatches.

**On screen**: `#actions` shows the capture progress; capture completes; `bench.decode_protocol` decodes the I²C; the rail trace shows a clear droop to ~2.6 V during ESP32 TX, with I²C arbitration errors only WHILE the rail is sagging.

**`@power`** posts the synthesised SmeResponse with `confidence=0.92`: "Root cause is rail droop. The I²C errors are a symptom, not the cause."

**`@signal`** updates: "Concede. Power is right; my I²C theory was downstream."

Dissent resolved.

### 1:15 — 1:45 — Action proposal & confirmation

**`@power`** proposes two actions in priority order:

1. `bench.set_psu(channel=2, voltage_v=3.6, current_limit_a=1.0)` to power the BME280 from a STIFFER supply via a flying lead (workaround test) — Risk = MEDIUM (V > 5V threshold not triggered but I > 0.5A is).
2. Recommend a physical fix (add a 100 µF bulk cap) — not actionable from the bench, surfaced as a `#scribe` note.

`SafetyGate` emits an `ActionCard` for action 1. Live verbally reads: "Power recommends powering the BME from PSU channel 2 at 3.6 V, 1 amp limit. Approve?"

**User says**: "Yes, do it."

`ConfirmationResponse(approverChannel="voice")` lands. Daemon dispatches `set_psu` then `enable_psu_output`.

### 1:45 — 2:30 — Verification

`@firmware` proposes `bench.serial_send(port="ttyACM0", payload="i2c-test")` — LOW risk, auto-allowed. The test command triggers a 20-sample BME280 read. Output streams into `#firmware`.

**On screen**: serial output shows 20 valid readings, no NaN.

**`@power`** posts final SmeResponse: "Confirmed. The fix is decoupling. Recommend a 100 µF tantalum or low-ESR ceramic near the BME280's Vcc pin."

**`@scribe`** posts a session-report excerpt into `#scribe` with the fix recommendation, the trace screenshots, and the BOM line for the cap.

### 2:30 — 2:50 — `@sentinel` cameo (scripted)

Eng B briefly brings a soldering iron near the camera frame.

**`@sentinel`** emits a WARN: "Hot iron near live bench. Recommend powering down PSU before any rework."

Live verbally reads it. `#sentinel` shows the message. This demonstrates the always-on safety surface without a true HALT.

### 2:50 — 3:00 — Close

**User says**: "Publish the report."

`@scribe` finalises and emits `publish_report` (which produces a stub Docs URL in dev mode; real URL if Workspace API is wired). `#actions` shows the link.

End.

---

## 4. The 60-second cut (video / submission)

Compressed to a single arc, captured as a screen-record of the phone plus a wide shot of the bench.

| Time | Beat |
|---|---|
| 0:00 | "BME280 keeps returning NaN" — point at PCB |
| 0:08 | guild channels start populating with deltas; `#dissent` lights up |
| 0:20 | jump cut: `@power`'s rail-droop hypothesis on screen |
| 0:28 | bench.capture_logic auto-runs; scope-like trace appears in `#power` |
| 0:38 | ActionCard appears; user voice-approves; PSU spins up |
| 0:46 | serial output streams clean readings into `#firmware` |
| 0:52 | `@sentinel` warning flashes |
| 0:58 | session report link appears |

Cuts removed from the live version:
- The second round of dissent (just show one pass)
- The `@librarian` datasheet pin
- The verification serial test

What MUST stay in: the parallel-channel deliberation, the dissent moment, one ActionCard with voice approval, sentinel.

---

## 5. Backup paths

### 5.1 The real bench fails to power on

- Switch `BENCH_DAEMON_URL` env to the mock daemon (`ws://localhost:9091`).
- Mock daemon serves synthesized telemetry and pre-recorded captures (see `05 §8`).
- The guild deliberation, dissent, MergeOpinion, SafetyGate, ConfirmationRequest flow ALL still work — only the bench tools are stubs.
- Demo narrative changes slightly: emphasize "the system is fully simulated for safety in this venue" rather than pretending the bench is real.

### 5.2 Gemini Live voice is down

- Fall back to typed input via `#general`.
- LiveSpeaker's text mirrors are still emitted; the chat-only demo shows the guild collaborating.
- Lose the "voice-approves an ActionCard" beat; replace with chat-tap approval.

### 5.3 One SME's environment refuses to warm

- Orchestrator detects on summon; surfaces "SME unavailable" in chat.
- Demo loses one channel but the rest of the flow holds. Choose a bug that doesn't depend on the unavailable SME.
- Most robust path: pre-load the BME280 bug scenario context into a backup SME (`@tutor` can stand in for `@power` if needed, with the right beliefs in `state/`).

### 5.4 Network goes down mid-demo

- Show the chat-bus replay feature: reconnect, watch the last 200 messages reload, pending ActionCard reappears, user can complete the approval.
- Pre-rehearsed: deliberately disconnect at 1:40 to demonstrate replay BUT ONLY IF the demo is going well; skip if tight on time.

### 5.5 `@sentinel` false-positives during the demo

- Eng A has a `force_clear_sentinel` admin command on the orchestrator CLI.
- Voice: "Sentinel, stand down" — the orchestrator recognizes this and silences `@sentinel` for 30s.
- Worst case, eng A toggles sentinel via admin CLI from a laptop in the wings.

### 5.6 We blow up the BME280 live

- This is the funny one. Eng B has 3 spare BME280 breakouts and a hot-swap socket on the demo PCB.
- Voice: "Forge, that smells like magic smoke." Sentinel will trigger; demo lean into it as proof the safety layer works.
- Reset the bench, swap the chip, restart the session.

---

## 6. Q&A prep

Anticipated judge questions and rehearsed answers.

| Question | One-line answer |
|---|---|
| "How is this different from a single big agent with tools?" | "Parallel SMEs with explicit dissent surface. The user sees disagreement before action, not after a wrong action." |
| "What stops the SMEs from hallucinating measurements?" | "Two layers: SafetyGate blocks unsupported actions; @sentinel monitors physical telemetry independently. SMEs propose; the system verifies." |
| "Why LangGraph?" | "Native HITL interrupts for the SafetyGate, native checkpointing for replay (which we just demoed), and conditional edges that let dissent loops bounded-retry." |
| "Why Managed Agents?" | "Per-SME persistent sandboxes mean each SME has scratchpad memory across turns, real code execution for analysis (rail-budget calc, OCR), and AGENTS.md/SKILL.md customisation without server-side prompt mgmt." |
| "How does Gemini Live fit?" | "Voice + camera entrypoint. Function-calling kicks off the guild via summon_guild; the result reflects back through Live's voice for the answer." |
| "What about latency?" | "Cold-start is our worst case; we pre-warm. Once warm, single-round deliberation is sub-5s p95. (Spike 3 numbers as of demo: …)" |
| "Could a real engineer trust this?" | "The audit trail is immutable in Firestore and the local bench log. Every decision is reproducible from the checkpoint. The bench daemon has hard limits the orchestrator can't override." |
| "What if two SMEs deadlock?" | "DissentDetector bounces once and only once. After that, MergeOpinion presents both views to the user as openQuestions." |
| "How does sentinel actually detect hazards?" | "It subscribes to frames, transcript, and bench telemetry. The model + a small set of heuristics (overcurrent > 2× setpoint for 200ms etc.). It's the only agent that can pre-empt voice." |
| "Could the user override sentinel?" | "Yes, by voice. Sentinel is a fail-safe, not a lockout. Override is logged and persists for the session." |

---

## 7. What we do NOT say on stage

- The 5 spike statuses by name (if a spike is unresolved, the demo paths around it; we don't draw attention to it).
- "DEPENDS ON SPIKE N" terminology (internal).
- The mock-bench fallback (unless explicitly asked about backups).
- Anything about Forge v1 unless asked.

---

## 8. Submission deliverables (5 PM PT)

| Item | Owner | Notes |
|---|---|---|
| 60-second video | eng A | upload to YouTube unlisted; submit link |
| 3-minute live demo | both | done at venue or recorded if we miss the slot |
| Repo URL | eng B | tag v2-demo at submission time |
| Architecture diagram (PNG) | eng A | export the §1 process map from 00_wire_protocol.md cleaned up |
| Written description | eng B | 250 words max; lead with "parallel SMEs with explicit dissent" |
| "Best use of Managed Agents" prize justification | eng A | 100 words; emphasize per-SME persistent sandboxes + AGENTS.md/SKILL.md + code execution for analysis |

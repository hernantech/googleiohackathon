# 06 — Demo Script

> Minute-by-minute 3-minute live demo. Backup paths. 60-second cut. Q&A prep.
> Forge advises a **human operator**; it actuates nothing. Every physical step is performed by the presenter at the bench, on Forge's spoken/carded instruction. There is no bench daemon and no BME280.
> Cross-refs: `02_sme_persona_format.md` (SME behavior), `03_safety_gate_matrix.md` (InstructionCards), `05_board_knowledge_api.md` (board profile + lookups), `07_environment_setup.md` §5 (pre-warm), `08_test_plan.md` §3.6 (this script as an integration test).

---

## 1. The bug we demo

**Primary**: an **ESP32 + BQ79616** 16-cell battery-monitor bring-up board. The ESP32 host reports a **comm timeout** — it cannot read any cell voltages from the BQ79616 over the daisy-chain (via the BQ79600 bridge).

Root cause: the operator energized only the **VIO/logic** supply; the **emulated cell stack is not applied** to the BQ79616's VC inputs. Per the BQ79616 datasheet §7 power-up sequence, the AFE will not complete wake-up or respond on the comm bus until a valid stack is present on its cell pins. **The comm timeout is a symptom, not the cause.**

Why this bug:
- Uses exactly the hardware on hand: ESP32, BQ79616, bench PSU, multimeter, soldering station.
- Shows ≥4 SMEs (`@firmware`, `@signal`, `@power`, `@librarian`) plus `@sentinel`'s opportunistic interjection.
- Produces a genuine, *visible* disagreement: `@firmware` and `@signal` chase the comm bus (baud/init, daisy-chain integrity); `@power` spots the missing stack voltage. The "symptom vs cause" reveal is the showpiece.
- Every value Forge tells the presenter (the 30 V cell-stack, the 0.5 A limit, the J3 connector) comes from a **`lookup_board_doc` / `get_documented_limit` call against the board profile** — the literal "tool call to find what voltage to set" beat.
- Satisfying physical before/after: "comm timeout, NaN cell voltages" → after the human applies the stack → "16 valid cell voltages."

**Backup**: a plain ESP32 with a serial console at the wrong baud (9600 vs 115200), so the host log is garbled. Exercises `@firmware` + `@librarian`, voice transcript with garbled characters, and a single guided step (`serial_send` at the right baud, then re-read). Use this if the BQ79616 board won't stage.

---

## 2. Pre-demo checklist (executed 10 minutes before)

| # | Step | Owner | Verify |
|---|---|---|---|
| 1 | `forge-orchestrator` running on `localhost:8080` | eng A | `curl localhost:8080/healthz` returns `{"ok":true}` |
| 2 | Board profile loaded (`~/.forge/board.yaml` = `bq79616-bringup-2026-05`) | eng B | `forge-orchestrator-cli board show` prints J3 max=30 V, preconditions |
| 3 | All SME envs pre-warmed (07 §5) | eng A | `forge-orchestrator-cli sme status` shows all "warm" |
| 4 | Phone client connected, all channels visible | eng A | `#power`, `#signal`, `#firmware`, `#sentinel`, `#dissent`, `#actions`, `#live-feed` visible |
| 5 | Gemini Live session up, voice tested ("hello") | eng A | hear voice response within 2 s |
| 6 | Bug staged: ESP32 flashed with the BQ79616 host sketch; VIO powered but cell-stack PSU OFF; ESP32 console shows "comm timeout" | eng B | console prints timeout; no cell reads |
| 7 | Bench PSU + DMM + soldering station in camera frame, PSU output OFF | eng B | camera sees the bench clearly |
| 8 | Chat-bus replay tested by reconnecting client | eng A | last 200 messages reload, InstructionCard reappears if pending |
| 9 | Confirm projector / Zoom mirrors the phone screen | eng A | audience sees chat UI clearly |
| 10 | Sentinel false-positive guard: cover camera lens, verify no spurious HALT in 30 s | eng B | `#sentinel` quiet |

If any check fails, see §5 (backup paths).

---

## 3. Three-minute live script

Timing assumes ~5 s for any voice prompt to complete and ~3 s of guild deliberation per round (Spike 3 dependent; if cold-start p95 exceeds 8 s we use the warm-pool, `07 §5`).

### 0:00 — 0:15 — Opening

Presenter picks up the phone, points the camera at the board and the ESP32 serial console.

**Presenter says**: "Forge, my ESP32 can't read the BQ79616 — I'm getting a comm timeout every read. What's going on?"

**On screen**:
- `#live-feed` shows the transcript and "Consulting the guild on bq79616-comm-timeout…"
- The FrameTap has already sampled the bench frame; `#power`, `#signal`, `#firmware`, `#librarian` begin streaming token deltas.

This is the money shot: multiple agents reasoning in parallel, visibly.

### 0:15 — 0:45 — First-round deliberation

**`@firmware`** posts first (most likely): "Reading the host log in frame — the BQ79600 init returns, but cell reads time out. Suspect baud or the wake/init register sequence."

**`@signal`** posts: "If it's the daisy-chain, COMH/COML integrity or termination could be the issue. I'd want to scope the comm lines during a read."

**`@power`** posts (key moment): "Look at the bench — only the VIO supply is on. The BQ79616 needs a valid cell stack on its VC pins to wake. `lookup_board_doc` → datasheet §7 power-up. If the stack isn't applied, it never responds. The timeout is a symptom."

**`@librarian`** (always-on, passive): pins the BQ79616 datasheet §7 page describing the power-up requirement and the wake-tone timing.

`#dissent` auto-emits a DissentReport:
- `@power` vs `@firmware`/`@signal` on root cause (missing stack power vs comm bus)

### 0:45 — 1:15 — Cross-examination round

`DissentDetector` triggers a second round with a cross-exam prompt. Presenter can also tap `[🔀 cross-examine]` or say "have them cross-examine."

**`@firmware`** concedes the ordering: "Fair — if the AFE isn't powered, my init would time out exactly like this regardless of baud."

**`@power`** proposes the discriminating step: "Apply the emulated cell stack, then re-read. If reads succeed, it's power; if they still fail, it's the bus." Posts a `probe_net` step (LOW) to confirm VIO first, and a `set_psu` step for the cell-sim ladder.

Dissent resolves toward `@power`.

### 1:15 — 1:45 — Guided action & confirmation

**`@power`** proposes, in order:

1. `probe_net(net="VIO", test_point="TP4", mode="dcv")` — LOW, auto-shown. **Forge says**: "Probe VIO at TP4 and tell me the reading." Presenter measures, says "3.28 volts." (Confirms logic supply is fine — sharpens the story.)
2. `set_psu(channel=1, voltage_v=30.0, current_limit_a=0.5, target="cell-sim ladder J3")` — value pulled from `get_documented_limit(J3)` = 30 V max. Risk = **HIGH** (V > 12 V), within documented limit → confirm with 3 s delay.

`SafetyGate` emits an **InstructionCard** for step 2 showing "board doc max: 30 V". Live verbally reads: "Set the bench PSU to 30 volts, half-amp limit, across the cell-sim ladder at J3, then enable the output. The board doc confirms 30 volts is the documented max. Tell me when it's done."

**Presenter physically sets the PSU**, enables output, and says: "Done."

`ConfirmationResponse(approved=True, approverChannel="voice")` lands → audit `operatorOutcome="done"`. (Forge dispatched nothing — the human did it.)

### 1:45 — 2:30 — Verification

**`@firmware`** proposes `serial_send(port="ttyACM0", payload="read_cells")` — LOW, auto-shown. **Forge says**: "Now send `read_cells` in your console and read me the output."

**On screen / in frame**: the ESP32 console now prints 16 valid cell voltages, no timeout. Presenter reads a couple aloud; `@firmware` echoes them into `#firmware`.

**`@power`** posts the final SmeResponse: "Confirmed — root cause was the missing cell stack, not the comm bus. Going forward, document 'apply stack before host read' in the bring-up checklist."

**`@scribe`** posts a session-report excerpt into `#scribe` with the root cause, the datasheet citation, and the corrected bring-up step.

### 2:30 — 2:50 — `@sentinel` cameo (scripted)

Eng B brings the (hot) soldering iron toward the board while the PSU output is still on.

**`@sentinel`** emits a WARN: "Hot iron near a powered board. If you're about to rework, turn the PSU output off first." (`rework_requires_psu_off` precondition.)

Live verbally reads it; `#sentinel` shows it. Demonstrates the always-on safety surface without a true HALT — and shows that Forge tells the *human* to power down, because Forge has no kill switch.

### 2:50 — 3:00 — Close

**Presenter says**: "Publish the report."

`@scribe` finalises and proposes `publish_report` (stub Docs URL in dev mode; real URL if Workspace API wired). `#actions` shows the link.

End.

---

## 4. The 60-second cut (video / submission)

Compressed to a single arc: screen-record of the phone plus a wide shot of the bench (hands visible).

| Time | Beat |
|---|---|
| 0:00 | "ESP32 can't read the BQ79616 — comm timeout" — point at the board + console |
| 0:08 | guild channels populate with deltas; `#dissent` lights up |
| 0:20 | jump cut: `@power`'s "missing cell stack, comm is a symptom" on screen |
| 0:30 | InstructionCard: "Set PSU to 30 V across J3 (board doc max 30 V)"; presenter turns the knob |
| 0:42 | presenter says "done"; sends `read_cells` |
| 0:48 | ESP32 console streams 16 valid cell voltages into `#firmware` |
| 0:54 | `@sentinel` "power down before rework" warning flashes |
| 0:58 | session report link appears |

Cuts removed from the live version: the second dissent round (show one pass), the `@librarian` datasheet pin, the VIO `probe_net`.

What MUST stay in: the parallel-channel deliberation, the dissent moment, **one InstructionCard with a documented-limit citation and a voice "done"**, sentinel.

---

## 5. Backup paths

### 5.1 No network / APIs flaky at the venue

- Run fully offline (`05 §6`): no `GEMINI_API_KEY`/`MANAGED_AGENTS_API_KEY`/`VERTEX_SEARCH_DATASTORE_ID`. SMEs run from `GEMINI_SME_MODEL` stubs or canned responses; datasheet/board-doc lookups serve canned excerpts from the bundled profile.
- The guild deliberation, dissent, MergeOpinion, SafetyGate, InstructionCard flow ALL still work — only the *quality* of SME reasoning degrades. Narrative: "the full advisory loop runs locally."

### 5.2 Gemini Live voice is down

- Fall back to typed input via `#general`. LiveSpeaker's text mirrors still emit; the chat-only demo shows the guild collaborating.
- The "voice 'done'" beat becomes a chat-tap "I did it" on the InstructionCard.

### 5.3 One SME's environment refuses to warm

- Orchestrator detects on summon; surfaces "SME unavailable" in chat.
- Demo loses one channel but the rest holds. The BQ79616 story survives losing @signal or @reverse; protect `@power` and `@firmware`.
- Most robust path: pre-load the BQ79616 scenario context into a backup SME (`@tutor` can stand in for `@power` with the right `state/beliefs.md`).

### 5.4 Network drops mid-demo

- Show chat-bus replay: reconnect with the same `sessionId`, watch the last 200 messages reload, the pending InstructionCard reappears, the presenter completes "I did it."
- Pre-rehearsed: deliberately disconnect at ~1:40 to demonstrate replay — ONLY if the demo is going well; skip if tight on time.

### 5.5 `@sentinel` false-positives during the demo

- Voice: "Forge, stand down" — the orchestrator silences `@sentinel` for 30 s and logs the override (`03 §8`).
- Eng A has a `force_clear_sentinel` admin command on the orchestrator CLI as a last resort.

### 5.6 We actually damage the board live

- Eng B has a spare bring-up board and a hot-swap socket for the BQ79616.
- Because Forge never energized anything, any damage was operator error — lean into "the safety layer warned us, and the human is always in the loop." Power down, swap, restart the session.

---

## 6. Q&A prep

| Question | One-line answer |
|---|---|
| "How is this different from a single big agent with tools?" | "Parallel SMEs with an explicit dissent surface. You see the disagreement *before* you touch anything, not after a wrong action." |
| "Wait — it doesn't control the bench?" | "By design. The human is the operator; Forge is the guild that watches, deliberates, and hands you cited, safety-gated instructions. No relay it can flip means no relay it can flip *wrongly*." |
| "What stops the SMEs from hallucinating a setpoint?" | "Two layers: every value-bearing instruction must cite a documented limit (`get_documented_limit`), and SafetyGate denies anything above it. No citation → it's downgraded to 'verify this yourself'." |
| "Why LangGraph?" | "Native HITL interrupts for the confirmation pause, native checkpointing for replay (which we just demoed), and bounded conditional edges for the dissent loop." |
| "Why Managed Agents?" | "Per-SME persistent sandboxes: scratchpad memory across turns, real code execution for analysis (rail-budget calc, OCR of the chip in frame), and AGENTS.md/SKILL.md customisation without server-side prompt management." |
| "How does Gemini Live fit?" | "Voice + camera entrypoint. One media stream; we tap frames server-side so the SMEs see exactly what Live sees. `summon_guild` kicks off the guild; the answer reflects back through Live's voice." |
| "Why is there no separate camera/frame upload?" | "The video already flows to the orchestrator on its way to Live. A second JPEG channel would just double the bandwidth and let the two streams drift. We tap and sample the one stream — single source of truth." |
| "What about latency?" | "Cold-start is our worst case; we pre-warm. Once warm, single-round deliberation is sub-5 s p95. (Spike 3 numbers as of demo: …)" |
| "Could a real engineer trust this?" | "The audit trail is immutable in Firestore — every decision, every cited limit, every operator outcome is reproducible from the checkpoint. And the engineer's own hands and eyes are the final layer." |
| "What if two SMEs deadlock?" | "DissentDetector bounces once and only once. After that, MergeOpinion presents both views as openQuestions for the human to decide." |
| "How does sentinel detect hazards without telemetry?" | "Vision and voice — it watches the camera and listens. It can't read a current sensor because there is no sensor wire; it watches for smoke, a hot iron over a live board, and panic in your voice, and it commands you to power down." |

---

## 7. What we do NOT say on stage

- The 5 spike statuses by name (if a spike is unresolved, the demo paths around it).
- "DEPENDS ON SPIKE N" terminology (internal).
- The offline/stub fallback (unless explicitly asked about backups).
- Anything about Forge v1 unless asked.

---

## 8. Submission deliverables (5 PM PT)

| Item | Owner | Notes |
|---|---|---|
| 60-second video | eng A | upload to YouTube unlisted; submit link |
| 3-minute live demo | both | done at venue or recorded if we miss the slot |
| Repo URL | eng B | tag v2-demo at submission time |
| Architecture diagram (PNG) | eng A | export the §1 topology from ARCHITECTURE.md cleaned up |
| Written description | eng B | 250 words max; lead with "parallel SMEs with explicit dissent, guiding a human operator" |
| "Best use of Managed Agents" prize justification | eng A | 100 words; emphasize per-SME persistent sandboxes + AGENTS.md/SKILL.md + code execution for analysis |

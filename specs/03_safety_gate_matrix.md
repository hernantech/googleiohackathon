# 03 — Safety Gate Matrix

> Every operator step × invoker × gate decision × risk level × confirmation UI.
> Forge advises a human; it actuates nothing. The gate governs **what Forge instructs the operator to do**. Two independent safety layers: the orchestrator `SafetyGate` LangGraph node, and the **documented board limits** (`05 §4`) plus `@sentinel`'s live hazard watch. (There is no bench daemon, so there is no daemon-side enforcement layer.)
> Cross-refs: `00_wire_protocol.md` §2 (ConfirmationRequest / ActionCard / SafetyInterrupt / ProposedAction.actor), `01_langgraph_state_machine.md` §3.7 (SafetyGate node behavior), `05_board_knowledge_api.md` §4 (documented limits).

---

## 1. Risk ladder

Risk describes **how dangerous it is to follow the instruction**, not how dangerous a machine action is.

| Risk | Meaning | UI treatment | Default behavior |
|---|---|---|---|
| `LOW` | read-only or trivially reversible (probe a net, look something up, move the camera) | inline notice in `#actions` channel | auto-show, no confirm |
| `MEDIUM` | changes the board's energized state but bounded and reversible (enable output, set a modest PSU voltage) | InstructionCard in chat + Live verbal summary | require explicit "I did it" / "Skip" |
| `HIGH` | high voltage/current, code flash, or hot-iron rework — anything that can damage hardware or the operator | InstructionCard with diff + documented-limit citation + Live summary + 3 s delay before the affirm button enables | require explicit "I did it" + voice OR chat acknowledgement |
| `HALT` | hazard mitigation (`@sentinel` only) | full-screen takeover; voice interrupt; "POWER DOWN NOW" | command attention; instruct the human to kill the PSU; then log |

`HALT` is not a normal risk level. It is `@sentinel`'s privileged channel and bypasses the normal confirmation flow (see §5). It still cannot actuate anything — it commands the human's attention.

---

## 2. Confirmation flow

```
SafetyGate (01 §3.7) ──> emits ConfirmationRequest(callId, actionCard) ──┐
                                                                          │
                                                                          ▼
              ChatBus (04) renders InstructionCard      LiveSpeaker reads the step
                          │                                       │
                          ▼                                       ▼
              user taps "I did it" / "Skip"        user says "done" / "skip" / "hold"
                          │                                       │
                          └─────────────┬─────────────────────────┘
                                        ▼
                       ConfirmationResponse(callId, approved, approverChannel)
                                        │
                                        ▼
                          SafetyGate resumes (HITL interrupt)
                                        │
                          approved? ───┤
                                        ├── True  ("I did it") → record outcome="done", continue
                                        └── False ("Skip")     → drop step, outcome="skipped", log
```

`approved=True` means **the human reports they performed the step** — not that a machine executed it. Whichever channel (voice or chat) responds first wins; the losing channel's UI updates to "noted via <channel>" and dismisses.

Timeout: 60 s of no response → Live re-asks ("did you do that step?"). +60 s further → record `outcome="timeout"` and continue (do NOT block the session — a stuck card mid-demo is worse than a missing one).

---

## 3. Step × invoker × gate matrix

Columns:
- **Step**: the `ProposedAction.tool` verb (with `actor="operator"` unless noted)
- **Invokable by**: which SME personas may recommend this step (mirrors `02` AGENTS.md lanes; SafetyGate re-checks)
- **Gate (orchestrator)**: what SafetyGate does
- **Documented-limit check**: the second layer — value validated against the board profile (`05 §4`); fail → DENY
- **Risk**: default classification (may be elevated by gate logic)
- **Confirmation UI**: what the operator sees

### 3.1 Physical operator steps

| Step | Invokable by | Gate (orchestrator) | Documented-limit check | Risk | Confirmation UI |
|---|---|---|---|---|---|
| `probe_net` (measure a net with the DMM, read value back) | @power, @signal, @firmware, @sentinel | allow | none (read-only) | LOW | `#actions` log only |
| `inspect_closeup` (move the phone camera nearer a part) | @reverse, @signal, @power | allow | none | LOW | inline notice |
| `set_psu` (V ≤ 5 V AND I ≤ 0.5 A) | @power | allow | V,I ≤ documented net max | LOW | inline notice |
| `set_psu` (V > 5 V OR I > 0.5 A) | @power | confirm; include "now → set" diff in card | V,I ≤ documented net max | MEDIUM | InstructionCard with diff |
| `set_psu` (V > 12 V OR I > 1 A) | @power | confirm; HIGH; 3 s delay; show documented limit | V,I ≤ documented net max | HIGH | InstructionCard, delayed-affirm |
| `set_psu` (> documented net max) | nobody | DENY at gate; emit WARN citing the limit | hard fail | — | denied notice |
| `enable_psu_output` | @power | confirm always | a fresh `set_psu` was instructed first | MEDIUM | InstructionCard |
| `disable_psu_output` | @power, @sentinel | allow (always safe to tell someone to power down) | none | LOW | inline notice |
| `disable_psu_output` (@sentinel HALT path) | @sentinel | bypass normal flow → full-screen takeover | none | HALT | "POWER DOWN NOW" banner + voice |
| `serial_send` (no dangerous-pattern match) | @firmware | allow | none | LOW | log |
| `serial_send` (matches `reset`, `calibrate`, `format`, `erase`, `shutdown`) | @firmware | confirm; HIGH | none | HIGH | InstructionCard |
| `flash_mcu` (tell operator to flash an image) | @firmware | confirm; HIGH; show firmware hash + size | require PSU output off first; image hash present | HIGH | InstructionCard with diff |
| `reflow_pin` / `rework` (soldering-iron step) | @reverse, @signal | confirm; HIGH; require PSU off + sentinel-clear | none | HIGH | InstructionCard ("power down first") |

### 3.2 Non-physical steps (`actor="guild"` unless noted)

| Step | Invokable by | Gate (orchestrator) | Risk | Confirmation UI |
|---|---|---|---|---|
| `summon_guild` | LiveSpeaker (Live function call) | allow | LOW | none — internal |
| `request_snapshot` (ask the operator to tap 📷 for a sharp look) | any SME | allow | LOW | spoken prompt + 📷 button highlight; the human chooses to capture |
| `lookup_datasheet` / `lookup_board_doc` / `get_documented_limit` | any SME | allow (read-only) | LOW | none — surfaces as @librarian / `#actions` note |
| `web_fetch` (from within sandbox) | any SME with tool | allow (sandbox-scoped) | LOW | log only |
| `request_human_confirmation` | any SME | always confirm | varies | InstructionCard |
| `publish_report` | @scribe, user | confirm if includes private data | LOW–MEDIUM | InstructionCard |
| `sourcing.order_parts` | @sourcing | DENY in hackathon scope; surface as `request_human_confirmation` only | n/a | card with "this would order; copy URL to your browser to complete" |

### 3.3 Cross-cutting rules

1. **Risk elevation override**: any SME may raise the risk in its `proposedAction.risk` field. SafetyGate takes `max(table_default, sme_declared)`.
2. **Unknown SME**: steps from an smeId not in the roster are DENIED.
3. **Forbidden invoker**: if an SME recommends a step it isn't on the "Invokable by" list for, SafetyGate DENIES with reason "out of scope for invoker" AND emits `SafetyInterrupt(WARN)`. Defense in depth beyond the SME's own AGENTS.md "Steps you may NOT recommend".
4. **Pending step limit**: maximum 3 simultaneous `pendingConfirmations`. Excess steps queue with a "queued" notice in `#actions`.
5. **Repeated skips**: if the user skips the same `(tool, args)` tuple twice in a session, SafetyGate adds the tuple to a session denylist; future identical proposals are auto-suppressed with "operator already skipped this".
6. **Setpoint provenance**: a step carrying a numeric value (voltage/current/baud) MUST carry a `documentedLimitRef`. A step without one is downgraded to `request_human_confirmation` ("the guild couldn't cite a source for this value — verify before doing it"). This enforces "never invent a setpoint" at the gate, not just in the persona.

---

## 4. InstructionCard rendering rules

Defined in `00_wire_protocol.md` §2.1 as `ActionCard`. Concrete renderer expectations:

| Field | Render rule |
|---|---|
| `title` | bold, 1 line ("@power asks you to:") |
| `bodyMarkdown` | the step spelled out, scrollable |
| `diffMarkdown` | optional 2-column table (Now / Set) for `set_psu` etc. |
| `documentedLimit` | a cited "board doc max: 30 V" line so the human can sanity-check the instruction against the board's own paperwork |
| `risk` | colored pill: LOW=green, MEDIUM=amber, HIGH=red, HALT=red strobe |
| `affirmLabel` | primary button (default "I did it") |
| `denyLabel` | secondary button (default "Skip") |

Risk-specific UX:
- **LOW**: no card, inline `#actions` notice ("@power: probe VIO at TP4 and tell me the reading").
- **MEDIUM**: card with default labels.
- **HIGH**: card with the affirm button DISABLED for 3 seconds (countdown shown). Forces the human to read. For `reflow_pin`/`flash_mcu`, the card leads with "Power down the PSU first."
- **HALT** (sentinel): no card — full-screen "POWER DOWN NOW" takeover with the reason, then a card afterward to acknowledge the hazard is cleared.

The card displays the invoker's avatar (smeId) prominently — the human should never wonder which SME asked for this.

---

## 5. `@sentinel` interrupt authority

`@sentinel` is the only SME that can:

1. **Pre-empt the voice channel.** Emits `SafetyInterrupt(severity=HALT|WARN)`. `LiveSpeaker` (`01 §3.8`) listens on a priority bus; it cuts the current utterance, reads the sentinel reason, then resumes (after WARN) or holds (after HALT until the human acks).

2. **Take over the screen and bypass the normal confirmation flow for `disable_psu_output`.** On `severity=HALT`, the client renders a full-screen "POWER DOWN NOW" takeover and Live speaks the command immediately — no InstructionCard, no affirm delay. This is the **command-attention** pattern. It does NOT power anything down; the *human* turns the knob. Forge's job is to make sure the human cannot miss the warning.

3. **Block pending instructions.** All queued `pendingConfirmations` are suspended until the human verbally clears the hazard.

All other sentinel recommendations go through the normal SafetyGate.

Anti-abuse:
- Sentinel HALT takeovers are rate-limited to 1 per 60 seconds. Subsequent HALTs within the window are coalesced into the existing takeover (the banner text updates) rather than stacking.
- Every sentinel HALT writes an immutable audit record with the triggering frame + transcript line.

---

## 6. Second safety layer: documented board limits + `@sentinel`

Independent of the orchestrator gate. There is no daemon; the second layer is:

1. **Documented board limits** (from the board profile, `05 §4`). Before surfacing any value-bearing step, SafetyGate calls `get_documented_limit(net|rail|part)` and rejects values that exceed the documented maximum. The limits are *data the board's own documentation provides* — Forge never lets the guild instruct the human to exceed them.

| Limit | Source |
|---|---|
| Max voltage per net / connector | `board_profile.nets[].max_voltage_v` (`05 §2`) |
| Max current per rail | `board_profile.rails[].max_current_a` |
| Absolute-max ratings per part | datasheet via `lookup_datasheet` (`05 §3`) |
| "PSU off before flash / rework" precondition | `board_profile.preconditions` |

2. **`@sentinel` live hazard watch** (`02 §8`, §5 above). Continuous monitoring **via Gemini Live's always-on view** (`00 §4.1`) + the voice transcript + any on-demand snapshot — independent of the gate. There is no dedicated frame feed, so this layer is best-effort vision (whatever Live perceives) plus the human's own eyes. If the gate is misconfigured and surfaces a bad instruction, the human still has the sentinel and their own judgment.

Fallback when limits are unknown: if the board profile is absent, SafetyGate uses the hardcoded conservative defaults `SAFETY_DEFAULT_MAX_VOLTAGE_V` / `SAFETY_DEFAULT_MAX_CURRENT_A` (`07 §2.1`), and every value-bearing step is forced to at least MEDIUM (so the human is always asked to confirm a setpoint that lacks a documented source).

---

## 7. Audit records

Every gate decision is written to `sessions/{sessionId}/safety/{callId}` with:

```json
{
  "callId": "<ulid>",
  "ts": <ns>,
  "tool": "set_psu",
  "actor": "operator",
  "args": {"channel": 1, "voltage_v": 30.0, "current_limit_a": 0.5, "target": "cell-sim ladder J3"},
  "invokerSmeId": "@power",
  "gateDecision": "require_confirmation",
  "riskAssigned": "HIGH",
  "riskRationale": "voltage_v > 12",
  "documentedLimit": {"net": "J3", "max_voltage_v": 30.0, "source": "board_doc p.4"},
  "confirmationOutcome": "done",
  "approverChannel": "voice",
  "approverLatencyMs": 6120,
  "operatorOutcome": "done",
  "frameRef": "gs://…/frame-00412.jpg"
}
```

These records back the demo-replay and the prize-submission story ("here is every decision the system made, with provenance — and every value it told the human came with a citation").

---

## 8. Failure modes the matrix does NOT cover

Documented so the lead engineers don't forget:

- **Operator goes silent mid-step.** A pending HIGH card sits unanswered. After 60 s Live re-asks; after another 60 s it records `outcome="timeout"` and the guild continues advisory-only. Nothing is energized on Forge's say-so, so the worst case is a stalled conversation, not an unsafe board.
- **`@sentinel` SME goes down.** Orchestrator detects via missing 30 s keepalive from `@sentinel`'s env. Falls back to a degraded mode where the **documented board limits** are the only programmatic protection; the human's own judgment remains. Orchestrator emits a sustained chat banner ("safety watch degraded — you are on your own eyes").
- **Guild instructs a value with no citation.** Rule §3.3.6 catches it: the step is downgraded to `request_human_confirmation` with a "no documented source" warning.
- **Sentinel false positive.** User can voice-override ("cancel sentinel"). Orchestrator records the override; it persists for the session.
- **User performs a step they shouldn't have / lies about "I did it".** Out of scope at this layer — the human is the operator and the authority. The audit log records what Forge instructed and what the human reported. Future: a `probe_net` verification step after risky changes (have the human read back a measurement that confirms the change took).

---

## 9. Open decisions for lead engineers

- Exact documented `max_voltage_v` per net for the demo board (BQ79616 cell-sim ladder J3, ESP32 3V3 rail). Hardcoded fallback if no profile loaded: 12 V / 1 A (which would force the 30 V cell-sim step to DENY — so the demo board profile MUST define J3's 30 V limit; see `05 §2`).
- `@bench-tech` is **removed** (resolving the prior open question): with no actuation there is no "the one SME allowed to touch the bench." Electrical steps come from @power, firmware steps from @firmware, rework from @reverse/@signal — each gated by lane. No third party needed.
- HALT coalescing window: 60 s. If the same hazard keeps firing, the takeover banner persists rather than re-strobing; the session should not continue until the human clears it.

---

## 10. Test cases (component-level — the gate truth table)

Run: `pytest orchestrator/safety/tests/`. The matrix in §3 is loaded as data (`safety/matrix.py`) and exercised as a truth table; the documented-limit check is faked with an in-memory board profile. No graph, no SMEs, no network.

**Design patterns under test:** table-driven policy, `max(risk)` elevation, two-layer enforcement, "never invent a setpoint."

| ID | Test | Pass criterion |
|---|---|---|
| SG-1 | Every `(step, invoker)` pair in §3 resolves to exactly one gate decision; no ambiguous/overlapping rows | total + deterministic |
| SG-2 | `set_psu(30 V, 0.5 A)` by @power, board doc J3 max = 30 V → MEDIUM/HIGH (HIGH: V>12) confirm, NOT denied | HIGH confirm |
| SG-3 | `set_psu(35 V)` by @power, J3 max = 30 V → DENY + `SafetyInterrupt(WARN)` citing limit | denied with citation |
| SG-4 | `set_psu(30 V)` by @firmware (out of lane) → DENY "out of scope for invoker" + WARN | lane enforced |
| SG-5 | risk elevation: matrix default LOW + `sme_declared=HIGH` → resolved HIGH | `max()` applied |
| SG-6 | `disable_psu_output` from @sentinel HALT → full-screen takeover path, bypasses confirm, rate-limited 1/60 s | bypass + rate-limit |
| SG-7 | second HALT within 60 s → coalesced into existing takeover, no new card | coalesced |
| SG-8 | value-bearing `set_psu` with `documentedLimitRef=None` → downgraded to `request_human_confirmation` | provenance rule fires |
| SG-9 | no board profile loaded → defaults 12 V/1 A applied; `set_psu(30 V)` → DENY; every value step ≥ MEDIUM | fallback safe |
| SG-10 | repeated skip of identical `(tool,args)` twice → third proposal auto-suppressed | denylist works |
| SG-11 | flash_mcu without "PSU off" precondition met → DENY; with precondition → HIGH confirm | precondition enforced |
| SG-12 | knowledge lookups (`actor="guild"`) → always allow, no card, no pending entry | lookups ungated |

SG-2/SG-3/SG-6 are the seams reused by the system-level safety test `08 §3.4`.

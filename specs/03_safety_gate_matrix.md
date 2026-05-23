# 03 — Safety Gate Matrix

> Every action × invoker × gate decision × risk level × confirmation UI.
> Two layers of enforcement: orchestrator-level `SafetyGate` LangGraph node, and bench-daemon-level hard limits (defense in depth).
> Cross-refs: `00_wire_protocol.md` §2 (ConfirmationRequest / ActionCard / SafetyInterrupt), `01_langgraph_state_machine.md` §3.7 (SafetyGate node behavior), `05_bench_daemon_api.md` §4 (daemon-side limits).

---

## 1. Risk ladder

| Risk | Meaning | UI treatment | Default behavior |
|---|---|---|---|
| `LOW` | read-only or fully reversible action | inline notice in `#actions` channel | auto-allow, no confirm |
| `MEDIUM` | bench-altering but bounded and reversible | ActionCard in chat + Live verbal summary | require explicit user approve |
| `HIGH` | unbounded power, code flash, or anything that can damage hardware | ActionCard with diff + Live verbal summary + 3s delay before button enables | require explicit user approve + voice OR chat confirmation |
| `HALT` | hazard mitigation (`@sentinel` only) | full-screen takeover; voice interrupt; auto-execute then notify | auto-execute, log, voice-explain after |

`HALT` is not a normal risk level. It is `@sentinel`'s privileged channel and bypasses SafetyGate entirely (see §5).

---

## 2. Confirmation flow

```
SafetyGate (01 §3.7) ──> emits ConfirmationRequest(callId, actionCard) ──┐
                                                                          │
                                                                          ▼
              ChatBus (04) renders ActionCard          LiveSpeaker reads summary
                          │                                       │
                          ▼                                       ▼
              user taps Approve/Deny              user says "yes" / "no" / "hold"
                          │                                       │
                          └─────────────┬─────────────────────────┘
                                        ▼
                       ConfirmationResponse(callId, approved, approverChannel)
                                        │
                                        ▼
                          SafetyGate resumes (HITL interrupt)
                                        │
                          approved? ───┤
                                        ├── yes → adapter dispatched
                                        └── no  → action dropped, logged
```

Whichever channel (voice or chat) responds first wins. The losing channel's UI updates to "decided via <channel>" and dismisses.

Timeout: 60s of no response → Live re-asks ("did you mean to approve?"). +60s further → auto-deny + `#actions` log.

---

## 3. Action × invoker × gate matrix

Columns:
- **Action**: orchestrator-visible tool name (typically a `bench.<method>` or special tool)
- **Invokable by**: which SME personas may propose this action (mirrors `02_sme_persona_format.md` AGENTS.md "Tools FORBIDDEN" enforcement; SafetyGate re-checks)
- **Gate (orchestrator)**: what SafetyGate does
- **Limit (daemon)**: hard limit the bench daemon enforces independently
- **Risk**: default risk classification (may be elevated by gate logic)
- **Confirmation UI**: what the user sees

### 3.1 Bench / hardware actions

| Action | Invokable by | Gate (orchestrator) | Limit (daemon) | Risk | Confirmation UI |
|---|---|---|---|---|---|
| `bench.meter_read` | @power, @signal, @firmware, @bench-tech, @sentinel | allow | rate-limit 10 Hz | LOW | `#actions` log only |
| `bench.capture_logic` | @signal, @firmware, @power, @bench-tech | allow if duration_ms ≤ 5000 ELSE confirm | duration ≤ 30 s; cap samples 1M | LOW (≤500ms) / MEDIUM (>500ms) | log / ActionCard |
| `bench.decode_protocol` | @signal, @firmware | allow (pure decode of prior capture) | none | LOW | log |
| `bench.set_psu` (V ≤ 5 V, I ≤ 0.5 A) | @power, @bench-tech | allow | enforce ≤ device profile max | LOW | inline notice |
| `bench.set_psu` (V > 5 V OR I > 0.5 A) | @power, @bench-tech | confirm; include before/after diff in ActionCard | enforce ≤ device profile max | MEDIUM | ActionCard with diff |
| `bench.set_psu` (V > 12 V OR I > 1 A) | @power, @bench-tech | confirm; HIGH risk; 3s delay | enforce ≤ device profile max | HIGH | ActionCard, delayed-affirm |
| `bench.set_psu` (V > device profile max) | nobody | DENY at gate; emit error message in `#actions` | rejects at daemon too | — | denied notice |
| `bench.enable_psu_output(true)` | @power, @bench-tech | confirm always | requires last `set_psu` within 30s | MEDIUM | ActionCard |
| `bench.enable_psu_output(false)` | @power, @bench-tech, @sentinel | allow (always safe to disable) | none | LOW | inline notice |
| `bench.enable_psu_output(false)` | @sentinel HALT path | auto-execute, bypass gate | accepts unconditionally from sentinel auth | HALT | full-screen banner + voice interrupt |
| `bench.serial_send` (no dangerous-pattern match) | @firmware, @bench-tech | allow | rate-limit 100 msgs/s | LOW | log |
| `bench.serial_send` (matches `reset`, `calibrate`, `format`, `erase`, `shutdown`) | @firmware, @bench-tech | confirm; HIGH | rate-limit | HIGH | ActionCard |
| `bench.flash_mcu` | @firmware, @bench-tech | confirm; HIGH; show firmware hash + size diff | reject if PSU off; require image hash present | HIGH | ActionCard with diff |
| `bench.chip_capture` (close-up cam) | @reverse, @bench-tech | allow | rate-limit 1 Hz | LOW | log |
| `bench.move_probe` (if device profile has positioner) | @bench-tech | confirm; MEDIUM | enforce travel envelope | MEDIUM | ActionCard |

### 3.2 Non-bench actions

| Action | Invokable by | Gate (orchestrator) | Risk | Confirmation UI |
|---|---|---|---|---|
| `summon_guild` | LiveSpeaker (via Live function call) | allow | LOW | none — internal |
| `query_datasheet` | any SME | allow | LOW | none — surfaces as @librarian message |
| `web_fetch` (from within sandbox) | any SME with tool | allow (sandbox-scoped) | LOW | log only |
| `request_human_confirmation` | any SME | always confirm | varies | ActionCard |
| `publish_report` | @scribe, user | confirm if includes private data | LOW–MEDIUM | ActionCard |
| `sourcing.order_parts` | @sourcing | DENY at gate in hackathon scope; surface as `request_human_confirmation` only | n/a | confirm card with "this would order; copy URL to browser to complete" |
| `drone.*` | none in v2 | DENY; out of scope | — | — |

### 3.3 Cross-cutting rules

1. **Risk elevation override**: any SME may elevate the risk in its `proposedAction.risk` field. SafetyGate takes `max(table_default, sme_declared)`.
2. **Unknown SME**: actions from a smeId not in the roster are DENIED.
3. **Forbidden invoker**: if an SME proposes an action it isn't on the "Invokable by" list for, SafetyGate DENIES with reason "out of scope for invoker" AND emits `SafetyInterrupt(WARN)` to surface the violation in chat. This is a defense-in-depth check beyond the SME's own AGENTS.md "Tools FORBIDDEN" list.
4. **Pending action limit**: maximum 3 simultaneous `pendingConfirmations`. Excess actions queue with a "queued" notice in `#actions`.
5. **Repeated denies**: if the user denies the same `(tool, args)` tuple twice within one session, SafetyGate adds the tuple to a session-scoped denylist; future proposals get auto-denied with a "user previously rejected" message.

---

## 4. ActionCard rendering rules

Defined in `00_wire_protocol.md` §2.1 as `ActionCard`. Concrete renderer expectations:

| Field | Render rule |
|---|---|
| `title` | bold, 1 line |
| `bodyMarkdown` | rendered markdown, scrollable |
| `diffMarkdown` | optional, shown as a 2-column table (Current / Proposed) |
| `risk` | colored pill: LOW=green, MEDIUM=amber, HIGH=red, HALT=red strobe |
| `affirmLabel` | primary button label (default "Approve") |
| `denyLabel` | secondary button label (default "Hold") |

Risk-specific UX:
- **LOW**: no card, inline `#actions` notice ("Power ran `meter_read` on ch1: 4.97 V").
- **MEDIUM**: card with default labels.
- **HIGH**: card with affirm button DISABLED for 3 seconds (countdown shown). Forces user to read.
- **HALT** (sentinel): no card — full-screen takeover with the reason text, no choice, then a card afterward to acknowledge.

The card displays the invoker's avatar (smeId) prominently — users should never wonder which SME asked for this.

---

## 5. `@sentinel` interrupt authority

`@sentinel` is the only SME that can:

1. **Pre-empt the voice channel.** Emits a `SafetyInterrupt(severity=HALT|WARN)`. `LiveSpeaker` (`01 §3.8`) listens on a priority bus; it cuts the current utterance, reads the sentinel reason, then resumes (HALT path) or queues a follow-up (WARN path).

2. **Bypass SafetyGate for one specific action.** `bench.enable_psu_output(enabled=false)` originating from `@sentinel` with `severity=HALT` is auto-executed without `ConfirmationRequest`. Rationale: a kill-the-power action is always safer than the hazard it's responding to.

3. **Bypass SafetyGate for `bench.set_psu(0,0)`** under the same HALT path. Wind-down to zero is always allowed for sentinel.

All other sentinel proposals go through normal SafetyGate.

Anti-abuse:
- Sentinel-initiated HALT auto-actions are rate-limited to 1 per 60 seconds in orchestrator AND in the bench daemon. Subsequent HALT proposals within the window are queued as WARN (still surface visibly, just don't auto-execute).
- Every sentinel HALT writes an immutable audit record with the triggering frame/transcript/telemetry sample.

---

## 6. Bench-daemon hard limits (defense in depth)

Independent of orchestrator gates. The daemon enforces these against ALL incoming RPCs regardless of invoker. See `05_bench_daemon_api.md` §4 for the full set.

| Limit | Value | Source |
|---|---|---|
| Max PSU voltage | per `device_profile.psu.max_voltage_v` | YAML config |
| Max PSU current limit | per `device_profile.psu.max_current_a` | YAML config |
| Min PSU voltage | 0 V | hardcoded |
| Max logic capture duration | 30 s | hardcoded |
| Max logic capture samples | 1,048,576 | hardcoded |
| Serial msg rate | 100/s | hardcoded |
| Meter read rate | 10/s | hardcoded |
| Chip capture rate | 1/s | hardcoded |
| Sentinel HALT auto-action rate | 1/60s | hardcoded |
| Requires fresh `set_psu` before `enable_psu_output(true)` | ≤ 30 s old | hardcoded |
| Flash requires PSU output off | true | hardcoded |

If the orchestrator gate is misconfigured and lets a bad action through, the daemon still rejects with `error: {code: "limit_exceeded", limit: …, value: …}`. The orchestrator surfaces this rejection as a `SafetyInterrupt(WARN)`.

---

## 7. Audit records

Every gate decision is written to `sessions/{sessionId}/safety/{callId}` with:

```json
{
  "callId": "<ulid>",
  "ts": <ns>,
  "tool": "bench.set_psu",
  "args": {"channel": 1, "voltage_v": 7.0, "current_limit_a": 0.5},
  "invokerSmeId": "@power",
  "gateDecision": "require_confirmation",
  "riskAssigned": "MEDIUM",
  "riskRationale": "voltage_v > 5",
  "confirmationOutcome": "approved",
  "approverChannel": "voice",
  "approverLatencyMs": 4231,
  "daemonResponse": {"ok": true, "channel": 1, "voltage_v": 7.0, "current_a": 0.0}
}
```

These records back the demo-replay and the prize-submission story ("here is every decision the system made, with provenance").

---

## 8. Failure modes the matrix does NOT cover

Documented so the lead engineers don't forget:

- **Network partition between orchestrator and daemon.** Daemon enters a local-safe state (`enable_psu_output(false)` after 10s of no orchestrator heartbeat). Orchestrator emits `SafetyInterrupt(WARN, "bench daemon disconnected; PSU disabled")`.
- **Sentinel SME goes down.** Orchestrator detects via missing 30s-interval keepalive from `@sentinel`'s env. Falls back to a degraded mode where the bench daemon's local hard limits are the only protection. Orchestrator emits a sustained chat banner.
- **Two SMEs propose conflicting HALTs.** Only `@sentinel` can emit HALT-tier; the matrix has no other HALT invokers, so no conflict possible.
- **Sentinel false positive.** User can voice-override ("cancel sentinel"). Orchestrator records the override, re-enables PSU only on explicit `@user` request, not on sentinel quieting.
- **User confirms an action they shouldn't have.** Not the system's problem at this layer. The daemon's hardware limits remain. Future: add an "are you sure?" double-confirm for HIGH actions involving voltages > device max × 0.8.

---

## 9. Open decisions for lead engineers

- Exact `device_profile.psu.max_voltage_v` and `.max_current_a` defaults for the demo bench. Hardcoded fallback if no profile loaded: 12 V / 1 A.
- Whether `@bench-tech` is even necessary as a distinct invoker, or if the actual rule is "@power proposes electrical, @firmware proposes code, the SafetyGate dispatches via the daemon — no third party needed". Current spec assumes `@bench-tech` exists as the only SME that can actively MOVE things (probes); skip if no positioner on the demo bench.
- HALT auto-execute window: 60s rate-limit may be too aggressive if sentinel is genuinely seeing repeated hazards. Counter-argument: if the same hazard keeps firing, the SESSION should halt, not keep auto-zapping the PSU. Resolve before demo.

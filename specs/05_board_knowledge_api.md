# 05 — Board Knowledge & Operator-Guidance Protocol

> Replaces the former "Bench Daemon API". **There is no bench daemon and nothing actuates hardware.** Instead this spec defines the three things Forge needs to guide a human at the bench:
> 1. the **board profile** — a static, declarative description of the board under test (parts, rails, nets, documented limits, test points, preconditions);
> 2. the **knowledge-lookup tool API** — read-only calls SMEs make to find the right value ("what voltage does the doc say to apply to the cell-sim ladder?");
> 3. the **operator-step contract** — the shape of the manual instructions the guild hands the human.
> All of this lives **in-process** in the orchestrator's `KnowledgeAdapter` (ARCHITECTURE §2). There is no socket, no RPC, no instrument driver.
> Cross-refs: `00_wire_protocol.md` §2 (ProposedAction, FrameRef), `03_safety_gate_matrix.md` §6 (documented-limit second layer), `01_langgraph_state_machine.md` §3.7 (SafetyGate consumes `get_documented_limit`).

---

## 1. There is no connection

The old design opened a JSON-RPC WebSocket to a Linux box driving instruments. That box is gone. The bench is operated **by the human**, who reads Forge's instructions and turns the knobs, holds the probes, and wields the iron themselves.

What used to be RPCs are now two distinct things:

| Old (bench daemon RPC) | New |
|---|---|
| `set_psu`, `enable_psu_output`, `serial_send`, `flash_mcu`, `capture_logic`, `chip_capture` | **operator steps** (`ProposedAction(actor="operator")`) — instructions Forge renders for the human (`§5`) |
| `_telemetry`, `meter_read` | the human reads their own DMM/scope and **reports the value aloud**; it enters the system through the Live transcript (or an on-demand snapshot, `00 §4.2`), not a wire |
| `device_profile` over `_welcome` | the **board profile** YAML, loaded once at startup by the orchestrator (`§2`) |
| daemon-side hard limits | **documented board limits** consulted via `get_documented_limit` (`§4`) |

The KnowledgeAdapter additionally answers the guild's research questions (datasheets, app notes, board docs) so the SMEs cite sources instead of guessing.

---

## 2. Board profile YAML

Loaded by the orchestrator's KnowledgeAdapter at startup from `~/.forge/board.yaml` (or `BOARD_PROFILE` env). It is **documentation about the board**, not a device driver config. The guild reads it via `lookup_board_doc` / `get_documented_limit`; SafetyGate reads its limits.

```yaml
# ~/.forge/board.yaml — the board under test for this session
board_profile:
  id: "bq79616-bringup-2026-05"
  description: "ESP32 host + BQ79616 16-cell monitor bring-up board (Shack15 demo)"

  parts:
    - { ref: "U1", part: "ESP32-WROOM-32", role: "host MCU", datasheet: "esp32-wroom-32" }
    - { ref: "U2", part: "BQ79616",        role: "16s battery monitor AFE", datasheet: "bq79616" }
    - { ref: "U3", part: "BQ79600",        role: "host-side comm bridge (UART↔daisy-chain)", datasheet: "bq79600" }
    - { ref: "U4", part: "AMS1117-3.3",    role: "VIO LDO for U1/U3 logic", datasheet: "ams1117" }

  rails:
    - { id: "3V3",      nominal_v: 3.3, max_current_a: 0.5, powers: ["U1", "U3", "U4-out"] }
    - { id: "VIO",      nominal_v: 3.3, max_current_a: 0.1, powers: ["U2-logic"] }
    - { id: "CELLSTK",  nominal_v: 30.0, max_current_a: 0.5, powers: ["U2-cells"],
        note: "emulated cell stack; 16 series cells × ~1.875 V" }

  nets:                          # documented limits the SafetyGate enforces (§4)
    - { id: "J3",  desc: "cell-sim ladder input (top of stack)", max_voltage_v: 30.0, test_point: "J3-1" }
    - { id: "TP4", desc: "VIO at U2", max_voltage_v: 5.5, test_point: "TP4" }
    - { id: "TP7", desc: "U3 UART TX to U1", max_voltage_v: 3.6, test_point: "TP7" }

  test_points:                   # where to put the DMM probe for `probe_net`
    - { id: "TP4", net: "VIO",     desc: "VIO rail at U2 pin 12" }
    - { id: "TP9", net: "CELLSTK", desc: "top-of-stack at U2 BAT pin" }
    - { id: "J3-1", net: "J3",     desc: "cell-sim ladder positive" }

  preconditions:                 # SafetyGate checks these before HIGH steps (§4)
    flash_requires_psu_off: true
    rework_requires_psu_off: true

  procedures:                    # documented bring-up steps the guild can cite
    - id: "bq79616-power-up"
      summary: >
        BQ79616 will not respond on the comm bus until a valid cell stack is
        present on its VC pins. Apply the emulated stack to J3 BEFORE expecting
        any comm. See bq79616 datasheet §7 (Power-Up) and the wake-tone timing.
      cite: "bq79616 datasheet §7"
```

If `board.yaml` is absent, the KnowledgeAdapter serves an empty profile and SafetyGate falls back to conservative defaults (`03 §6`, `07 §2.1`), forcing every value-bearing step to confirm.

---

## 3. Knowledge-lookup tool API

These are the only "tools" SMEs call through the orchestrator side-channel (besides `summon_guild`/`confirm_step`). All are **read-only** and surface as `ProposedAction(actor="guild")` / `ToolCall` (`00 §10`). They never touch hardware.

### 3.1 `lookup_datasheet`

```typescript
params: { part: string, query: string, maxPages?: number }   // part matches board_profile.parts[].datasheet
result: {
  part: string,
  passages: Array<{ text: string, page: number, sourceUri: string, score: number }>,
  cite: string                 // human-citable reference, e.g. "bq79616 datasheet §7 p.41"
}
```

Backed by Vertex AI Search (datastore `VERTEX_SEARCH_DATASTORE_ID`). Stub mode: canned excerpts keyed by `(part, query)` from a hand-curated table.

### 3.2 `lookup_board_doc`

```typescript
params: { query: string }
result: {
  passages: Array<{ text: string, section: string, sourceUri: string }>,
  profileMatches: Array<{ kind: "part"|"rail"|"net"|"procedure", id: string, data: object }>
}
```

Queries the uploaded board documentation (PDF/markdown in the project's `state/projects/<id>/board_doc.pdf`) AND the structured `board_profile`. Returns both prose passages and structured profile hits.

### 3.3 `get_documented_limit`

The one SafetyGate depends on. Deterministic, no model in the loop.

```typescript
params: { target: string, kind: "net" | "rail" | "part" }   // e.g. {target:"J3", kind:"net"}
result: {
  target: string,
  found: boolean,
  maxVoltageV?: number,
  maxCurrentA?: number,
  source: string,              // citation, e.g. "board_doc p.4 / board_profile.nets[J3]"
  absoluteMax?: { voltageV?: number, currentA?: number, source: string }  // from datasheet if richer
}
```

Resolution order: `board_profile` structured limits first; if `kind="part"` or richer absolute-max needed, fall through to `lookup_datasheet` for the part's absolute-maximum-ratings table. `found=false` → SafetyGate uses the conservative defaults and forces the step to ≥ MEDIUM (`03 §6`).

### 3.4 Used by the snapshot analyzer too

The `SnapshotAnalyzer` (`00 §4.2`) runs the strong model with these same lookups available, so its `SnapshotAnalysis.cites` are grounded citations (e.g. "this is a BQ79616; per datasheet §7 it needs the stack present") rather than free-form vision guesses. Same read-only contract, no special path.

---

## 4. Documented limits (the second safety layer)

`get_documented_limit` is how the SafetyGate's second layer (`03 §6`) gets its numbers. The contract SafetyGate relies on:

1. **Determinism.** Same `(target, kind)` → same result within a session (cached). No LLM in the path.
2. **Citations.** Every limit carries a `source` string that ends up in the audit record (`03 §7`) and on the InstructionCard (`documentedLimit` field, `00 §2.1`).
3. **Preconditions.** `board_profile.preconditions` (e.g. `flash_requires_psu_off`) are exposed via `lookup_board_doc(query="preconditions")` and checked by SafetyGate for HIGH steps (`03 §3.1`).
4. **Fail-safe absence.** `found=false` never silently allows a value — it forces confirmation against conservative defaults.

This is exactly what the old daemon's hard limits did, except the limits now come from the board's own documentation, the human is the actuator, and the check is a pure function the gate can unit-test (`03 §10`, SG-2/SG-3/SG-9).

---

## 5. Operator-step instruction contract

Operator steps are `ProposedAction(actor="operator")` (`00 §2.1`). The KnowledgeAdapter does not execute them — `MergeOpinion` collects them, `SafetyGate` gates them, the client renders them as InstructionCards (`03 §4`), and the human performs them. Recognized `tool` verbs and their `argsJson`:

| `tool` | `argsJson` shape | Rendered instruction (example) |
|---|---|---|
| `set_psu` | `{ channel, voltage_v, current_limit_a, target }` | "Set bench PSU CH1 to 30.0 V, 0.5 A limit, across the cell-sim ladder (J3)." |
| `enable_psu_output` / `disable_psu_output` | `{ channel }` | "Enable PSU CH1 output." / "Turn the PSU output OFF now." |
| `probe_net` | `{ net, test_point, mode }` | "Probe VIO at TP4 with the DMM (DC volts) and tell me the reading." |
| `serial_send` | `{ port, payload }` | "In your serial console (115200 baud) send: `read_cells`." |
| `flash_mcu` | `{ image, expected_sha256 }` | "Flash `bq79616_host.bin` (sha256 1a2b…) to the ESP32. Power the board down first." |
| `reflow_pin` | `{ ref, pin, note }` | "Reflow U2 pin 14 with the soldering station. Power down the PSU first." |
| `inspect_closeup` | `{ ref, hint }` | "Move the camera close to U2 so I can read the markings." |

Every value-bearing step (`set_psu`, `serial_send` baud, `flash_mcu`) MUST carry a `documentedLimitRef` (`00 §2.1`, `03 §3.3.6`). The operator reports completion via `ConfirmationResponse(approved=True)` ("I did it") or `False` ("Skip"). Any reading the human speaks ("VIO is 3.28 volts") re-enters the graph through the Live transcript at the next `PerceptionGate` tick.

---

## 6. Stub mode (zero-config dev)

The system must boot with no env vars set (`07 §2.4`). With no API keys / no `board.yaml`:

- `lookup_datasheet` → canned excerpts from a hand-curated table covering the demo parts (BQ79616 §7 power-up, ESP32 UART, AMS1117 dropout).
- `lookup_board_doc` → returns the bundled demo profile (`bench_knowledge/examples/bq79616-bringup-2026-05.yaml`) plus a canned prose paragraph.
- `get_documented_limit` → reads the bundled profile if present, else `found=false` (defaults apply).
- No instruments are stubbed because there are no instruments — the human is always "real".

This means the **entire guild + dissent + SafetyGate + operator-instruction flow runs offline**, which is also the demo's robustness story (`06 §5`).

---

## 7. Audit & replay

Every knowledge lookup (tool, params, result citation, ts, invoker SME) is written to `sessions/{sessionId}/lookups/{ulid}`; every operator step + its outcome to `sessions/{sessionId}/safety/{callId}` (`03 §7`). A session replays from the Firestore log alone — there is no separate device log to reconcile because there is no device.

---

## 8. Test cases (component-level — knowledge & limits contract)

Run: `pytest orchestrator/knowledge/tests/`. The KnowledgeAdapter is exercised against a fixture `board.yaml` and a stub datastore; no network.

**Design patterns under test:** deterministic limit lookup (pure function), strategy + fallback (profile → datasheet), graceful degradation.

| ID | Test | Pass criterion |
|---|---|---|
| BK-1 | `board.yaml` parses; every `nets[].max_voltage_v`, `rails[].max_current_a`, `preconditions` field present and typed | schema valid |
| BK-2 | `get_documented_limit({target:"J3", kind:"net"})` → `maxVoltageV=30.0`, `found=true`, non-empty `source` | exact value + citation |
| BK-3 | `get_documented_limit` for an unknown target → `found=false` (so SafetyGate forces defaults) | safe miss |
| BK-4 | `get_documented_limit` is deterministic + cached: 100 calls → identical result, datastore hit ≤1 | determinism |
| BK-5 | `kind:"part"` falls through to `lookup_datasheet` absolute-max table when profile lacks the limit | fallback chain |
| BK-6 | `lookup_datasheet("bq79616","power-up")` → passage mentioning cell stack present at power-up, with a `cite` | relevant + cited |
| BK-7 | stub mode (no keys, no yaml): `lookup_datasheet`/`lookup_board_doc` return canned data, `get_documented_limit` → `found=false` | offline works |
| BK-8 | every operator-step `tool` verb in §5 maps to a renderer template in the client and a matrix row in `03 §3` | no orphan verbs |
| BK-9 | a `set_psu` operator step lacking `documentedLimitRef` is rejected by the provenance lint (mirrors SG-8) | provenance enforced |
| BK-10 | no symbol named `set_psu`/`flash_mcu` is *callable* in the KnowledgeAdapter — they exist only as step labels | no actuation path |
| BK-11 | `analyze_snapshot(img, ctx)` with a fixture image of the BQ79616 → the returned `SnapshotAnalysis.cites` reference a real datasheet/board-doc passage (grounded, not invented) | citation present |

BK-2 and BK-7 are reused by the system-level safety + offline tests (`08 §3.4`, `08 §3.6`); BK-11 by the snapshot integration test (`08 §3.5`).

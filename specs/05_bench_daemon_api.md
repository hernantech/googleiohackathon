# 05 — Bench Daemon API

> JSON-RPC over WebSocket between the orchestrator and the local bench daemon.
> This is channel (D) in `00_wire_protocol.md` §1.
> Cross-refs: `00_wire_protocol.md` §2 (ToolResult types), `03_safety_gate_matrix.md` §6 (daemon-side hard limits).

---

## 1. Connection

```
ORCHESTRATOR ──ws──> ws://<bench-host>:9090/v2/rpc
Sec-WebSocket-Protocol: forge.bench.v2, bearer.<token>
```

- Single WS per session. Orchestrator opens at session start; closes at session end.
- Orchestrator may have one daemon connection per active session if running multiple benches.
- Auth: same Firebase-or-shared-secret model as the chat bus (`00 §8`). **DEPENDS ON SPIKE 5** for whether the daemon validates Firebase tokens directly or trusts a shared HMAC.

On open, daemon sends a `Welcome` message with its device profile:

```json
{
  "jsonrpc": "2.0",
  "method": "_welcome",
  "params": {
    "daemonVersion": "0.2.0",
    "deviceProfileId": "demo-bench-2026-05",
    "deviceProfile": { /* full profile YAML rendered as JSON, see §2 */ },
    "capabilities": ["psu", "logic", "serial", "meter", "chip_capture"]
  }
}
```

If the orchestrator rejects the profile (e.g. expected `psu` capability missing), it sends a `_close` notification with reason and disconnects.

---

## 2. Device profile YAML

Loaded by the daemon at startup from `~/.forge/bench.yaml` (or `BENCH_PROFILE` env). The orchestrator never edits this file; it consumes the rendered JSON over the wire.

```yaml
# ~/.forge/bench.yaml
device_profile:
  id: "demo-bench-2026-05"
  description: "Shack15 demo bench"

  psu:
    backend: "rigol_dp832"      # driver id
    address: "USB0::0x1AB1::0x0E11::DP8F252701234::INSTR"
    channels:
      - { id: 1, max_voltage_v: 8.0, max_current_a: 1.0, default_voltage_v: 0.0 }
      - { id: 2, max_voltage_v: 5.0, max_current_a: 0.5, default_voltage_v: 0.0 }
      - { id: 3, max_voltage_v: 3.3, max_current_a: 0.3, default_voltage_v: 0.0 }

  logic:
    backend: "saleae_logic2"
    address: "auto"
    max_channels: 8
    max_sample_rate_hz: 25_000_000

  serial:
    backend: "pyserial"
    ports:
      - { id: "ttyUSB0", baud: 115200, dev: "/dev/ttyUSB0" }
      - { id: "ttyACM0", baud: 9600,   dev: "/dev/ttyACM0" }

  meter:
    backend: "fluke_8846a"
    address: "TCPIP::192.168.1.42::INSTR"
    modes: ["dcv", "dci", "res", "diode"]

  chip_capture:
    backend: "uvc_camera"
    device: "/dev/video2"
    resolution: [1920, 1080]
    focus_mode: "manual"
    focus_value: 230            # for the demo PCB

  mcu:
    backend: "avrdude"          # or "pyocd", "openocd"
    target: "atmega328p"
    programmer: "stk500v2"
    port: "/dev/ttyUSB1"

  hard_limits:                  # see 03 §6
    serial_rate_msgs_per_sec: 100
    meter_read_hz: 10
    chip_capture_hz: 1
    logic_max_duration_ms: 30_000
    logic_max_samples: 1_048_576
    psu_enable_requires_setpoint_within_s: 30
    flash_requires_psu_off: true
    sentinel_halt_rate_per_minute: 1
    orchestrator_heartbeat_timeout_s: 10
```

If a section is missing, the corresponding capability is omitted from `_welcome.capabilities` and the orchestrator's tool registry skips registering tools that depend on it.

---

## 3. JSON-RPC framing

Standard JSON-RPC 2.0 over WS text frames.

Request:
```json
{ "jsonrpc": "2.0", "id": "<ulid>", "method": "set_psu", "params": { ... } }
```

Response (success):
```json
{ "jsonrpc": "2.0", "id": "<ulid>", "result": { ... } }
```

Response (error):
```json
{ "jsonrpc": "2.0", "id": "<ulid>",
  "error": { "code": -32602, "message": "limit_exceeded",
             "data": { "limit": "max_voltage_v", "limitValue": 8.0, "requested": 12.0 } } }
```

Notification (no `id`, no response expected):
```json
{ "jsonrpc": "2.0", "method": "_telemetry", "params": { ... } }
```

Streamed responses (for capture/serial subscriptions) use the chunked-response extension: `result.chunk` + `result.done` semantics, identified by a server-generated `streamId`:

```json
{ "jsonrpc": "2.0", "id": "<ulid>",
  "result": { "streamId": "<ulid>", "chunk": "<base64>", "done": false, "seq": 17 } }
```

The orchestrator concatenates chunks until `done: true`.

---

## 4. Methods

All methods are async. Orchestrator must include `id`. Daemon enforces hard limits (§6 in `03_safety_gate_matrix.md`) and returns a `limit_exceeded` error if violated, regardless of whether the orchestrator gated the call.

### 4.1 `set_psu`

```typescript
params: {
  channel: number,            // matches device_profile.psu.channels[].id
  voltage_v: number,
  current_limit_a: number
}
result: {
  channel: number,
  voltage_v: number,          // actually set
  current_limit_a: number,
  output_enabled: boolean,    // current state; not modified by this call
  ts: number
}
```

Errors:
- `limit_exceeded` if `voltage_v > channel.max_voltage_v` or `current_limit_a > channel.max_current_a`
- `unknown_channel` if `channel` not in profile
- `device_unreachable` if PSU not responding

### 4.2 `enable_psu_output`

```typescript
params: { channel: number, enabled: boolean }
result: { channel: number, output_enabled: boolean, ts: number }
```

Errors:
- `setpoint_stale` if `enabled=true` and last `set_psu` for this channel was > `psu_enable_requires_setpoint_within_s` ago
- `unknown_channel`
- `device_unreachable`

### 4.3 `meter_read`

```typescript
params: { mode: "dcv" | "dci" | "res" | "diode" }
result: { value: number, unit: string, mode: string, ts: number }
```

Rate-limited to `meter_read_hz`. Errors: `rate_limited`, `mode_unsupported`, `device_unreachable`.

### 4.4 `capture_logic`

Streamed.

```typescript
params: {
  channels: number[],         // subset of 0..max_channels-1
  duration_ms: number,        // <= logic_max_duration_ms
  sample_rate_hz: number,     // <= logic.max_sample_rate_hz
  trigger?: {
    channel: number,
    edge: "rising" | "falling" | "either",
    pre_trigger_samples?: number
  }
}
result (streamed): {
  streamId: string,
  chunk: base64,              // raw samples; packing format described below
  done: boolean,
  seq: number,
  meta?: {                    // present in first chunk only
    captureId: string,
    sampleRateHz: number,
    channels: number[],
    durationMs: number,
    encoding: "packed_u8"     // 1 byte per sample, low N bits = N channels
  }
}
```

Errors: `limit_exceeded`, `device_unreachable`, `trigger_timeout`.

### 4.5 `decode_protocol`

Operates on a prior `captureId`.

```typescript
params: {
  capture_id: string,
  protocol: "uart" | "i2c" | "spi" | "1wire" | "can",
  options: { /* protocol-specific: baud, address mode, etc. */ }
}
result: {
  protocol: string,
  frames: Array<{ ts_ns: number, payload_hex: string, address_hex?: string, ack?: boolean }>
}
```

Errors: `unknown_capture`, `decode_failed`.

### 4.6 `serial_send`

```typescript
params: {
  port: string,               // matches device_profile.serial.ports[].id
  payload: string,            // ASCII or hex-prefixed "0x..."
  expect_response: boolean,
  response_timeout_ms?: number  // default 500
}
result: {
  sent: string,
  response?: string,
  ts: number
}
```

Errors: `unknown_port`, `rate_limited`, `response_timeout`, `device_unreachable`.

### 4.7 `chip_capture`

```typescript
params: {
  bbox?: { x1: number, y1: number, x2: number, y2: number },  // crop window in pixels
  focus_value?: number        // override profile default
}
result: {
  imageJpegBase64: string,
  width: number,
  height: number,
  ts: number
}
```

Rate-limited to `chip_capture_hz`. Errors: `rate_limited`, `device_unreachable`.

### 4.8 `flash_mcu`

```typescript
params: {
  firmware_hex_base64: string,
  expected_sha256: string,    // daemon verifies before flashing
  programmer?: string,        // override profile default
  port?: string
}
result: {
  bytes_written: number,
  sha256_verified: string,
  ts: number
}
```

Errors: `psu_must_be_off` (if `flash_requires_psu_off` and any PSU channel is currently enabled), `hash_mismatch`, `programmer_error`, `device_unreachable`.

### 4.9 `move_probe` (only if profile has `positioner` section)

Out of scope for hackathon demo bench. Method reserved.

### 4.10 `_emergency_stop`

Special method. Always succeeds (idempotent). Disables ALL PSU outputs immediately, regardless of state. Returns:

```typescript
result: { channels_disabled: number[], ts: number }
```

Invokable only by `@sentinel` HALT path; the orchestrator's session auth carries an `is_sentinel_halt: true` flag in the `params._auth` field for this call. Daemon checks the flag AND enforces `sentinel_halt_rate_per_minute`.

---

## 5. Notifications (daemon → orchestrator, no id)

### 5.1 `_telemetry`

Pushed at 5 Hz by default; rate configurable per channel.

```json
{
  "jsonrpc": "2.0",
  "method": "_telemetry",
  "params": {
    "ts": 1234567890,
    "psu": [
      { "channel": 1, "voltage_v": 3.30, "current_a": 0.142, "output_enabled": true },
      { "channel": 2, "voltage_v": 0.0,  "current_a": 0.0,   "output_enabled": false }
    ],
    "temps_c": [{ "sensor": "psu_ambient", "value": 36.2 }],
    "overcurrent_trips_in_last_minute": 0
  }
}
```

`@sentinel` consumes these (via the orchestrator) for hazard detection.

### 5.2 `_serial_async`

Unsolicited serial RX (when a port is in subscription mode).

```json
{ "jsonrpc": "2.0", "method": "_serial_async",
  "params": { "port": "ttyUSB0", "payload": "OK\r\n", "ts": 1234567890 } }
```

### 5.3 `_daemon_event`

Daemon-initiated lifecycle events.

```json
{ "jsonrpc": "2.0", "method": "_daemon_event",
  "params": { "kind": "psu_overcurrent_trip", "channel": 1, "ts": 1234567890 } }
```

Kinds: `psu_overcurrent_trip`, `psu_overvoltage_trip`, `device_unreachable`, `device_reconnected`, `heartbeat_missed`, `local_safe_state_entered`.

### 5.4 `_heartbeat`

Orchestrator sends every 2s; daemon sends every 2s back. If either misses 5 consecutive heartbeats (= 10s), the other side:
- daemon enters local safe state (`enable_psu_output(false)` on all channels)
- orchestrator emits `SafetyInterrupt(WARN, "bench daemon disconnected")` and disables bench-tool dispatch until reconnection

```json
{ "jsonrpc": "2.0", "method": "_heartbeat", "params": { "ts": 1234567890 } }
```

---

## 6. Local safety enforcement

The daemon enforces every hard limit from `03 §6` BEFORE forwarding any call to underlying instruments. This is independent of orchestrator gates.

Enforcement points:
1. **Pre-dispatch validation** — parameter ranges, rate limits, profile-defined max values.
2. **Cross-method preconditions** — e.g. `enable_psu_output(true)` requires recent `set_psu`; `flash_mcu` requires PSU off.
3. **Telemetry-driven trip** — overcurrent, overvoltage, overtemp detected in real time → auto-disable affected PSU channel + emit `_daemon_event`.
4. **Heartbeat-driven local safe state** — orchestrator silence > 10s → kill all PSU output.

All enforcement actions are logged to `~/.forge/bench.log` regardless of orchestrator audit state, so post-mortems work even if the network was down.

---

## 7. Auth

**DEPENDS ON SPIKE 5.** Two candidates:

### Candidate A — shared HMAC
- Daemon and orchestrator share a secret loaded from env `FORGE_BENCH_SHARED_SECRET`.
- Every WS open includes the secret in the subprotocol header.
- Per-call extra auth (e.g. sentinel HALT) uses HMAC-SHA256 over the call params.
- Pro: simple, no Firebase dependency in the daemon.
- Con: shared secret must be rotated manually.

### Candidate B — Firebase ID token
- Orchestrator forwards the user's Firebase ID token in subprotocol header.
- Daemon verifies via `firebase-admin` SDK locally (requires service-account creds on the daemon machine).
- Pro: unified auth story with the rest of Forge.
- Con: more setup, requires daemon machine to have Firebase project access.

Hackathon plan: implement Candidate A (shared HMAC) for the demo; document Candidate B as the production migration path.

---

## 8. Stub / mock mode

The bench daemon ships a `--mock` flag. In mock mode:
- All `device_profile` capabilities are advertised (faked).
- `set_psu` returns the requested values verbatim.
- `meter_read` returns plausible readings drawn from a per-channel sine + noise function.
- `capture_logic` generates a synthetic UART trace of "Hello, Forge!" at the requested baud.
- `chip_capture` returns one of three pre-recorded JPEGs based on a deterministic `bbox` hash.
- `_telemetry` is synthesized.
- Hard limits still apply.

This is the fallback path for `06_demo_script.md` §5 (if the real bench fails, the orchestrator points at the mock daemon and the demo continues with the guild deliberation intact).

---

## 9. Audit & replay

Every RPC (request, response or error, timestamp, invoker SME, gate decision) is also written by the orchestrator into Firestore at `sessions/{sessionId}/bench_calls/{ulid}`. The daemon's local `~/.forge/bench.log` is the second copy.

Reconstruction: a session can be replayed entirely from the Firestore log; the daemon's local log is for forensic use when the network was unavailable.

---

## 10. Open questions for lead engineers

- Whether the daemon should run on the same machine as the orchestrator (loopback WS) or strictly separate (network WS). The split lets the bench daemon survive orchestrator restarts; the unified deployment is simpler. Hackathon plan: separate, ws://localhost:9090 by default.
- Should `_telemetry` flow through the orchestrator into the chat bus as a low-priority channel, or stay private to `@sentinel`? Current spec: private. User can see derived measurements via `bench.meter_read`.
- Whether `flash_mcu` should also require the user to physically press a button on the bench (out-of-band consent). Future safety enhancement; out of scope for v2.

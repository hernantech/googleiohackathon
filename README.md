# Forge

A voice + vision **multi-agent advisor for a human at an electronics bench**. You hold the probes, turn the PSU knob, and wield the iron; Forge watches through your phone/Quest camera, summons a guild of specialist SME agents that deliberate visibly and in parallel, surfaces their disagreements, and hands you precise, safety-gated, step-by-step instructions — every value cited against the board's own documentation.

Forge actuates nothing. There is no bench daemon; the human is the operator and the final authority.

## Where to start

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — the shared mental model (topology, orchestrator internals, the LangGraph state machine, the demo sequence, UI, safety tree, design patterns).
- **[specs/](specs/)** — the contracts, numbered and cross-referenced:
  - `00_wire_protocol.md` — frozen event vocabulary across processes
  - `01_langgraph_state_machine.md` — the orchestrator graph, node by node
  - `02_sme_persona_format.md` — the SME sandbox layout (AGENTS.md / SKILL.md)
  - `03_safety_gate_matrix.md` — operator-instruction gating, two safety layers
  - `04_chat_bus_protocol.md` — the Discord-style client protocol
  - `05_board_knowledge_api.md` — board profile + read-only knowledge lookups
  - `06_demo_script.md` — the 3-minute BQ79616 bring-up demo
  - `07_environment_setup.md` — accounts, env vars, repo layout, pre-warm
  - `08_test_plan.md` — build-order gates + cross-process integration tests

Each spec ends with a component-level **Test cases** section; `08` owns the system-level tests that prove the contracts line up end-to-end.

---

## Current status

The orchestrator backbone (P0–P7) and FastAPI serving layer are **built, tested, and deployed**. The service is live at `http://20.230.188.247:8080`.

| Phase | What | State |
|---|---|---|
| P0–P7 | Orchestrator backbone (graph, safety, knowledge, chat bus, SME protocol) | ✅ done |
| Phase 1 | FastAPI serving surface (`/healthz`, `/v2/chat`, `/v2/live`, `/v2/snapshot`) | ✅ done |
| Phase 2 | Containerised + CI/CD deployed to Azure VM | ✅ done — live at `20.230.188.247:8080` |
| Phase 3 | Real Gemini model seams wired (classify/merge/dissent/summon/snapshot/Live) | ✅ done — activates with `GEMINI_API_KEY` |
| Phase 4 | VM secrets for live run | ✅ done (`GEMINI_API_KEY` secret set) |
| Phase 5 | Integration tests (§3.7–3.9, incremental streaming, @live variants) | ✅ done |
| Phase 6 | Edge client parity — iOS DeviceSource + Quest Live duplex | ✅ done |
| Guild | Persona/skill packs, concurrent fan-out, streamed deliberation, kept-warm `run_analysis` sandbox | ✅ done |
| Observer | Decoupled read-only manager dashboard (own container, taps the chat bus) | ✅ done |

**Stub mode / live mode:** The service boots clean with zero env vars. All model seams return canned responses. Set `GEMINI_API_KEY` and the real Gemini calls activate automatically — no restart protocol change, same wire.

---

## The service and its endpoints

Base URL (deployed): `http://20.230.188.247:8080`

| Method | Path | Description |
|---|---|---|
| `GET` | `/healthz` | Liveness + integration-mode report (`stub` vs `live`) |
| `WSS` | `/v2/chat?sessionId=` | Chat bus over WebSocket — JSON `AgentEvent` frames (spec 04) |
| `WSS` | `/v2/live?sessionId=` | Bidirectional Gemini Live bridge: PCM audio + JPEG frames in, TTS audio + transcripts out |
| `POST` | `/v2/snapshot?sessionId=&note=` | `image/jpeg` body → vision analysis card; returns `202 {jobId}` |

### `/v2/live` wire framing

Each binary WebSocket frame from the client carries a **1-byte type prefix**; the rest is the raw payload, forwarded verbatim (no transcode):

| Prefix byte | Payload |
|---|---|
| `0x01` | PCM audio — 16 kHz mono, little-endian int16 → Gemini Live `audio=` slot |
| `0x02` | JPEG frame (`image/jpeg`) → Gemini Live `video=` slot |

Gemini Live takes **PCM + JPEG, not H.264**. TTS audio comes back as bare binary frames (no prefix), 24 kHz PCM int16. Transcripts come back as text frames.

In stub mode (no key) the endpoint accepts and drains frames but sends nothing back — the socket stays open and well-behaved.

---

## Models

All model names are configurable via env vars and fall back to defaults:

| Seam | Default model | Env var |
|---|---|---|
| classify / merge / dissent | `gemini-3.5-flash` | `GEMINI_SME_MODEL` |
| SME responses (`summon_one`) | `gemini-3.5-flash` (tool-calling loop) | `GEMINI_SME_MODEL` |
| Snapshot vision | `gemini-3-pro-preview` | `GEMINI_SNAPSHOT_MODEL` |
| `/v2/live` bridge | `gemini-3.1-flash-live-preview` | `GEMINI_LIVE_MODEL` |

**SME execution note:** SMEs run as a fast `gemini-3.5-flash` **tool-calling loop**. Each loads its persona/skill from `smes/<id>/` (AGENTS.md + SKILL.md) and may call read-only knowledge tools plus `run_analysis` — a tool backed by a **single, kept-warm Antigravity compute sandbox** (one shared env, keep-warm pinged ~240 s) for real Python math. The guild fans out **concurrently** and streams its deliberation to the chat bus as it unfolds. An opt-in path (`FORGE_SME_USE_SANDBOX=1`) instead runs each SME as its own prewarmed per-SME Antigravity managed agent — latency-tolerant, default off; see [ROADMAP.md](ROADMAP.md).

---

## Run locally

```bash
# 1. Create and activate a venv
python3.12 -m venv .venv
source .venv/bin/activate

# 2. Install the package (dev + live extras)
pip install -e ".[dev,live]"

# 3. Optionally set the Gemini key to activate real models
export GEMINI_API_KEY=your-key-here   # omit for stub mode

# 4. Start the server
python -m uvicorn orchestrator.main:app --host 0.0.0.0 --port 8080

# 5. Confirm it's healthy
curl http://localhost:8080/healthz
```

Sample healthz response (stub mode):
```json
{
  "ok": true,
  "protocol_version": "2.0",
  "integrations": {
    "gemini": "stub",
    "smes": "stub",
    "board_profile": "bundled-demo",
    "model_seams": "stub",
    "live_bridge": "stub"
  },
  "sessions": 0
}
```

### Run the test suite

```bash
PYTHONPATH=. pytest -m "not live"
```

Tests that require real Gemini credentials are marked `@live` and excluded from the default run. The deterministic suite (backbone P0–P7, serving-layer integration, SME guild fan-out/streaming, knowledge + BOM, sandbox seams) has grown to **~270 tests** across ~30 modules, all offline. The `@live` marker keeps CI from calling Gemini on every PR. The `observer/` package ships its own test suite (`cd observer && pytest`).

---

## Try the live voice/video loop (laptop simulator)

`clients/live_device_sim.py` is a laptop stand-in for the iPhone/Quest client. It captures your webcam as JPEG frames and your mic as PCM 16 kHz, streams both to `/v2/live`, and plays the TTS audio that Gemini Live sends back. Transcripts print to stdout.

```bash
# Install client deps (webcam/audio stack — separate from the orchestrator package)
pip install -r clients/requirements.txt

# Point at the deployed VM and start talking
python clients/live_device_sim.py --url ws://20.230.188.247:8080/v2/live
```

Key flags:

| Flag | Default | Meaning |
|---|---|---|
| `--url` | `ws://localhost:8080/v2/live` | Orchestrator `/v2/live` WS URL |
| `--session` | random `sim-<hex>` | `sessionId` query param |
| `--camera` | `0` | OpenCV camera index |
| `--fps` | `2` | Webcam frames/sec sent to Live |
| `--no-video` | off | Audio only (useful for quick smoke tests) |
| `--token` | — | Dev auth token (only if `ALLOWED_DEV_TOKENS` is set on the server) |

**macOS:** the first run prompts for camera and microphone permissions — grant both in System Settings → Privacy & Security. On Linux you need `libportaudio2` for `sounddevice`.

If the orchestrator runs without `GEMINI_API_KEY`, the WS stays open but returns nothing — that is stub mode by design. Key it to hear Live respond.

---

## Deploy / CI

GitHub Actions (`.github/workflows/deploy-backend.yml`) handles the full pipeline automatically:

1. **build-and-push** — builds a `linux/arm64` image (the VM is Azure Ampere / aarch64) and pushes to `ghcr.io/hernantech/googleiohackathon-orchestrator` (`:latest` + `:<sha>`). Runs on every branch push touching `orchestrator/**`, `bench_knowledge/**`, `pyproject.toml`, `Dockerfile`, or `deploy/**`.
2. **deploy** — SSHes to the VM, writes `.env` from secrets, runs `docker compose pull && up -d`, and health-checks `/healthz`. Runs only on `main` or manual `workflow_dispatch`.

**Secrets required** (already set in the repo):

| Secret | Purpose |
|---|---|
| `VM_HOST` | VM public IP (`20.230.188.247`) |
| `VM_USER` | SSH user (`galois`) |
| `VM_SSH_PRIVATE_KEY` | Dedicated CI deploy key |
| `GEMINI_API_KEY` | Gemini Live / Flash / snapshot models |
| `MANAGED_AGENTS_API_KEY` | *(optional)* Antigravity sandbox; unset → stub |

See [deploy/README.md](deploy/README.md) for one-time VM bootstrap, manual redeploy, and rollback commands.

> Note: TLS is not yet in front of port 8080 — avoid broad public exposure until you add it (see deploy/README.md).

---

## Clients

| Directory | What it is |
|---|---|
| `forge_ios/` | iOS DeviceSource client (Swift / Xcode). Implements the v2 wire protocol: always-on `/v2/live` socket, on-tap `/v2/snapshot`, chat UI over `/v2/chat`. Phase 6 parity complete. |
| `quest/` | Meta Quest MR client (Android/Kotlin + Compose). Full Gemini Live duplex over `/v2/live` (mic PCM + passthrough JPEG up, jitter-buffered TTS down), live connection states, unified guild feed, HUD + confirmation panels in the MR scene. |
| `clients/` | Laptop device simulator (`live_device_sim.py`). Webcam JPEG + mic PCM → `/v2/live` → TTS playback. Quick integration smoke test without physical hardware. |

---

## Repo map

```
orchestrator/
  main.py            FastAPI app — /healthz, /v2/chat, /v2/live, /v2/snapshot
  config.py          Settings (env vars) + integration_status() for /healthz
  seams.py           Stub callables injected into GraphDeps (zero-config boot)
  genai_seams.py     Real Gemini seams (classify/merge/dissent/summon/snapshot)
  proto/             Frozen wire types (events.py) — do not break compatibility
  graph/             GraphEngine + ForgeState + LangGraph node functions
  chat_bus/          ChatBus, Session, WebSocketTransport (spec 04)
  live/              LiveDuplexBridge + Gemini Live session adapter (spec 00 §4.1)
  snapshot/          analyze_snapshot, handle_snapshot, InMemoryFrameStore
  knowledge/         KnowledgeAdapter — board profile, datasheet + BOM lookups (spec 05); bom.py = lookup_bom
  safety/            SafetyGate — table-driven operator-instruction gate (spec 03)
  managed_agents/    read_sme_response() strategy reader (spec 02 §4)
  storage/           InMemoryFrameStore (frame dedup + retrieval)

smes/                10 SME persona/skill packs (AGENTS.md + SKILL.md) loaded by the guild
observer/            Decoupled read-only manager dashboard — taps the chat bus, persists to SQLite, own container
specs/               Numbered contracts 00–08 (wire protocol through test plan)
bench_knowledge/     Bundled BQ79616 demo board profile + bring-up BOM (examples/)
testdata/wire/       Golden wire payloads — one JSON file per AgentEvent type
tests_integration/   End-to-end seam tests (§3.1, §3.4, §3.5, §3.6, §3.7–3.9)

forge_ios/           iOS client (Swift / Xcode)
quest/               Quest MR client (Android Kotlin / Compose)
clients/             Laptop device simulator
deploy/              CI/CD scripts, docker-compose.yml, VM bootstrap
```

**Key docs:**
- [ROADMAP.md](ROADMAP.md) — phases 1–6, status per phase, env var table, Managed Agents / Antigravity notes
- [HANDOFF.md](HANDOFF.md) — seam map: spec → module → the exact injection points to wire
- [ARCHITECTURE.md](ARCHITECTURE.md) — full topology, LangGraph state machine, safety tree, design patterns
- [observer/README.md](observer/README.md) — the manager dashboard: what it surfaces, the distiller, run/deploy

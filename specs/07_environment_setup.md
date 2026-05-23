# 07 — Environment Setup

> Bootstrap-from-scratch checklist. Accounts, env vars, local services, pre-warm script.
> No bench daemon — Forge advises a human operator and actuates nothing. The only "bench" config is the read-only **board profile** the guild consults.
> Cross-refs: `02_sme_persona_format.md` §9 (sandbox bootstrap), `05_board_knowledge_api.md` §2 (board profile), `06_demo_script.md` §2 (pre-demo checklist).

---

## 1. Accounts and APIs needed

| Service | Why | Required? | Account cost |
|---|---|---|---|
| GCP project (e.g. `forge-v2-demo`) | Firestore (audit, checkpoints), GCS (frame archive) | optional in dev (in-memory fallback); REQUIRED for replay-after-restart | free tier |
| Firebase project (linked to same GCP project) | client auth tokens | optional in dev (shared-secret fallback) | free tier |
| Google AI Studio / Vertex AI API key | Gemini Live + Gemini 2.5 Pro for SME models | REQUIRED for real demo | pay-as-you-go |
| Managed Agents API (Antigravity sandbox preview, May 2026) | per-SME sandboxes | REQUIRED for real demo; no fallback for sandbox execution | pay-as-you-go |
| Workspace API (Drive + Docs) | `@scribe` publish_report → real Google Doc | optional (stub returns fake URL) | free for personal accounts; Workspace tier otherwise |
| DigiKey Search API | `@sourcing` part lookup | optional (stub returns plausible substitutes) | free, requires app registration |
| Vertex AI Search (discovery engine) | `@librarian` datasheet RAG | optional (stub returns canned excerpts) | pay-as-you-go |

For the hackathon demo we need at minimum: Gemini API key, Managed Agents access. Everything else has a stub.

---

## 2. Env vars

Loaded from `~/.forge/v2.env` or process env. Pydantic-Settings on the orchestrator side; gradle properties on the phone client.

### 2.1 Orchestrator

```bash
# ── Service ──────────────────────────────────────────────
FORGE_PORT=8080
FORGE_HOST=0.0.0.0
FORGE_LOG_LEVEL=INFO
FORGE_PROTOCOL_VERSION=2.0

# ── Gemini ───────────────────────────────────────────────
GEMINI_API_KEY=                             # unset → Live runs in stub mode (synthetic transcripts)
GEMINI_LIVE_MODEL=gemini-2.0-flash-exp      # always-on H.264+audio path (00 §4.1); weaker/real-time
GEMINI_SME_MODEL=gemini-2.5-pro             # what each SME's sandbox is configured to use
GEMINI_SENTINEL_MODEL=gemini-2.5-flash      # cheaper/faster for always-on watcher
GEMINI_SNAPSHOT_MODEL=gemini-3-pro          # on-demand snapshot analysis (00 §4.2); strongest vision
                                            #   unset → snapshot falls back to GEMINI_SME_MODEL

# ── Managed Agents ───────────────────────────────────────
MANAGED_AGENTS_API_KEY=                     # unset → SMEs run in stub mode (canned responses)
MANAGED_AGENTS_ENDPOINT=https://managed-agents.googleapis.com/v1preview
MANAGED_AGENTS_REGION=us-central1
SME_ENV_TEMPLATE=ubuntu-2404-python
SME_KEEPWARM_INTERVAL_S=240                 # 0 to disable; see Spike 3

# ── GCP ──────────────────────────────────────────────────
GCP_PROJECT_ID=                             # unset → all GCP services in-memory
GCP_REGION=us-central1
FIRESTORE_DATABASE=(default)
FRAME_BUCKET=                               # gs:// bucket; unset → in-memory ring buffer
GOOGLE_APPLICATION_CREDENTIALS=             # path to service-account JSON if not on GCP host

# ── Firebase ─────────────────────────────────────────────
FIREBASE_PROJECT_ID=                        # unset → shared-secret auth
ALLOWED_DEV_TOKENS=forge-dev-shared-secret  # comma-separated

# ── Board knowledge (read-only; no instruments) ──────────
BOARD_PROFILE=~/.forge/board.yaml           # unset → empty profile; SafetyGate uses defaults below
SNAPSHOT_MAX_EDGE_PX=4096                   # client downscales hi-res snapshots to this before upload (00 §4.3)

# ── Safety thresholds (defaults if board profile absent) ──
SAFETY_DEFAULT_MAX_VOLTAGE_V=12.0           # forces 30 V cell-sim step to DENY → board.yaml MUST set J3
SAFETY_DEFAULT_MAX_CURRENT_A=1.0
SAFETY_DANGEROUS_SERIAL_PATTERNS=reset,calibrate,format,erase,shutdown
SAFETY_CONFIRM_TIMEOUT_S=60

# ── External adapters ────────────────────────────────────
VERTEX_SEARCH_DATASTORE_ID=
DIGIKEY_CLIENT_ID=
DIGIKEY_CLIENT_SECRET=
WORKSPACE_DRIVE_FOLDER_ID=

# ── Replay / checkpoints ─────────────────────────────────
LANGGRAPH_CHECKPOINTER=firestore            # "firestore" | "memory"; firestore requires GCP_PROJECT_ID
LANGGRAPH_REPLAY_WINDOW=200                 # last N chat messages on reconnect
```

### 2.2 Phone / Quest client

Set via gradle properties or BuildConfig:

```
-PCHAT_WS_URL=wss://orchestrator.forge.ai/v2/chat
-PLIVE_WS_URL=wss://orchestrator.forge.ai/v2/live      # (channel B: always-on H.264 + audio)
-PSNAPSHOT_URL=https://orchestrator.forge.ai/v2/snapshot # (F: one hi-res JPEG per 📷 tap)
-PAUTH_TOKEN=forge-dev-shared-secret               # OR signed in via Firebase
-PSESSION_ID=                                       # auto-generated if unset
```

**DeviceSource contract** (what every client — iPhone or Quest — must emit, so the orchestrator stays device-agnostic): from **one camera session with two outputs**, (1) an always-on **H.264 + audio** stream on the Live WS (B), and (2) a **hi-res JPEG** still POSTed to `/v2/snapshot` (F) on each 📷 tap. The device does all encoding; the orchestrator never transcodes. The iPhone implements this with AVFoundation (`AVCaptureVideoDataOutput`/movie + `AVCapturePhotoOutput`); Android/Quest with Camera2 (encoder surface + `ImageReader`). There is no bench-daemon client config because there is no bench daemon.

### 2.3 Fallback behavior summary

| Missing | Behavior |
|---|---|
| `GEMINI_API_KEY` | Live emits synthetic transcripts; SMEs run from canned responses |
| `MANAGED_AGENTS_API_KEY` | SMEs run in stub mode: orchestrator generates SmeResponses locally from a tiny prompt against `GEMINI_SME_MODEL`. Less realistic but the chat/dissent UI still demos. |
| `GCP_PROJECT_ID` | audit + checkpoints in memory; lost on orchestrator restart |
| `FRAME_BUCKET` | frames in memory ring buffer (256 most recent per session) |
| `FIREBASE_PROJECT_ID` | shared-secret auth via `ALLOWED_DEV_TOKENS` |
| `BOARD_PROFILE` | empty profile; SafetyGate uses `SAFETY_DEFAULT_*`; `get_documented_limit` → `found=false` (every value-bearing step forced to confirm — `03 §6`) |
| `VERTEX_SEARCH_DATASTORE_ID` | `@librarian` / `lookup_datasheet` return canned excerpts (`05 §6`) |
| `DIGIKEY_CLIENT_ID/SECRET` | `@sourcing` returns plausible substitutes from a hand-curated table |
| `WORKSPACE_DRIVE_FOLDER_ID` | `publish_report` returns `https://docs.google.com/document/d/STUB-{sessionId}/edit` |

The system MUST come up cleanly with zero env vars set. This is the dev-loop contract carried over from v1.

---

## 3. Local services

A full local run is **one process** (plus a one-shot pre-warm). There is no bench daemon to run — the human at the bench is the "instrument".

```
┌──────────────────────────┐
│  forge-orchestrator      │
│  python -m forge_v2      │
│  PORT 8080               │
│  (FastAPI + LangGraph +  │
│   GeminiLiveBridge +     │
│   SnapshotAnalyzer +     │
│   KnowledgeAdapter)      │
└──────────────────────────┘
```

Bring up sequence:

```bash
# Terminal 1 — orchestrator
cd forge_v2
source .venv/bin/activate
forge-orchestrator                          # reads ~/.forge/v2.env, loads BOARD_PROFILE

# Terminal 2 — pre-warm script (one-shot)
forge-orchestrator-cli prewarm --all-smes
```

Phone client connects to `wss://<host>:8080/v2/chat` (A) and `wss://<host>:8080/v2/live` (B). The presenter is the bench operator.

---

## 4. Repository layout

```
forge_v2/
├── README.md
├── pyproject.toml
├── Dockerfile
├── .env.example
├── specs/                          # this directory
│   ├── 00_wire_protocol.md
│   ├── 01_langgraph_state_machine.md
│   ├── 02_sme_persona_format.md
│   ├── 03_safety_gate_matrix.md
│   ├── 04_chat_bus_protocol.md
│   ├── 05_board_knowledge_api.md
│   ├── 06_demo_script.md
│   ├── 07_environment_setup.md
│   └── 08_test_plan.md
├── orchestrator/                   # FastAPI + LangGraph
│   ├── main.py
│   ├── config.py
│   ├── state.py
│   ├── proto/
│   │   ├── events.py               # frozen — wire contract from 00
│   │   └── tests/                  # WP-* contract tests (00 §11)
│   ├── graph/
│   │   ├── nodes/                  # one file per node from 01 §3
│   │   ├── subgraphs/              # sentinel, scribe, librarian
│   │   ├── checkpointer.py
│   │   └── tests/                  # GR-* node tests (01 §8)
│   ├── chat_bus/
│   │   ├── ws.py                   # implements 04
│   │   ├── channels.py
│   │   ├── renderer_hints.py
│   │   └── tests/                  # CB-* framing tests (04 §13)
│   ├── live/
│   │   ├── bridge.py               # google-genai Live wrapper; passes H.264+audio (no decode)
│   │   └── deferred_calls.py       # Spike 1 logic
│   ├── snapshot/                   # on-demand hi-res path (00 §4.2)
│   │   ├── endpoint.py             # POST /v2/snapshot
│   │   ├── analyzer.py             # analyze_snapshot() → strong model → SnapshotAnalysis
│   │   └── tests/                  # snapshot contract tests (00 §4.2)
│   ├── managed_agents/
│   │   ├── client.py               # wraps interactions.create + files
│   │   ├── pool.py                 # Spike 2 branch B
│   │   └── structured_output.py    # Spike 4 multi-strategy reader
│   ├── safety/
│   │   ├── matrix.py               # 03 §3 table as data
│   │   ├── gate.py
│   │   └── tests/                  # SG-* gate truth-table tests (03 §10)
│   ├── knowledge/                  # replaces bench/ — read-only, no instruments
│   │   ├── board_profile.py        # loads board.yaml (05 §2)
│   │   ├── lookups.py              # lookup_datasheet / lookup_board_doc (05 §3)
│   │   ├── limits.py               # get_documented_limit (05 §3.3, §4)
│   │   └── tests/                  # BK-* knowledge tests (05 §8)
│   ├── adapters/
│   │   ├── vertex_search.py        # backs lookup_datasheet RAG
│   │   ├── digikey.py
│   │   └── workspace.py
│   └── storage/
│       ├── firestore_audit.py
│       └── frame_store.py
├── bench_knowledge/                # static board docs/profiles (NOT a daemon)
│   └── examples/
│       └── bq79616-bringup-2026-05.yaml
├── smes/                           # one dir per SME, contents uploaded into sandbox
│   ├── power/
│   │   ├── AGENTS.md
│   │   ├── skills/
│   │   └── tools/
│   ├── signal/
│   ├── firmware/
│   ├── layout/
│   ├── librarian/
│   ├── sourcing/
│   ├── reverse/
│   ├── sentinel/
│   ├── scribe/
│   ├── tutor/
│   └── tests/                      # SME-* persona-contract tests (02 §11)
├── tests_integration/             # system-level tests (08) — cross-process
│   ├── test_full_request.py        # 08 §3.x end-to-end flows
│   └── test_demo_flow.py           # the 06 script as an integration test (08 §3.6)
└── client/                         # phone app (Kotlin, Compose)
    └── (mirrors forge_quest/ layout; chat-first UI)
```

(`bench_daemon/` and its instrument drivers — `rigol_dp832`, `saleae_logic2`, `fluke_8846a`, `avrdude`, `uvc_chip_cam` — are **deleted**: Forge drives no instruments.)

---

## 5. Pre-warm script

`forge-orchestrator-cli prewarm` does:

1. For each SME in the roster:
   a. If `environment_id` already known and ping succeeds, skip.
   b. Else `environments.create(template=SME_ENV_TEMPLATE, labels={"sme": id})`.
   c. Upload `smes/<id>/AGENTS.md`, `skills/*`, `tools/*`.
   d. `run_shell("pip install <sme-specific deps>")` per the SME's `deps.txt`.
   e. `run_python("import sys; print(sys.version)")` smoke test.
   f. Send a dummy `interactions.create(prompt="You are warmed. Respond with 'ready'.")`.
   g. Register `environment_id` in Firestore at `forge_v2_smes/{sme_id}` AND in process memory.

2. Optionally start the keepwarm task:
   - Every `SME_KEEPWARM_INTERVAL_S`, ping each env with a `run_shell("echo warm")`.
   - **DEPENDS ON SPIKE 3** — if warm-after-5min latency is acceptable, set `SME_KEEPWARM_INTERVAL_S=0`.

3. Validate the board profile:
   - Load `BOARD_PROFILE` (`~/.forge/board.yaml`); assert it parses (BK-1).
   - Assert `get_documented_limit({target:"J3", kind:"net"})` returns the documented max (BK-2) — this is the value the demo's HIGH `set_psu` step is gated against. If it returns `found=false`, the 30 V step will be DENIED (`03 §6`), so fail pre-warm loudly.

4. Validate Live + snapshot:
   - Open a throwaway Live session, send 1 s of silence + a short H.264 segment, confirm a response within 3 s (and that the bridge forwarded it without decoding).
   - POST a test JPEG to `/v2/snapshot`, confirm a `SnapshotAnalysis` comes back over the chat bus within ~4 s and `latestFrame` is set.
   - Close session.

5. Run a synthetic full-graph dry-run with a canned transcript ("test, please summon @power about J3"):
   - Confirm `SupervisorRouter` → `ParallelSummonSMEs` → `MergeOpinion` → `SafetyGate` chain emits the expected events, including a gated `set_psu` InstructionCard carrying a `documentedLimit`.
   - Confirm no errors in `outboundEvents`.

Exit code 0 if all steps pass, non-zero with a diagnostic otherwise. (This dry-run is the smoke variant of the system-level test `08 §3.x`.)

---

## 6. Board profile setup

Copy `bench_knowledge/examples/bq79616-bringup-2026-05.yaml` to `~/.forge/board.yaml` and edit (`05 §2`):
- `parts` — the ICs on your board, each with a `datasheet` id resolvable by `lookup_datasheet`.
- `rails` / `nets` — set conservative documented `max_voltage_v` / `max_current_a`. **These bound what the guild may instruct the operator to do** (`03 §6`); the demo's 30 V cell-sim step requires `nets[J3].max_voltage_v: 30.0`.
- `preconditions` — e.g. `flash_requires_psu_off`, `rework_requires_psu_off`.

Validate before the demo:

```bash
forge-orchestrator-cli board validate --profile ~/.forge/board.yaml
```

Returns 0 if the profile parses and every `parts[].datasheet` resolves in the datastore (or stub); nonzero with what's missing. No instruments are probed — there are none.

---

## 7. SME-specific dependencies

Each `smes/<id>/deps.txt` lists pip packages the sandbox needs:

| SME | Extra deps |
|---|---|
| @power | `numpy`, `scipy` |
| @signal | `numpy`, `scipy`, `pyserial` |
| @firmware | `pyserial`, `intelhex` |
| @layout | (none beyond template) |
| @librarian | `pypdf`, `tiktoken` |
| @sourcing | `httpx`, `pyyaml` |
| @reverse | `pillow`, `pytesseract`, `numpy` |
| @sentinel | `pillow`, `numpy` |
| @scribe | `jinja2`, `markdown` |
| @tutor | (none) |

Template ships with: `python 3.12`, `pip`, `pytest`, `pydantic`, `httpx`, `pyyaml`.

---

## 8. Auth setup quickstart

Dev mode (no Firebase):
```bash
export ALLOWED_DEV_TOKENS=hackathon-demo
# Phone:
./gradlew :app:installDebug -PAUTH_TOKEN=hackathon-demo
```

Real mode (Firebase):
```bash
# Once: enable Firebase Auth in the GCP console
# Once: create a test user
export FIREBASE_PROJECT_ID=forge-v2-demo
export GOOGLE_APPLICATION_CREDENTIALS=~/.forge/sa.json
# Phone: sign in via Firebase Auth UI; the SDK provides the ID token
```

Auth covers only the client↔orchestrator channels (ChatBus + Live). There is no bench-daemon auth path to reconcile — there is no daemon.

---

## 9. Observability

Structlog → stdout JSON by default. To ship to Cloud Logging:

```bash
export FORGE_LOG_SINK=cloud
export GCP_PROJECT_ID=forge-v2-demo
```

LangGraph traces:
- Stored as Firestore checkpoints when `LANGGRAPH_CHECKPOINTER=firestore`.
- LangSmith integration is OFF by default to avoid leaking demo data. To enable:
  ```bash
  export LANGSMITH_API_KEY=...
  export LANGSMITH_PROJECT=forge-v2
  ```

Metrics (counters of: events emitted, SME summons, dissents detected, confirmations approved/denied, safety interrupts) are written to `sessions/{sessionId}/metrics/` regardless.

---

## 10. Tear-down

`forge-orchestrator-cli teardown` does:
1. Close all open WS connections (chat bus, Live).
2. For each SME env: optional `environments.delete(env_id)` if `--release-envs` flag set. Default leaves them for the next session.
3. Flush any pending Firestore writes.
4. Print a session summary (count of messages, confirmations, safety interrupts).

Hackathon convention: do NOT release envs between practice runs; only release at end-of-day. Avoids re-warm tax.

---

## 11. Open questions for lead engineers

- Do we run the orchestrator on Cloud Run or on a laptop at the venue? Cloud Run is more impressive ("our service is live"); a laptop is lower-risk on venue wifi. With no bench daemon there's no LAN-to-instrument constraint pinning us to local, so Cloud Run is more viable than before. Recommend: laptop for the live demo, Cloud Run as a stretch/credibility goal.
- Where does the phone client live? Already-built APK on a dedicated demo phone, or sideload at the venue? Dedicated phone reduces day-of risk.
- The Antigravity Managed Agents preview's rate limits as of 2026-05-23: unknown. If the per-env QPS limit is < 1 Hz, the StreamingAggregator becomes the bottleneck. Add to Spike 3 measurements.
- Cost ceiling for the demo: ~$50 of API spend. The 5-SME parallel deliberation at Pro pricing is the dominant line item. Consider downgrading 1–2 always-on SMEs to Flash if budget tightens.

# Forge — Integration Roadmap

> Living plan for taking the orchestrator backbone from "tested library" to
> "deployed, running service." **Subagents: read this + [`HANDOFF.md`](HANDOFF.md)
> before starting a phase.** The design is in [`ARCHITECTURE.md`](ARCHITECTURE.md)
> and [`specs/00–08`](specs/); this doc is *what to build, in what order*.

**Source-of-truth hierarchy:** `specs/` (contracts) → `HANDOFF.md` (seam map) →
this file (sequencing). If they disagree, the spec wins; fix this file.

---

## Current state (2026-05-23)

- ✅ Orchestrator backbone **P0–P7** merged to `main` (`orchestrator/`, 127 tests, deterministic, offline).
- ✅ Deploy pipeline live: GitHub Actions builds `linux/arm64` → GHCR → SSH-deploys to Azure VM `galois-cloud-vm-2` (westus2, aarch64). Secrets set (`VM_*`, `GEMINI_API_KEY`).
- ✅ **Phase 1 done** — FastAPI serving surface (`main.py`, `seams.py`, `chat_bus/ws.py`, `config.py`); 127 tests green + TestClient smoke.
- ✅ **Phase 2 done** — real `orchestrator/` containerized (root `Dockerfile`) and **deployed**; `forge_v2/` scaffold retired. Live at `http://20.230.188.247:8080` (`/healthz`, `/v2/chat`, `/v2/live`, `/v2/snapshot`), running in **stub mode**.
- ⛔ Next: the **4 model seams are stubs** (Phase 3) — no live Gemini/Antigravity yet. Port 8080 is open (un-TLS'd — front with TLS before broad exposure).

**Definition of "ready" — two tiers:**
- **Runnable (stub mode):** Phases 1–2. Real backbone deploys; boots with zero env vars; iOS client can connect. Demos the UI/flow with canned model output.
- **Real demo:** Phases 3–4. Live Gemini + Antigravity SMEs + snapshot vision.

**Critical path:** Phase 1 → Phase 2 (deployable) → Phases 3 + 4 (real demo). Phases 5–6 run in parallel.

---

## Phases

### Phase 1 — FastAPI serving surface (stub mode)  ·  task #6  ·  HANDOFF §2.A  ·  ✅ DONE (063ea72)
*"Start here; it makes the whole thing runnable."* Boots clean with zero env vars (`07 §2.4`).
- [x] `orchestrator/config.py` — service settings + integration-mode detection
- [ ] `orchestrator/chat_bus/ws.py` — `WebSocketTransport` satisfying the `Transport` protocol (sync `send` → `asyncio.Queue` → ws writer task)
- [ ] `orchestrator/seams.py` — stub `classify`/`summon_one`/`merge_fn`/`dissent_fn` + snapshot `model_call` stub + `build_graph_deps(knowledge)`
- [ ] `orchestrator/main.py`:
  - `GET /healthz`
  - `WSS /v2/chat?sessionId=&client=` — subscribe `Session` to bus, `replay()` on connect, dispatch inbound by `kind` (`Hello`, `ChatMessage`→`engine.run`, `ConfirmationResponse`→`engine.resume` + `bus.resolve_confirmation`, `Pong`→`bus.on_pong`), drain+clear `state.outboundEvents` → `bus.publish_many`
  - `WSS /v2/live?sessionId=` — `LivePassthrough` w/ stub no-op sink; `receive_bytes`→`forward`
  - `POST /v2/snapshot?sessionId=&note=` (`image/jpeg`) — `handle_snapshot(bus=None)` → `engine.ingest_snapshot` → drain to bus; return `202 {jobId}`
  - auth: shared-secret via `Sec-WebSocket-Protocol` (`ALLOWED_DEV_TOKENS`), `uid`→`ForgeState.userId`
  - per-session registry (engine+state) shared by `/v2/chat` and `/v2/snapshot`
- [ ] **Verify:** full pytest stays **127 green**; FastAPI `TestClient` smoke (`/healthz`, snapshot `202`, chat `Hello`→replay handshake).

### Phase 2 — Containerize the real orchestrator + repoint CI  ·  task #7  ·  ✅ DONE (90ccc72)
Make `orchestrator/` (repo root, package `forge-orchestrator`) deploy through the existing pipeline.
- [ ] Add `fastapi`, `uvicorn[standard]`, `websockets` to `pyproject.toml` deps; `google-genai` as optional `[live]` extra
- [ ] **Root `Dockerfile`** (context = repo root): COPY `pyproject.toml` + `orchestrator/` + `bench_knowledge/`; `pip install .`; HEALTHCHECK `/healthz`; CMD `uvicorn orchestrator.main:app --host 0.0.0.0 --port 8080`; non-root user
- [ ] Root `.dockerignore`
- [ ] Repoint `.github/workflows/deploy-backend.yml`: build context `./forge_v2` → `.`; paths `forge_v2/**` → `orchestrator/**`, `pyproject.toml`, `Dockerfile`
- [ ] **LAYOUT DECISION (team):** code is at repo root, but `07 §4` says `forge_v2/orchestrator/`. Keep root (less churn) or move under `forge_v2/`. Then retire the `forge_v2/` scaffold.
- [ ] **Verify:** arm64 image builds in CI, deploys, `/healthz` green on the VM.

### Phase 3 — Wire the 4 real model seams  ·  task #8  ·  HANDOFF §2.B–D  ·  ✅ DONE (verified live on gemini-3.5-flash)

> **SME execution decision:** SMEs (`summon_one`) run as fast **model-only `gemini-3.5-flash`** calls (~10s, forced-JSON → `SmeResponse`), NOT the Antigravity sandbox. The Antigravity sandbox agent is *also* 3.5-flash (verified vs docs) but ~70s cold per SME — too slow for live deliberation. Sandbox path + prewarm/keep-warm (spec 07 §5) is a deferred upgrade. classify/merge/dissent use `gemini-3.5-flash`; snapshot uses `gemini-3-pro-preview`.

Replace stubs with real SDK calls; gate behind key presence, fall back to stubs.
- [ ] `classify` → Gemini Flash (`GEMINI_SME_MODEL`), `01 §3.2` → `RouteDecision`
- [ ] `merge_fn` → Gemini Flash, `01 §3.5` → `(headline, supportingSmes)`
- [ ] `dissent_fn` → Gemini Flash, `01 §3.6` → `DissentResult`
- [ ] `summon_one` → **Antigravity Interactions API** (see note below) → `managed_agents.read_sme_response(...)` → `SmeResponse`
- [ ] snapshot `model_call` → Gemini vision `generateContent` on `GEMINI_SNAPSHOT_MODEL`
- [ ] Live session sink → `google-genai` Live session; `live_sink(chunk)` ships bytes, **NO transcode** (`08 §3.5a`)
- [ ] *(optional)* `build_langgraph(deps)` StateGraph + Firestore checkpointer (HANDOFF §2.E)

### Phase 4 — Deploy secrets/env for a live run  ·  task #9  ·  HANDOFF §4
- [ ] **`MANAGED_AGENTS_API_KEY` is likely NOT needed** — Antigravity authenticates with the same `GEMINI_API_KEY` (see note). Verify our key has preview access first.
- [ ] Set `GEMINI_SNAPSHOT_MODEL=gemini-3-pro`, `GEMINI_SME_MODEL` (Flash)
- [ ] `ALLOWED_DEV_TOKENS` shared secret (`00 §8`)
- [ ] `BOARD_PROFILE` (optional; bundled `bq79616` demo otherwise); `VERTEX_SEARCH_DATASTORE_ID` (optional)
- [ ] Add to the workflow's "Render .env on VM" step
- [ ] Rotate the temporary `GEMINI_API_KEY`

### Phase 5 — Missing integration tests  ·  task #10  ·  HANDOFF §5
- [ ] `§3.7` zero-config boot · `§3.8` replay-across-reconnect · `§3.9` device conformance (parse all `testdata/wire/*.json`, WP-6 server-side)
- [ ] `@live` variants (`08 §5`) — real Gemini/Antigravity, pre-demo only, excluded from CI by the `live` marker
- [ ] Wire CI to run the deterministic suite (`08 §2` P0–P7 gate) on PRs

### Phase 6 — Edge client parity (iOS first)  ·  task #11  ·  HANDOFF §3
The orchestrator is device-blind; clients emit the one **DeviceSource contract**. `forge_ios/` exists — align it.
- [ ] One camera session, two outputs (iOS: `AVCaptureVideoDataOutput` H.264 + `AVCapturePhotoOutput` still) — never two sessions
- [ ] Always-on → `WSS /v2/live`: H.264 + PCM audio (16 kHz mono mic, 24 kHz speaker out)
- [ ] 📷 tap → `POST /v2/snapshot`: full-res JPEG ≤ `SNAPSHOT_MAX_EDGE_PX`; analysis returns over `/v2/chat`
- [ ] Chat UI → `WSS /v2/chat`: render `AgentEvent` union (`00 §2.2`) + typed cards (`04 §3`); InstructionCard → `ConfirmationResponse(approved)`
- [ ] Client parity: parse all `testdata/wire/*.json`
- [ ] *(later)* Quest client (normalize at edge); SME persona content `smes/*/AGENTS.md`

---

## Env vars (HANDOFF §4)

**None required** — zero env vars boots clean in full stub mode (the dev-loop contract). For a live run:

| Var | Purpose | Status |
|---|---|---|
| `GEMINI_API_KEY` | Live + Flash (classify/merge/dissent) + snapshot **+ Antigravity SMEs** | ✅ GH secret set |
| `GEMINI_SNAPSHOT_MODEL` | strong vision model, e.g. `gemini-3-pro` | ❌ |
| `GEMINI_SME_MODEL` | Flash for router/merge/dissent | ❌ |
| `ALLOWED_DEV_TOKENS` | dev shared-secret auth (`00 §8`) | ❌ |
| `BOARD_PROFILE` | board.yaml path | optional — bundled `bq79616` demo |
| `VERTEX_SEARCH_DATASTORE_ID` | datasheet RAG | optional — canned fallback |
| ~~`MANAGED_AGENTS_API_KEY`~~ | — | **not needed** (same Gemini key authenticates Antigravity) |

---

## Managed Agents = Antigravity Interactions API (verified 2026-05-23)

Source: <https://ai.google.dev/gemini-api/docs/custom-agents>. Per-SME work runs in
Antigravity sandboxes (Linux, 4 CPU / 16 GB, Python 3.12), reused via `environment_id`.

- **Auth:** the same `GEMINI_API_KEY` (`AIza...`). No separate key, Vertex, GCP, or OAuth. `genai.Client()` reads it automatically. **✅ VERIFIED 2026-05-23** with a live `interactions.create` (our key returned `status=completed`; a sandbox `environment_id` was created) — `google-genai 2.6.0` exposes `client.interactions` + `client.agents`.
- **Status:** preview. Sandbox compute is **free during preview** (token pricing still applies). Max 1000 agents/account. `antigravity-preview-05-2026` is the only base agent. **Possible allowlist** — if `interactions.create` 403/404s, check the managed-agents quickstart for preview signup.
- **Correct call shape** (our assumed `interactions.create(environment_id=..., stream=True)` was wrong):
  ```python
  from google import genai
  client = genai.Client()                      # reads GEMINI_API_KEY
  it = client.interactions.create(
      agent="antigravity-preview-05-2026",     # required
      input="<task prompt>",                    # not prompt=/contents=
      environment=env_id,                       # reuse sandbox; NOT environment_id=
      previous_interaction_id=prev_id,          # multi-turn continuity
  )
  ```

---

## Conventions (HANDOFF §6)

- Wire types in `proto/events.py` are **frozen**. Extend additively; regenerate `testdata/wire/` with `python testdata/wire/_generate.py`, keep `WP-6` green.
- Every value Forge tells the operator must carry a `documentedLimitRef`; the gate downgrades un-cited setpoints (`03 §3.3.6`). **Never bypass the gate.**
- Commits atomic and test-gated (build order `08 §2`).

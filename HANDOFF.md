# Forge — Integration Handoff

> For the teammate wiring real endpoints + edge devices. This maps **spec → built code → the exact seams to wire**. It is not a redesign — the design lives in `ARCHITECTURE.md` and `specs/00–08`. Read this to find *where to plug in*, not *how it works*.

**Status:** orchestrator backbone P0–P7 implemented and tested (`127 passing`, deterministic, no network). All model/SME/Live calls are **injected as callables** so the graph runs offline; your job is to replace the injection points with real Google SDK calls and add the FastAPI surface. Nothing actuates hardware — Forge advises a human operator (see `ARCHITECTURE.md §0`).

Run the suite: `python -m venv .venv && .venv/bin/pip install -e . pytest && PYTHONPATH=. .venv/bin/pytest`

---

## 1. What exists (spec → module → tests)

| Spec | Module | Tests | Notes |
|---|---|---|---|
| `00` wire protocol | `orchestrator/proto/events.py` | `WP-1..12` | Frozen contract; everything imports it. Golden corpus in `testdata/wire/`. |
| `05` board knowledge | `orchestrator/knowledge/` | `BK-1..11` | `KnowledgeAdapter`: `lookup_datasheet`, `lookup_board_doc`, `get_documented_limit` (deterministic). Stub table when no datastore. |
| `03` safety | `orchestrator/safety/` | `SG-1..12` | `SafetyGate.evaluate(action, invoker, session)` — pure, table-driven. |
| `04` chat bus | `orchestrator/chat_bus/` | `CB-1..11` | `ChatBus` + `Session` are transport-agnostic; need a FastAPI WS adapter (see §2). |
| `02 §4` SME output | `orchestrator/managed_agents/` | `SME-4` | `read_sme_response(...)` strategy+fallback reader. |
| `00 §4.2` snapshot | `orchestrator/snapshot/` + `orchestrator/storage/` | `§3.5b`, `BK-11` | `analyze_snapshot(...)`, `handle_snapshot(...)`, `InMemoryFrameStore`. |
| `00 §4.1` Live | `orchestrator/live/bridge.py` | `§3.5a` | `LivePassthrough` — forwards H.264 byte-for-byte, no transcode. |
| `01` graph | `orchestrator/graph/` | `GR-1..15` | `GraphEngine` runs the topology; HITL pause/resume; error envelope. |
| `08` integration | `tests_integration/` | `§3.1/3.4/3.5b/3.6` | End-to-end seams + the demo flow as one test. |

---

## 2. The seams to wire (priority order)

Each is an **injection point already isolated** for you. Replace the double with the real call; the surrounding logic, contracts, and tests already hold.

### A. FastAPI app (`orchestrator/main.py` — NEW)
Stand up the routes from `specs/04 §1`, `00 §1`, `07`:

| Method | Path | Payload | Wraps |
|---|---|---|---|
| `GET` | `/healthz` | — | `{"ok": true}` |
| `WSS` | `/v2/chat?sessionId=&client=` | `AgentEvent` JSON (`00 §2`) | a `chat_bus.Session` over the WS (see `Transport` protocol in `chat_bus/bus.py`) |
| `WSS` | `/v2/live?sessionId=` | H.264 video + PCM audio (Gemini Live framing) | `live.LivePassthrough` → real Gemini Live session |
| `POST` | `/v2/snapshot?sessionId=&note=` | `image/jpeg` body | `snapshot.endpoint.handle_snapshot(...)` → `202 {jobId}` |

Auth rides in `Sec-WebSocket-Protocol` (`00 §8`); dev mode = shared secret (`ALLOWED_DEV_TOKENS`).

### B. Real model calls (replace the `GraphDeps` callables — `orchestrator/graph/state.py`)
`GraphEngine` takes a `GraphDeps`. Today the tests inject doubles; wire these to `google-genai`:

| `GraphDeps` field | Replace with | Model (`07 §2.1`) |
|---|---|---|
| `classify(transcript, recent)` | SupervisorRouter Flash call → `RouteDecision` (`01 §3.2` prompt) | `GEMINI_SME_MODEL`/Flash |
| `summon_one(smeId, summon)` | Managed-Agents `interactions.create(environment_id=…, stream=True)`, then `managed_agents.read_sme_response(...)` to parse `SmeResponse` | Managed Agents |
| `merge_fn(kept)` | MergeOpinion Flash call → `(headline, supportingSmes)` (`01 §3.5`) | Flash |
| `dissent_fn(responses, round)` | DissentDetector Flash call → `DissentResult` (`01 §3.6`) | Flash |

`SafetyGate` and `KnowledgeAdapter` are **already real** — pass live instances.

### C. Snapshot strong model (`orchestrator/snapshot/analyzer.py`)
`analyze_snapshot(..., model_call=...)` — `model_call(jpeg_bytes, context, model_name) -> str`. Wire to Gemini `generateContent` on `GEMINI_SNAPSHOT_MODEL` (3.x/4.x). Grounding via `KnowledgeAdapter` is already done; just supply the vision call. Offline fallback already handled by `resolve_snapshot_model`.

### D. Live session sink (`orchestrator/live/bridge.py`)
`LivePassthrough(live_sink)` — `live_sink(chunk: bytes)` ships one media chunk to the open Gemini Live session. Do **not** decode/re-encode (that's the whole point — `08 §3.5a`).

### E. Production graph assembly (`orchestrator/graph/` — OPTIONAL)
The node functions are langgraph-shaped. Add `build_langgraph(deps)` assembling a `StateGraph` + Firestore checkpointer (`01 §5/§6`) if you want native interrupts/replay in prod. The `GraphEngine` already gives correct behavior; this is a substrate swap, not new logic.

---

## 3. Edge-device integration (iPhone + Quest)

The orchestrator is **device-blind**. Each client must emit the one **DeviceSource contract** (`07 §2.2`, `00 §4`):

1. **One camera session, two outputs** — never two camera sessions.
   - iOS: `AVCaptureSession` + `AVCaptureVideoDataOutput`/movie (H.264) **and** `AVCapturePhotoOutput` (still).
   - Android/Quest: one `CameraDevice` + encoder surface + `ImageReader`.
2. **Always-on** → `WSS /v2/live`: H.264 video + PCM audio (16 kHz mono mic; 24 kHz speaker out).
3. **On 📷 tap** → `POST /v2/snapshot`: one full-res JPEG (downscale to `SNAPSHOT_MAX_EDGE_PX`). The analysis returns over `/v2/chat` as a `SnapshotAnalysis` card.
4. **Chat UI** → `WSS /v2/chat`: render the `AgentEvent` union (`00 §2.2` has the Kotlin types) + the typed cards (`04 §3`). InstructionCards reply with `ConfirmationResponse(approved=…)` ("I did it" / "Skip").

Quest specifics to normalize **at the client edge** (don't push into the server): pick one passthrough eye (mono), center-crop/undistort the wide FOV, resample audio to 16 kHz, map gaze/controller ray → a normalized frame coord in the `META` sidecar. Optional pose/depth ride as ignorable `META` extras.

Golden wire payloads for client parity testing: `testdata/wire/*.json` (one per type). The Kotlin client must parse all of them (this is `WP-6`, currently validated Python-side only).

---

## 4. Env to set for a live run (`07 §2`)

```
GEMINI_API_KEY=…                 # Live + Flash + snapshot models
GEMINI_SNAPSHOT_MODEL=gemini-3-pro
MANAGED_AGENTS_API_KEY=…         # per-SME sandboxes
BOARD_PROFILE=~/.forge/board.yaml  # bundled demo at bench_knowledge/examples/bq79616-bringup-2026-05.yaml
VERTEX_SEARCH_DATASTORE_ID=…     # optional; falls back to canned datasheet table
```
The system boots with **zero env vars** (everything stubs) — that's the dev-loop contract (`07 §2.4`).

---

## 5. Known gaps (not yet built)

- `main.py` FastAPI surface (§2.A) — **start here; it makes the whole thing runnable.**
- Real SDK wiring at the four seams (§2.B–D).
- Integration tests `§3.7` (zero-config boot), `§3.8` (replay across reconnect), `§3.9` (device conformance) — specced, not coded.
- `@live` test variants (`08 §5`) — run against real Gemini/Managed Agents pre-demo.
- Kotlin client (phone + Quest) and SME persona content (`smes/*/AGENTS.md`).

---

## 6. Conventions

- Wire types are **frozen** (`proto/events.py`). Extend additively; regenerate the golden corpus with `python testdata/wire/_generate.py` and keep `WP-6` green.
- Every value Forge tells the operator must carry a `documentedLimitRef` (the gate downgrades un-cited setpoints — `03 §3.3.6`). Don't bypass the gate.
- Keep commits atomic and test-gated (build order: `08 §2`).

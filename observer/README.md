# Forge Observer — live manager view of the bench floor

A small, **decoupled, read-only** dashboard that lets a shift manager see — at a
glance, live — what each bench operator is doing while they run Forge and rework
electronics. It taps the orchestrator's chat bus as a WebSocket *client*,
persists what it sees to SQLite, distills it into a one-line "what they're doing
right now" per operator (Gemini), and serves a single polling web page.

It **never modifies and never imports** anything under `orchestrator/`. The wire
shapes are mirrored here so the observer builds and ships in its own container,
independent of the orchestrator's source and release cadence.

---

## What a manager sees, and why (the design)

A manager walking the floor doesn't want a log viewer. They want to answer four
questions in two seconds: *Who needs me? Who's stuck? Who's safe? Who's making
progress?* So the view surfaces only the highest-signal things:

| Surface | Why it helps the manager |
|---|---|
| **One operator card per active session** with a 🟢/⚪ live dot and "active 12m / idle 4m" | Instant read of who's working and how long. |
| **AI-distilled one-line headline** — *"Operator 12 min into a 3V3-rail rework; @power flagged a possible short; 1 confirmation pending 2 min."* | The single most valuable cell: turns dozens of raw events into the gist a busy human can absorb without reading. |
| **Board / task** (mined from the orchestrator's own `SummonGuild` topic) | "What are they actually working on" without asking. |
| **Attention flags** — `safety_halt`, `safety_warn`, `repeated_dissent`, `stuck_confirmation`, `long_pause` | The triage signal. Flagged cards float to the top and turn red. These are **computed deterministically** (not by the LLM) so they're reliable even if the model is down or wrong. |
| **Pending confirmations + how long they've been waiting** (a global alert bar *and* per-card) | The "operator stuck?" signal. We surface **age**, not just count — a HIGH-risk confirmation sitting unanswered for 4 min is the thing a manager should walk over for. |
| **SMEs consulted + their key finding** (highest-confidence claim each) | Shows which experts weighed in and what they concluded — e.g. `@power 91% — possible short near U4`. |
| **Click a card → timeline drawer** | Drill-down: the ordered steps / consults / safety events for that session, with a **"Full history"** toggle that shows EVERY persisted event for the session (all kinds, paginated) + per-event raw-JSON. |
| **"All events" tab** | The complete firehose — every persisted event since the start of the DB, filterable by kind / session / free-text, keyset-paginated, each row expandable to its raw JSON. Plus a per-kind count breakdown. Nothing in the DB is invisible from the browser. |
| **Offline operators are kept, never dropped** | A session whose newest event is older than the 1-hour recent window stays listed (greyed, marked offline) so its distilled status is always reachable. The header shows `online / total`. |

What we **deliberately left out** of the at-a-glance grid (but still persist +
expose under "All events"): raw audio, partial ASR, ping/pong heartbeats, and the
per-reconnect replay envelopes (`ChannelList`/`ReplayDone`/`BackpressureNotice`)
are **never persisted** — they're pure noise with no stable id. Everything else
the bus emits IS persisted and IS visible in the firehose view, even if it's too
low-signal for the manager grid (e.g. streaming `ChannelUpdate` token deltas).

### The clever bit: a "managed agent" distiller

Every ~20s a Gemini `gemini-3.5-flash` call reads each session's recent events
and writes one manager-readable **status row** to SQLite. The prompt asks for a
single sentence answering *what / how long / SME flag / pending confirmation +
age*. This mirrors how the orchestrator's own SMEs distill (a fast model-only
JSON call — **not** the slow Antigravity sandbox; see
`orchestrator/genai_seams.py` and `ROADMAP.md` §"Managed Agents").

The same `GEMINI_API_KEY` the orchestrator uses authenticates this. **No key? No
problem** — the distiller falls back to a deterministic templated headline built
from the same facts, so the dashboard (and the test suite) works fully offline.

---

## Architecture: one container, three concerns

```
                   ws://…:8080/v2/chat  (orchestrator chat bus — we are a CLIENT)
                              │  fan-out of every published event
                              ▼
   ┌──────────────────────────────────────── observer container ───────────────┐
   │  ingest_loop   →  normalize  →  SQLite (events)        [Docker named volume]│
   │  distill_loop  →  Gemini 3.5-flash (or heuristic)  →   SQLite (status)      │
   │  FastAPI app   ←  read endpoints  ←  SQLite (WAL)                           │
   └──────────────────────────────────────────────────────────────────────────┘
                              │  GET /  +  GET /api/overview  (polled every 4s)
                              ▼
                         manager's browser
```

**Why one container, not two (ingest+distill vs. web):** all three concerns
share one small SQLite file. Splitting them would force either (a) sharing a
SQLite file across container boundaries — fragile locking, a known footgun — or
(b) adding a network/IPC layer, which is pure overhead for a hackathon MVP. One
process with three asyncio tasks over a single **WAL-mode** SQLite file lets the
reader and writers coexist without blocking. The three concerns are still clean,
separate modules (`ingest.py`, `distill.py`, `web.py`), so splitting later is a
small refactor, not a rewrite.

**Why SQLite on a Docker volume:** the brief calls for persistence across
restarts/redeploys on the Azure VM. A named volume (`observer-data`) gives that
with zero external dependencies. WAL mode is enabled for concurrent read/write.

**Why polling, not SSE/WebSocket to the browser:** a manager view refreshing
every 4s is more than live enough for human reaction times, and polling has zero
reconnect logic and survives proxy/restart hiccups. Less to break.

### Modules

| File | Role |
|---|---|
| `observer/ingest.py` | WS client loop (reconnect/backoff) + pure `normalize`/`persist_event` seam |
| `observer/store.py` | SQLite schema + reads/writes (events, status, pending-confirmation ages) |
| `observer/distill.py` | deterministic `compute_facts` + attention flags; Gemini/heuristic headline |
| `observer/web.py` | FastAPI read endpoints |
| `observer/static/index.html` | the single-page dashboard (vanilla JS, polls `/api/overview`) |
| `observer/main.py` | wires the three tasks into one uvicorn app |

### Reconnect dedup

The orchestrator replays its last-200 `ChatMessage`s + pending
`ConfirmationRequest`s on every WS reconnect (`orchestrator/chat_bus/bus.py`).
To keep a flapping connection from inflating counts/timelines, the `events`
table has a `dedup_key` column with a `UNIQUE` index and inserts use
`INSERT OR IGNORE`. Replay-able events get a stable key (`ChatMessage:<messageId>`,
`ConfirmationRequest:<callId>`); everything else gets `NULL` (SQLite treats
multiple `NULL`s as distinct, so genuinely separate events — e.g. two
`SmeResponse`s sharing a `callId` — are never over-deduped). The store migrates
older DBs on open (adds the column, collapses any legacy duplicate rows, builds
the index) so the persistent volume survives the upgrade.

---

## Run it locally

### A) Fully offline (synthetic data, no orchestrator, no Gemini key)

```bash
cd observer
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest httpx

# seed a synthetic rework session (add --loop to keep emitting chatter)
python -m tools.replay_synthetic --db /tmp/observer.db --loop &

# serve the dashboard against the same DB (bogus bus URL → ingest harmlessly retries)
OBSERVER_DB_PATH=/tmp/observer.db OBSERVER_BUS_URL=ws://127.0.0.1:1/disabled \
  uvicorn observer.main:app --port 8090
# open http://localhost:8090
```

### B) Against the LIVE VM firehose

The orchestrator is live at `ws://20.230.188.247:8080/v2/chat` (no auth token
currently). Point the observer at it:

```bash
cd observer
OBSERVER_BUS_URL=ws://20.230.188.247:8080/v2/chat \
OBSERVER_DB_PATH=/tmp/observer-live.db \
GEMINI_API_KEY=<your-key>            # optional; omit for heuristic headlines
  uvicorn observer.main:app --port 8090
# open http://localhost:8090 and drive a session from the phone/Quest client
```

Without `GEMINI_API_KEY` the headlines are heuristic (still useful); with it,
they're Gemini-distilled. Real smoke against the VM is **optional / not run in
CI** (it depends on live traffic existing).

---

## Test

```bash
cd observer
pip install -r requirements.txt pytest httpx
pytest                       # 49 tests, deterministic, no network
```

The suite proves the contract end-to-end with **synthetic** bus events and a
**stubbed** Gemini call (no network):

- `test_ingest_persist.py` — synthetic events → `persist_event` → SQLite →
  read back; audio/heartbeat dropped; pending-confirmation ages tracked;
  reconnect-replay dedup (duplicate `ChatMessage`/`ConfirmationRequest` ignored,
  genuinely distinct events not over-deduped).
- `test_distill.py` — `compute_facts` extracts SMEs/task/flags; each attention
  flag fires on its trigger; `distill_once` with a **stub** model writes a
  status row; falls back to heuristic when the model raises or the key is unset.
- `test_web.py` — FastAPI `TestClient`: `/api/overview` returns the operator
  with its distilled headline + SMEs + pending confirmation **and keeps offline
  operators listed (marked offline) past the recent window**; `/api/session/{id}`
  returns the compact timeline and (with `full=true`) the FULL paginated history;
  `/api/events` returns the complete firehose with kind/session/text filters +
  `before_id` pagination; `/api/kinds` returns the per-kind breakdown.
- `test_store_views.py` — keyset pagination walks the whole DB, text/kind/session
  filters, full per-session history, last-activity (incl. stale sessions), kind
  counts.

---

## Configuration (all via env; never commit a key)

| Var | Default | Purpose |
|---|---|---|
| `OBSERVER_BUS_URL` | `ws://20.230.188.247:8080/v2/chat` | orchestrator chat bus to tap |
| `OBSERVER_DB_PATH` | `/data/observer.db` | SQLite file (on the Docker volume) |
| `OBSERVER_SESSION_ID` | `observer-dashboard` | our subscription's sessionId (`/v2/chat` requires one) |
| `OBSERVER_DISTILL_INTERVAL_S` | `20` | distiller cadence |
| `OBSERVER_DISTILL_WINDOW_S` | `900` | how far back each distill looks (15 min) |
| `GEMINI_API_KEY` | *(unset)* | enables Gemini headlines; unset → heuristic |
| `OBSERVER_GEMINI_MODEL` | `gemini-3.5-flash` | distiller model |

---

## Known limitation: per-operator attribution (honest MVP note)

**The chat bus does not tag fan-out events with the originating `sessionId`.**
`ChatBus.publish` (orchestrator/chat_bus/bus.py) fans every event to every
subscriber, and only `Hello` carries a `sessionId`; `ChatMessage`,
`SmeResponse`, `ConfirmationRequest`, `SafetyInterrupt`, etc. do not. So a
single passive subscriber cannot reliably tell **which operator** a given event
belongs to.

**What the MVP does within that constraint:** events are attributed to the
observer's own subscription id (`OBSERVER_SESSION_ID`), and `Hello.sessionId`
overrides when present. With one active bench session this is correct; with
several concurrent sessions, events from different operators currently merge into
one card. The store, distiller, and dashboard are **already keyed by
`session_id`** end-to-end, so the moment events carry a real session tag,
multi-operator attribution lights up with **no observer change**.

**Proposed minimal, additive orchestrator follow-up:** stamp the originating
`sessionId` onto each fanned-out event at the `publish`/`publish_many` seam, and
emit a `Presence` event on `/v2/chat` + `/v2/live` connect/disconnect. Both are
additive (forward-compatible per the wire protocol's "ignore extra fields"
rule), touch only the publish/connect seam, and need no client changes. **The
observer side is already implemented** — `normalize` keys on `sessionId` when
present (single-bucket fallback otherwise) and a `Presence` row is handled. The
full spec (event shape, hook points, observer consumption) is in
[`ATTRIBUTION.md`](ATTRIBUTION.md). That hook turns this dashboard into a true
multi-operator floor view with no further observer change.

### Other unverified / optional items
- **Live VM smoke** is optional and not run in CI (needs live traffic). The
  offline replay path (above) exercises the same ingest→distill→serve pipeline.
- **Real Gemini call** is exercised only via the stub in tests; the live path
  uses the same SDK shape as `orchestrator/genai_seams.py` (verified pattern).

# Per-operator attribution — the minimal additive orchestrator hook

**Status:** observer side is DONE and ships today. The orchestrator side is a
small, additive change the parent will implement. This file is the spec.

---

## The problem

`ChatBus.publish(event)` (`orchestrator/chat_bus/bus.py`) fans **every** event
out to **every** subscribed session **without tagging which operator it came
from**. Only `Hello` carries a `sessionId`; `ChatMessage`, `SmeResponse`,
`ConfirmationRequest`, `SafetyInterrupt`, `SummonGuild`, `ToolCall`, etc. do not.

A passive subscriber (the observer) therefore can't tell which operator a given
event belongs to. Today the observer attributes everything to its own
subscription id (`OBSERVER_SESSION_ID`, default `observer-dashboard`), so **all
operators merge into one card**. `Hello.sessionId` is the only per-operator
signal, and it only fires at connect.

The observer's store, distiller, dashboard, and all the new views are **already
keyed by `session_id` end-to-end**. The instant events carry a real session tag,
multi-operator attribution lights up with **no further observer change**.

---

## The hook (orchestrator side — additive, forward-compatible)

Two additive pieces, both at the publish/connect seam. Neither touches the
frozen `orchestrator/proto/events.py` union or any client.

### 1. Tag each fanned-out event with its originating `sessionId`

The wire protocol ignores unknown/extra fields (WP-3, pydantic v2 default), so
**stamping an extra `sessionId` onto the JSON each subscriber receives is
forward-compatible** — existing clients ignore it; the observer reads it.

Where to hook: `ChatBus.publish` / `publish_many` is the single fan-out seam.
Thread the originating session id through to it and stamp it on the serialized
frame as each subscriber is enqueued. Sketch (illustrative — exact wiring is the
parent's call):

```python
# orchestrator/chat_bus/bus.py
def publish(self, event, *, origin_session_id: str | None = None, flush: bool = True):
    self._record(event)
    for session in self._sessions.values():
        # stamp the originator onto the outbound frame (additive field)
        session.enqueue(event, extra={"sessionId": origin_session_id})
        ...
```

The caller already knows the originator: `orchestrator/main.py::_drain_to_bus`
(or wherever per-session agent output is drained) publishes on behalf of a known
session — pass that id as `origin_session_id`. **No new event type, no client
change, no schema migration.**

### 2. Emit a presence event on connect/disconnect (`/v2/chat` + `/v2/live`)

So the dashboard shows **connected vs. idle** operators (not just "had activity
recently"), publish a small presence event when a session subscribes/unsubscribes:

```jsonc
{
  "kind": "Presence",          // NEW additive kind; non-union ⇒ clients ignore it
  "sessionId": "op-bench-07",
  "client": "phone",           // "phone" | "quest" | ...
  "state": "online",           // "online" on subscribe, "offline" on unsubscribe
  "ts": 1737590400000000000    // ns, like every other event
}
```

Where to hook: `ChatBus.subscribe(session)` → publish `Presence(state="online")`;
`ChatBus.unsubscribe(session_id)` → publish `Presence(state="offline")`. Mirror
the same on the `/v2/live` connect/disconnect path so a live-only operator
(no chat traffic) still appears. `Goodbye` is **not** sufficient on its own — it
carries no `sessionId` in the frozen schema, so it can't be attributed.

---

## How the observer consumes it (already implemented)

- **`ingest.normalize`** already reads `session_id = data.get("sessionId") or
  default_session_id`. The moment fan-out events carry `sessionId` (piece 1),
  each event is stored under the right operator. When absent, it falls back to
  `OBSERVER_SESSION_ID` — i.e. **today's single-bucket behavior, unchanged**.
- **`Presence` handling** is in place: `ingest._summary_for` renders a
  `Presence` row (`"phone online (op-bench-07)"`) and it's persisted keyed by
  `sessionId`, so it shows in the firehose and contributes to the operator's
  last-activity. `Hello` already overrides the session id when present.
- **`web.overview`** lists the union of every status row + every session ever
  seen, marking each `online`/offline from its newest event time — so each
  tagged operator becomes its own card, online ones first, stale ones greyed but
  retained. (A future refinement: treat an explicit `Presence(state="offline")`
  as an immediate offline flip ahead of the idle-window heuristic.)

**Net:** piece 1 alone turns the merged single card into true per-operator cards.
Piece 2 adds the connected/idle distinction. Both are additive,
forward-compatible, touch only the publish/connect seam, and need zero client
or observer changes beyond what already ships here.

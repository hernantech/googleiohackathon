"""`ChatBus` — the transport-agnostic core of the chat bus (P3).

Implements the server-side semantics from `04`:
- pub/sub fan-out to subscribed sessions (§5)
- a 30-min / last-200 replay buffer of `ChatMessage`s (§6, §12)
- a bounded per-session queue (256 events) that drops `ChannelUpdate`s
  preferentially over `ChatMessage`s on overflow and emits a
  `BackpressureNotice` (§5)
- ULID idempotency / dedup hooks (§5; client-side dedup also modeled)
- reconnect/replay: `ChannelList` + last-200 + pending `ConfirmationRequest`s
  + `ReplayDone` (§6)
- a 20s `Ping` heartbeat with "missing 2 pongs ⇒ dead" liveness (§5, CB-10)

No FastAPI / network here so the CB tests can drive it with an in-memory WS
pair. A `Transport` is just "something with a synchronous `send(event)`"; the
test harness supplies a queue-backed fake. A thin FastAPI adapter can wrap a
`Session` later without changing this logic.
"""

from __future__ import annotations

import time
from typing import Iterable, Protocol, Union, runtime_checkable

from orchestrator.proto.events import (
    ChannelUpdate,
    ChatMessage,
    ConfirmationRequest,
    new_ulid,
)

from orchestrator.chat_bus.channels import build_channel_list
from orchestrator.chat_bus.envelopes import (
    BackpressureNotice,
    ChannelList,
    Ping,
    Pong,
    Presence,
    ReplayDone,
)

#: Anything the bus may hand to a transport. Union events are pydantic models;
#: so are the chat-bus envelopes. We treat them structurally (all have `kind`).
BusEvent = object

#: Tunables (§5, §6, §12).
MAX_QUEUE = 256                       # per-session bounded queue
REPLAY_MAX = 200                      # last-N messages replayed
REPLAY_WINDOW_NS = 30 * 60 * 1_000_000_000  # 30 minutes, in ns
PING_INTERVAL_S = 20                  # heartbeat cadence (§5)
MAX_MISSED_PINGS = 2                  # missing 2 pongs ⇒ dead (§5, CB-10)


@runtime_checkable
class Transport(Protocol):
    """Minimal sink the bus writes to. The FastAPI adapter and the test fake
    both satisfy this. `send` must not raise on a healthy connection."""

    def send(self, event: object) -> None: ...


def _kind_of(event: object) -> str:
    """Best-effort discriminator read (pydantic models carry `.kind`)."""
    return getattr(event, "kind", type(event).__name__)


def _now_ns() -> int:
    return time.time_ns()


class Session:
    """One connected client. Owns a bounded outbound queue and the dedup set.

    The bus calls `enqueue(event)`; `flush()` drains the queue into the
    transport. Tests can also read `transport` directly. The queue applies the
    §5 backpressure policy: when full, drop the oldest `ChannelUpdate`; if none
    remain, the event is rejected (never silently drop a `ChatMessage`).
    """

    def __init__(self, session_id: str, transport: Transport, *, max_queue: int = MAX_QUEUE):
        self.session_id = session_id
        self.transport = transport
        self.max_queue = max_queue
        self._queue: list[object] = []
        # Parallel, lockstep list of the originating sessionId for each queued
        # event (None = untagged). Kept separate from `_queue` so the queue still
        # holds raw event models — the backpressure tests + ClientMessageStore
        # rely on `isinstance` over `_queue` (observer/ATTRIBUTION.md §1).
        self._queue_origins: list[str | None] = []
        self._seen_ids: set[str] = set()        # ULID dedup (ChatMessage.messageId)
        self.dropped_count = 0                   # cumulative dropped ChannelUpdates
        self._drop_since_ts: int | None = None   # ts of first drop in current burst
        self.missed_pings = 0                    # heartbeat liveness (§5)
        self.alive = True

    # ── idempotency (§5) ──
    def _is_duplicate(self, event: object) -> bool:
        """ChatMessage dedup by `messageId`. Replay re-sends the same ULID; the
        client renders once. We model that server-side too (defensive)."""
        if isinstance(event, ChatMessage):
            mid = event.messageId
            if mid in self._seen_ids:
                return True
            self._seen_ids.add(mid)
        return False

    # ── backpressure (§5) ──
    def enqueue(self, event: object, *, origin_session_id: str | None = None) -> bool:
        """Queue an event for this session. Returns False if it was dropped.

        Overflow policy: prefer dropping queued `ChannelUpdate`s over the
        incoming event. A `ChatMessage` is never dropped while any droppable
        `ChannelUpdate` sits ahead of it. When something is dropped we record
        it so the bus can emit a single `BackpressureNotice`.

        ``origin_session_id`` (optional, defaulted) is the id of the session the
        event originated from; it's stamped on the outbound JSON frame at flush
        so a passive subscriber can attribute it (observer/ATTRIBUTION.md §1).
        Tracked in a parallel list so the queue still holds raw event models.
        """
        if self._is_duplicate(event):
            return False

        if len(self._queue) < self.max_queue:
            self._append(event, origin_session_id)
            return True

        # Queue is full. Try to make room by evicting the oldest ChannelUpdate.
        idx = next(
            (i for i, e in enumerate(self._queue) if isinstance(e, ChannelUpdate)),
            None,
        )
        if idx is not None:
            self._pop(idx)
            self._note_drop()
            self._append(event, origin_session_id)
            return True

        # No droppable ChannelUpdate left. If the incoming event is itself a
        # ChannelUpdate, drop it (protect ChatMessages). Otherwise force-room by
        # evicting the oldest queued item that is NOT a ChatMessage; if the
        # queue is all ChatMessages, drop the incoming ChannelUpdate-or-keep.
        if isinstance(event, ChannelUpdate):
            self._note_drop()
            return False

        # Incoming is high-priority (e.g. ChatMessage) and queue is saturated
        # with ChatMessages: still admit it by evicting the oldest non-message
        # if one exists; otherwise admit and let the queue grow by one (the
        # ChatMessage guarantee wins over the hard cap).
        non_msg_idx = next(
            (i for i, e in enumerate(self._queue) if not isinstance(e, ChatMessage)),
            None,
        )
        if non_msg_idx is not None:
            self._pop(non_msg_idx)
            self._note_drop()
        self._append(event, origin_session_id)
        return True

    def _append(self, event: object, origin_session_id: str | None) -> None:
        """Append to the event queue + the lockstep origin list."""
        self._queue.append(event)
        self._queue_origins.append(origin_session_id)

    def _pop(self, idx: int) -> None:
        """Pop from the event queue + the lockstep origin list (same index)."""
        self._queue.pop(idx)
        self._queue_origins.pop(idx)

    def _note_drop(self) -> None:
        self.dropped_count += 1
        if self._drop_since_ts is None:
            self._drop_since_ts = _now_ns()

    def take_backpressure_notice(self) -> BackpressureNotice | None:
        """If drops occurred since the last call, return a notice and reset the
        burst window. The bus enqueues this so the client can show a banner."""
        if self.dropped_count and self._drop_since_ts is not None:
            notice = BackpressureNotice(
                dropped=self.dropped_count,
                sinceTs=self._drop_since_ts,
            )
            return notice
        return None

    # ── draining ──
    def flush(self) -> list[object]:
        """Drain the queue into the transport, in order. Returns what was sent.

        Each event is sent with its originating sessionId as additive serialization
        `extra` (observer/ATTRIBUTION.md §1). Transports that don't accept the
        keyword (the test fake's bare `send(event)`) get the plain call — the
        origin tagging is purely a wire-serialization concern, so dropping it on
        those sinks is harmless and keeps the `Transport` protocol unchanged."""
        sent = list(self._queue)
        origins = list(self._queue_origins)
        self._queue.clear()
        self._queue_origins.clear()
        for event, origin in zip(sent, origins):
            self._send(event, origin)
        return sent

    def _send(self, event: object, origin_session_id: str | None) -> None:
        extra = {"sessionId": origin_session_id} if origin_session_id is not None else None
        try:
            self.transport.send(event, extra=extra)
        except TypeError:
            # Backward-compatible sink: plain send(event) (e.g. the test fake).
            self.transport.send(event)

    # ── heartbeat (§5, CB-10) ──
    def on_ping_sent(self) -> None:
        self.missed_pings += 1
        if self.missed_pings > MAX_MISSED_PINGS:
            self.alive = False

    def on_pong(self, pong: Pong) -> None:
        self.missed_pings = 0


class ChatBus:
    """The server. Holds the replay buffer + pending confirmations and fans
    events out to subscribed sessions."""

    #: Default client label for a Presence event when the caller doesn't name one.
    DEFAULT_CLIENT = "operator"

    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._buffer: list[ChatMessage] = []                # replay buffer (§6)
        self._pending_confirmations: dict[str, ConfirmationRequest] = {}  # by callId
        self._clients: dict[str, str] = {}                  # session_id -> client label

    # ── session lifecycle ──
    def subscribe(self, session: Session, *, client: str | None = None) -> Session:
        """Register a session for fan-out and announce its presence.

        Publishes `Presence(state="online")` tagged with this session's id so the
        observer can show the operator as connected (observer/ATTRIBUTION.md §2).
        `client` ("phone" | "quest" | ...) is remembered so the matching
        `Presence(state="offline")` on unsubscribe carries the same label.
        Returns the session for chaining (unchanged signature otherwise)."""
        self._sessions[session.session_id] = session
        label = client or self.DEFAULT_CLIENT
        self._clients[session.session_id] = label
        self._emit_presence(session.session_id, label, "online")
        return session

    def unsubscribe(self, session_id: str, *, client: str | None = None) -> None:
        """Deregister a session and announce its departure.

        Publishes `Presence(state="offline")` (tagged with this session's id)
        BEFORE removing the session so every remaining subscriber — incl. the
        observer — receives it (observer/ATTRIBUTION.md §2). No-op presence if
        the session was never subscribed."""
        if session_id in self._sessions:
            label = client or self._clients.get(session_id) or self.DEFAULT_CLIENT
            self._emit_presence(session_id, label, "offline")
        self._sessions.pop(session_id, None)
        self._clients.pop(session_id, None)

    def _emit_presence(self, session_id: str, client: str, state: str) -> None:
        """Fan a Presence envelope out to subscribers, attributed to `session_id`.

        Delivered to every OTHER subscriber (the observer is the consumer), never
        to the subject session itself — so it can't jump ahead of that session's
        own replay handshake (ChannelList → … → ReplayDone must stay first on
        connect). Presence is a non-union envelope (clients ignore the unknown
        kind, WP-3) and is NOT recorded into the replay buffer (only ChatMessages
        are) so it never re-fires on reconnect."""
        self.publish(
            Presence(sessionId=session_id, client=client, state=state),
            origin_session_id=session_id,
            exclude_session_id=session_id,
        )

    @property
    def sessions(self) -> list[Session]:
        return list(self._sessions.values())

    # ── publish / fan-out (§5) ──
    def publish(
        self, event: object, *, origin_session_id: str | None = None,
        exclude_session_id: str | None = None, flush: bool = True,
    ) -> None:
        """Fan an event out to every subscribed session.

        Records `ChatMessage`s into the replay buffer and tracks pending
        `ConfirmationRequest`s (cleared by a matching `ConfirmationResponse`).
        Per-session backpressure is applied in `Session.enqueue`; if drops
        happen, a `BackpressureNotice` is enqueued right behind the event.

        ``origin_session_id`` (optional, defaulted None) is the id of the session
        the event originated from. It's carried through to each subscriber's
        outbound JSON as an additive `sessionId` field so a passive subscriber
        (the observer) can attribute it per-operator (observer/ATTRIBUTION.md §1).

        ``exclude_session_id`` (optional) skips one subscriber — used for Presence
        so a connecting session never receives its own presence ahead of its
        replay handshake.
        """
        self._record(event)

        for session in self._sessions.values():
            if exclude_session_id is not None and session.session_id == exclude_session_id:
                continue
            session.enqueue(event, origin_session_id=origin_session_id)
            notice = session.take_backpressure_notice()
            if notice is not None:
                # Enqueue the notice too (it is high-priority, never dropped).
                session.enqueue(notice)
            if flush:
                session.flush()

    def publish_many(
        self, events: Iterable[object], *, origin_session_id: str | None = None,
        flush: bool = True,
    ) -> None:
        for e in events:
            self.publish(e, origin_session_id=origin_session_id, flush=False)
        if flush:
            for s in self._sessions.values():
                s.flush()

    def _record(self, event: object) -> None:
        """Update server-side state from an outgoing/seen event."""
        if isinstance(event, ChatMessage):
            self._append_to_buffer(event)
        elif isinstance(event, ConfirmationRequest):
            self._pending_confirmations[event.callId] = event

    def _append_to_buffer(self, msg: ChatMessage) -> None:
        # Dedup by messageId so replay re-sends don't double-store (§5).
        if any(m.messageId == msg.messageId for m in self._buffer):
            return
        self._buffer.append(msg)
        self._evict_buffer()

    def _evict_buffer(self) -> None:
        # Trim to the 30-min window and the last-200 cap (§6, §12).
        cutoff = _now_ns() - REPLAY_WINDOW_NS
        self._buffer = [m for m in self._buffer if m.ts >= cutoff]
        if len(self._buffer) > REPLAY_MAX:
            self._buffer = self._buffer[-REPLAY_MAX:]

    # ── confirmations ──
    def resolve_confirmation(self, call_id: str) -> None:
        """Drop a pending confirmation once it's answered (so replay won't
        re-offer it)."""
        self._pending_confirmations.pop(call_id, None)

    @property
    def pending_confirmations(self) -> list[ConfirmationRequest]:
        return list(self._pending_confirmations.values())

    # ── reconnect / replay (§6, CB-7) ──
    def replay(self, session_id: str, *, checkpoint_id: str | None = None) -> list[object]:
        """Emit the replay sequence to the named session, in contract order:

            ChannelList → last-200 ChatMessages (ts order) →
            pending ConfirmationRequests → ReplayDone

        Returns the exact ordered list of events sent (the tests assert on it).
        Replayed ChatMessages bypass per-session dedup so the buffer is fully
        re-sent; client-side ULID dedup is what keeps renders idempotent (CB-4).
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"no such session: {session_id}")

        self._evict_buffer()
        ordered = sorted(self._buffer, key=lambda m: m.ts)
        unread = self._unread_by_channel(ordered)

        out: list[object] = []
        out.append(build_channel_list(unread=unread))
        out.extend(ordered)
        out.extend(self.pending_confirmations)
        resume_ts = ordered[-1].ts if ordered else _now_ns()
        out.append(ReplayDone(resumeTs=resume_ts, checkpointId=checkpoint_id))

        for event in out:
            session.transport.send(event)
        return out

    @staticmethod
    def _unread_by_channel(messages: list[ChatMessage]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for m in messages:
            counts[m.channelId] = counts.get(m.channelId, 0) + 1
        return counts

    # ── heartbeat (§5, CB-10) ──
    def heartbeat(self, *, nonce: str | None = None) -> dict[str, Ping]:
        """Emit a `Ping` to every session and bump its missed-ping counter.

        Returns the per-session Ping sent. A session that has now missed more
        than `MAX_MISSED_PINGS` is marked `alive=False`; callers may reap it.
        """
        nonce = nonce or new_ulid()
        sent: dict[str, Ping] = {}
        for session in self._sessions.values():
            ping = Ping(nonce=nonce)
            session.transport.send(ping)
            session.on_ping_sent()
            sent[session.session_id] = ping
        return sent

    def on_pong(self, session_id: str, pong: Pong) -> None:
        session = self._sessions.get(session_id)
        if session is not None:
            session.on_pong(pong)

    def reap_dead(self) -> list[str]:
        """Remove and return the ids of sessions that missed too many pings."""
        dead = [sid for sid, s in self._sessions.items() if not s.alive]
        for sid in dead:
            self.unsubscribe(sid)
        return dead


# ───────────────────── client-side delta assembly (§3.2, §5) ─────────────────────
# Modeled here (transport-agnostic) so the streaming/dedup/buffering CB tests
# can exercise the renderer contract without a Kotlin client.

class ClientMessageStore:
    """Minimal client-side model: dedups by `messageId`, assembles streaming
    `ChannelUpdate` deltas into the parent `ChatMessage.body`, and buffers
    orphan updates ≤2s awaiting their parent (§5, CB-2/CB-3/CB-4)."""

    ORPHAN_TTL_NS = 2 * 1_000_000_000  # 2 seconds (§5)

    def __init__(self):
        self.messages: dict[str, ChatMessage] = {}     # messageId -> message (mutable body)
        self.order: list[str] = []                      # render order, first-seen
        self.done: set[str] = set()
        self._orphans: dict[str, list[tuple[int, ChannelUpdate]]] = {}  # messageId -> [(seen_ns, upd)]
        self.render_count: dict[str, int] = {}          # messageId -> times rendered (dedup proof)

    def ingest(self, event: object, *, now_ns: int | None = None) -> None:
        now = now_ns if now_ns is not None else _now_ns()
        if isinstance(event, ChatMessage):
            self._ingest_message(event)
        elif isinstance(event, ChannelUpdate):
            self._ingest_update(event, now)

    def _ingest_message(self, msg: ChatMessage) -> None:
        if msg.messageId in self.messages:
            # Idempotent: re-rendering the same ULID is a no-op for body (CB-4).
            self.render_count[msg.messageId] = self.render_count.get(msg.messageId, 1)
            return
        # Store a copy we can mutate as deltas arrive.
        self.messages[msg.messageId] = msg.model_copy(deep=True)
        self.order.append(msg.messageId)
        self.render_count[msg.messageId] = 1
        if not msg.streaming:
            self.done.add(msg.messageId)
        # Apply any buffered orphan updates that were waiting on this parent.
        pending = self._orphans.pop(msg.messageId, [])
        for _seen, upd in pending:
            self._apply_update(upd)

    def _ingest_update(self, upd: ChannelUpdate, now: int) -> None:
        if upd.messageId in self.messages:
            self._apply_update(upd)
        else:
            # Orphan: parent not seen yet. Buffer ≤2s (§5, CB-3).
            self._orphans.setdefault(upd.messageId, []).append((now, upd))

    def _apply_update(self, upd: ChannelUpdate) -> None:
        msg = self.messages.get(upd.messageId)
        if msg is None:
            return
        if upd.messageId in self.done:
            return  # truncated/closed; do not append further (§3.2)
        msg.body += upd.deltaText
        if upd.done:
            self.done.add(upd.messageId)

    def sweep_orphans(self, *, now_ns: int | None = None) -> int:
        """Drop orphan updates older than 2s. Returns how many were dropped
        (CB-3: buffered ≤2s then dropped, no crash)."""
        now = now_ns if now_ns is not None else _now_ns()
        dropped = 0
        for mid in list(self._orphans.keys()):
            kept = [(seen, upd) for seen, upd in self._orphans[mid]
                    if now - seen < self.ORPHAN_TTL_NS]
            dropped += len(self._orphans[mid]) - len(kept)
            if kept:
                self._orphans[mid] = kept
            else:
                del self._orphans[mid]
        return dropped

    @property
    def orphan_count(self) -> int:
        return sum(len(v) for v in self._orphans.values())

    def body_of(self, message_id: str) -> str:
        return self.messages[message_id].body

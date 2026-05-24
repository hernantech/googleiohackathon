"""Ingest: normalize bus events + the WS-client loop that taps /v2/chat.

The chat bus fans EVERY published event out to every subscriber (see
orchestrator/chat_bus/bus.py ``publish``). We join as an ordinary subscriber
and persist what we see. ``normalize`` is pure + deterministic so the test
suite can feed synthetic events straight into the persist path with no socket.

Event shapes are mirrored from orchestrator/proto/events.py + chat_bus/
envelopes.py. We intentionally do NOT import them (decoupling); we read fields
defensively by name so a forward-compatible extra field never crashes ingest.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any
from urllib.parse import urlencode

from observer.config import Settings
from observer.store import Store, now_ms, ns_to_ms

log = logging.getLogger("observer.ingest")

# Audio is high-volume + zero manager signal — never persist it. The replay
# envelopes (ChannelList/ReplayDone/BackpressureNotice) are re-sent on every
# reconnect, carry no manager signal, and have no stable id to dedup on — drop.
_DROP_KINDS = {
    "AudioChunk", "Ping", "Pong",
    "ChannelList", "ReplayDone", "BackpressureNotice",
}


def _dedup_key_for(kind: str, data: dict[str, Any]) -> str | None:
    """Stable id for events the bus REPLAYS on reconnect, so re-delivery is
    idempotent. The orchestrator's replay buffer holds ChatMessages (keyed by
    the client-stable ``messageId`` ULID) and re-sends pending
    ConfirmationRequests (one per ``callId``). Everything else returns ``None``
    (never deduped — SQLite treats NULL dedup_key rows as distinct)."""
    if kind == "ChatMessage" and data.get("messageId"):
        return f"ChatMessage:{data['messageId']}"
    if kind == "ConfirmationRequest" and data.get("callId"):
        return f"ConfirmationRequest:{data['callId']}"
    return None


def _truncate(text: str | None, n: int = 280) -> str | None:
    if text is None:
        return None
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _chatmessage_summary(data: dict[str, Any]) -> str | None:
    """ChatMessage snippet. A snapshot-analysis card rides inside a ChatMessage
    with ``bodyContentType=application/json`` and a JSON body holding a
    ``SnapshotAnalysis`` (kind + analysis text) — surface its analysis prose so
    the manager sees what the vision pass concluded, not an opaque JSON blob."""
    body = data.get("body")
    if data.get("bodyContentType") == "application/json" and body:
        try:
            obj = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            obj = None
        if isinstance(obj, dict) and obj.get("kind") == "SnapshotAnalysis":
            return _truncate(f"snapshot: {obj.get('analysis', '')}")
    return _truncate(body)


def _summary_for(kind: str, data: dict[str, Any]) -> str | None:
    """A short, human-facing snippet per event kind — the timeline one-liner."""
    if kind == "ChatMessage":
        return _chatmessage_summary(data)
    if kind == "SmeResponse":
        claim = data.get("claim") or ""
        conf = data.get("confidence")
        prefix = f"[{conf:.0%}] " if isinstance(conf, (int, float)) else ""
        return _truncate(prefix + claim)
    if kind == "SummonGuild":
        smes = ", ".join(data.get("smes", []))
        return _truncate(f"summon {smes} re: {data.get('topic', '')}")
    if kind == "ConfirmationRequest":
        return _truncate(f"[{data.get('risk', '?')}] {data.get('summary', '')}")
    if kind == "ConfirmationResponse":
        verb = "confirmed" if data.get("approved") else "declined"
        return _truncate(f"operator {verb} {data.get('callId', '')}")
    if kind == "SafetyInterrupt":
        return _truncate(f"[{data.get('severity', '?')}] {data.get('reason', '')}")
    if kind == "DissentReport":
        return _truncate(f"dissent on {data.get('axis', '')}: {data.get('summary', '')}")
    if kind == "Transcript":
        if data.get("partial"):
            return None  # skip partial ASR — too noisy for the timeline
        return _truncate(data.get("text"))
    if kind == "ToolCall":
        return _truncate(f"call {data.get('name', '')}")
    if kind == "ToolResult":
        deferred = " (deferred)" if data.get("deferred") else ""
        return _truncate(f"result{deferred} {data.get('resultJson', '')}")
    if kind == "ChannelUpdate":
        # Streaming token delta — low signal, but keep a snippet so it's never
        # an opaque blank row in the firehose view.
        done = " ✓done" if data.get("done") else ""
        return _truncate(f"…{data.get('deltaText', '')}{done}")
    if kind == "CheckpointMarker":
        return _truncate(f"checkpoint {data.get('graphNodeName', '')}")
    if kind == "Hello":
        return _truncate(f"{data.get('client', '?')} joined session {data.get('sessionId', '')}")
    if kind == "Goodbye":
        return _truncate(f"left: {data.get('reason', '')}")
    if kind == "Presence":
        # ADDITIVE forward-hook (see ATTRIBUTION.md): the orchestrator may emit a
        # presence event when an operator connects/disconnects on /v2/chat or
        # /v2/live. We persist it keyed by its sessionId so the dashboard can show
        # connected-vs-idle. Unknown today → simply never arrives (graceful).
        state = data.get("state", "?")
        return _truncate(f"{data.get('client', 'operator')} {state} ({data.get('sessionId', '')})")
    return None


def _author_of(kind: str, data: dict[str, Any]) -> str | None:
    if "authorId" in data:
        return data.get("authorId")
    if "smeId" in data:
        return data.get("smeId")
    if "invokerSmeId" in data:
        return data.get("invokerSmeId")
    if kind == "Transcript":
        return data.get("smeId") or data.get("speaker")
    if kind == "SafetyInterrupt":
        return "@sentinel"
    if kind in ("ConfirmationResponse",):
        return "operator"
    return None


def normalize(data: dict[str, Any], *, default_session_id: str | None = None) -> dict[str, Any] | None:
    """Turn a raw bus event dict into a flat row dict for ``Store.insert_event``.

    Returns ``None`` for events we deliberately drop (audio/heartbeat). Pure +
    side-effect-free so tests can call it directly.

    ``default_session_id`` attributes the event to a session when the bus event
    itself carries no session tag (the common case today — see README MVP
    attribution note). ``Hello.sessionId`` overrides it when present.
    """
    kind = data.get("kind") or "Unknown"
    if kind in _DROP_KINDS:
        return None

    session_id = data.get("sessionId") or default_session_id

    ts_ms = ns_to_ms(data.get("ts")) or now_ms()
    return {
        "ts_ms": ts_ms,
        "received_ms": now_ms(),
        "kind": kind,
        "session_id": session_id,
        "channel_id": data.get("channelId"),
        "author_id": _author_of(kind, data),
        "call_id": data.get("callId"),
        "summary": _summary_for(kind, data),
        "dedup_key": _dedup_key_for(kind, data),
        "raw_json": json.dumps(data, separators=(",", ":")),
    }


def persist_event(
    store: Store, data: dict[str, Any], *, default_session_id: str | None = None
) -> int | None:
    """Normalize + persist a single event. Returns the row id, or None if dropped.

    This is the seam the tests drive: feed synthetic dicts, assert they land in
    SQLite via ``store.recent_events``.
    """
    row = normalize(data, default_session_id=default_session_id)
    if row is None:
        return None
    return store.insert_event(row)


# ── live WS client ──────────────────────────────────────────────────────────

def _connect_url(settings: Settings) -> str:
    q = urlencode({"sessionId": settings.observer_session_id, "client": settings.observer_client})
    sep = "&" if "?" in settings.bus_url else "?"
    return f"{settings.bus_url}{sep}{q}"


async def ingest_loop(store: Store, settings: Settings, *, stop: asyncio.Event | None = None) -> None:
    """Connect to /v2/chat and persist every event, with reconnect backoff.

    Imports ``websockets`` lazily so the persist path (and its tests) carry no
    socket dependency. On any disconnect we reconnect with jittered exponential
    backoff. The orchestrator replays its last-200 ChatMessages + pending
    ConfirmationRequests on reconnect; the ``dedup_key`` UNIQUE index in the
    store (see ``_dedup_key_for``) makes those re-deliveries idempotent so a
    flapping connection never inflates counts/timelines.
    """
    import websockets  # lazy: only the live path needs it

    url = _connect_url(settings)
    backoff = settings.reconnect_min_s
    log.info("ingest: tapping %s", url)

    while stop is None or not stop.is_set():
        try:
            async with websockets.connect(url, max_size=8 * 1024 * 1024) as ws:
                log.info("ingest: connected")
                backoff = settings.reconnect_min_s  # reset on success
                async for raw in ws:
                    if isinstance(raw, (bytes, bytearray)):
                        continue  # binary frames are /v2/live media, not chat
                    try:
                        data = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    try:
                        persist_event(store, data, default_session_id=settings.observer_session_id)
                    except Exception:  # noqa: BLE001 — never let one bad event kill ingest
                        log.exception("ingest: failed to persist event kind=%s", data.get("kind"))
                    if stop is not None and stop.is_set():
                        break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("ingest: connection error (%s); reconnecting in %.1fs", exc, backoff)
            try:
                await asyncio.wait_for(
                    stop.wait() if stop else asyncio.sleep(backoff), timeout=backoff
                )
            except asyncio.TimeoutError:
                pass
            backoff = min(settings.reconnect_max_s, backoff * 2) + random.uniform(0, 0.5)

    log.info("ingest: stopped")

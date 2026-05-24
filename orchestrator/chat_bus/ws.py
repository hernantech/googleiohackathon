"""FastAPI WebSocket adapter for the chat bus (HANDOFF §2.A, spec 04 §1).

`ChatBus` is transport-agnostic: it writes events to anything satisfying the
`Transport` protocol via a synchronous `send(event)`. A FastAPI WebSocket send
is a coroutine, so `WebSocketTransport.send` enqueues onto an asyncio.Queue that
the `writer()` task drains — decoupling the sync bus from the async socket
without blocking the event loop.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket

_SENTINEL = object()


def event_to_json(event: object, *, extra: dict[str, Any] | None = None) -> str:
    """Serialize a bus event. All wire events + chat-bus envelopes are pydantic
    models (have model_dump_json); fall back to json.dumps for anything else.

    ``extra`` stamps additive fields (e.g. the originating ``sessionId``) onto the
    serialized frame without mutating the event model — forward-compatible per
    WP-3 (clients ignore unknown fields; the observer reads them). Keys already
    present on the event are NOT overwritten (e.g. ``Hello.sessionId`` wins), and
    ``None`` values are skipped so an untagged publish stays byte-for-byte the
    same as before. See observer/ATTRIBUTION.md §1."""
    dump = getattr(event, "model_dump_json", None)
    base = dump() if callable(dump) else json.dumps(event)
    if not extra:
        return base
    stamped = {k: v for k, v in extra.items() if v is not None}
    if not stamped:
        return base
    try:
        obj = json.loads(base)
    except (json.JSONDecodeError, TypeError):
        return base
    if not isinstance(obj, dict):
        return base
    # Don't clobber a field the event already carries (e.g. Hello.sessionId).
    for k, v in stamped.items():
        obj.setdefault(k, v)
    return json.dumps(obj)


class WebSocketTransport:
    """Satisfies chat_bus.bus.Transport. One per connected WebSocket."""

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._closed = False

    def send(self, event: object, *, extra: dict[str, Any] | None = None) -> None:
        """Synchronous, non-blocking — called by ChatBus.flush(). Drops once closed.

        ``extra`` (optional, defaulted) carries additive serialization fields —
        e.g. ``{"sessionId": origin}`` — stamped on the outbound frame by the
        writer. Defaulted so the bare ``send(event)`` Transport contract is
        unchanged (observer/ATTRIBUTION.md §1)."""
        if not self._closed:
            self._queue.put_nowait((event, extra))

    async def writer(self) -> None:
        """Drain the queue to the socket until closed. Run as a background task
        for the lifetime of the connection."""
        while True:
            item = await self._queue.get()
            if item is _SENTINEL:
                return
            event, extra = item
            await self._ws.send_text(event_to_json(event, extra=extra))

    def close(self) -> None:
        self._closed = True
        # wake the writer so it can exit
        self._queue.put_nowait(_SENTINEL)

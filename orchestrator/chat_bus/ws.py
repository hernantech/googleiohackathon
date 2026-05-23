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


def event_to_json(event: object) -> str:
    """Serialize a bus event. All wire events + chat-bus envelopes are pydantic
    models (have model_dump_json); fall back to json.dumps for anything else."""
    dump = getattr(event, "model_dump_json", None)
    if callable(dump):
        return dump()
    return json.dumps(event)


class WebSocketTransport:
    """Satisfies chat_bus.bus.Transport. One per connected WebSocket."""

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._closed = False

    def send(self, event: object) -> None:
        """Synchronous, non-blocking — called by ChatBus.flush(). Drops once closed."""
        if not self._closed:
            self._queue.put_nowait(event)

    async def writer(self) -> None:
        """Drain the queue to the socket until closed. Run as a background task
        for the lifetime of the connection."""
        while True:
            event = await self._queue.get()
            if event is _SENTINEL:
                return
            await self._ws.send_text(event_to_json(event))

    def close(self) -> None:
        self._closed = True
        # wake the writer so it can exit
        self._queue.put_nowait(_SENTINEL)

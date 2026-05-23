"""LivePassthrough — forward client H.264 + audio to Gemini Live untouched.

Implements the always-on path (00 §4.1, 08 §3.5a): the bytes that reach Live are
byte-identical to what the client sent. There is deliberately NO decode/encode/
transcode method on this class — the device produces the codec, the server only
relays. The Live session is injected as a sink so this is testable with no
network and so the real google-genai session can be swapped in unchanged.
"""

from __future__ import annotations

from typing import Callable

#: A sink that accepts one media chunk and ships it to the Gemini Live session.
LiveSink = Callable[[bytes], None]


class LivePassthrough:
    """One persistent media path: client → (this) → Gemini Live. Pass-through."""

    def __init__(self, live_sink: LiveSink):
        self._sink = live_sink
        #: Exactly one persistent media socket exists for the session (08 §3.5a).
        self.media_sockets = 1
        self.bytes_forwarded = 0

    def forward(self, chunk: bytes) -> None:
        """Relay a media chunk verbatim. No codec is instantiated."""
        self._sink(chunk)
        self.bytes_forwarded += len(chunk)

    def reconnect(self, live_sink: LiveSink) -> None:
        """Re-establish the single media path after a drop — one lifecycle, no
        second socket (08 §3.5a)."""
        self._sink = live_sink
        # media_sockets stays 1: we replace the path, we don't add one.

    def close(self) -> None:
        self.media_sockets = 0

"""Gemini Live bridge (P4 always-on path) — 00 §4.1.

The bridge forwards the device's H.264 + audio to the Live session **without
decoding or re-encoding** (the device emits the codec; the server never
transcodes). This is the only persistent media socket.
"""

from orchestrator.live.bridge import LivePassthrough  # noqa: F401

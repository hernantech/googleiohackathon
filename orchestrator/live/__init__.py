"""Gemini Live bridge (P4 always-on path) — 00 §4.1.

The bridge forwards the device's media to the Live session **without decoding or
re-encoding** (the orchestrator never transcodes). For the Live path the media
contract is **PCM audio (16 kHz mono) + JPEG frames** — Gemini Live does NOT
accept H.264 — so each chunk is *labelled* (a :class:`MediaKind`) and routed to
the correct realtime-input slot; the bytes themselves are relayed verbatim. This
is the only persistent media socket.

Phase 3 adds ``LiveDuplexBridge``: the OUT direction streams TTS audio back to
the client and routes final transcripts + tool-calls into the graph. The real
google-genai session lives in ``orchestrator.live.session`` (gated behind the
optional ``[live]`` extra + ``GEMINI_API_KEY``); the bridge takes the session as
an injected dependency so it is testable with no network.
"""

from orchestrator.live.bridge import (  # noqa: F401
    AudioOut,
    LiveDuplexBridge,
    LiveEvent,
    LivePassthrough,
    LiveSession,
    LiveSink,
    MediaKind,
    OnToolCall,
    OnTranscript,
)

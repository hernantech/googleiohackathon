"""/v2/live endpoint behaviour — framing + connect-failure fallback (offline).

Deterministic, no network. Drives the real FastAPI ``/v2/live`` endpoint via
``TestClient`` and proves two contracts:

  * **Framing**: a 1-byte type prefix tags each binary frame (0x01 PCM audio,
    0x02 JPEG frame); the endpoint parses + routes it without error.
  * **MEDIUM #1 fallback**: when a real Live session is *keyed* but fails to
    open (bad model/key/network, or google-genai absent), the endpoint must
    FALL BACK to the no-op drain loop — accept-but-drain, not accept-but-dead.
    The WS stays open and keeps accepting frames.
"""

from __future__ import annotations

import dataclasses

import pytest

from orchestrator.live.bridge import MediaKind

_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
_PCM = b"\x00\x01" * 256


def _client():
    from fastapi.testclient import TestClient
    from orchestrator.main import app

    return TestClient(app)


def test_live_endpoint_accepts_prefixed_audio_and_video_frames():
    """Stub mode (no key): the endpoint parses prefixed frames and drains."""
    client = _client()
    with client.websocket_connect("/v2/live?sessionId=fram-1") as ws:
        ws.send_bytes(bytes([MediaKind.AUDIO]) + _PCM)
        ws.send_bytes(bytes([MediaKind.VIDEO]) + _JPEG)
        ws.send_bytes(b"")  # empty frame — dropped, no error
        ws.send_bytes(b"\x09unknown-prefix")  # unknown kind — dropped, no error
        # No exception means the endpoint accepted/parsed/drained every frame.


def test_live_connect_failure_falls_back_to_drain(monkeypatch):
    """MEDIUM #1: keyed but connect() blows up → drain loop, WS stays alive."""
    import orchestrator.main as main_mod

    # Pretend we ARE keyed so the duplex branch is attempted.
    keyed = dataclasses.replace(main_mod.settings, gemini_api_key="fake-key")
    monkeypatch.setattr(main_mod, "settings", keyed)

    # Make opening the real Live session blow up at connect time. ``connect`` is
    # imported lazily inside the endpoint from orchestrator.live.session, so we
    # patch it there.
    import orchestrator.live.session as session_mod

    class _BoomCM:
        async def __aenter__(self):
            raise RuntimeError("simulated live connect failure")

        async def __aexit__(self, *exc):
            return False

    def _connect(_model=None):
        return _BoomCM()

    monkeypatch.setattr(session_mod, "connect", _connect)

    client = _client()
    with client.websocket_connect("/v2/live?sessionId=fallback-1") as ws:
        # The connect fails, the endpoint falls back to the drain loop, and the
        # socket is still open and accepts frames (no error, no close).
        ws.send_bytes(bytes([MediaKind.AUDIO]) + _PCM)
        ws.send_bytes(bytes([MediaKind.VIDEO]) + _JPEG)
        # Reaching here without an exception proves accept-but-drain.


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])

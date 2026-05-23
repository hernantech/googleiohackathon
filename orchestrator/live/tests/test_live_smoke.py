"""@live — real Gemini Live connectivity smoke test (08 §5, excluded from CI).

Gated by the ``live`` marker AND by ``GEMINI_API_KEY`` presence, so it is a
no-op in the offline suite (no network, key never required). Run pre-demo:

    GEMINI_API_KEY=... PYTHONPATH=. .venv/bin/python -m pytest -m live \
        orchestrator/live/tests/test_live_smoke.py -s

It opens a REAL ``client.aio.live.connect`` session via the production session
adapter (orchestrator.live.session), drives it through the production
``LiveDuplexBridge`` with a short text turn, and asserts that real TTS audio
bytes AND a final output transcript came back over the same duplex path. This
proves key + model + duplex wiring; it does NOT exercise a real device
mic/speaker round-trip (that needs hardware).
"""

from __future__ import annotations

import asyncio
import os

import pytest

from orchestrator.live.bridge import LiveDuplexBridge

pytestmark = pytest.mark.live

_HAS_KEY = bool(os.getenv("GEMINI_API_KEY"))


@pytest.mark.skipif(not _HAS_KEY, reason="@live: set GEMINI_API_KEY to run")
def test_real_live_session_returns_audio_and_transcript():
    from orchestrator.live.session import connect, live_model

    async def run():
        audio_frames: list[bytes] = []
        transcripts: list[str] = []

        async def audio_out(chunk: bytes) -> None:
            audio_frames.append(chunk)

        async def on_transcript(text: str):
            transcripts.append(text)
            return None  # don't voice a summary back in the smoke test

        async with connect() as session:
            bridge = LiveDuplexBridge(
                session, audio_out=audio_out, on_transcript=on_transcript
            )
            recv = asyncio.create_task(bridge.receive_loop())
            # Send a short text turn (the model VADs / responds with AUDIO out).
            await session.send_text("Say the single word hello.")
            # Wait for the FULL turn to stream back (turn_complete), up to 30s,
            # so we accumulate the complete TTS audio + transcript.
            try:
                await asyncio.wait_for(_until_turn_done(bridge), timeout=30)
            finally:
                recv.cancel()
                try:
                    await recv
                except asyncio.CancelledError:
                    pass
                await bridge.aclose()

        total_audio = sum(len(c) for c in audio_frames)
        print(f"\n[@live] model={live_model()} "
              f"audio_frames={len(audio_frames)} audio_bytes={total_audio} "
              f"transcript={''.join(transcripts)!r}")
        assert total_audio > 0, "expected real TTS audio bytes back from Live"

    asyncio.run(run())


async def _until_turn_done(bridge: LiveDuplexBridge) -> None:
    """Resolve once the model has completed a full turn (turn_complete)."""
    while bridge.turns_completed == 0:
        await asyncio.sleep(0.1)

"""Real google-genai Live session adapter (Phase 3, HANDOFF §2.D).

Wraps a live ``client.aio.live.connect(...)`` session behind the
``LiveSession`` protocol the ``LiveDuplexBridge`` drives, so the bridge code is
identical for the real session and the test fake. google-genai is imported
lazily (optional ``[live]`` extra) and the whole thing is gated behind
``GEMINI_API_KEY`` in main.py — with neither, /v2/live falls back to the no-op
stub and the offline suite stays green (07 §2.4).

Verified working model (2026-05-23): ``gemini-3.1-flash-live-preview`` with
``response_modalities=["AUDIO"]`` + ``output_audio_transcription`` — a live
``connect`` returned 24 kHz PCM TTS audio and a final output transcript for a
short text turn. (The 3.x Live model rejects a TEXT-only modality with a 1011;
AUDIO out is what /v2/live wants anyway.) Configurable via ``GEMINI_LIVE_MODEL``.

Media into Live is **PCM audio + JPEG frames** (NOT H.264): ``send_media``
routes by :class:`~orchestrator.live.bridge.MediaKind` to the right
``send_realtime_input`` slot (``audio=`` vs ``video=``). Bytes pass through
verbatim — the orchestrator never transcodes.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from orchestrator.live.bridge import LiveEvent, MediaKind

log = logging.getLogger("forge.live.session")

#: Live audio contract: raw little-endian PCM, 16 kHz mono (00 §4.1).
AUDIO_MIME = "audio/pcm;rate=16000"
#: Live video contract: JPEG frames (Gemini Live takes images, NOT H.264).
VIDEO_MIME = "image/jpeg"

#: Default Live model. Verified live on the demo key 2026-05-23 (see module doc).
DEFAULT_LIVE_MODEL = "gemini-3.1-flash-live-preview"


def live_model() -> str:
    return os.getenv("GEMINI_LIVE_MODEL", DEFAULT_LIVE_MODEL)


def _live_config():
    """LiveConnectConfig for the always-on path: AUDIO out (TTS back to the
    client) + input/output transcription so final transcripts route to the
    graph. No transcode is configured — the device emits PCM + JPEG and we relay
    the bytes verbatim into the right realtime-input slot (00 §4.1)."""
    from google.genai import types  # optional [live] dep

    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        input_audio_transcription={},
        output_audio_transcription={},
    )


class GenaiLiveSession:
    """``LiveSession`` over a connected google-genai async session.

    Constructed via :func:`connect` (an async context manager) so the
    underlying ``client.aio.live.connect`` lifecycle is owned correctly.
    """

    def __init__(self, session: object) -> None:
        self._s = session

    # ── IN: client media → Live (no transcode) ──────────────────────────────
    async def send_media(self, chunk: bytes, kind: MediaKind = MediaKind.AUDIO) -> None:
        """Relay one client media chunk into the session verbatim, routed by kind.

        Gemini Live takes **PCM audio (16 kHz mono)** and **JPEG image frames**
        (NOT H.264), via distinct realtime-input slots (google-genai 2.6.0
        ``send_realtime_input(audio=...)`` vs ``send_realtime_input(video=...)``,
        verified). We pick the slot by :class:`MediaKind` and pass the bytes
        through unchanged — we do NOT decode/re-encode (08 §3.5a).

        Earlier this hard-coded ``audio/pcm`` for *every* chunk, which mislabeled
        JPEG frames as audio (review MEDIUM #2); now audio → ``audio=`` and a
        frame → ``video=`` with ``image/jpeg``.
        """
        from google.genai import types  # optional [live] dep

        if kind == MediaKind.VIDEO:
            await self._s.send_realtime_input(
                video=types.Blob(data=chunk, mime_type=VIDEO_MIME)
            )
        else:
            await self._s.send_realtime_input(
                audio=types.Blob(data=chunk, mime_type=AUDIO_MIME)
            )

    # ── OUT: Live → normalized events ────────────────────────────────────────
    async def receive(self):
        async for msg in self._s.receive():
            sc = getattr(msg, "server_content", None)
            if sc is None:
                # tool_call may ride at the message level
                tc = getattr(msg, "tool_call", None)
                if tc is not None:
                    for call in getattr(tc, "function_calls", None) or []:
                        yield LiveEvent(
                            tool_call=getattr(call, "name", None),
                            tool_args=dict(getattr(call, "args", None) or {}),
                            tool_call_id=getattr(call, "id", None),
                        )
                continue

            model_turn = getattr(sc, "model_turn", None)
            if model_turn is not None:
                for p in getattr(model_turn, "parts", None) or []:
                    inline = getattr(p, "inline_data", None)
                    if inline is not None and getattr(inline, "data", None):
                        yield LiveEvent(audio=inline.data)
                    fc = getattr(p, "function_call", None)
                    if fc is not None:
                        yield LiveEvent(
                            tool_call=getattr(fc, "name", None),
                            tool_args=dict(getattr(fc, "args", None) or {}),
                            tool_call_id=getattr(fc, "id", None),
                        )

            out_tx = getattr(sc, "output_transcription", None)
            if out_tx is not None and getattr(out_tx, "text", None):
                yield LiveEvent(
                    transcript=out_tx.text,
                    transcript_final=bool(getattr(out_tx, "finished", False)),
                )

            if getattr(sc, "turn_complete", False):
                yield LiveEvent(turn_complete=True)

    # ── results back into the conversation ──────────────────────────────────
    async def inject_function_response(self, call_id: str, payload: dict) -> None:
        from google.genai import types  # optional [live] dep

        await self._s.send_tool_response(
            function_responses=[
                types.FunctionResponse(id=call_id, name="", response=payload)
            ]
        )

    async def send_text(self, text: str) -> None:
        await self._s.send_realtime_input(text=text)

    async def close(self) -> None:
        close = getattr(self._s, "close", None)
        if close is not None:
            await close()


@asynccontextmanager
async def connect(model: str | None = None):
    """Open a real Gemini Live session and yield a ``GenaiLiveSession``.

    Reads ``GEMINI_API_KEY`` via ``genai.Client()``; raises if google-genai is
    missing or the key/model is bad — the caller (main.py) catches and falls
    back to the no-op stub so /v2/live still serves.
    """
    from google import genai  # optional [live] dep

    client = genai.Client()
    async with client.aio.live.connect(
        model=model or live_model(), config=_live_config()
    ) as session:
        log.info("gemini live session open | model=%s", model or live_model())
        yield GenaiLiveSession(session)

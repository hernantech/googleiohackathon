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


#: System instruction for the Live agent: chat normally, call a tool ONLY when
#: warranted (the user does NOT want every utterance routed through the guild).
LIVE_SYSTEM_INSTRUCTION = (
    "You are Forge's live voice assistant at an electronics workbench, advising "
    "a HUMAN operator. Forge actuates nothing — you only talk and recommend. "
    "Chat naturally and answer simple questions yourself. DELIBERATELY call a "
    "tool only when it is warranted:\n"
    "  • call summon_guild(topic) when the operator's question needs expert "
    "deliberation from the specialist engineering guild (power, signal, "
    "firmware, layout, safety, …) — e.g. diagnosing a bring-up failure or a "
    "design trade-off;\n"
    "  • call parse_schematic(source_uri, hint) when the operator references or "
    "points at a schematic image/PDF and you need its components/nets;\n"
    "  • call lookup_schematic(query) to answer follow-ups about a schematic you "
    "already parsed this session (e.g. 'what's on net 3V3?').\n"
    "Never invent a voltage/current setpoint; schematic data is advisory only."
)


def _live_guild_tool_decl() -> dict:
    """The summon_guild function declaration offered to the Live model. The
    existing main.py on_tool_call runs the guild and injects the merged headline,
    so this mostly needs the DECLARATION."""
    return {
        "name": "summon_guild",
        "description": (
            "Consult the SME guild of specialist engineers to deliberate on the "
            "operator's question and return a merged recommendation. Use when "
            "expert, multi-discipline deliberation is warranted (a bring-up "
            "failure, a design trade-off, a safety-relevant step), NOT for small "
            "talk or simple factual answers you can give directly."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "the operator's question / topic for the guild",
                },
            },
            "required": ["topic"],
        },
    }


def _live_tool():
    """Build the combined Live `Tool` declaring summon_guild + the schematic
    functions, or None if google-genai is unavailable (graceful, 01 §7)."""
    try:
        from google.genai import types  # optional [live] dep

        from orchestrator.live.schematic_tools import _LIVE_FUNCTION_DECLS

        decls = [_live_guild_tool_decl(), *_LIVE_FUNCTION_DECLS]
        return types.Tool(function_declarations=decls)
    except Exception as e:  # noqa: BLE001 — Live just won't offer tools
        log.warning("live tool declarations unavailable (%s); Live runs without them", e)
        return None


def _live_config():
    """LiveConnectConfig for the always-on path: AUDIO out (TTS back to the
    client) + input/output transcription so final transcripts route to the
    graph. No transcode is configured — the device emits PCM + JPEG and we relay
    the bytes verbatim into the right realtime-input slot (00 §4.1).

    Also declares function tools so the Live model can chat normally and
    DELIBERATELY EMIT a function-call when warranted (voice → Gemini decides →
    tool call): `summon_guild(topic)` for expert deliberation and
    `parse_schematic(source_uri, hint)` / `lookup_schematic(query)` for a
    schematic image. main.py's on_tool_call dispatches by name — summon_guild →
    the guild (engine.run), the schematic tools → the SAME shared dispatch seam
    the SME tool-loop uses. A `system_instruction` tells the agent WHEN to call
    each. Declarations + system_instruction are additive + best-effort: if tools
    can't be built we connect without them (graceful, 01 §7)."""
    from google.genai import types  # optional [live] dep

    kwargs: dict = dict(
        response_modalities=["AUDIO"],
        input_audio_transcription={},
        output_audio_transcription={},
        system_instruction=LIVE_SYSTEM_INSTRUCTION,
    )
    tool = _live_tool()
    if tool is not None:
        kwargs["tools"] = [tool]
    return types.LiveConnectConfig(**kwargs)


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

"""Live bridges — always-on PCM audio + JPEG video ↔ Gemini Live (00 §4.1).

Two layers, both dependency-injected so they are testable with no network:

- ``LivePassthrough`` — the one-direction relay primitive (kept from P4): client
  bytes → injected ``LiveSink`` → Gemini Live, **byte-for-byte, no transcode**
  (08 §3.5a). Exactly one persistent media socket; single-lifecycle reconnect.

Media contract for the Live path (supersedes the spec's H.264 passthrough for
THIS path): Gemini Live does NOT accept H.264. It takes **PCM audio (16 kHz
mono)** + **JPEG image frames**. So the device emits PCM + JPEG and the bridge
routes each chunk to Live by its declared :class:`MediaKind` — audio as a
realtime ``audio=Blob(mime audio/pcm;rate=16000)`` and a frame as a realtime
``video=Blob(mime image/jpeg)`` (google-genai 2.6.0
``session.send_realtime_input``). Bytes are still relayed verbatim — the
orchestrator never decodes/re-encodes; it only *labels* the chunk so Live gets
the right realtime-input slot. The labelling itself is carried over /v2/live by
a 1-byte type prefix on each binary frame (see ``main.py`` + the device-sim
client) — ``0x01`` = PCM audio, ``0x02`` = JPEG frame.

- ``LiveDuplexBridge`` — the full duplex session wiring (Phase 3, HANDOFF §2.D,
  ARCHITECTURE §2/§4). It owns an injected ``LiveSession`` (the real
  google-genai ``client.aio.live.connect`` session in production, a fake in
  tests) and wires both directions:

    IN  : client media chunks → ``session.send_media(chunk)`` (reuses the
          ``LivePassthrough`` relay so the no-transcode contract is preserved).
    OUT : a receive loop drains the session and
            • ships TTS **audio** back to the client over the same /v2/live WS
              (binary frames via the injected ``audio_out`` sink), and
            • routes **final transcripts + tool/function-calls** into the graph
              (``on_transcript`` / ``on_tool_call`` callbacks — main.py wires
              these to ``GraphEngine.run`` + ``_drain_to_bus``), then surfaces
              the graph result back into the session (``inject_function_response``
              / a spoken summary) per ARCHITECTURE §2/§4.

The graph is *not* imported here — the bridge is pure transport + routing and
takes the graph hooks as callables, matching the seam pattern used everywhere
else in the orchestrator (orchestrator/seams.py).
"""

from __future__ import annotations

import asyncio
import enum
import logging
from typing import Awaitable, Callable, Protocol

log = logging.getLogger("forge.live.bridge")


class MediaKind(enum.IntEnum):
    """How one client media chunk should be routed into Gemini Live.

    The integer values double as the 1-byte type prefix used on the /v2/live
    WebSocket framing (see ``main.py`` + ``clients/live_device_sim.py``):

      * ``AUDIO`` (``0x01``) — PCM, 16 kHz mono → Live realtime ``audio=`` input.
      * ``VIDEO`` (``0x02``) — a JPEG frame → Live realtime ``video=`` input
        (``mime_type='image/jpeg'``).

    Gemini Live takes PCM + JPEG (NOT H.264), so the bridge must route by kind,
    not send everything as audio.
    """

    AUDIO = 0x01
    VIDEO = 0x02


#: A sink that accepts one media chunk + its kind and ships it to the Gemini
#: Live session (audio → realtime audio input, video → realtime image input).
LiveSink = Callable[[bytes, MediaKind], None]

#: Ship one TTS audio chunk back to the connected client (binary WS frame).
AudioOut = Callable[[bytes], Awaitable[None]]

#: Route a final transcript from Live into the graph; returns a spoken summary
#: (or None) to voice back through the session.
OnTranscript = Callable[[str], Awaitable[str | None]]

#: Route a Live tool/function-call into the graph. Returns the structured
#: function-response payload to inject back into the session (or None).
OnToolCall = Callable[[str, dict, str], Awaitable[dict | None]]


class LivePassthrough:
    """One persistent media path: client → (this) → Gemini Live. Pass-through.

    A chunk carries its :class:`MediaKind` so the sink can route audio vs. a
    JPEG frame to the correct Live realtime-input slot. The bytes themselves are
    never touched — no codec is instantiated (08 §3.5a)."""

    def __init__(self, live_sink: LiveSink):
        self._sink = live_sink
        #: Exactly one persistent media socket exists for the session (08 §3.5a).
        self.media_sockets = 1
        self.bytes_forwarded = 0

    def forward(self, chunk: bytes, kind: MediaKind = MediaKind.AUDIO) -> None:
        """Relay a media chunk verbatim. No codec is instantiated."""
        self._sink(chunk, kind)
        self.bytes_forwarded += len(chunk)

    def reconnect(self, live_sink: LiveSink) -> None:
        """Re-establish the single media path after a drop — one lifecycle, no
        second socket (08 §3.5a)."""
        self._sink = live_sink
        # media_sockets stays 1: we replace the path, we don't add one.

    def close(self) -> None:
        self.media_sockets = 0


class LiveSession(Protocol):
    """The subset of a Gemini Live session the duplex bridge drives.

    Implemented for real by ``orchestrator.live.session.GenaiLiveSession`` (wraps
    ``client.aio.live.connect``); a ``FakeLiveSession`` in tests satisfies the
    same shape with no network. Mirrors google-genai's async session API so the
    real adapter is a thin pass-through.
    """

    async def send_media(self, chunk: bytes, kind: MediaKind = MediaKind.AUDIO) -> None:
        """Relay one client media chunk into the session (no transcode).

        ``kind`` selects the Live realtime-input slot: ``AUDIO`` → PCM audio,
        ``VIDEO`` → a JPEG image frame."""
        ...

    def receive(self):  # -> AsyncIterator[LiveEvent]
        """Async-iterate normalized inbound events from the session."""
        ...

    async def inject_function_response(self, call_id: str, payload: dict) -> None:
        """Push a deferred tool result back into the open conversation."""
        ...

    async def send_text(self, text: str) -> None:
        """Inject a spoken/summary turn into the session (Live voices it)."""
        ...

    async def close(self) -> None:
        ...


class LiveEvent:
    """Normalized inbound event from a Live session, transport-agnostic.

    Exactly one of the payload fields is set per event. The real adapter
    (session.py) maps google-genai ``LiveServerMessage`` → this; the fake emits
    these directly. Keeping a tiny neutral type here means the duplex routing
    logic is identical for the real session and the test double.
    """

    __slots__ = ("audio", "transcript", "transcript_final", "tool_call",
                 "tool_args", "tool_call_id", "turn_complete")

    def __init__(
        self,
        *,
        audio: bytes | None = None,
        transcript: str | None = None,
        transcript_final: bool = False,
        tool_call: str | None = None,
        tool_args: dict | None = None,
        tool_call_id: str | None = None,
        turn_complete: bool = False,
    ) -> None:
        self.audio = audio
        self.transcript = transcript
        self.transcript_final = transcript_final
        self.tool_call = tool_call
        self.tool_args = tool_args or {}
        self.tool_call_id = tool_call_id
        self.turn_complete = turn_complete


class LiveDuplexBridge:
    """Full-duplex /v2/live ↔ Gemini Live session wiring (Phase 3).

    Args:
        session: the injected Live session (real google-genai or a fake).
        audio_out: ships one TTS audio chunk back to the client (binary frame).
        on_transcript: routes a *final* transcript into the graph; the returned
            string (if any) is voiced back through the session.
        on_tool_call: routes a Live tool/function-call into the graph; the
            returned dict (if any) is injected back as a function-response.

    The bridge holds exactly one ``LivePassthrough`` so the IN direction keeps
    the byte-for-byte no-transcode contract (08 §3.5a).
    """

    def __init__(
        self,
        session: LiveSession,
        *,
        audio_out: AudioOut,
        on_transcript: OnTranscript | None = None,
        on_tool_call: OnToolCall | None = None,
    ) -> None:
        self._session = session
        self._audio_out = audio_out
        self._on_transcript = on_transcript
        self._on_tool_call = on_tool_call
        # IN direction reuses the relay primitive: the sink hands bytes to the
        # session. Wrapped sync→async via a fire-and-forget task so the relay
        # API (sync forward) is unchanged.
        self._passthrough = LivePassthrough(live_sink=self._enqueue_media)
        self._send_tasks: set[asyncio.Task] = set()
        # accumulates partial output-transcription deltas until turn_complete
        self._pending_out_transcript: list[str] = []
        self.audio_chunks_out = 0
        self.audio_chunks_in = 0
        self.video_frames_in = 0
        self.transcripts_routed = 0
        self.tool_calls_routed = 0
        self.turns_completed = 0

    # ── IN: client → session ────────────────────────────────────────────────
    def _enqueue_media(self, chunk: bytes, kind: MediaKind) -> None:
        """LiveSink: schedule the chunk onto the session (no transcode),
        carrying its kind so the session routes audio vs. JPEG correctly."""
        task = asyncio.ensure_future(self._session.send_media(chunk, kind))
        self._send_tasks.add(task)
        task.add_done_callback(self._send_tasks.discard)

    def forward_client_chunk(
        self, chunk: bytes, kind: MediaKind = MediaKind.AUDIO
    ) -> None:
        """Relay one client media chunk verbatim into the Live session.

        ``kind`` (parsed from the /v2/live 1-byte frame prefix) routes the chunk
        to Live as PCM audio (``AUDIO``) or a JPEG image frame (``VIDEO``)."""
        self._passthrough.forward(chunk, kind)
        if kind == MediaKind.AUDIO:
            self.audio_chunks_in += 1
        elif kind == MediaKind.VIDEO:
            self.video_frames_in += 1

    @property
    def bytes_forwarded(self) -> int:
        return self._passthrough.bytes_forwarded

    @property
    def media_sockets(self) -> int:
        return self._passthrough.media_sockets

    # ── OUT: session → client + graph ────────────────────────────────────────
    async def receive_loop(self) -> None:
        """Drain the Live session: stream audio back to the client, route final
        transcripts + tool-calls into the graph, surface results into Live."""
        async for ev in self._session.receive():
            if ev.audio:
                await self._audio_out(ev.audio)
                self.audio_chunks_out += 1
            if ev.transcript:
                self._pending_out_transcript.append(ev.transcript)
            if ev.tool_call and self._on_tool_call is not None:
                await self._handle_tool_call(ev)
            if (ev.transcript_final or ev.turn_complete) and self._pending_out_transcript:
                await self._handle_final_transcript()
            if ev.turn_complete:
                self.turns_completed += 1

    async def _handle_final_transcript(self) -> None:
        text = "".join(self._pending_out_transcript).strip()
        self._pending_out_transcript.clear()
        if not text or self._on_transcript is None:
            return
        self.transcripts_routed += 1
        try:
            summary = await self._on_transcript(text)
        except Exception as e:  # noqa: BLE001 — never fail-stop the live loop
            log.warning("on_transcript failed (%s); continuing", e)
            return
        if summary:
            try:
                await self._session.send_text(summary)
            except Exception as e:  # noqa: BLE001
                log.warning("send_text failed (%s); continuing", e)

    async def _handle_tool_call(self, ev: LiveEvent) -> None:
        self.tool_calls_routed += 1
        call_id = ev.tool_call_id or ev.tool_call or ""
        try:
            payload = await self._on_tool_call(ev.tool_call, ev.tool_args, call_id)
        except Exception as e:  # noqa: BLE001 — never fail-stop the live loop
            log.warning("on_tool_call failed (%s); continuing", e)
            return
        if payload is not None:
            try:
                await self._session.inject_function_response(call_id, payload)
            except Exception as e:  # noqa: BLE001
                log.warning("inject_function_response failed (%s); continuing", e)

    async def aclose(self) -> None:
        for t in list(self._send_tasks):
            t.cancel()
        self._passthrough.close()
        try:
            await self._session.close()
        except Exception as e:  # noqa: BLE001
            log.warning("session.close failed (%s); continuing", e)

"""Phase 3 — duplex /v2/live ↔ Gemini Live wiring (HANDOFF §2.D, ARCH §2/§4).

Deterministic, offline: a FAKE LiveSession is injected so there is NO network.
Async coroutines are driven with ``asyncio.run`` in plain sync tests so the
suite needs no extra pytest plugin (matches the zero-extra-dep convention).

Proves the three duplex contracts:

  IN   : client bytes reach the session, byte-for-byte, no transcode.
  OUT  : session audio reaches the client transport (binary frames).
  ROUTE: a final transcript / a tool-call triggers GraphEngine.run → events
         land on the chat bus, and the spoken summary / function-response is
         pushed back into the session.
"""

from __future__ import annotations

import asyncio

from orchestrator.chat_bus.bus import ChatBus, Session
from orchestrator.graph.engine import GraphEngine
from orchestrator.graph.state import ForgeState
from orchestrator.knowledge import KnowledgeAdapter
from orchestrator.live.bridge import LiveDuplexBridge, LiveEvent, MediaKind
from orchestrator.seams import build_graph_deps


# ── Fakes (no network) ───────────────────────────────────────────────────────
class FakeLiveSession:
    """Satisfies the LiveSession protocol with an in-memory scripted feed."""

    def __init__(self, scripted: list[LiveEvent]):
        self._scripted = scripted
        self.received_media: list[bytes] = []
        #: (kind, chunk) pairs so tests can assert routing of audio vs JPEG.
        self.received_by_kind: list[tuple[MediaKind, bytes]] = []
        self.injected_responses: list[tuple[str, dict]] = []
        self.sent_text: list[str] = []
        self.closed = False

    async def send_media(self, chunk: bytes, kind: MediaKind = MediaKind.AUDIO) -> None:
        self.received_media.append(chunk)
        self.received_by_kind.append((kind, chunk))

    async def receive(self):
        for ev in self._scripted:
            yield ev
            await asyncio.sleep(0)  # let the loop interleave

    async def inject_function_response(self, call_id: str, payload: dict) -> None:
        self.injected_responses.append((call_id, payload))

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)

    async def close(self) -> None:
        self.closed = True


class FakeClientTransport:
    """Captures binary frames the bridge ships back to the client WS."""

    def __init__(self):
        self.audio_frames: list[bytes] = []

    async def send_bytes(self, chunk: bytes) -> None:
        self.audio_frames.append(chunk)


# ── IN: client → session, byte-for-byte ──────────────────────────────────────
def test_client_bytes_reach_session_verbatim():
    async def run():
        session = FakeLiveSession(scripted=[])
        client = FakeClientTransport()
        bridge = LiveDuplexBridge(session, audio_out=client.send_bytes)

        chunks = [b"\x00\x00\x00\x01h264-nal", b"pcm-audio-bytes", b"\x00\x00\x00\x01more"]
        for c in chunks:
            bridge.forward_client_chunk(c)
        await asyncio.sleep(0)  # let the fire-and-forget send tasks run

        assert session.received_media == chunks  # byte-for-byte, no transcode
        assert bridge.bytes_forwarded == sum(len(c) for c in chunks)
        assert bridge.media_sockets == 1  # one persistent socket (08 §3.5a)

    asyncio.run(run())


# ── OUT: session audio → client transport ────────────────────────────────────
def test_session_audio_reaches_client_transport():
    async def run():
        audio = [b"tts-chunk-1", b"tts-chunk-2"]
        session = FakeLiveSession(
            scripted=[LiveEvent(audio=a) for a in audio] + [LiveEvent(turn_complete=True)]
        )
        client = FakeClientTransport()
        bridge = LiveDuplexBridge(session, audio_out=client.send_bytes)

        await bridge.receive_loop()

        assert client.audio_frames == audio
        assert bridge.audio_chunks_out == 2

    asyncio.run(run())


# ── ROUTE: final transcript → GraphEngine.run → bus + spoken summary ──────────
def test_final_transcript_drives_graph_and_bus():
    async def run():
        # Real (stub-seam) graph + a real ChatBus with a capturing subscriber.
        knowledge = KnowledgeAdapter()
        engine = GraphEngine(build_graph_deps(knowledge))
        state = ForgeState(sessionId="live-test")

        captured: list[object] = []

        class CapturingTransport:
            def send(self, event: object) -> None:
                captured.append(event)

        bus = ChatBus()
        bus.subscribe(Session("live-test", CapturingTransport()))
        captured.clear()  # drop the replay handshake

        async def on_transcript(transcript: str):
            result = await asyncio.to_thread(engine.run, state, transcript)
            events = list(state.outboundEvents)
            state.outboundEvents.clear()
            bus.publish_many(events)
            return state.liveSpeakerScript if result.status != "paused" else None

        # A final transcript that @-mentions a SME → forces the guild path
        # (stub_classify routes @-mentions), so the graph fans out and emits events.
        session = FakeLiveSession(scripted=[
            LiveEvent(transcript="ask @power about the J3 rail", transcript_final=True),
        ])
        client = FakeClientTransport()
        bridge = LiveDuplexBridge(
            session, audio_out=client.send_bytes, on_transcript=on_transcript
        )

        await bridge.receive_loop()

        # The transcript was routed exactly once and drove the graph.
        assert bridge.transcripts_routed == 1
        assert state.latestTranscriptFinal == "ask @power about the J3 rail"
        # Graph emitted events onto the bus (checkpoint markers, SME aggregate, etc).
        kinds = [getattr(e, "kind", type(e).__name__) for e in captured]
        assert "CheckpointMarker" in kinds, kinds
        assert any(k in ("ChatMessage", "Transcript") for k in kinds), kinds
        # A spoken summary (LiveSpeaker line) was voiced back into the session.
        assert session.sent_text, "expected a spoken summary back into the Live session"

    asyncio.run(run())


# ── ROUTE: tool/function-call → graph → function-response injected back ───────
def test_tool_call_routed_and_function_response_injected():
    async def run():
        knowledge = KnowledgeAdapter()
        engine = GraphEngine(build_graph_deps(knowledge))
        state = ForgeState(sessionId="live-tc")

        async def on_tool_call(name: str, args: dict, call_id: str):
            topic = str(args.get("topic") or name)
            await asyncio.to_thread(engine.run, state, topic)
            merged = state.mergedOpinion
            return {"tool": name, "headline": merged.headline if merged else ""}

        session = FakeLiveSession(scripted=[
            LiveEvent(tool_call="summon_guild",
                      tool_args={"topic": "@power J3 rail"},
                      tool_call_id="call-123"),
        ])
        client = FakeClientTransport()
        bridge = LiveDuplexBridge(
            session, audio_out=client.send_bytes, on_tool_call=on_tool_call
        )

        await bridge.receive_loop()

        assert bridge.tool_calls_routed == 1
        assert len(session.injected_responses) == 1
        call_id, payload = session.injected_responses[0]
        assert call_id == "call-123"
        assert payload["tool"] == "summon_guild"
        assert "headline" in payload

    asyncio.run(run())


# ── IN routing: PCM → audio slot, JPEG → image slot (review MEDIUM #2) ────────
def test_audio_and_video_chunks_route_by_kind():
    async def run():
        session = FakeLiveSession(scripted=[])
        client = FakeClientTransport()
        bridge = LiveDuplexBridge(session, audio_out=client.send_bytes)

        pcm = b"pcm-16khz-mono-bytes"
        jpeg = b"\xff\xd8\xff\xe0jpeg-frame\xff\xd9"
        bridge.forward_client_chunk(pcm, MediaKind.AUDIO)
        bridge.forward_client_chunk(jpeg, MediaKind.VIDEO)
        bridge.forward_client_chunk(b"more-pcm", MediaKind.AUDIO)
        await asyncio.sleep(0)  # let the fire-and-forget send tasks run

        # Each chunk reached the session tagged with its kind, byte-for-byte.
        assert session.received_by_kind == [
            (MediaKind.AUDIO, pcm),
            (MediaKind.VIDEO, jpeg),
            (MediaKind.AUDIO, b"more-pcm"),
        ]
        assert bridge.audio_chunks_in == 2
        assert bridge.video_frames_in == 1
        assert bridge.media_sockets == 1  # still one persistent socket

    asyncio.run(run())


# ── session adapter: kind selects the google-genai realtime-input slot ────────
def test_genai_session_send_media_selects_audio_vs_video_slot():
    """The real adapter maps AUDIO → send_realtime_input(audio=Blob(audio/pcm))
    and VIDEO → send_realtime_input(video=Blob(image/jpeg)). Uses a fake genai
    session (still no network) to capture the kwargs the adapter emits."""

    async def run():
        from orchestrator.live.session import (
            AUDIO_MIME,
            VIDEO_MIME,
            GenaiLiveSession,
        )

        class FakeGenaiSession:
            def __init__(self):
                self.calls: list[dict] = []

            async def send_realtime_input(self, **kwargs):
                self.calls.append(kwargs)

        fake = FakeGenaiSession()
        adapter = GenaiLiveSession(fake)

        await adapter.send_media(b"pcm-bytes", MediaKind.AUDIO)
        await adapter.send_media(b"\xff\xd8jpeg", MediaKind.VIDEO)

        assert len(fake.calls) == 2
        # call 0: audio slot only, correct mime + verbatim bytes
        assert set(fake.calls[0]) == {"audio"}
        assert fake.calls[0]["audio"].mime_type == AUDIO_MIME
        assert fake.calls[0]["audio"].data == b"pcm-bytes"
        # call 1: video slot only, image/jpeg + verbatim bytes (NOT audio)
        assert set(fake.calls[1]) == {"video"}
        assert fake.calls[1]["video"].mime_type == VIDEO_MIME
        assert fake.calls[1]["video"].data == b"\xff\xd8jpeg"

    asyncio.run(run())


# ── /v2/live framing: 1-byte prefix parse routes audio vs video ───────────────
def test_live_frame_prefix_parsing():
    from orchestrator.main import _parse_live_frame

    # 0x01 → audio, payload is everything after the prefix, byte-for-byte.
    payload, kind = _parse_live_frame(b"\x01" + b"pcm-payload")
    assert kind == MediaKind.AUDIO and payload == b"pcm-payload"

    # 0x02 → video (JPEG)
    payload, kind = _parse_live_frame(b"\x02" + b"\xff\xd8jpeg")
    assert kind == MediaKind.VIDEO and payload == b"\xff\xd8jpeg"

    # empty frame and unknown prefix are dropped (None), never mislabeled.
    assert _parse_live_frame(b"") is None
    assert _parse_live_frame(b"\x09garbage") is None
    # a prefix with no payload is a valid (empty) chunk, not None.
    payload, kind = _parse_live_frame(b"\x01")
    assert kind == MediaKind.AUDIO and payload == b""


# ── never fail-stop: a hook that raises does not kill the loop ────────────────
def test_hook_exception_does_not_kill_loop():
    async def run():
        async def boom(_transcript: str):
            raise RuntimeError("graph blew up")

        session = FakeLiveSession(scripted=[
            LiveEvent(transcript="x", transcript_final=True),
            LiveEvent(audio=b"still-flowing"),
        ])
        client = FakeClientTransport()
        bridge = LiveDuplexBridge(
            session, audio_out=client.send_bytes, on_transcript=boom
        )

        await bridge.receive_loop()  # must not raise

        # audio after the failing transcript still reached the client
        assert client.audio_frames == [b"still-flowing"]

    asyncio.run(run())

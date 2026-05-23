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
from orchestrator.live.bridge import LiveDuplexBridge, LiveEvent
from orchestrator.seams import build_graph_deps


# ── Fakes (no network) ───────────────────────────────────────────────────────
class FakeLiveSession:
    """Satisfies the LiveSession protocol with an in-memory scripted feed."""

    def __init__(self, scripted: list[LiveEvent]):
        self._scripted = scripted
        self.received_media: list[bytes] = []
        self.injected_responses: list[tuple[str, dict]] = []
        self.sent_text: list[str] = []
        self.closed = False

    async def send_media(self, chunk: bytes) -> None:
        self.received_media.append(chunk)

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

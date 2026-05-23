"""FastAPI serving surface (HANDOFF §2.A, specs 00/04/07).

Wraps the tested, transport-agnostic backbone in the v2 endpoints:

  GET  /healthz                          liveness + integration modes
  WSS  /v2/chat?sessionId=&client=       chat bus over a WebSocket (spec 04)
  WSS  /v2/live?sessionId=               PCM audio + JPEG frames → Gemini Live
  POST /v2/snapshot?sessionId=&note=     hi-res JPEG → analysis card (00 §4.2)

Boots clean with zero env vars (07 §2.4): the model seams are stubs
(orchestrator/seams.py) until Phase 3 wires real SDK calls. Auth is a dev
shared secret offered as a WebSocket subprotocol (00 §8); Firebase is TODO.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from dataclasses import dataclass

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from orchestrator.chat_bus.bus import ChatBus, Session
from orchestrator.chat_bus.envelopes import Pong
from orchestrator.chat_bus.ws import WebSocketTransport
from orchestrator.config import settings
from orchestrator.graph.engine import GraphEngine
from orchestrator.graph.state import ForgeState
from orchestrator.knowledge import KnowledgeAdapter
from orchestrator.proto.events import ConfirmationResponse, new_ulid
from orchestrator.seams import build_graph_deps, stub_snapshot_model_call
from orchestrator.snapshot.endpoint import handle_snapshot
from orchestrator.storage.frame_store import InMemoryFrameStore

logging.basicConfig(level=settings.log_level)
log = logging.getLogger("forge.orchestrator")

HEARTBEAT_S = 20

# ── Process-wide singletons (transport-agnostic core) ──────────────────────
bus = ChatBus()
knowledge = KnowledgeAdapter()  # bundled bq79616 demo profile unless BOARD_PROFILE set
deps = build_graph_deps(knowledge)
frame_store = InMemoryFrameStore()


@dataclass
class SessionCtx:
    """Per-session graph engine + mutable state, shared by /v2/chat and /v2/snapshot."""

    engine: GraphEngine
    state: ForgeState


_sessions: dict[str, SessionCtx] = {}


def get_ctx(session_id: str, user_id: str | None = None) -> SessionCtx:
    ctx = _sessions.get(session_id)
    if ctx is None:
        ctx = SessionCtx(
            engine=GraphEngine(deps),
            state=ForgeState(sessionId=session_id, userId=user_id),
        )
        _sessions[session_id] = ctx
    return ctx


def _drain_to_bus(state: ForgeState) -> None:
    """Publish + clear the graph's outbound events (ChatMessages, cards,
    ConfirmationRequests, CheckpointMarkers) to all subscribed sessions."""
    if state.outboundEvents:
        events = list(state.outboundEvents)
        state.outboundEvents.clear()
        bus.publish_many(events)


def _auth_subprotocol(ws: WebSocket) -> tuple[bool, str | None]:
    """Dev shared-secret auth (00 §8): token rides as a WS subprotocol. When no
    ALLOWED_DEV_TOKENS are configured we accept (zero-config dev). Returns
    (ok, subprotocol_to_echo)."""
    offered_hdr = ws.headers.get("sec-websocket-protocol")
    offered = [p.strip() for p in offered_hdr.split(",")] if offered_hdr else []
    tokens = [t.strip() for t in os.getenv("ALLOWED_DEV_TOKENS", "").split(",") if t.strip()]
    if tokens:
        match = next((p for p in offered if p in tokens), None)
        return (match is not None), match
    return True, (offered[0] if offered else None)


# ── App + heartbeat lifecycle ──────────────────────────────────────────────
app = FastAPI(title="Forge Orchestrator", version=settings.protocol_version)


@app.on_event("startup")
async def _startup() -> None:
    log.info("forge orchestrator up | integrations=%s", settings.integration_status())
    app.state.heartbeat = asyncio.create_task(_heartbeat_loop())


@app.on_event("shutdown")
async def _shutdown() -> None:
    task = getattr(app.state, "heartbeat", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _heartbeat_loop() -> None:
    """Emit chat-bus pings (spec 04 §5). We do not reap on missed pongs yet —
    early clients may not pong; liveness reaping is a TODO."""
    while True:
        await asyncio.sleep(HEARTBEAT_S)
        with contextlib.suppress(Exception):
            bus.heartbeat()


@app.get("/healthz")
async def healthz() -> dict:
    return {
        "ok": True,
        "protocol_version": settings.protocol_version,
        "integrations": settings.integration_status(),
        "sessions": len(_sessions),
    }


@app.get("/")
async def root() -> dict:
    return {"service": "forge-orchestrator", "see": "specs/00_wire_protocol.md"}


# ── /v2/chat ───────────────────────────────────────────────────────────────
@app.websocket("/v2/chat")
async def chat(ws: WebSocket) -> None:
    session_id = ws.query_params.get("sessionId")
    replay_from = ws.query_params.get("replayFrom")
    if not session_id:
        await ws.close(code=1008)  # policy violation: sessionId required
        return
    ok, subprotocol = _auth_subprotocol(ws)
    if not ok:
        await ws.close(code=1008)
        return
    await ws.accept(subprotocol=subprotocol)

    transport = WebSocketTransport(ws)
    session = Session(session_id, transport)
    bus.subscribe(session)
    writer = asyncio.create_task(transport.writer())
    ctx = get_ctx(session_id)

    try:
        # Reconnect/replay: ChannelList → last-200 → pending confirmations → ReplayDone
        bus.replay(session_id, checkpoint_id=replay_from)

        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = data.get("kind")

            if kind == "ChatMessage":
                ctx.engine.run(ctx.state, data.get("body", ""))
                _drain_to_bus(ctx.state)
            elif kind == "ConfirmationResponse":
                resp = ConfirmationResponse(**{k: v for k, v in data.items() if k != "kind"})
                ctx.engine.resume(ctx.state, resp)
                bus.resolve_confirmation(resp.callId)
                _drain_to_bus(ctx.state)
            elif kind == "Pong":
                bus.on_pong(session_id, Pong(nonce=data.get("nonce", "")))
            elif kind == "Hello":
                log.info("hello from %s session=%s", data.get("client"), session_id)
            else:
                log.debug("ignoring inbound kind=%s", kind)
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(session_id)
        transport.close()
        writer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await writer


# ── /v2/live ─────────────────────────────────────────────────────────────--
#
# WS framing (binary frames, client → orchestrator): each binary frame is a
# single media chunk prefixed with ONE type byte, the rest is the raw payload
# relayed verbatim (no transcode):
#
#     byte 0   payload[1:]
#     ──────   ─────────────────────────────────────────────
#     0x01     PCM audio, 16 kHz mono little-endian  → Live audio= slot
#     0x02     a JPEG frame (image/jpeg)             → Live video= slot
#
# Gemini Live takes PCM + JPEG (NOT H.264). The orchestrator only reads the
# prefix to pick the realtime-input slot; the payload bytes are forwarded
# unchanged. A frame with an unknown/zero-length prefix is dropped. TTS audio
# (24 kHz PCM) comes BACK as bare binary frames (no prefix) over the same WS.
# See orchestrator/live/bridge.py MediaKind + clients/live_device_sim.py.
from orchestrator.live.bridge import MediaKind as _MediaKind

_KIND_BY_PREFIX = {int(k): k for k in _MediaKind}


def _parse_live_frame(data: bytes) -> tuple[bytes, "_MediaKind"] | None:
    """Split a /v2/live binary frame into (payload, kind) per the 1-byte prefix.

    Returns ``None`` for an empty frame or an unknown type byte (the caller
    drops it rather than mislabeling it)."""
    if not data:
        return None
    kind = _KIND_BY_PREFIX.get(data[0])
    if kind is None:
        return None
    return data[1:], kind


def _stub_live_sink(chunk: bytes, kind: "_MediaKind" = _MediaKind.AUDIO) -> None:
    """No-op Gemini Live sink (used when no GEMINI_API_KEY / no [live] extra,
    or when opening a real Live session fails).

    Keeps /v2/live serving with zero env vars (07 §2.4): the WS still accepts and
    drains client media (audio AND video frames); nothing is sent back. The real
    duplex path is wired by `_make_live_graph_hooks` + `LiveDuplexBridge` below
    when a Live session opens.
    """
    return None


def _make_live_graph_hooks(session_id: str):
    """Build the (on_transcript, on_tool_call) graph hooks for one Live session.

    Both route into the SAME per-session ctx as /v2/chat and /v2/snapshot
    (get_ctx) and drain the graph's outbound events to the chat bus so SME
    deliberation shows up on /v2/chat. The synchronous GraphEngine runs in a
    worker thread so the Live receive loop never blocks the event loop.
    """
    ctx = get_ctx(session_id)

    async def on_transcript(transcript: str) -> str | None:
        # ARCHITECTURE §2/§4: a final Live transcript drives the main pipeline.
        result = await asyncio.to_thread(ctx.engine.run, ctx.state, transcript)
        _drain_to_bus(ctx.state)
        # Voice the LiveSpeaker line back through Live (the spoken summary).
        return ctx.state.liveSpeakerScript if result.status != "paused" else None

    async def on_tool_call(name: str, args: dict, call_id: str) -> dict | None:
        # A Live function-call (e.g. summon_guild) drives the guild deliberation;
        # the structured result is injected back as a function-response (00 §3).
        transcript = str(args.get("topic") or args.get("text") or name)

        def _run() -> dict:
            ctx.engine.run(ctx.state, transcript)
            merged = ctx.state.mergedOpinion
            return {
                "tool": name,
                "headline": merged.headline if merged else "",
                "supportingSmes": list(merged.supportingSmes) if merged else [],
            }

        payload = await asyncio.to_thread(_run)
        _drain_to_bus(ctx.state)
        return payload

    return on_transcript, on_tool_call


async def _drain_stub_loop(ws: WebSocket) -> None:
    """No-op one-way fallback: accept-but-DRAIN /v2/live (not silently dead).

    Used with zero env vars OR when opening a real Live session fails (bad
    model/key/network, or google-genai absent while keyed) — review MEDIUM #1.
    The WS still accepts and drains client media (audio AND JPEG video frames,
    routed per the 1-byte prefix into a no-op sink); nothing is sent back.
    """
    from orchestrator.live.bridge import LivePassthrough

    bridge = LivePassthrough(live_sink=_stub_live_sink)
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if data is None:
                continue
            parsed = _parse_live_frame(data)
            if parsed is None:
                continue  # empty/unknown-prefix frame — drop, never mislabel
            payload, kind = parsed
            bridge.forward(payload, kind)
    except WebSocketDisconnect:
        pass
    except RuntimeError:
        # The WS is already closed (e.g. we fell back here after a mid-session
        # error). Nothing to drain — exit quietly.
        pass
    finally:
        bridge.close()


@app.websocket("/v2/live")
async def live(ws: WebSocket) -> None:
    from orchestrator.live.bridge import LiveDuplexBridge

    session_id = ws.query_params.get("sessionId")
    if not session_id:
        await ws.close(code=1008)
        return
    ok, subprotocol = _auth_subprotocol(ws)
    if not ok:
        await ws.close(code=1008)
        return
    await ws.accept(subprotocol=subprotocol)

    # Decide whether to *attempt* a real duplex Gemini Live session (gated behind
    # GEMINI_API_KEY + the [live] extra). The actual connect happens inside the
    # `async with` below; ANY failure there falls back to the drain loop so the
    # socket stays accept-but-drain rather than accept-but-silently-dead.
    live_cm = None
    if settings.gemini_api_key:
        try:
            from orchestrator.live.session import connect as _live_connect

            live_cm = _live_connect(settings.gemini_live_model)
        except Exception as e:  # noqa: BLE001 — google-genai missing / import error
            log.warning("live session unavailable (%s); using no-op stub", e)
            live_cm = None

    if live_cm is None:
        # ── stub mode: one-way relay into a no-op sink ──────────────────────
        await _drain_stub_loop(ws)
        return

    # ── duplex mode: real Gemini Live session both directions ───────────────
    async def audio_out(chunk: bytes) -> None:
        await ws.send_bytes(chunk)

    on_transcript, on_tool_call = _make_live_graph_hooks(session_id)
    bridge: LiveDuplexBridge | None = None
    try:
        async with live_cm as session:
            bridge = LiveDuplexBridge(
                session,
                audio_out=audio_out,
                on_transcript=on_transcript,
                on_tool_call=on_tool_call,
            )
            recv = asyncio.create_task(bridge.receive_loop())
            try:
                while True:
                    msg = await ws.receive()
                    if msg.get("type") == "websocket.disconnect":
                        break
                    data = msg.get("bytes")
                    if data is None:
                        continue
                    parsed = _parse_live_frame(data)
                    if parsed is None:
                        continue  # empty/unknown-prefix frame — drop
                    payload, kind = parsed
                    bridge.forward_client_chunk(payload, kind)
            except WebSocketDisconnect:
                pass
            finally:
                recv.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await recv
                # aclose() runs here for the normal path AND the receive-error
                # path (try/finally), so the session is always torn down once.
                await bridge.aclose()
                bridge = None  # already closed; don't double-close below
    except Exception as e:  # noqa: BLE001 — live session died; don't 500 the WS
        # Connect/session failure BEFORE the WS loop ran → fall back to the
        # drain loop so the client isn't talking to a dead socket (MEDIUM #1).
        log.warning("live duplex session error (%s)", e)
        if bridge is not None:
            with contextlib.suppress(Exception):
                await bridge.aclose()
        await _drain_stub_loop(ws)


# ── /v2/snapshot ─────────────────────────────────────────────────────────--
@app.post("/v2/snapshot")
async def snapshot(request: Request) -> JSONResponse:
    session_id = request.query_params.get("sessionId")
    if not session_id:
        return JSONResponse({"error": "sessionId required"}, status_code=400)
    note = request.query_params.get("note")
    width = int(request.query_params.get("w", "0") or 0)
    height = int(request.query_params.get("h", "0") or 0)
    jpeg = await request.body()

    ctx = get_ctx(session_id)
    # Analyze (no bus here), feed perception into the graph, then drain the
    # resulting #live-feed card + checkpoint to subscribers.
    snap = handle_snapshot(
        session_id=session_id,
        jpeg_bytes=jpeg,
        width=width,
        height=height,
        note=note,
        knowledge=knowledge,
        model_call=stub_snapshot_model_call,
        store=frame_store,
        bus=None,
    )
    ctx.engine.ingest_snapshot(ctx.state, snap)
    _drain_to_bus(ctx.state)
    return JSONResponse({"jobId": new_ulid()}, status_code=202)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level.lower())


if __name__ == "__main__":
    main()

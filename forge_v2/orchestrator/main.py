"""FastAPI entrypoint — connection layer + health.

Scaffold only. The LangGraph engine (specs/01), managed-agent dispatch
(specs/02) and the full chat-bus protocol (specs/04) plug in here. The
/v2/chat WebSocket currently performs the protocol handshake and echoes a
stub event so clients can integrate against a live endpoint today.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .config import settings

logging.basicConfig(level=settings.forge_log_level)
log = logging.getLogger("forge.orchestrator")

app = FastAPI(title="Forge Orchestrator", version=settings.forge_protocol_version)


@app.on_event("startup")
async def _log_modes() -> None:
    log.info("forge orchestrator up | integrations=%s", settings.integration_status())


@app.get("/healthz")
async def healthz() -> dict:
    """Liveness + integration modes. Used by the container HEALTHCHECK and CI."""
    return {
        "status": "ok",
        "protocol_version": settings.forge_protocol_version,
        "integrations": settings.integration_status(),
    }


@app.get("/")
async def root() -> dict:
    return {"service": "forge-orchestrator", "see": "specs/00_wire_protocol.md"}


@app.websocket("/v2/chat")
async def chat(ws: WebSocket) -> None:
    """Stub chat-bus endpoint (specs/04). Accepts the connection and emits a
    single hello event; full per-channel fan-out lands with the chat bus."""
    await ws.accept()
    await ws.send_json(
        {
            "type": "system",
            "channel": "#live-feed",
            "text": "orchestrator scaffold online (stub mode)",
            "protocol_version": settings.forge_protocol_version,
        }
    )
    try:
        while True:
            msg = await ws.receive_text()
            await ws.send_json({"type": "echo", "channel": "#live-feed", "text": msg})
    except WebSocketDisconnect:
        log.info("chat ws disconnected")


def main() -> None:
    import uvicorn

    uvicorn.run(
        "orchestrator.main:app",
        host=settings.forge_host,
        port=settings.forge_port,
        log_level=settings.forge_log_level.lower(),
    )


if __name__ == "__main__":
    main()

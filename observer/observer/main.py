"""Observer entrypoint: one process, three concurrent concerns.

  - ingest_loop  : WS client tapping /v2/chat → SQLite
  - distill_loop : periodic Gemini (or heuristic) → status rows
  - FastAPI app  : the manager dashboard, served by uvicorn

All three share one SQLite file on a Docker volume (WAL mode). See README for
the one-container rationale. Run with:

    uvicorn observer.main:app --host 0.0.0.0 --port 8090
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Optional

from observer.config import Settings, load_settings
from observer.distill import (
    ModelCall,
    distill_once,
    gemini_model_call,
    managed_agent_model_call,
)
from observer.ingest import ingest_loop
from observer.store import Store
from observer.web import build_app

settings: Settings = load_settings()
logging.basicConfig(level=settings.log_level)
log = logging.getLogger("observer")

store = Store(settings.db_path)
app = build_app(store, distill_window_s=settings.distill_window_s)

_stop = asyncio.Event()


def _build_model_call() -> tuple[Optional[ModelCall], str]:
    """Pick the distiller backend (settings.distill_mode) → (model_call, source).
    Falls back to the heuristic on missing key / unknown mode / init failure so
    the dashboard never goes blank."""
    if not settings.gemini_enabled or settings.distill_mode == "heuristic":
        log.info("distill: heuristic headlines (no model call)")
        return None, "heuristic"
    try:
        if settings.distill_mode == "managed":
            log.info("distill: using Antigravity MANAGED AGENT (%s) for headlines",
                     settings.managed_agent)
            return (
                managed_agent_model_call(settings.gemini_api_key, settings.managed_agent),
                "managed",
            )
        log.info("distill: using Gemini %s for headlines", settings.gemini_model)
        return gemini_model_call(settings.gemini_api_key, settings.gemini_model), "gemini"
    except Exception:  # noqa: BLE001
        log.exception("distill: could not init model; falling back to heuristic")
        return None, "heuristic"


async def _distill_loop(model_call: Optional[ModelCall], model_source: str = "gemini") -> None:
    while not _stop.is_set():
        try:
            n = distill_once(
                store,
                window_s=settings.distill_window_s,
                max_events=settings.distill_max_events,
                model_call=model_call,
                model_source=model_source,
            )
            if n:
                log.debug("distill: wrote %d status row(s)", n)
        except Exception:  # noqa: BLE001
            log.exception("distill: cycle failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(_stop.wait(), timeout=settings.distill_interval_s)


@app.on_event("startup")
async def _startup() -> None:
    log.info("observer up | bus=%s | db=%s | gemini=%s",
             settings.bus_url, settings.db_path, settings.gemini_enabled)
    model_call, model_source = _build_model_call()
    app.state.tasks = [
        asyncio.create_task(ingest_loop(store, settings, stop=_stop)),
        asyncio.create_task(_distill_loop(model_call, model_source)),
    ]


@app.on_event("shutdown")
async def _shutdown() -> None:
    _stop.set()
    for task in getattr(app.state, "tasks", []):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    store.close()

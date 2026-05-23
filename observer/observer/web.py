"""FastAPI app: read-only manager view over the SQLite store.

Endpoints:
  GET /                 → the single-page dashboard (polls /api/overview)
  GET /healthz          → liveness + event count
  GET /api/overview     → the whole manager view as JSON (operators, flags,
                          pending confirmations, distilled status)
  GET /api/session/{id} → drill-down: that session's full timeline + SMEs
  GET /api/events       → raw event feed (debug / firehose tail)

The page POLLS (simple + robust) rather than SSE: a manager dashboard refreshing
every few seconds is plenty live for human reaction times, and polling survives
proxies/restarts with zero reconnect logic.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from observer.distill import compute_facts
from observer.store import Store, now_ms

_HERE = os.path.dirname(__file__)
_INDEX_HTML = os.path.join(_HERE, "static", "index.html")

# Sessions idle longer than this are shown as "offline" but still listed.
RECENT_WINDOW_MS = 60 * 60 * 1000  # 1 hour


def build_app(store: Store, *, distill_window_s: float = 900.0) -> FastAPI:
    app = FastAPI(title="Forge Observer", version="0.1.0")
    window_ms = int(distill_window_s * 1000)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True, "events": store.event_count()}

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        try:
            with open(_INDEX_HTML, "r", encoding="utf-8") as fh:
                return HTMLResponse(fh.read())
        except FileNotFoundError:
            return HTMLResponse("<h1>Forge Observer</h1><p>UI not found.</p>", status_code=200)

    @app.get("/api/overview")
    async def overview() -> JSONResponse:
        now = now_ms()
        since = now - RECENT_WINDOW_MS

        # The distilled status rows (headline + facts) are the primary content.
        status_rows = {s["session_id"]: s for s in store.all_status()}

        # Also fold in any session that has very recent events but no status row
        # yet (distiller hasn't run for it) so nothing is invisible.
        operators: list[dict[str, Any]] = []
        seen: set[str] = set()
        for sid in store.session_ids(since_ms=since):
            seen.add(sid)
            row = status_rows.get(sid)
            if row is not None:
                facts = row["detail"]
                operators.append(
                    {
                        "session_id": sid,
                        "headline": row["headline"],
                        "headline_source": row["source"],
                        "updated_ms": row["updated_ms"],
                        **_present(facts, now),
                    }
                )
            else:
                events = store.recent_events(limit=120, since_ms=since, session_id=sid)
                facts = compute_facts(events, session_id=sid, now=now)
                operators.append(
                    {
                        "session_id": sid,
                        "headline": "(awaiting first distill)",
                        "headline_source": "pending",
                        "updated_ms": None,
                        **_present(facts, now),
                    }
                )

        operators.sort(key=lambda o: (not o["active"], -(o.get("last_activity_ms") or 0)))

        return JSONResponse(
            {
                "now_ms": now,
                "operators": operators,
                "pending_confirmations": store.pending_confirmations(now=now),
                "totals": {
                    "events": store.event_count(),
                    "operators": len(operators),
                    "flagged": sum(1 for o in operators if o["flags"]),
                },
            }
        )

    @app.get("/api/session/{session_id}")
    async def session_detail(session_id: str) -> JSONResponse:
        now = now_ms()
        since = now - window_ms
        events = store.recent_events(limit=300, since_ms=since, session_id=session_id)
        facts = compute_facts(events, session_id=session_id, now=now)
        return JSONResponse({"now_ms": now, "facts": facts})

    @app.get("/api/events")
    async def events(limit: int = 100, kind: Optional[str] = None) -> JSONResponse:
        kinds = (kind,) if kind else None
        rows = store.recent_events(limit=min(limit, 500), kinds=kinds)
        for r in rows:
            r["raw"] = json.loads(r.pop("raw_json"))
        return JSONResponse({"events": rows})

    return app


def _present(facts: dict[str, Any], now: int) -> dict[str, Any]:
    """Project the rich facts dict down to the fields the overview card shows."""
    return {
        "active": facts.get("active", False),
        "active_for_ms": facts.get("active_for_ms", 0),
        "idle_ms": facts.get("idle_ms"),
        "last_activity_ms": facts.get("last_activity_ms"),
        "board_task": facts.get("board_task"),
        "flags": facts.get("flags", []),
        "smes_consulted": facts.get("smes_consulted", []),
        "pending_confirmations": facts.get("pending_confirmations", []),
        "timeline": facts.get("timeline", []),
        "event_count": facts.get("event_count", 0),
    }

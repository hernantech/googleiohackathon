"""FastAPI app: read-only manager view over the SQLite store.

Endpoints:
  GET /                 → the single-page dashboard (polls /api/overview)
  GET /healthz          → liveness + event count
  GET /api/overview     → the whole manager view as JSON (operators, flags,
                          pending confirmations, distilled status)
  GET /api/session/{id} → drill-down: that session's distilled facts + (opt) the
                          FULL raw event history for that session
  GET /api/events       → the complete, filterable, keyset-paginated firehose
                          (by kind / session / text), every persisted event
  GET /api/kinds        → distinct kinds + per-kind counts (firehose breakdown)

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
        # Last activity for EVERY session ever seen — so a session whose newest
        # event is >1h old (its status row outlives the recent window) is still
        # listed, just marked offline. Nothing persisted is ever invisible.
        last_activity = store.session_last_activity()

        # Union of: every session with a status row + every session that ever
        # produced an event. We no longer drop sessions older than the recent
        # window — we mark them offline and keep showing them.
        all_sids: set[str] = set(status_rows) | set(last_activity)

        operators: list[dict[str, Any]] = []
        for sid in all_sids:
            online = (last_activity.get(sid, 0) >= since)
            row = status_rows.get(sid)
            if row is not None:
                facts = dict(row["detail"])
                # The stored facts' active/idle reflect distill time; recompute
                # online purely from the latest event so a stale card flips
                # offline even before the next distill cycle runs.
                present = _present(facts, now)
                present["active"] = present["active"] and online
                present["last_activity_ms"] = last_activity.get(sid) or present.get("last_activity_ms")
                operators.append(
                    {
                        "session_id": sid,
                        "headline": row["headline"],
                        "headline_source": row["source"],
                        "updated_ms": row["updated_ms"],
                        "online": online,
                        **present,
                    }
                )
            else:
                # Seen in events but no status row yet (distiller hasn't run).
                events = store.recent_events(limit=120, session_id=sid)
                facts = compute_facts(events, session_id=sid, now=now)
                present = _present(facts, now)
                present["active"] = present["active"] and online
                present["last_activity_ms"] = last_activity.get(sid) or present.get("last_activity_ms")
                operators.append(
                    {
                        "session_id": sid,
                        "headline": "(awaiting first distill)",
                        "headline_source": "pending",
                        "updated_ms": None,
                        "online": online,
                        **present,
                    }
                )

        # Online + most-recently-active first; offline (stale) sink to the bottom
        # but are still present.
        operators.sort(
            key=lambda o: (not o["online"], -(o.get("last_activity_ms") or 0))
        )

        return JSONResponse(
            {
                "now_ms": now,
                "operators": operators,
                "pending_confirmations": store.pending_confirmations(now=now),
                "totals": {
                    "events": store.event_count(),
                    "operators": len(operators),
                    "online": sum(1 for o in operators if o["online"]),
                    "flagged": sum(1 for o in operators if o["flags"]),
                },
            }
        )

    @app.get("/api/session/{session_id}")
    async def session_detail(
        session_id: str,
        full: bool = False,
        limit: int = 500,
        before_id: Optional[int] = None,
    ) -> JSONResponse:
        """Distilled facts for the session, plus its raw event history.

        ``full=false`` (default): facts computed over the recent distill window —
        the compact, manager-facing drill-down.
        ``full=true``: the COMPLETE event history for the session (all kinds, no
        interesting-set filter, since the beginning of the DB), keyset-paginated
        by ``before_id`` for deep scrolling. Each row carries its parsed ``raw``.
        """
        now = now_ms()
        since = now - window_ms
        events = store.recent_events(limit=300, since_ms=since, session_id=session_id)
        facts = compute_facts(events, session_id=session_id, now=now)
        resp: dict[str, Any] = {
            "now_ms": now,
            "facts": facts,
            "total_events": store.session_event_count(session_id),
        }
        if full:
            rows = store.session_events(
                session_id, limit=min(limit, 1000), before_id=before_id
            )
            for r in rows:
                r["raw"] = json.loads(r.pop("raw_json"))
            resp["events"] = rows
            resp["next_before_id"] = rows[-1]["id"] if rows else None
        return JSONResponse(resp)

    @app.get("/api/events")
    async def events(
        limit: int = 100,
        kind: Optional[str] = None,
        session_id: Optional[str] = None,
        q: Optional[str] = None,
        before_id: Optional[int] = None,
    ) -> JSONResponse:
        """The complete firehose: every persisted event, newest-first, filterable
        by ``kind`` / ``session_id`` / ``q`` (text search over summary + raw +
        author) and keyset-paginated by ``before_id`` so the browser can page all
        the way back to the beginning of the DB. Each row carries parsed ``raw``."""
        kinds = (kind,) if kind else None
        rows = store.events_page(
            limit=min(limit, 500),
            before_id=before_id,
            session_id=session_id,
            kinds=kinds,
            text=q,
        )
        for r in rows:
            r["raw"] = json.loads(r.pop("raw_json"))
        return JSONResponse(
            {
                "events": rows,
                # The smallest id on this page → pass as before_id for the next
                # (older) page. null ⇒ end of the firehose.
                "next_before_id": rows[-1]["id"] if rows else None,
            }
        )

    @app.get("/api/kinds")
    async def kinds() -> JSONResponse:
        """Distinct kinds + per-kind row counts — drives the firehose filter
        dropdown and shows the manager the full persisted breakdown."""
        return JSONResponse(
            {"kinds": store.event_kinds(), "counts": store.kind_counts()}
        )

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

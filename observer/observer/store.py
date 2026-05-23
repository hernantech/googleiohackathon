"""SQLite persistence for the observer.

Two tables:

  events  — the raw event firehose, one row per bus event (append-only).
  status  — one *current* manager-readable summary row per session, rewritten
            by the distiller each cycle (latest-wins, keyed by session_id).

WAL mode lets the FastAPI reader and the ingest/distill writers share the file
without blocking each other — the key reason a single SQLite file is safe for
the ingest+distill+serve trio in one container.

All timestamps are stored as *epoch milliseconds* (``ts_ms``). The wire uses
nanoseconds (``ts``); ``ingest`` normalizes ns→ms so the dashboard never has to
reason about units.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms       INTEGER NOT NULL,          -- event time (epoch ms), bus ts if present else ingest time
    received_ms INTEGER NOT NULL,          -- when WE saw it (epoch ms); monotone-ish ingest order
    kind        TEXT    NOT NULL,          -- ChatMessage | SmeResponse | SafetyInterrupt | ...
    session_id  TEXT,                      -- best-effort; often the observer's own subscription id (see README)
    channel_id  TEXT,                      -- #power, #actions, ...
    author_id   TEXT,                      -- @power, @firmware, user, system
    call_id     TEXT,                      -- correlates ConfirmationRequest/Response, SummonGuild/SmeResponse
    summary     TEXT,                      -- short human-facing snippet (body/claim/reason)
    raw_json    TEXT    NOT NULL           -- the full normalized event for the distiller + drill-down
);
CREATE INDEX IF NOT EXISTS idx_events_received ON events (received_ms);
CREATE INDEX IF NOT EXISTS idx_events_session  ON events (session_id, received_ms);
CREATE INDEX IF NOT EXISTS idx_events_kind     ON events (kind, received_ms);
CREATE INDEX IF NOT EXISTS idx_events_call     ON events (call_id);

CREATE TABLE IF NOT EXISTS status (
    session_id   TEXT PRIMARY KEY,
    updated_ms   INTEGER NOT NULL,         -- when the distiller wrote this row
    headline     TEXT NOT NULL,            -- the one-line "what they're doing right now"
    detail_json  TEXT NOT NULL,            -- structured fields the dashboard renders (see distill.StatusRow)
    source       TEXT NOT NULL             -- 'gemini' | 'heuristic' (honesty about provenance)
);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


def ns_to_ms(ts_ns: int | None) -> int | None:
    if ts_ns is None:
        return None
    # Wire ts is nanoseconds since epoch. Guard against accidental ms/s inputs.
    if ts_ns > 1_000_000_000_000_000:  # ~> year 2001 in ns
        return ts_ns // 1_000_000
    return int(ts_ns)


class Store:
    """Thin SQLite wrapper. Safe for use from multiple threads/tasks via a lock
    around writes; reads open short-lived connections (WAL → no reader stall)."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._write_lock = threading.Lock()
        if db_path != ":memory:":
            parent = os.path.dirname(db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        # A single shared connection works for :memory: (otherwise each connect
        # gets a fresh empty DB). For file DBs we still keep one writer conn.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        if db_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        cur = self._conn.cursor()
        try:
            yield cur
        finally:
            cur.close()

    # ── writes ────────────────────────────────────────────────────────────
    def insert_event(self, event: dict[str, Any]) -> int:
        """Persist one normalized event. Returns the new row id.

        ``event`` is the dict produced by ``ingest.normalize`` — it carries the
        flattened columns plus ``raw_json``.
        """
        with self._write_lock, self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO events
                    (ts_ms, received_ms, kind, session_id, channel_id,
                     author_id, call_id, summary, raw_json)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    event["ts_ms"],
                    event["received_ms"],
                    event["kind"],
                    event.get("session_id"),
                    event.get("channel_id"),
                    event.get("author_id"),
                    event.get("call_id"),
                    event.get("summary"),
                    event["raw_json"],
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def upsert_status(
        self, session_id: str, headline: str, detail: dict[str, Any], source: str
    ) -> None:
        """Latest-wins status row for a session (the distiller's output)."""
        with self._write_lock, self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO status (session_id, updated_ms, headline, detail_json, source)
                VALUES (?,?,?,?,?)
                ON CONFLICT(session_id) DO UPDATE SET
                    updated_ms=excluded.updated_ms,
                    headline=excluded.headline,
                    detail_json=excluded.detail_json,
                    source=excluded.source
                """,
                (session_id, now_ms(), headline, json.dumps(detail), source),
            )
            self._conn.commit()

    # ── reads ─────────────────────────────────────────────────────────────
    def recent_events(
        self,
        *,
        limit: int = 200,
        since_ms: int | None = None,
        session_id: str | None = None,
        kinds: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        if since_ms is not None:
            clauses.append("received_ms >= ?")
            params.append(since_ms)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            clauses.append(f"kind IN ({placeholders})")
            params.extend(kinds)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT id, ts_ms, received_ms, kind, session_id, channel_id, "
            "author_id, call_id, summary, raw_json FROM events"
            f"{where} ORDER BY received_ms DESC, id DESC LIMIT ?"
        )
        params.append(limit)
        with self._cursor() as cur:
            rows = cur.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def session_ids(self, *, since_ms: int | None = None) -> list[str]:
        """Distinct non-null session ids seen in events (optionally since a time)."""
        sql = "SELECT DISTINCT session_id FROM events WHERE session_id IS NOT NULL"
        params: list[Any] = []
        if since_ms is not None:
            sql += " AND received_ms >= ?"
            params.append(since_ms)
        with self._cursor() as cur:
            rows = cur.execute(sql, params).fetchall()
        return [r["session_id"] for r in rows]

    def all_status(self) -> list[dict[str, Any]]:
        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT session_id, updated_ms, headline, detail_json, source "
                "FROM status ORDER BY updated_ms DESC"
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = json.loads(d.pop("detail_json"))
            out.append(d)
        return out

    def pending_confirmations(self, *, now: int | None = None) -> list[dict[str, Any]]:
        """ConfirmationRequests with no matching ConfirmationResponse (by call_id),
        oldest first, annotated with how long they've been pending (pending_ms).

        This is the "operator stuck?" signal — we surface age, not just count.
        """
        now = now if now is not None else now_ms()
        with self._cursor() as cur:
            reqs = cur.execute(
                "SELECT call_id, ts_ms, received_ms, summary, raw_json, session_id "
                "FROM events WHERE kind='ConfirmationRequest' AND call_id IS NOT NULL"
            ).fetchall()
            resolved = {
                r["call_id"]
                for r in cur.execute(
                    "SELECT DISTINCT call_id FROM events "
                    "WHERE kind='ConfirmationResponse' AND call_id IS NOT NULL"
                ).fetchall()
            }
        out = []
        seen: set[str] = set()
        for r in reqs:
            cid = r["call_id"]
            if cid in resolved or cid in seen:
                continue
            seen.add(cid)
            raw = json.loads(r["raw_json"])
            out.append(
                {
                    "call_id": cid,
                    "summary": r["summary"],
                    "risk": raw.get("risk"),
                    "invoker": raw.get("invokerSmeId"),
                    "session_id": r["session_id"],
                    "since_ms": r["received_ms"],
                    "pending_ms": max(0, now - r["received_ms"]),
                }
            )
        out.sort(key=lambda x: x["pending_ms"], reverse=True)
        return out

    def event_count(self) -> int:
        with self._cursor() as cur:
            return int(cur.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"])

    def close(self) -> None:
        self._conn.close()

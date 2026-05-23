"""Runtime configuration for the observer (all via env; never hard-code keys)."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # The orchestrator chat bus we tap as a WS client. Points at the live VM by
    # default so `docker compose up` on the VM "just works"; override locally to
    # aim at the firehose or a local orchestrator.
    bus_url: str = os.getenv("OBSERVER_BUS_URL", "ws://20.230.188.247:8080/v2/chat")

    # sessionId is REQUIRED by /v2/chat (main.py closes 1008 without it). The
    # observer joins as its own session purely to subscribe to the fan-out.
    observer_session_id: str = os.getenv("OBSERVER_SESSION_ID", "observer-dashboard")
    observer_client: str = os.getenv("OBSERVER_CLIENT", "observer")

    # SQLite lives on a Docker named volume so it survives restarts/redeploys.
    db_path: str = os.getenv("OBSERVER_DB_PATH", "/data/observer.db")

    # Distiller cadence + window.
    distill_interval_s: float = float(os.getenv("OBSERVER_DISTILL_INTERVAL_S", "20"))
    distill_window_s: float = float(os.getenv("OBSERVER_DISTILL_WINDOW_S", "900"))  # 15 min
    distill_max_events: int = int(os.getenv("OBSERVER_DISTILL_MAX_EVENTS", "120"))

    # Gemini — same key the orchestrator uses. Empty key ⇒ heuristic fallback
    # distiller (no network), so the dashboard still works with zero secrets.
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("OBSERVER_GEMINI_MODEL", "gemini-3.5-flash")

    # WS reconnect backoff bounds (seconds).
    reconnect_min_s: float = float(os.getenv("OBSERVER_RECONNECT_MIN_S", "1"))
    reconnect_max_s: float = float(os.getenv("OBSERVER_RECONNECT_MAX_S", "30"))

    log_level: str = os.getenv("OBSERVER_LOG_LEVEL", "INFO")

    @property
    def gemini_enabled(self) -> bool:
        return bool(self.gemini_api_key)


def load_settings() -> Settings:
    return Settings()

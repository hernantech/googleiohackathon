"""Runtime configuration.

Mirrors specs/07_environment_setup.md §2.1. Every field is optional and the
service MUST start cleanly with zero env vars set (the dev-loop contract,
spec §2.4) — unset external integrations fall back to stub mode.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Service ──────────────────────────────────────────────
    forge_port: int = 8080
    forge_host: str = "0.0.0.0"
    forge_log_level: str = "INFO"
    forge_protocol_version: str = "2.0"

    # ── Gemini ───────────────────────────────────────────────
    gemini_api_key: str = ""  # unset → Live runs in stub mode
    gemini_live_model: str = "gemini-2.0-flash-exp"
    gemini_sme_model: str = "gemini-2.5-pro"
    gemini_sentinel_model: str = "gemini-2.5-flash"

    # ── Managed Agents ───────────────────────────────────────
    managed_agents_api_key: str = ""  # unset → SMEs run in stub mode
    managed_agents_endpoint: str = "https://managed-agents.googleapis.com/v1preview"
    managed_agents_region: str = "us-central1"
    sme_env_template: str = "ubuntu-2404-python"
    sme_keepwarm_interval_s: int = 240

    # ── GCP ──────────────────────────────────────────────────
    gcp_project_id: str = ""  # unset → all GCP services in-memory
    gcp_region: str = "us-central1"
    firestore_database: str = "(default)"
    frame_bucket: str = ""
    google_application_credentials: str = ""

    # ── Firebase ─────────────────────────────────────────────
    firebase_project_id: str = ""  # unset → shared-secret auth
    allowed_dev_tokens: str = "forge-dev-shared-secret"

    # ── Bench daemon ─────────────────────────────────────────
    bench_daemon_url: str = ""  # unset → all bench tools in stub
    bench_shared_secret: str = ""
    bench_heartbeat_interval_s: int = 2

    # ── Replay / checkpoints ─────────────────────────────────
    langgraph_checkpointer: str = "memory"  # "firestore" | "memory"
    langgraph_replay_window: int = 200

    @property
    def live_stub_mode(self) -> bool:
        return not self.gemini_api_key

    @property
    def smes_stub_mode(self) -> bool:
        return not self.managed_agents_api_key

    def integration_status(self) -> dict[str, str]:
        """Human-readable mode per integration, surfaced on /healthz."""
        return {
            "gemini_live": "stub" if self.live_stub_mode else "live",
            "managed_agents": "stub" if self.smes_stub_mode else "live",
            "gcp": "in-memory" if not self.gcp_project_id else "firestore/gcs",
            "auth": "shared-secret" if not self.firebase_project_id else "firebase",
            "bench": "stub" if not self.bench_daemon_url else "connected",
        }


settings = Settings()

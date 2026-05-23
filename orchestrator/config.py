"""Service configuration for the FastAPI surface (specs/07 §2.1).

The orchestrator core is dependency-injected and boots clean with zero env vars
(07 §2.4); this only carries the serving-layer knobs and detects which
integrations have credentials so /healthz can report stub-vs-live honestly.
The four model seams (07 §"injection seams") are stubbed until real SDK wiring
lands — see orchestrator/seams.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"
    protocol_version: str = "2.0"

    # Credentials — presence flips integration mode (no values stored long-term).
    gemini_api_key: str = ""
    managed_agents_api_key: str = ""
    board_profile: str = ""  # path; unset → bundled bq79616 demo profile

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=os.getenv("FORGE_HOST", "0.0.0.0"),
            port=int(os.getenv("FORGE_PORT", "8080")),
            log_level=os.getenv("FORGE_LOG_LEVEL", "INFO"),
            protocol_version=os.getenv("FORGE_PROTOCOL_VERSION", "2.0"),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            managed_agents_api_key=os.getenv("MANAGED_AGENTS_API_KEY", ""),
            board_profile=os.getenv("BOARD_PROFILE", ""),
        )

    def integration_status(self) -> dict[str, str]:
        # Seams are stubbed regardless of keys until real SDK wiring lands.
        return {
            "gemini": "key-present (seams stubbed)" if self.gemini_api_key else "stub",
            "managed_agents": "key-present (seams stubbed)"
            if self.managed_agents_api_key
            else "stub",
            "board_profile": self.board_profile or "bundled-demo",
            "model_seams": "stub",
        }


settings = Settings.from_env()

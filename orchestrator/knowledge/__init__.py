"""KnowledgeAdapter (P1) — in-process board knowledge & operator guidance.

Direct implementation of `specs/05_board_knowledge_api.md`. There is no bench
daemon, no socket, no RPC, and **nothing here actuates hardware**. The adapter
answers three read-only questions for the guild + SafetyGate:

  * `lookup_datasheet(part, query, maxPages)` — datasheet passages + a cite (§3.1)
  * `lookup_board_doc(query)`                 — board-doc prose + profile hits (§3.2)
  * `get_documented_limit(target, kind)`      — deterministic, cached limit (§3.3, §4)

The operator-step verbs in §5 (`set_psu`, `flash_mcu`, ...) are *labels* the
human performs; there is deliberately no callable behind any of them here
(BK-10). They live in `orchestrator.proto.events.OPERATOR_STEP_TOOLS`.
"""

from __future__ import annotations

import os
import pathlib

from orchestrator.knowledge.board_profile import BoardProfile, load_board_profile
from orchestrator.knowledge.bom import BomResult, lookup_bom as _lookup_bom
from orchestrator.knowledge.limits import DocumentedLimit, LimitResolver
from orchestrator.knowledge.lookups import (
    BoardDocResult,
    DatasheetResult,
    lookup_board_doc as _lookup_board_doc,
    lookup_datasheet as _lookup_datasheet,
)

#: Bundled demo profile (§6). Default when no path / env is given and it exists.
EXAMPLE_PROFILE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "bench_knowledge"
    / "examples"
    / "bq79616-bringup-2026-05.yaml"
)


def _default_profile_path() -> str | os.PathLike | None:
    """BOARD_PROFILE env > bundled demo fixture (if present) > None (empty)."""
    if os.environ.get("BOARD_PROFILE"):
        return None  # let load_board_profile read the env var itself
    if EXAMPLE_PROFILE_PATH.exists():
        return EXAMPLE_PROFILE_PATH
    return None


class KnowledgeAdapter:
    """Facade over the board profile + the three lookup tools (§3).

    Construct with an explicit profile path, or omit it to use `BOARD_PROFILE`
    / the bundled demo fixture / an empty profile, in that order. An absent
    profile yields an empty one (§6) — the adapter never crashes on boot.
    """

    def __init__(self, profile_path: str | os.PathLike | None = None):
        if profile_path is None:
            profile_path = _default_profile_path()
        self.board_profile: BoardProfile = load_board_profile(profile_path)
        self._resolver = LimitResolver(self.board_profile)

    # ── §3.1 ──
    def lookup_datasheet(
        self, part: str, query: str, maxPages: int | None = None
    ) -> DatasheetResult:
        return _lookup_datasheet(part, query, maxPages, profile=self.board_profile)

    # ── §3.2 ──
    def lookup_board_doc(self, query: str) -> BoardDocResult:
        return _lookup_board_doc(query, profile=self.board_profile)

    # ── §3.3 / §4 — deterministic, cached, no model in the loop ──
    def get_documented_limit(self, target: str, kind: str) -> DocumentedLimit:
        return self._resolver.get(target, kind)

    # ── BOM lookup ──
    def lookup_bom(self, query: str, max_results: int = 10) -> BomResult:
        return _lookup_bom(query, max_results=max_results)

    @property
    def datastore_hits(self) -> int:
        """Datasheet-table reads behind get_documented_limit (BK-4)."""
        return self._resolver.datastore_hits


__all__ = [
    "KnowledgeAdapter",
    "BoardProfile",
    "load_board_profile",
    "DocumentedLimit",
    "DatasheetResult",
    "BoardDocResult",
    "BomResult",
    "EXAMPLE_PROFILE_PATH",
]

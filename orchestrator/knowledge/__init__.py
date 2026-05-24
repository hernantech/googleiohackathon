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
        #: Session cache of the most-recent parsed schematic (09 §5.2). Set by
        #: ingest_schematic; read by lookup_schematic so SMEs can query a parse
        #: by ref/net/part without re-running vision. Typed loosely to avoid an
        #: import cycle (schematic depends only on knowledge, not vice-versa).
        self.schematic: "object | None" = None

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

    # ── schematic ingest + retrieval (09 §5.2 / §5.3) ──
    def ingest_schematic(self, sch: "object") -> dict:
        """Cache a parsed `SchematicJSON` for the session AND merge its
        components/nets ADDITIVELY into the in-memory board profile, marked
        `source="schematic_image"` with NO limit fields.

        After ingest the EXISTING lookups answer board-topology questions from
        the parse with zero SME-side change: `lookup_board_doc`'s profileMatches
        surface the merged parts/nets, and `get_documented_limit` still returns
        `found=False` for a schematic-only net (no invented limit — limits stay
        sourced from documented YAML / datasheet values only, 03 §3.3.6).

        Returns `{"parts_added","nets_added","cite"}`. Never raises (01 §7)."""
        self.schematic = sch
        try:
            counts = self.board_profile.merge_schematic(sch)
        except Exception:  # noqa: BLE001 — never fail-stop on a bad parse
            counts = {"parts_added": 0, "nets_added": 0}
        # The LimitResolver caches per (target, kind); a net we just merged has
        # no limit fields, so a fresh resolve correctly returns found=False. But
        # drop any cached miss for the new targets so a later documented update
        # would re-resolve (defensive; merged nets carry no limits regardless).
        self._resolver = LimitResolver(self.board_profile)
        counts["cite"] = getattr(sch, "cite", "") or "parsed schematic"
        return counts

    def lookup_schematic(self, query: str) -> dict:
        """Query the session-cached parsed schematic by ref / net / part (09
        §5.2). Returns the matching `SchComponent`/`SchNet` subset + the parse's
        `cite`, WITHOUT re-running vision. Empty result when nothing is cached or
        nothing matches (never raises)."""
        sch = self.schematic
        if sch is None:
            return {"query": query, "components": [], "nets": [],
                    "cite": "", "note": "no schematic parsed in this session"}
        q = (query or "").strip().casefold()
        comps = list(getattr(sch, "components", None) or [])
        nets = list(getattr(sch, "nets", None) or [])

        def _comp_hit(c: "object") -> bool:
            if not q:
                return False
            fields = [getattr(c, "ref", None), getattr(c, "part", None),
                      getattr(c, "type", None), getattr(c, "value", None),
                      getattr(c, "description", None)]
            if any(f and q in str(f).casefold() for f in fields):
                return True
            # match by a net the component touches
            for p in getattr(c, "pins", None) or []:
                if getattr(p, "net", None) and q in str(p.net).casefold():
                    return True
            return False

        def _net_hit(n: "object") -> bool:
            if not q:
                return False
            if getattr(n, "id", None) and q in str(n.id).casefold():
                return True
            for node in getattr(n, "nodes", None) or []:
                if getattr(node, "ref", None) and q in str(node.ref).casefold():
                    return True
            return False

        matched_comps = [c for c in comps if _comp_hit(c)]
        matched_nets = [n for n in nets if _net_hit(n)]
        dump = lambda m: m.model_dump() if hasattr(m, "model_dump") else m
        return {
            "query": query,
            "components": [dump(c) for c in matched_comps],
            "nets": [dump(n) for n in matched_nets],
            "cite": getattr(sch, "cite", "") or "parsed schematic",
            "note": (
                "Schematic-derived (model vision) — advisory only. "
                "Limits still come from get_documented_limit, never from a "
                "nominalVGuess/classGuess."
            ),
        }

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

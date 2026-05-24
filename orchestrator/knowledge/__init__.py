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

import json
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

_BENCH_KNOWLEDGE_DIR = pathlib.Path(__file__).resolve().parents[2] / "bench_knowledge"

#: Bundled demo profile (§6). Default when no path / env is given and it exists.
EXAMPLE_PROFILE_PATH = (
    _BENCH_KNOWLEDGE_DIR / "examples" / "bq79616-bringup-2026-05.yaml"
)

#: REAL 25E precharge board, seeded from the Altium 25E_AMB_Rev2 project
#: (bench_knowledge/seed_25e.py). Selectable via FORGE_BOARD=25e, which loads the
#: profile YAML AND auto-ingests the SchematicJSON (real designators + nets).
BOARD_25E_PROFILE_PATH = _BENCH_KNOWLEDGE_DIR / "25e-precharge-2026-05.yaml"
BOARD_25E_SCHEMATIC_PATH = _BENCH_KNOWLEDGE_DIR / "25e-precharge-2026-05-schematic.json"

#: Named board shortcuts for FORGE_BOARD. Each maps to (profile_path,
#: schematic_json_path | None). FORGE_BOARD is a convenience over BOARD_PROFILE;
#: an explicit profile_path arg or BOARD_PROFILE still wins (see _default_…).
_NAMED_BOARDS: dict[str, tuple[pathlib.Path, pathlib.Path | None]] = {
    "25e": (BOARD_25E_PROFILE_PATH, BOARD_25E_SCHEMATIC_PATH),
    "25e-precharge": (BOARD_25E_PROFILE_PATH, BOARD_25E_SCHEMATIC_PATH),
    "bq79616": (EXAMPLE_PROFILE_PATH, None),
}


def _named_board() -> tuple[pathlib.Path, pathlib.Path | None] | None:
    """Resolve the FORGE_BOARD env shortcut to (profile, schematic) paths.

    Returns None when FORGE_BOARD is unset / unknown so the caller falls back to
    BOARD_PROFILE / the bundled demo. Never raises.
    """
    name = (os.environ.get("FORGE_BOARD") or "").strip().casefold()
    if not name:
        return None
    return _NAMED_BOARDS.get(name)


def _default_profile_path() -> str | os.PathLike | None:
    """BOARD_PROFILE env > FORGE_BOARD shortcut > bundled demo fixture > None.

    BOARD_PROFILE keeps priority so an explicit YAML path always wins. A
    FORGE_BOARD shortcut (e.g. ``25e``) selects a bundled real board profile.
    """
    if os.environ.get("BOARD_PROFILE"):
        return None  # let load_board_profile read the env var itself
    named = _named_board()
    if named is not None and named[0].exists():
        return named[0]
    if EXAMPLE_PROFILE_PATH.exists():
        return EXAMPLE_PROFILE_PATH
    return None


def _load_schematic_json(path: str | os.PathLike) -> "object | None":
    """Load a seeded ``SchematicJSON`` from a JSON file, or None on any error.

    Imported lazily to avoid a knowledge<->schematic import cycle. Never raises.
    """
    try:
        from orchestrator.schematic.schema import SchematicJSON

        raw = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        return SchematicJSON.model_validate(raw)
    except Exception:  # noqa: BLE001 — degrade gracefully (01 §7)
        return None


class KnowledgeAdapter:
    """Facade over the board profile + the three lookup tools (§3).

    Construct with an explicit profile path, or omit it to use `BOARD_PROFILE`
    / the bundled demo fixture / an empty profile, in that order. An absent
    profile yields an empty one (§6) — the adapter never crashes on boot.
    """

    def __init__(self, profile_path: str | os.PathLike | None = None):
        explicit = profile_path is not None
        if profile_path is None:
            profile_path = _default_profile_path()
        self.board_profile: BoardProfile = load_board_profile(profile_path)
        self._resolver = LimitResolver(self.board_profile)
        #: Session cache of the most-recent parsed schematic (09 §5.2). Set by
        #: ingest_schematic; read by lookup_schematic so SMEs can query a parse
        #: by ref/net/part without re-running vision. Typed loosely to avoid an
        #: import cycle (schematic depends only on knowledge, not vice-versa).
        self.schematic: "object | None" = None

        # FORGE_BOARD shortcut may bundle a seeded SchematicJSON (real
        # designators + nets) alongside the profile. Auto-ingest it so
        # lookup_schematic / lookup_board_doc answer about the real board with
        # zero caller change. Skipped when a profile_path was passed explicitly
        # or BOARD_PROFILE overrides (the caller is steering, not FORGE_BOARD).
        if not explicit and not os.environ.get("BOARD_PROFILE"):
            named = _named_board()
            if named is not None and named[1] is not None and named[1].exists():
                sch = _load_schematic_json(named[1])
                if sch is not None:
                    self.ingest_schematic(sch)

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
    "BOARD_25E_PROFILE_PATH",
    "BOARD_25E_SCHEMATIC_PATH",
]

"""Documented-limit resolution — `specs/05_board_knowledge_api.md` §3.3, §4.

`get_documented_limit(target, kind)` is the one call the SafetyGate's second
layer (`03 §6`) depends on. The contract (§4):

  1. Determinism — same `(target, kind)` -> same result within a session,
     cached. No LLM in the path; it is a pure function over the profile +
     the (stubbed/cached) datasheet table.
  2. Citations — every found limit carries a non-empty `source`.
  3. Fail-safe absence — `found=False` on a miss; never silently allow.

Resolution order: `board_profile` structured limits first; for `kind="part"`
(or when a richer absolute-max is wanted) fall through to `lookup_datasheet`
for the part's absolute-maximum-ratings table.
"""

from __future__ import annotations

from pydantic import BaseModel

from orchestrator.knowledge.board_profile import BoardProfile
from orchestrator.knowledge import lookups


# ───────────────────────── result shape (§3.3) ──────────────────────────

class AbsoluteMax(BaseModel):
    voltageV: float | None = None
    currentA: float | None = None
    source: str


class DocumentedLimit(BaseModel):
    target: str
    found: bool
    maxVoltageV: float | None = None
    maxCurrentA: float | None = None
    source: str = ""  # citation; non-empty whenever found is True
    absoluteMax: AbsoluteMax | None = None


# ───────────────────── deterministic, cached resolver ────────────────────

class LimitResolver:
    """Caches `get_documented_limit` results per `(target, kind)` for the life
    of a session and counts datastore hits so determinism is testable (BK-4).
    """

    def __init__(self, profile: BoardProfile):
        self._profile = profile
        self._cache: dict[tuple[str, str], DocumentedLimit] = {}
        #: number of times we reached past the cache into the datasheet table.
        self.datastore_hits = 0

    def get(self, target: str, kind: str) -> DocumentedLimit:
        key = (target, kind)
        cached = self._cache.get(key)
        if cached is not None:
            # Return a copy so callers cannot mutate the cached instance and
            # weaken determinism (BK-4 compares value-equality across calls).
            return cached.model_copy(deep=True)

        result = self._resolve(target, kind)
        self._cache[key] = result
        return result.model_copy(deep=True)

    # ── pure resolution (no model in the loop) ──

    def _resolve(self, target: str, kind: str) -> DocumentedLimit:
        if kind == "net":
            return self._from_net(target)
        if kind == "rail":
            return self._from_rail(target)
        if kind == "part":
            return self._from_part(target)
        return DocumentedLimit(target=target, found=False)

    def _from_net(self, target: str) -> DocumentedLimit:
        n = self._profile.net(target)
        if n is None or (n.max_voltage_v is None and n.max_current_a is None):
            return DocumentedLimit(target=target, found=False)
        return DocumentedLimit(
            target=target,
            found=True,
            maxVoltageV=n.max_voltage_v,
            maxCurrentA=n.max_current_a,
            source=f"board_profile.nets[{target}]",
        )

    def _from_rail(self, target: str) -> DocumentedLimit:
        r = self._profile.rail(target)
        if r is None or (r.max_current_a is None and r.nominal_v is None):
            return DocumentedLimit(target=target, found=False)
        return DocumentedLimit(
            target=target,
            found=True,
            # rails document a current ceiling; nominal_v is informational.
            maxVoltageV=None,
            maxCurrentA=r.max_current_a,
            source=f"board_profile.rails[{target}]",
        )

    def _from_part(self, target: str) -> DocumentedLimit:
        # §3.3: kind="part" falls through to the datasheet absolute-max table.
        # (The profile carries no per-part limit field, so the datasheet is
        # the authoritative source here.)
        part = self._profile.part(target)
        ds_part = part.datasheet if (part and part.datasheet) else target

        self.datastore_hits += 1
        absmax = lookups.datasheet_absolute_max(ds_part, profile=self._profile)
        if absmax is None:
            return DocumentedLimit(target=target, found=False)

        am = AbsoluteMax(
            voltageV=absmax.get("voltageV"),
            currentA=absmax.get("currentA"),
            source=absmax["source"],
        )
        return DocumentedLimit(
            target=target,
            found=True,
            maxVoltageV=am.voltageV,
            maxCurrentA=am.currentA,
            source=am.source,
            absoluteMax=am,
        )

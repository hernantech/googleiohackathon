"""Board profile loader — `specs/05_board_knowledge_api.md` §2.

A board profile is *documentation about the board under test* (parts, rails,
nets, documented limits, test points, preconditions, procedures). It is NOT a
device driver config and nothing here actuates hardware.

The KnowledgeAdapter loads one profile at startup (from `~/.forge/board.yaml`,
the `BOARD_PROFILE` env var, or the bundled demo fixture). If the file is
absent we serve an **empty** profile rather than crashing (§2, §6): SafetyGate
then falls back to conservative defaults and forces every value-bearing step
to confirm.
"""

from __future__ import annotations

import os
import pathlib

import yaml
from pydantic import BaseModel, Field


# ───────────────────────── typed structure (§2) ──────────────────────────
# Extra YAML keys are tolerated (forward-compatible, matches proto policy).

class Part(BaseModel):
    ref: str
    part: str
    role: str | None = None
    datasheet: str | None = None


class Rail(BaseModel):
    id: str
    nominal_v: float | None = None
    max_current_a: float | None = None
    powers: list[str] = Field(default_factory=list)
    note: str | None = None


class Net(BaseModel):
    id: str
    desc: str | None = None
    max_voltage_v: float | None = None
    max_current_a: float | None = None
    test_point: str | None = None


class TestPoint(BaseModel):
    id: str
    net: str | None = None
    desc: str | None = None


class Preconditions(BaseModel):
    flash_requires_psu_off: bool = False
    rework_requires_psu_off: bool = False


class Procedure(BaseModel):
    id: str
    summary: str | None = None
    cite: str | None = None


class BoardProfile(BaseModel):
    """The static description of the board under test (§2)."""

    id: str | None = None
    description: str | None = None
    parts: list[Part] = Field(default_factory=list)
    rails: list[Rail] = Field(default_factory=list)
    nets: list[Net] = Field(default_factory=list)
    test_points: list[TestPoint] = Field(default_factory=list)
    preconditions: Preconditions = Field(default_factory=Preconditions)
    procedures: list[Procedure] = Field(default_factory=list)

    # ── convenience lookups (deterministic, used by limits/lookups) ──

    @property
    def is_empty(self) -> bool:
        return not (self.parts or self.rails or self.nets)

    def net(self, target: str) -> Net | None:
        for n in self.nets:
            if n.id == target:
                return n
        return None

    def rail(self, target: str) -> Rail | None:
        for r in self.rails:
            if r.id == target:
                return r
        return None

    def part(self, target: str) -> Part | None:
        """Match a part by component ref (U2) or by part number (BQ79616)."""
        t = target.casefold()
        for p in self.parts:
            if p.ref.casefold() == t or p.part.casefold() == t:
                return p
        return None

    def procedure(self, target: str) -> Procedure | None:
        for proc in self.procedures:
            if proc.id == target:
                return proc
        return None


# ───────────────────────── loading (§2, §6) ──────────────────────────

def _candidate_path(path: str | os.PathLike | None) -> pathlib.Path | None:
    """Resolve which YAML to load: explicit arg > BOARD_PROFILE env > None."""
    if path is not None:
        return pathlib.Path(path)
    env = os.environ.get("BOARD_PROFILE")
    if env:
        return pathlib.Path(env)
    return None


def load_board_profile(path: str | os.PathLike | None = None) -> BoardProfile:
    """Load a board profile YAML into a typed BoardProfile.

    An absent or empty file yields an *empty* profile (never raises) so the
    system boots zero-config (§6). The top-level `board_profile:` key is
    unwrapped if present.
    """
    candidate = _candidate_path(path)
    if candidate is None or not candidate.exists():
        return BoardProfile()

    try:
        raw = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return BoardProfile()

    if not raw:
        return BoardProfile()
    if isinstance(raw, dict) and "board_profile" in raw:
        raw = raw["board_profile"]
    if not isinstance(raw, dict):
        return BoardProfile()

    return BoardProfile.model_validate(raw)

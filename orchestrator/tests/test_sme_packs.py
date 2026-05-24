"""SME persona/skill pack loading + fallback (Part B).

The richer per-SME persona comes from `smes/<id>/AGENTS.md` (+ SKILL.md) when
present and falls back to the inline SME_ROLES one-liner when absent — so the
packs are a purely additive upgrade and zero-config boot is unaffected.

Deterministic + offline: the Gemini client is faked (real google.genai.types so
the scripted tool loop is shaped like the SDK). We assert the persona reaching
the model as the system instruction is the pack when one exists, and the
one-liner when it does not.
"""

from __future__ import annotations

import json

import pytest

from orchestrator import genai_seams as gs
from orchestrator.knowledge import KnowledgeAdapter
from orchestrator.proto.events import SummonGuild

genai_types = pytest.importorskip("google.genai.types")

# reuse the faked client/contents-serializer from the seam tests.
from orchestrator.tests.test_genai_seams import _FakeClient, _serialize  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_packs():
    """Each test re-loads packs (FORGE_SMES_DIR may be repointed)."""
    gs.reset_sme_packs_for_tests()
    yield
    gs.reset_sme_packs_for_tests()


# ───────────────────────── on-disk loading ─────────────────────────────────

def test_smes_dir_resolves_to_repo_packs():
    d = gs._smes_dir()
    assert d.is_dir()
    assert (d / "power" / "AGENTS.md").is_file()


def test_every_roster_sme_has_a_pack():
    """All 10 SMEs in the roster ship an AGENTS.md + SKILL.md."""
    for sme in gs.SME_ROSTER.split():
        persona = gs._sme_persona(sme)
        assert persona is not None, f"{sme} has no AGENTS.md pack"
        # the SKILL.md output-contract section is folded in (bounded).
        assert "Skill pack" in persona, f"{sme} pack missing SKILL.md"


def test_pack_persona_is_the_system_instruction(monkeypatch):
    """When a pack exists, its persona text reaches the model as the system
    instruction (not the SME_ROLES one-liner)."""
    final = json.dumps({"confidence": 0.9, "claim": "ok.", "rationale": "r"})

    captured: dict = {}

    class _CapModels:
        def generate_content(self, model, contents, config=None):
            captured["system"] = getattr(config, "system_instruction", None)
            import types as _t
            return _t.SimpleNamespace(text=final, function_calls=[], candidates=[])

    class _Cap(_FakeClient):
        def __init__(self):
            self.models = _CapModels()

    monkeypatch.setattr(gs, "_genai", lambda: _Cap())
    resp = gs.real_summon_one("@power", SummonGuild(callId="c", topic="t", smes=["@power"]))
    assert resp.claim == "ok."
    sysi = captured["system"] or ""
    # the rich pack content (a phrase unique to @power's AGENTS.md) is present...
    assert "Power Engineer" in sysi
    assert "never invent a setpoint" in sysi.lower()
    # ...and the standing-instructions framing is appended.
    assert "advising a HUMAN operator" in sysi


def test_pack_loading_is_cached(monkeypatch):
    """The pack is read from disk once and cached (module-level)."""
    calls = {"n": 0}
    real_build = gs._build_persona

    def counting(sme_id):
        calls["n"] += 1
        return real_build(sme_id)

    monkeypatch.setattr(gs, "_build_persona", counting)
    a = gs._sme_persona("@signal")
    b = gs._sme_persona("@signal")
    assert a == b and a is not None
    assert calls["n"] == 1  # built once, cached thereafter


# ───────────────────────── fallback when no pack ───────────────────────────

def test_falls_back_to_one_liner_when_no_pack(monkeypatch, tmp_path):
    """With FORGE_SMES_DIR pointed at an empty dir, real_summon_one uses the
    inline SME_ROLES one-liner — zero-config / no-pack boot still works."""
    monkeypatch.setenv("FORGE_SMES_DIR", str(tmp_path))  # no packs here
    gs.reset_sme_packs_for_tests()
    assert gs._sme_persona("@power") is None

    final = json.dumps({"confidence": 0.8, "claim": "fallback.", "rationale": "r"})

    captured: dict = {}

    class _CapModels:
        def generate_content(self, model, contents, config=None):
            captured["system"] = getattr(config, "system_instruction", None)
            import types as _t
            return _t.SimpleNamespace(text=final, function_calls=[], candidates=[])

    class _Cap(_FakeClient):
        def __init__(self):
            self.models = _CapModels()

    monkeypatch.setattr(gs, "_genai", lambda: _Cap())
    resp = gs.real_summon_one("@power", SummonGuild(callId="c", topic="t", smes=["@power"]))
    assert resp.claim == "fallback."
    sysi = captured["system"] or ""
    # the inline one-liner role, NOT the rich pack.
    assert "Power Engineer" in sysi          # the SME_ROLES one-liner role
    assert "Skill pack" not in sysi          # no SKILL.md folded in (no pack)
    assert "## Role" not in sysi             # not the AGENTS.md markdown


def test_missing_agents_file_is_none(monkeypatch, tmp_path):
    """A dir with only SKILL.md (no AGENTS.md) → no persona (one-liner path)."""
    (tmp_path / "power").mkdir()
    (tmp_path / "power" / "SKILL.md").write_text("skill only", encoding="utf-8")
    monkeypatch.setenv("FORGE_SMES_DIR", str(tmp_path))
    gs.reset_sme_packs_for_tests()
    assert gs._sme_persona("@power") is None


def test_build_persona_swallows_read_errors(monkeypatch, tmp_path):
    """The production _build_persona never raises on a bad pack — it returns
    None so the one-liner is used (01 §7 never-fail-stop)."""
    bad = tmp_path / "power"
    bad.mkdir()
    agents = bad / "AGENTS.md"
    agents.write_text("# @power", encoding="utf-8")
    monkeypatch.setenv("FORGE_SMES_DIR", str(tmp_path))

    # make read_text blow up to simulate a corrupt/locked file.
    import pathlib
    orig = pathlib.Path.read_text

    def maybe_boom(self, *a, **k):
        if self.name == "AGENTS.md":
            raise OSError("locked")
        return orig(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", maybe_boom)
    assert gs._build_persona("@power") is None  # degraded, did not raise

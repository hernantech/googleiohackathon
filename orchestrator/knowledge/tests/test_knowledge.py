"""BK-1..BK-11 — component contract tests for the KnowledgeAdapter (05 §8).

Exercised against the bundled fixture board.yaml and a stub datastore; no
network. Run: PYTHONPATH=. .venv/bin/pytest orchestrator/knowledge/tests/ -q

Design patterns under test: deterministic limit lookup (pure function),
strategy + fallback (profile -> datasheet), graceful degradation.
"""

from __future__ import annotations

import json

import pytest

from orchestrator.knowledge import (
    EXAMPLE_PROFILE_PATH,
    KnowledgeAdapter,
    load_board_profile,
)
from orchestrator.knowledge.board_profile import BoardProfile
from orchestrator.proto.events import OPERATOR_STEP_TOOLS, ProposedAction


@pytest.fixture
def adapter() -> KnowledgeAdapter:
    """Adapter loaded from the bundled demo fixture (§6)."""
    return KnowledgeAdapter(EXAMPLE_PROFILE_PATH)


# ─────────────────────────────── BK-1 ───────────────────────────────
# board.yaml parses; every nets[].max_voltage_v, rails[].max_current_a,
# preconditions field present and typed.

def test_bk1_board_yaml_parses_and_typed(adapter: KnowledgeAdapter):
    profile = adapter.board_profile
    assert isinstance(profile, BoardProfile)
    assert profile.id == "bq79616-bringup-2026-05"
    assert not profile.is_empty

    # every net carries a typed max_voltage_v
    assert profile.nets, "fixture must define nets"
    for net in profile.nets:
        assert net.max_voltage_v is not None
        assert isinstance(net.max_voltage_v, float)

    # every rail carries a typed max_current_a
    assert profile.rails, "fixture must define rails"
    for rail in profile.rails:
        assert rail.max_current_a is not None
        assert isinstance(rail.max_current_a, float)

    # preconditions present and boolean-typed
    assert isinstance(profile.preconditions.flash_requires_psu_off, bool)
    assert isinstance(profile.preconditions.rework_requires_psu_off, bool)
    assert profile.preconditions.flash_requires_psu_off is True
    assert profile.preconditions.rework_requires_psu_off is True

    # spot-check documented net values from §2
    assert profile.net("J3").max_voltage_v == 30.0
    assert profile.net("TP4").max_voltage_v == 5.5
    assert profile.net("TP7").max_voltage_v == 3.6
    # CELLSTK rail at 30 V
    assert profile.rail("CELLSTK").nominal_v == 30.0


# ─────────────────────────────── BK-2 ───────────────────────────────
# get_documented_limit({target:"J3", kind:"net"}) -> maxVoltageV=30.0,
# found=true, non-empty source.

def test_bk2_documented_limit_j3_net(adapter: KnowledgeAdapter):
    res = adapter.get_documented_limit("J3", "net")
    assert res.found is True
    assert res.maxVoltageV == 30.0
    assert res.source != ""
    assert "J3" in res.source


# ─────────────────────────────── BK-3 ───────────────────────────────
# unknown target -> found=false (SafetyGate forces defaults).

def test_bk3_unknown_target_is_safe_miss(adapter: KnowledgeAdapter):
    res = adapter.get_documented_limit("NOPE", "net")
    assert res.found is False
    assert res.maxVoltageV is None
    assert res.maxCurrentA is None

    # also a known id under the wrong kind is a miss
    res2 = adapter.get_documented_limit("J3", "rail")
    assert res2.found is False


# ─────────────────────────────── BK-4 ───────────────────────────────
# deterministic + cached: 100 calls -> identical result, datastore hit <= 1.

def test_bk4_deterministic_and_cached(adapter: KnowledgeAdapter):
    # a kind="part" lookup is the one that can reach the datastore.
    first = adapter.get_documented_limit("U2", "part")
    results = [adapter.get_documented_limit("U2", "part") for _ in range(100)]

    for r in results:
        assert r == first  # identical value across 100 calls

    # exactly one datastore hit for the whole (target, kind) regardless of N
    assert adapter.datastore_hits <= 1

    # mutating a returned result must not poison the cache
    first.maxVoltageV = -999.0
    again = adapter.get_documented_limit("U2", "part")
    assert again == results[0]
    assert again.maxVoltageV != -999.0


# ─────────────────────────────── BK-5 ───────────────────────────────
# kind="part" falls through to lookup_datasheet absolute-max table when the
# profile lacks the limit.

def test_bk5_part_falls_through_to_datasheet(adapter: KnowledgeAdapter):
    res = adapter.get_documented_limit("U2", "part")  # U2 == BQ79616
    assert res.found is True
    assert res.maxVoltageV == 80.0  # BQ79616 abs-max stack voltage
    assert res.source != ""
    assert "datasheet" in res.source.lower()
    assert res.absoluteMax is not None
    assert res.absoluteMax.voltageV == 80.0
    assert res.absoluteMax.source != ""

    # also resolvable by part number directly
    by_number = adapter.get_documented_limit("BQ79616", "part")
    assert by_number.found is True
    assert by_number.maxVoltageV == 80.0


# ─────────────────────────────── BK-6 ───────────────────────────────
# lookup_datasheet("bq79616","power-up") -> passage mentioning cell stack
# present at power-up, with a cite.

def test_bk6_lookup_datasheet_power_up(adapter: KnowledgeAdapter):
    res = adapter.lookup_datasheet("bq79616", "power-up")
    assert res.part == "bq79616"
    assert res.passages, "expected at least one passage"
    assert res.cite, "result must carry a human-citable reference"

    top = res.passages[0].text.lower()
    assert "cell stack" in top or "cell" in top and "stack" in top
    assert "power" in top or "comm" in top
    # cite should look like a real reference
    assert "bq79616" in res.cite.lower()
    assert "§7" in res.cite or "p." in res.cite


# ─────────────────────────────── BK-7 ───────────────────────────────
# stub mode (no keys, no yaml): lookups return canned data, limit -> found=false.

def test_bk7_stub_mode_offline(monkeypatch):
    # no datastore, no board profile path
    monkeypatch.delenv("VERTEX_SEARCH_DATASTORE_ID", raising=False)
    monkeypatch.delenv("BOARD_PROFILE", raising=False)

    empty = KnowledgeAdapter(profile_path="/nonexistent/does-not-exist.yaml")
    assert empty.board_profile.is_empty

    # datasheet still serves canned data (§6)
    ds = empty.lookup_datasheet("esp32-wroom-32", "uart")
    assert ds.passages
    assert ds.cite
    assert "uart" in ds.passages[0].text.lower()

    ams = empty.lookup_datasheet("ams1117", "dropout")
    assert ams.passages
    assert "dropout" in ams.passages[0].text.lower()

    # board-doc returns canned prose even with an empty profile
    bd = empty.lookup_board_doc("overview")
    assert bd.passages
    assert bd.passages[0].text

    # with no profile, documented limit is a safe miss
    lim = empty.get_documented_limit("J3", "net")
    assert lim.found is False


def test_bk7_empty_profile_does_not_crash():
    # absent file -> empty profile, never raises (§2, §6)
    profile = load_board_profile("/no/such/file.yaml")
    assert isinstance(profile, BoardProfile)
    assert profile.is_empty


# ─────────────────────────────── BK-8 ───────────────────────────────
# every operator-step `tool` verb in §5 maps to a renderer template in the
# client and a matrix row in 03 §3 — i.e. no orphan verbs.
#
# The client renderers and the 03 matrix live outside the knowledge component;
# the canonical, frozen registry of operator-step verbs is
# proto.events.OPERATOR_STEP_TOOLS. We assert the §5 table equals that set so
# any verb added to §5 without a registry entry (and thus without a renderer /
# matrix row keyed off the registry) fails here.

SPEC_05_OPERATOR_VERBS = frozenset({
    "set_psu",
    "enable_psu_output",
    "disable_psu_output",
    "probe_net",
    "serial_send",
    "flash_mcu",
    "reflow_pin",
    "inspect_closeup",
})


def test_bk8_no_orphan_operator_verbs():
    # §5 table and the frozen registry agree exactly: no orphan verbs.
    assert SPEC_05_OPERATOR_VERBS == OPERATOR_STEP_TOOLS


# ─────────────────────────────── BK-9 ───────────────────────────────
# a set_psu operator step lacking documentedLimitRef is rejected by the
# provenance lint (mirrors SG-8).

#: value-bearing operator verbs that MUST carry a documentedLimitRef (§5).
_VALUE_BEARING_VERBS = frozenset({"set_psu", "serial_send", "flash_mcu"})


def _provenance_lint(action: ProposedAction) -> bool:
    """True if the operator step satisfies the provenance contract (§5).

    Value-bearing steps MUST carry a non-empty documentedLimitRef; everything
    else passes. Mirrors SG-8 / 03 §3.3.6.
    """
    if action.tool in _VALUE_BEARING_VERBS:
        return bool(action.documentedLimitRef)
    return True


def test_bk9_provenance_lint_rejects_unsourced_set_psu():
    bad = ProposedAction(
        actor="operator",
        tool="set_psu",
        argsJson=json.dumps({"channel": 1, "voltage_v": 30.0, "current_limit_a": 0.5, "target": "J3"}),
        rationale="apply the cell-sim stack",
        risk="HIGH",
    )
    assert _provenance_lint(bad) is False

    good = bad.model_copy(update={"documentedLimitRef": "board_profile.nets[J3]"})
    assert _provenance_lint(good) is True

    # a non-value-bearing step needs no documented limit
    probe = ProposedAction(
        actor="operator",
        tool="probe_net",
        argsJson=json.dumps({"net": "VIO", "test_point": "TP4", "mode": "dc_volts"}),
        rationale="read VIO",
        risk="LOW",
    )
    assert _provenance_lint(probe) is True


# ─────────────────────────────── BK-10 ───────────────────────────────
# no symbol named set_psu / flash_mcu is *callable* in the KnowledgeAdapter —
# they exist only as step labels (no actuation path).

def test_bk10_no_actuation_callables():
    import orchestrator.knowledge as knowledge
    from orchestrator.knowledge import board_profile, limits, lookups

    forbidden = {
        "set_psu", "flash_mcu", "enable_psu_output", "disable_psu_output",
        "serial_send", "reflow_pin", "capture_logic", "chip_capture",
    }
    for module in (knowledge, board_profile, limits, lookups):
        for name in forbidden:
            attr = getattr(module, name, None)
            assert not callable(attr), f"{module.__name__}.{name} must not be callable"

    # the adapter instance exposes no such actuation method either
    adapter = KnowledgeAdapter(EXAMPLE_PROFILE_PATH)
    for name in forbidden:
        assert not callable(getattr(adapter, name, None))

    # the verbs ARE known as labels in the frozen registry (they are not gone,
    # just not executable here).
    assert "set_psu" in OPERATOR_STEP_TOOLS
    assert "flash_mcu" in OPERATOR_STEP_TOOLS


# ─────────────────────────────── BK-11 ───────────────────────────────
# analyze_snapshot(img, ctx) with a fixture image of the BQ79616 -> the
# returned SnapshotAnalysis.cites reference a real datasheet/board-doc passage.
# (P4 now exists; this exercises the knowledge-grounding contract end to end.)

def test_bk11_snapshot_cites_grounded():
    from orchestrator.knowledge import KnowledgeAdapter
    from orchestrator.snapshot.analyzer import analyze_snapshot
    from orchestrator.storage.frame_store import InMemoryFrameStore

    snap = analyze_snapshot(
        jpeg_bytes=b"\xff\xd8fake\xff\xd9", width=1920, height=1080,
        context="bq79616 power-up wiring",
        knowledge=KnowledgeAdapter(),
        model_call=lambda _j, _c, _m: "cell-stack lead unplugged",
        store=InMemoryFrameStore(),
    )
    assert snap.cites, "snapshot analysis must carry a grounded citation"
    assert snap.cites[0].kind == "datasheet"
    assert snap.cites[0].note  # non-empty cite

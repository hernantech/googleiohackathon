"""BOM-12..BOM-24 — component contract tests for lookup_bom (BOM knowledge).

All tests run offline (no network, no datastore). The BOM YAML is loaded from
bench_knowledge/bq79616-bringup-bom.yaml which is version-controlled.

Key limitation documented by every test where relevant:
    Designators are INFERRED (designator_inferred=True) — the source BOM export
    had no Ref Des column. Ref-based lookups work for the demo but are not
    authoritative. See bom.py module docstring for full explanation.
"""

from __future__ import annotations

import pytest

from orchestrator.knowledge import KnowledgeAdapter
from orchestrator.knowledge.bom import BomResult, lookup_bom


# ─────────────────────────── BOM-12: basic smoke ─────────────────────────────
# lookup_bom is callable via KnowledgeAdapter and returns a BomResult.

def test_bom12_adapter_exposes_lookup_bom():
    adapter = KnowledgeAdapter()
    result = adapter.lookup_bom("resistor")
    assert isinstance(result, BomResult)
    assert result.matches, "expected at least one resistor match"
    assert result.cite, "result must carry a cite"


# ─────────────────────────── BOM-13: value queries ───────────────────────────
# "4.99k" matches the 4.9k line (value: 4.99kΩ, package: 0805, 2× units).
# "100k" matches the 100kΩ 2512 line (6× units).
# "0.1uF" matches both 0603 and 0402 0.1uF caps.

def test_bom13_value_query_4k99():
    r = lookup_bom("4.99k")
    assert r.matches, "4.99k should match the 4.9k/4.99kΩ resistor line"
    top = r.matches[0]
    assert top.type == "resistor"
    assert top.value is not None and ("4.99" in top.value or "4.9" in top.value)
    assert top.package == "0805"
    assert top.mpn == "CRCW08054K99FKEA"
    # Both units of the ×2 line should appear
    des_set = {m.designator for m in r.matches if m.mpn == "CRCW08054K99FKEA"}
    assert len(des_set) == 2, f"expected R10 and R11, got {des_set}"


def test_bom13_value_query_100k():
    r = lookup_bom("100k")
    assert r.matches
    top = r.matches[0]
    assert top.type == "resistor"
    assert top.value == "100kΩ"
    assert top.package == "2512"
    # 6 units → R1..R6
    des_set = {m.designator for m in r.matches if m.mpn == "CRCW2512100KFKEG"}
    assert len(des_set) == 6, f"expected 6 units (R1-R6), got {des_set}"


def test_bom13_value_query_0_1uF():
    r = lookup_bom("0.1uF")
    assert r.matches
    types = {m.type for m in r.matches}
    assert "capacitor" in types
    # Should find both the 0603 and 0402 0.1uF caps
    mpns = {m.mpn for m in r.matches}
    assert "C0603C104K3RACTU" in mpns or "GRM155R71C104KA88D" in mpns


# ─────────────────────────── BOM-14: type queries ────────────────────────────
# "comparator" → LM2903AVQDRQ1 (×2 units, SOIC-8).
# "regulator"  → NCP785AH120T1G (×4) + UA78L05CPK (×1).
# "fuse"       → Littelfuse 0452.500MRL + 0885001.DR.

def test_bom14_type_comparator():
    r = lookup_bom("comparator")
    assert r.matches
    comp_matches = [m for m in r.matches if m.type == "comparator"]
    assert comp_matches, "comparator type query must find comparator items"
    mpns = {m.mpn for m in comp_matches}
    assert "LM2903AVQDRQ1" in mpns
    pkgs = {m.package for m in comp_matches if m.package}
    assert any("soic" in p.lower() or "8" in p for p in pkgs)


def test_bom14_type_regulator():
    r = lookup_bom("regulator")
    assert r.matches
    reg_matches = [m for m in r.matches if m.type == "regulator"]
    assert reg_matches
    mpns = {m.mpn for m in reg_matches}
    assert "NCP785AH120T1G" in mpns
    assert "UA78L05CPK" in mpns


def test_bom14_type_fuse():
    r = lookup_bom("fuse")
    assert r.matches
    fuse_matches = [m for m in r.matches if m.type == "fuse"]
    assert fuse_matches
    mpns = {m.mpn for m in fuse_matches}
    assert "0452.500MRL" in mpns or "0885001.DR" in mpns


# ─────────────────────────── BOM-15: MPN queries ─────────────────────────────
# Exact MPN and partial MPN prefix both resolve.

def test_bom15_mpn_exact_NCP785():
    r = lookup_bom("NCP785AH120T1G")
    assert r.matches
    top = r.matches[0]
    assert top.mpn == "NCP785AH120T1G"
    assert top.type == "regulator"
    assert top.value == "12V"
    # 4 units → U1..U4
    des_set = {m.designator for m in r.matches if m.mpn == "NCP785AH120T1G"}
    assert len(des_set) == 4, f"expected U1-U4, got {des_set}"


def test_bom15_mpn_partial_NCP785():
    r = lookup_bom("NCP785")
    assert r.matches
    assert any(m.mpn == "NCP785AH120T1G" for m in r.matches)


def test_bom15_mpn_LM2903():
    r = lookup_bom("LM2903AVQDRQ1")
    assert r.matches
    top = r.matches[0]
    assert top.mpn == "LM2903AVQDRQ1"
    assert top.type == "comparator"


# ─────────────────────────── BOM-16: designator queries ──────────────────────
# Inferred designators: R5 → 100kΩ 2512, C1 → 0.1uF 0603, U5 → comparator.
# Every matched item must carry designator_inferred=True.

def test_bom16_designator_R5():
    r = lookup_bom("R5")
    assert r.matches, "R5 should resolve to a resistor"
    top = r.matches[0]
    assert top.designator == "R5"
    assert top.designator_inferred is True
    assert top.type == "resistor"
    assert top.value == "100kΩ"
    assert top.package == "2512"
    assert top.mpn == "CRCW2512100KFKEG"


def test_bom16_designator_C1():
    r = lookup_bom("C1")
    assert r.matches
    top = r.matches[0]
    assert top.designator == "C1"
    assert top.designator_inferred is True
    assert top.type == "capacitor"
    assert top.value == "0.1uF"
    assert top.package == "0603"


def test_bom16_designator_U5():
    r = lookup_bom("U5")
    assert r.matches
    top = r.matches[0]
    assert top.designator == "U5"
    assert top.designator_inferred is True
    assert top.type == "comparator"
    assert top.mpn == "LM2903AVQDRQ1"


def test_bom16_designator_TP1():
    r = lookup_bom("TP1")
    assert r.matches
    top = r.matches[0]
    assert top.designator == "TP1"
    assert top.type == "test-point"


def test_bom16_designator_F1():
    r = lookup_bom("F1")
    assert r.matches
    top = r.matches[0]
    assert top.designator == "F1"
    assert top.type == "fuse"
    assert top.mpn == "0452.500MRL"


def test_bom16_designator_K1():
    r = lookup_bom("K1")
    assert r.matches
    top = r.matches[0]
    assert top.designator == "K1"
    assert top.type == "relay"
    assert top.mpn == "G3VM-61G2"


# ─────────────────────────── BOM-17: name queries ───────────────────────────
# The BOM Name column contains shorthand like "4.9k", "SSR", "Comparator".

def test_bom17_name_SSR():
    r = lookup_bom("SSR")
    assert r.matches
    top = r.matches[0]
    assert top.name == "SSR"
    assert top.type == "relay"
    assert top.mpn == "G3VM-61G2"


def test_bom17_name_866():
    r = lookup_bom("866")
    assert r.matches
    res_matches = [m for m in r.matches if "866" in (m.value or "") or m.name == "866"]
    assert res_matches
    assert res_matches[0].type == "resistor"


# ─────────────────────────── BOM-18: description keyword ─────────────────────

def test_bom18_description_keyword_slo_blo():
    r = lookup_bom("slo-blo")
    assert r.matches
    assert any("Slo-Blo" in m.description for m in r.matches)


def test_bom18_description_keyword_thick_film():
    r = lookup_bom("thick film")
    assert r.matches
    types = {m.type for m in r.matches}
    assert "resistor" in types


# ─────────────────────────── BOM-19: result shape ────────────────────────────
# Every match has required fields, score > 0, cite non-empty.

def test_bom19_result_shape():
    r = lookup_bom("capacitor")
    assert isinstance(r, BomResult)
    assert r.cite
    assert r.note
    for m in r.matches:
        assert m.designator
        assert isinstance(m.designator_inferred, bool)
        assert m.type
        assert m.score > 0
        assert m.cite
        assert isinstance(m.unit_index, int)
        assert isinstance(m.quantity, int) and m.quantity >= 1


# ─────────────────────────── BOM-20: ranking ─────────────────────────────────
# Exact designator match scores higher than a description keyword hit.
# Exact MPN match scores higher than a partial name hit.

def test_bom20_ranking_designator_beats_keyword():
    """R5 by exact designator should beat a description-only match for 'R5'."""
    r = lookup_bom("R5")
    assert r.matches
    top = r.matches[0]
    assert top.designator == "R5"  # exact designator must win


def test_bom20_ranking_mpn_beats_generic():
    r = lookup_bom("CRCW2512100KFKEG")
    assert r.matches
    # All top results should be the 100k 2512 resistor
    assert all(m.mpn == "CRCW2512100KFKEG" for m in r.matches[:6])


# ─────────────────────────── BOM-21: graceful empty query ───────────────────
# Empty / whitespace / unknown query → clean empty result, never raises.

def test_bom21_empty_query():
    r = lookup_bom("")
    assert isinstance(r, BomResult)
    assert r.matches == []
    assert r.cite


def test_bom21_whitespace_query():
    r = lookup_bom("   ")
    assert isinstance(r, BomResult)
    assert r.matches == []


def test_bom21_unknown_part_query():
    r = lookup_bom("XYZZY_NONEXISTENT_PART_ABC123")
    assert isinstance(r, BomResult)
    assert r.matches == []
    assert r.cite


# ─────────────────────────── BOM-22: adapter passthrough ────────────────────
# KnowledgeAdapter.lookup_bom delegates correctly.

def test_bom22_adapter_passthrough():
    adapter = KnowledgeAdapter()
    r = adapter.lookup_bom("NCP785AH120T1G")
    assert isinstance(r, BomResult)
    assert r.matches
    assert r.matches[0].mpn == "NCP785AH120T1G"


# ─────────────────────────── BOM-23: qty and unit integrity ─────────────────
# Each physical unit from a ×N BOM line has a unique designator but shared qty.

def test_bom23_quantity_and_unit_integrity():
    """All units from the 6× 100kΩ line share qty=6 but have unique designators."""
    r = lookup_bom("CRCW2512100KFKEG")
    units = [m for m in r.matches if m.mpn == "CRCW2512100KFKEG"]
    assert len(units) == 6
    # All report qty=6 (that's the BOM line quantity, for context)
    assert all(u.quantity == 6 for u in units)
    # All designators unique
    des_list = [u.designator for u in units]
    assert len(set(des_list)) == 6
    # unit_index 0..5
    idx_set = {u.unit_index for u in units}
    assert idx_set == {0, 1, 2, 3, 4, 5}


# ─────────────────────────── BOM-24: no actuation callables ─────────────────
# bom.py must not expose any callable that actuates hardware.

def test_bom24_no_actuation_callables():
    import orchestrator.knowledge.bom as bom_mod
    forbidden = {"set_psu", "flash_mcu", "enable_psu_output", "disable_psu_output",
                 "serial_send", "reflow_pin", "capture_logic"}
    for name in forbidden:
        attr = getattr(bom_mod, name, None)
        assert not callable(attr), f"bom.{name} must not be callable"

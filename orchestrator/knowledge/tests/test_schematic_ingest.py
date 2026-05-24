"""KnowledgeAdapter.ingest_schematic — additive merge + the existing lookups
answering from a parsed schematic, with the safety guard that a guessed value
never becomes a documented limit (spec 09 §5.3, §6 steps 6-8; 03 §3.3.6, 05 §4).

Offline; the SchematicJSON is built directly (no vision).
"""

from __future__ import annotations

from orchestrator.knowledge import EXAMPLE_PROFILE_PATH, KnowledgeAdapter
from orchestrator.schematic.schema import SchematicJSON


def _sch() -> SchematicJSON:
    """A parsed schematic carrying a NEW net (VSENSE, with a nominalVGuess) and a
    NEW part (Q1), plus a net that re-states a documented one (3V3 — a rail)."""
    return SchematicJSON.model_validate({
        "schematicId": "bq79616-bringup-2026-05",
        "source": {"kind": "image", "uri": "snapshot://f1", "model": "gemini-3-pro-preview"},
        "confidence": 0.71,
        "components": [
            # U4 is already a documented part — must NOT be overwritten.
            {"ref": "U4", "part": "AMS1117-3.3", "type": "regulator"},
            # Q1 is new — should be minted source="schematic_image".
            {"ref": "Q1", "part": "2N7002", "type": "transistor",
             "pins": [{"pin": "1", "name": "G", "net": "VSENSE"}]},
        ],
        "nets": [
            {"id": "VSENSE", "nodes": [{"ref": "Q1", "pin": "1"}],
             "classGuess": "signal", "nominalVGuess": 3.3},
            {"id": "3V3", "nodes": [{"ref": "U4", "pin": "2"}],
             "classGuess": "power", "nominalVGuess": 3.3},
        ],
        "warnings": [],
        "cite": "schematic image (operator upload) · gemini-3-pro-preview · 2026-05-23",
    })


def test_ingest_merges_additively_and_marks_source():
    ka = KnowledgeAdapter(EXAMPLE_PROFILE_PATH)
    n_parts, n_nets = len(ka.board_profile.parts), len(ka.board_profile.nets)

    counts = ka.ingest_schematic(_sch())

    # U4 already documented → not re-added; Q1 is new.
    assert counts["parts_added"] == 1
    q1 = ka.board_profile.part("Q1")
    assert q1 is not None and q1.source == "schematic_image"
    # documented U4 untouched (no schematic_image marker, datasheet preserved)
    u4 = ka.board_profile.part("U4")
    assert u4.source is None and u4.datasheet == "ams1117"
    # nets: VSENSE + 3V3 both new as NETS (3V3 was only a rail before)
    assert counts["nets_added"] == 2
    vsense = ka.board_profile.net("VSENSE")
    assert vsense is not None and vsense.source == "schematic_image"
    assert len(ka.board_profile.parts) == n_parts + 1
    assert len(ka.board_profile.nets) == n_nets + 2


def test_ingest_is_idempotent():
    ka = KnowledgeAdapter(EXAMPLE_PROFILE_PATH)
    ka.ingest_schematic(_sch())
    parts1, nets1 = len(ka.board_profile.parts), len(ka.board_profile.nets)
    counts = ka.ingest_schematic(_sch())  # again
    assert counts == {"parts_added": 0, "nets_added": 0,
                      "cite": _sch().cite}
    assert len(ka.board_profile.parts) == parts1
    assert len(ka.board_profile.nets) == nets1


def test_existing_lookup_board_doc_answers_from_parsed_schematic():
    """After ingest, the EXISTING lookup_board_doc surfaces a parsed net in
    profileMatches with ZERO SME-side change (09 §5.3)."""
    ka = KnowledgeAdapter(EXAMPLE_PROFILE_PATH)
    # before ingest: VSENSE is unknown to the profile
    assert ka.board_profile.net("VSENSE") is None
    ka.ingest_schematic(_sch())

    res = ka.lookup_board_doc("VSENSE")
    ids = [(m.kind, m.id) for m in res.profileMatches]
    assert ("net", "VSENSE") in ids
    # and the matched net is marked schematic-derived
    match = next(m for m in res.profileMatches if m.id == "VSENSE")
    assert match.data.get("source") == "schematic_image"


def test_guessed_value_is_never_promoted_to_a_documented_limit():
    """SAFETY GUARD (03 §3.3.6 / 05 §4): a parsed nominalVGuess must NEVER become
    a documented limit. A schematic-only net resolves found=False; the documented
    YAML limits are untouched."""
    ka = KnowledgeAdapter(EXAMPLE_PROFILE_PATH)
    ka.ingest_schematic(_sch())

    # VSENSE had nominalVGuess=3.3 in the parse → still NO documented limit.
    lim = ka.get_documented_limit("VSENSE", "net")
    assert lim.found is False
    assert lim.maxVoltageV is None and lim.source == ""

    # the merged net carries no limit fields at all.
    vsense = ka.board_profile.net("VSENSE")
    assert vsense.max_voltage_v is None and vsense.max_current_a is None

    # documented nets keep their documented limits, unaffected by the parse.
    j3 = ka.get_documented_limit("J3", "net")
    assert j3.found and j3.maxVoltageV == 30.0 and j3.source == "board_profile.nets[J3]"


def test_lookup_schematic_returns_subset_with_cite():
    ka = KnowledgeAdapter(EXAMPLE_PROFILE_PATH)
    # nothing cached yet
    empty = ka.lookup_schematic("Q1")
    assert empty["components"] == [] and "no schematic" in empty["note"].lower()

    ka.ingest_schematic(_sch())

    by_ref = ka.lookup_schematic("Q1")
    assert [c["ref"] for c in by_ref["components"]] == ["Q1"]
    assert by_ref["cite"]  # provenance

    by_net = ka.lookup_schematic("VSENSE")
    assert "VSENSE" in [n["id"] for n in by_net["nets"]]
    # the component touching VSENSE is also surfaced (pin↔net match)
    assert "Q1" in [c["ref"] for c in by_net["components"]]
    # advisory note present so a consumer treats it as model-derived
    assert "advisory" in by_net["note"].lower()

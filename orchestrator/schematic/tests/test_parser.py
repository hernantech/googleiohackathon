"""Schematic → JSON parser/schema/normalizer tests (spec 09 §6 steps 1-3).

All offline: the vision `model_call` is a fake (no network), exactly the
injected-`ModelCall` pattern of orchestrator/snapshot/analyzer.py. A `@live`
variant for the real Gemini call lives elsewhere / is excluded from CI.
"""

from __future__ import annotations

import json

from orchestrator.schematic.normalize import categorise, normalise_header, normalize
from orchestrator.schematic.parser import DEFAULT_SCHEMATIC_MODEL, parse_schematic
from orchestrator.schematic.schema import SchematicJSON

# spec 09 §2 representative output (the power section of the demo board).
SPEC_EXAMPLE = {
    "schematicId": "bq79616-bringup-2026-05",
    "source": {"kind": "image", "uri": "snapshot://f1", "model": "gemini-3-pro-preview"},
    "confidence": 0.78,
    "components": [
        {"ref": "U4", "part": "AMS1117-3.3", "type": "regulator", "value": "3.3V",
         "package": "SOT-223", "description": "fixed 3.3V LDO", "sheet": "power",
         "pins": [
             {"pin": "1", "name": "GND", "net": "GND"},
             {"pin": "2", "name": "VOUT", "net": "3V3"},
             {"pin": "3", "name": "VIN", "net": "VIN_5V"},
         ]},
        {"ref": "U1", "part": "ESP32-WROOM-32", "type": "ic", "value": None,
         "package": "module", "description": "host MCU", "sheet": "digital",
         "pins": [{"pin": "2", "name": "3V3", "net": "3V3"},
                  {"pin": "1", "name": "GND", "net": "GND"}]},
    ],
    "nets": [
        {"id": "3V3", "nodes": [{"ref": "U4", "pin": "2"}, {"ref": "U1", "pin": "2"}],
         "classGuess": "power", "nominalVGuess": 3.3},
        {"id": "VIN_5V", "nodes": [{"ref": "U4", "pin": "3"}], "classGuess": "power"},
        {"id": "GND", "nodes": [{"ref": "U4", "pin": "1"}, {"ref": "U1", "pin": "1"}],
         "classGuess": "ground"},
    ],
    "warnings": ["pin numbers for U1 partially obscured; nets inferred from labels"],
    "cite": "schematic image (operator upload) · gemini-3-pro-preview · 2026-05-23",
}

_JPEG = b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9"


def _fake_model(returns: str):
    def _call(image_bytes, mime, hint, model_name):
        return returns
    return _call


# ── §6.1 schema round-trip ────────────────────────────────────────────────

def test_schema_round_trips_spec_example():
    sch = SchematicJSON.model_validate(SPEC_EXAMPLE)
    assert sch.components[0].ref == "U4"
    assert sch.components[0].pins[1].net == "3V3"
    assert sch.nets[0].id == "3V3" and sch.nets[0].nominalVGuess == 3.3
    # forward-compatible: re-dumping and reloading is lossless on known fields
    again = SchematicJSON.model_validate(sch.model_dump())
    assert again.nets[2].classGuess == "ground"


def test_schema_rejects_missing_ref():
    bad = dict(SPEC_EXAMPLE)
    bad["components"] = [{"part": "AMS1117-3.3"}]  # no ref
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SchematicJSON.model_validate(bad)


def test_schema_tolerates_extra_keys():
    payload = dict(SPEC_EXAMPLE)
    payload["futureField"] = {"anything": 1}
    payload["components"] = [dict(SPEC_EXAMPLE["components"][0], surprise="ok")]
    sch = SchematicJSON.model_validate(payload)  # extra="allow" → no raise
    assert sch.components[0].ref == "U4"


# ── §6.2 normalizer (ported cloud _PREFIX_CATEGORY / _HEADER_MAP) ──────────

def test_categorise_from_refdes_prefix():
    assert categorise("U4") == "ic"
    assert categorise("R5") == "resistor"
    assert categorise("J3") == "connector"
    assert categorise("C12") == "capacitor"
    assert categorise("TP4") == "test_point"
    assert categorise("D2") == "diode"
    assert categorise("SW1") == "switch"
    assert categorise("Z9") == "other"


def test_normalise_header_maps_bom_columns():
    assert normalise_header("Reference") == "ref"
    assert normalise_header("Comment") == "value"
    assert normalise_header("Footprint") == "package"
    assert normalise_header("Mfr Part Number") == "part"
    assert normalise_header("nonsense column") is None


def test_normalize_fills_missing_type_and_tidies_values():
    payload = {
        "source": {"kind": "image", "model": "m"},
        "components": [
            {"ref": "R5", "value": "  4.99k  "},   # type blank → resistor
            {"ref": "U4", "type": "regulator", "value": "3.3V"},  # type preserved
        ],
        "nets": [],
        "cite": "x",
    }
    sch = normalize(SchematicJSON.model_validate(payload))
    assert sch.components[0].type == "resistor"   # filled from prefix
    assert sch.components[0].value == "4.99k"      # whitespace tidied
    assert sch.components[1].type == "regulator"   # model value preserved


# ── §6.3 parse_schematic: validate → normalize → stamp provenance ──────────

def test_parse_validates_and_normalizes_stubbed_model():
    raw = json.dumps({
        "source": {"kind": "image", "model": "x"},
        "confidence": 0.78,
        "components": [{"ref": "R5", "value": "4.99k"}],  # no type → filled
        "nets": [{"id": "3V3", "classGuess": "power", "nominalVGuess": 3.3}],
        "warnings": ["w"],
        "cite": "model-said-this",
    })
    sch = parse_schematic(
        _JPEG, "bq79616 power section",
        model_call=_fake_model(raw),
        model_name="gemini-3-pro-preview",
        source_uri="snapshot://f1",
    )
    assert isinstance(sch, SchematicJSON)
    assert sch.components[0].type == "resistor"          # normalizer ran
    assert sch.source.model == "gemini-3-pro-preview"     # provenance stamped
    assert sch.source.uri == "snapshot://f1"
    assert sch.source.kind == "image"
    # the cite is the ORCHESTRATOR's, not the model's (provenance honesty)
    assert "gemini-3-pro-preview" in sch.cite
    assert sch.cite != "model-said-this"
    assert sch.confidence == 0.78


def test_parse_detects_pdf_source_kind():
    raw = json.dumps({"source": {"kind": "image", "model": "x"},
                      "components": [], "nets": [], "cite": "c"})
    sch = parse_schematic(b"%PDF-1.7 ...", None, model_call=_fake_model(raw))
    assert sch.source.kind == "pdf"


def test_parse_retries_once_then_succeeds():
    calls = {"n": 0}

    def flaky(image_bytes, mime, hint, model_name):
        calls["n"] += 1
        if calls["n"] == 1:
            return "this is not json at all"   # 1st: invalid → triggers retry
        return json.dumps({"source": {"kind": "image", "model": "x"},
                           "components": [{"ref": "U1"}], "nets": [], "cite": "c"})

    sch = parse_schematic(_JPEG, None, model_call=flaky)
    assert calls["n"] == 2                       # exactly one retry
    assert sch.components[0].ref == "U1"
    assert sch.confidence is not None            # backfilled


def test_parse_degrades_to_low_confidence_stub_on_garbage():
    sch = parse_schematic(_JPEG, None, model_call=_fake_model("totally not json"))
    assert sch.confidence == 0.0
    assert sch.components == [] and sch.nets == []
    assert sch.warnings and "validation" in sch.warnings[0].lower()
    assert sch.cite  # still cited


def test_parse_never_raises_on_model_exception():
    def boom(*a, **k):
        raise RuntimeError("vision exploded")

    sch = parse_schematic(_JPEG, None, model_call=boom, model_name="m")
    assert sch.confidence == 0.0                 # never-fail-stop (01 §7)
    assert "vision call failed" in sch.warnings[0]


def test_default_model_is_snapshot_vision_model():
    # the default label matches the snapshot vision model (gemini-3-pro-preview)
    assert DEFAULT_SCHEMATIC_MODEL == "gemini-3-pro-preview"

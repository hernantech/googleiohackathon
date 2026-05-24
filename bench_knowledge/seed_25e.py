#!/usr/bin/env python3
"""Seed generator for the REAL "25E precharge board" (25E_Precharge_Board_v2).

Extracts structured board knowledge from an Altium project and emits the Forge
seed artifacts in this directory:

  * ``25e-precharge-2026-05.yaml``      — board profile (BoardProfile schema)
  * ``25e-precharge-schematic.json``    — SchematicJSON (components + nets)
  * ``25e-precharge-bom.yaml``          — BOM seed (lookup_bom shape)

Parsing approach (mirrors the cloud pipeline, ``cloud/pipeline/pipeline/nodes/
conversion.py``):

  1. ``.BomDoc`` (readable ISO-8859-1, pipe-delimited Altium LiveBOM) is parsed
     with the EXACT ``_parse_bomdoc`` / ``_parse_bomdoc_line`` logic PORTED from
     the cloud node (not imported). It yields procurement components keyed by
     ``DesignItemId`` (MPN, manufacturer, description, value/USERCOMMENTS,
     datasheet URL) but NO reference designators.

  2. The cloud node parses ``.SchDoc`` connectivity via the optional
     ``altium-schematic-parser`` library (the ``altium`` extra in cloud's
     ``pyproject.toml``). That package is NOT published to PyPI and is not
     installed here, so per the task's fallback we DO NOT depend on it. Instead
     we read the schematic OLE streams directly with ``olefile`` (the binary
     ``FileHeader`` stream holds Altium's ``|KEY=VALUE|`` record list as
     latin-1 text). This recovers the REAL reference designators (RECORD=34
     ``Name=Designator``), the per-component ``DesignItemId`` (RECORD=1), and
     the net names (net labels RECORD=25, ports RECORD=18, power ports
     RECORD=17). ``DesignItemId`` is the join key back to the BomDoc rows.

     If ``olefile`` is unavailable, the script degrades to BomDoc-only
     components (no designators / nets) and marks the artifacts accordingly.

Real data only: NO documented limits (max-voltage / max-current) are invented.
Limit fields are left empty — same provenance rule as a schematic-image source.

Run (read-only on the Altium project)::

    python3.12 bench_knowledge/seed_25e.py /path/to/25E_AMB_Rev2

Defaults to ``~/Downloads/25E_AMB_Rev2`` when no path is given.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
from typing import Any

import yaml

# ── provenance constants ─────────────────────────────────────────────────────
BOARD_ID = "25e-precharge-2026-05"
BOARD_DESC = (
    "25E_Precharge_Board_v2 — EV high-voltage precharge board (AMB Rev2). "
    "Real Altium project; HV precharge check, shutdown-circuit (SDC) logic, "
    "12V/GLV step-down, and connectors."
)
BOMDOC_NAME = "25E_Precharge_Board_v2.BomDoc"
SCH_SHEETS = [
    "[1] HV_Precharge_Check.SchDoc",
    "[2] SDC_Logic.SchDoc",
    "[3] 12V_Stepdown.SchDoc",
    "[4] Connectors.SchDoc",
    "GLV_Stepdown.SchDoc",
]

# ─────────────────────────────────────────────────────────────────────────────
# 1) BomDoc parsing — PORTED VERBATIM from cloud conversion.py (_parse_bomdoc*)
# ─────────────────────────────────────────────────────────────────────────────

# Broken-bar separator used by Altium BomDoc for multi-value fields (byte 0xA6).
_BOMDOC_MULTI_SEP = "\xa6"


def _parse_bomdoc_line(line: str) -> dict[str, str]:
    """Parse a single BomDoc pipe-delimited line into key=value pairs.

    Ported from cloud ``conversion._parse_bomdoc_line``.
    """
    fields: dict[str, str] = {}
    for token in line.split("|"):
        token = token.strip()
        if not token:
            continue
        eq_idx = token.find("=")
        if eq_idx == -1:
            continue
        key = token[:eq_idx].strip()
        value = token[eq_idx + 1:].strip()
        fields[key] = value
    return fields


def _rank_datasheet_url(url: str) -> int:
    """Sort key for datasheet URL preference (lower = better).

    Ported from cloud ``conversion._rank_datasheet_url``.
    """
    lower = url.lower()
    aggregator_domains = ("digikey.com", "mouser.com", "octopart.com", "findchips.com")
    if "ciiva.com" in lower:
        return 1
    for agg in aggregator_domains:
        if agg in lower:
            return 3
    return 0


def parse_bomdoc(file_path: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse an Altium .BomDoc → (components, datasheet_hints).

    Ported from cloud ``conversion._parse_bomdoc``. The BomDoc is a procurement
    BOM — it does NOT contain reference designators. Each CatalogItem keeps its
    ``DesignItemId`` and ``LineNumber`` so a schematic parse can join designators
    back on ``DesignItemId``.
    """
    path = pathlib.Path(file_path)
    text = path.read_text(encoding="iso-8859-1")

    catalog_items: list[dict[str, Any]] = []
    current_item: dict[str, Any] | None = None
    current_choices: list[dict[str, str]] = []

    def _flush() -> None:
        nonlocal current_item, current_choices
        if current_item is not None:
            current_item["_part_choices"] = current_choices
            catalog_items.append(current_item)
        current_item = None
        current_choices = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fields = _parse_bomdoc_line(line)
        record_type = fields.get("RECORD", "")
        if record_type == "CatalogItem":
            _flush()
            current_item = fields
        elif record_type == "PartChoice" and current_item is not None:
            current_choices.append(fields)
    _flush()

    components: list[dict[str, Any]] = []
    datasheet_hints: list[dict[str, Any]] = []

    for item in catalog_items:
        choices = item.get("_part_choices", [])
        first_choice = choices[0] if choices else {}

        mpn = first_choice.get("MANUFACTURERPARTNO", "").strip()
        manufacturer = first_choice.get("MANUFACTURER", "").strip()
        description = (
            item.get("DESCRIPTION", "").strip()
            or first_choice.get("DESCRIPTION", "").strip()
        )
        value = item.get("USERCOMMENTS", "").strip()

        comp: dict[str, Any] = {
            "designator": "",  # BomDoc has no designators
            "mpn": mpn,
            "manufacturer": manufacturer,
            "description": description,
            "value": value,
            "package": "",
            "quantity": 1,
            "category": "other",
            "needs_datasheet": bool(mpn),
            # Forge extras — kept so the schematic can join on DesignItemId.
            "design_item_id": item.get("DESIGNITEMID", "").strip(),
            "line_number": item.get("LINENUMBER", "").strip(),
            "lifecycle": first_choice.get("LIFECYCLESTATUS", "").strip(),
            "supplier": first_choice.get("SUPPLIER", "").strip(),
            "supplier_pn": first_choice.get("SUPPLIERPARTNO", "").strip(),
        }
        components.append(comp)

        if mpn:
            all_urls: list[str] = []
            for choice in choices:
                raw_ds = choice.get("DATASHEETS", "")
                if raw_ds:
                    urls = [u.strip() for u in raw_ds.split(_BOMDOC_MULTI_SEP) if u.strip()]
                    all_urls.extend(urls)
            seen: set[str] = set()
            unique_urls: list[str] = []
            for url in all_urls:
                if url not in seen:
                    seen.add(url)
                    unique_urls.append(url)
            unique_urls.sort(key=_rank_datasheet_url)
            if unique_urls:
                datasheet_hints.append(
                    {
                        "mpn": mpn,
                        "manufacturer": manufacturer,
                        "urls": unique_urls,
                        "source": "bomdoc",
                    }
                )

    return components, datasheet_hints


# ─────────────────────────────────────────────────────────────────────────────
# 2) SchDoc connectivity — olefile fallback (altium-schematic-parser unavailable)
# ─────────────────────────────────────────────────────────────────────────────

def _altium_records(data: bytes) -> list[str]:
    """Split an Altium ``FileHeader`` stream into its record strings.

    Each record is framed by a 4-byte little-endian length (low 3 bytes are the
    payload length; the 4th byte is a flag) followed by the latin-1 payload and a
    trailing NUL.
    """
    recs: list[str] = []
    i, n = 0, len(data)
    while i + 4 <= n:
        ln = data[i] | (data[i + 1] << 8) | (data[i + 2] << 16)
        i += 4
        if ln == 0 or i + ln > n:
            break
        recs.append(data[i:i + ln].decode("latin-1").rstrip("\x00"))
        i += ln
    return recs


def _altium_fields(rec: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for tok in rec.split("|"):
        tok = tok.strip()
        if not tok:
            continue
        j = tok.find("=")
        if j == -1:
            continue
        out[tok[:j]] = tok[j + 1:]
    return out


def parse_schdoc(file_path: str) -> dict[str, Any] | None:
    """Extract components (ref + DesignItemId) and net names from one .SchDoc.

    Returns ``None`` if ``olefile`` is unavailable or the file can't be read.
    Net names are gathered from net labels (RECORD=25), ports (RECORD=18) and
    power ports (RECORD=17). Designators come from RECORD=34 (Name=Designator)
    linked to their owning component (RECORD=1) by ``OwnerIndex`` — which is the
    0-based position in the record stream (the parsed list carries a leading
    non-record header, so the component sits at ``OwnerIndex + 1``).
    """
    try:
        import olefile  # type: ignore[import-untyped]
    except ImportError:
        return None
    try:
        ole = olefile.OleFileIO(file_path)
        if not ole.exists("FileHeader"):
            return None
        data = ole.openstream("FileHeader").read()
    except Exception:
        return None

    parsed = [_altium_fields(r) for r in _altium_records(data)]

    # designator (+ part comment if present) by owning-component stream position
    designators: dict[int, str] = {}
    for fl in parsed:
        if fl.get("RECORD") == "34" and fl.get("Name") == "Designator":
            oi = fl.get("OwnerIndex")
            if oi is not None and oi.lstrip("-").isdigit():
                designators[int(oi) + 1] = fl.get("Text", "").strip()

    components: list[dict[str, str]] = []
    for idx, fl in enumerate(parsed):
        if fl.get("RECORD") != "1":
            continue
        components.append(
            {
                "ref": designators.get(idx, ""),
                "design_item_id": fl.get("DesignItemId", "").strip(),
                "lib_ref": fl.get("LibReference", "").strip(),
                "description": fl.get("ComponentDescription", "").strip(),
            }
        )

    nets: list[str] = []
    for fl in parsed:
        rec = fl.get("RECORD")
        if rec == "25":  # net label
            t = fl.get("Text", "").strip()
            if t:
                nets.append(t)
        elif rec == "18":  # port
            t = fl.get("Name", "").strip()
            if t:
                nets.append(t)
        elif rec == "17":  # power port
            t = fl.get("Text", "").strip()
            if t:
                nets.append(t)

    return {"components": components, "nets": list(dict.fromkeys(nets))}


# ─────────────────────────────────────────────────────────────────────────────
# Net-name canonicalisation (cosmetic merge of obvious aliases on this board)
# ─────────────────────────────────────────────────────────────────────────────

def _canon_net(name: str) -> str:
    """Collapse whitespace and uppercase so 'AIR_COIL+' / 'AIR COIL+' merge.

    Purely cosmetic dedup of operator-typed aliases on the SAME board; it does
    NOT assert electrical connectivity (the OLE fallback does not trace wires).
    """
    return re.sub(r"\s+", "_", name.strip()).upper()


# ── category from ref-des prefix (cloud _categorise, longest-prefix-first) ──
_PREFIX_CATEGORY = [
    ("SW", "switch"), ("TP", "test_point"), ("R", "resistor"), ("C", "capacitor"),
    ("L", "inductor"), ("U", "ic"), ("J", "connector"), ("P", "connector"),
    ("D", "diode"), ("Q", "transistor"), ("Y", "crystal"), ("X", "crystal"),
    ("F", "fuse"), ("T", "transformer"), ("K", "relay"),
]


def categorise(ref: str) -> str:
    upper = (ref or "").upper().strip()
    for prefix, category in _PREFIX_CATEGORY:
        if upper.startswith(prefix):
            return category
    return "other"


def _natural_key(ref: str) -> tuple:
    m = re.match(r"^([A-Za-z]+)(\d+)$", ref or "")
    if m:
        return (m.group(1), int(m.group(2)))
    return (ref or "", 0)


# ─────────────────────────────────────────────────────────────────────────────
# Build seed artifacts
# ─────────────────────────────────────────────────────────────────────────────

def build(project_dir: str) -> dict[str, Any]:
    d = pathlib.Path(project_dir)
    bomdoc_path = d / BOMDOC_NAME
    bom_components, datasheet_hints = parse_bomdoc(str(bomdoc_path))

    # index BomDoc by DesignItemId (join key) and by datasheet hint MPN
    bom_by_dii: dict[str, dict[str, Any]] = {}
    for c in bom_components:
        dii = c.get("design_item_id") or ""
        if dii and dii not in bom_by_dii:
            bom_by_dii[dii] = c
    ds_by_mpn = {h["mpn"]: h["urls"] for h in datasheet_hints}

    # parse each schematic sheet (olefile fallback)
    sch_available = True
    sheet_results: list[tuple[str, dict[str, Any]]] = []
    for sheet in SCH_SHEETS:
        res = parse_schdoc(str(d / sheet))
        if res is None:
            sch_available = False
            break
        sheet_results.append((sheet, res))

    # Assemble per-designator components by joining schematic ref + DesignItemId
    # back to the rich BomDoc row.
    parts: list[dict[str, Any]] = []
    sch_components: list[dict[str, Any]] = []
    raw_net_names: set[str] = set()
    canon_to_display: dict[str, str] = {}

    if sch_available:
        for sheet, res in sheet_results:
            for c in res["components"]:
                ref = c.get("ref", "").strip()
                if not ref:
                    continue
                dii = c.get("design_item_id", "")
                bom = bom_by_dii.get(dii, {})
                mpn = bom.get("mpn") or ""
                desc = bom.get("description") or c.get("description") or ""
                value = bom.get("value") or ""
                manufacturer = bom.get("manufacturer") or ""
                category = categorise(ref)
                datasheet_url = (ds_by_mpn.get(mpn) or [None])[0]
                parts.append(
                    {
                        "ref": ref,
                        "mpn": mpn,
                        "value": value,
                        "manufacturer": manufacturer,
                        "description": desc,
                        "type": category,
                        "design_item_id": dii,
                        "sheet": sheet,
                        "datasheet_url": datasheet_url,
                    }
                )
                sch_components.append(
                    {
                        "ref": ref,
                        "part": mpn or None,
                        "type": category,
                        "value": value or None,
                        "description": desc or None,
                        "sheet": sheet,
                    }
                )
            for n in res["nets"]:
                raw_net_names.add(n)
                canon_to_display.setdefault(_canon_net(n), n.strip())

    # canonical, deduped nets (cosmetic alias merge only)
    canon_nets = sorted(canon_to_display.keys())

    return {
        "bom_components": bom_components,
        "datasheet_hints": datasheet_hints,
        "parts": sorted(parts, key=lambda p: _natural_key(p["ref"])),
        "sch_components": sorted(sch_components, key=lambda c: _natural_key(c["ref"])),
        "canon_nets": canon_nets,
        "canon_to_display": canon_to_display,
        "sch_available": sch_available,
    }


def write_artifacts(out_dir: pathlib.Path, data: dict[str, Any]) -> dict[str, str]:
    parts = data["parts"]
    sch_components = data["sch_components"]
    canon_nets = data["canon_nets"]
    canon_to_display = data["canon_to_display"]
    sch_available = data["sch_available"]
    bom_components = data["bom_components"]

    provenance_note = (
        "Extracted from the real Altium project 25E_AMB_Rev2 "
        "(25E_Precharge_Board_v2). Components/datasheet URLs from the .BomDoc "
        "(cloud _parse_bomdoc logic). "
        + (
            "Reference designators + net names from the .SchDoc OLE FileHeader "
            "records (olefile fallback; altium-schematic-parser is unavailable). "
            "Designator<->part joined on Altium DesignItemId."
            if sch_available
            else "altium-schematic-parser AND olefile both unavailable: "
            "designators / nets could NOT be extracted (BomDoc-only)."
        )
        + " NO documented voltage/current limits are asserted — limit fields are "
        "intentionally empty (real-data-only provenance, same as a "
        "schematic-image source)."
    )

    # ── board profile YAML ──────────────────────────────────────────────────
    profile_parts = []
    for p in parts:
        entry: dict[str, Any] = {"ref": p["ref"], "part": p["mpn"] or p["type"]}
        role_bits = [b for b in (p.get("value"), p.get("description")) if b]
        if role_bits:
            entry["role"] = " — ".join(role_bits)[:160]
        if p.get("datasheet_url"):
            entry["datasheet"] = p["datasheet_url"]
        entry["source"] = "altium_schdoc"
        profile_parts.append(entry)

    # nets carry NO limit fields (real-data-only); desc records membership sheet
    profile_nets = []
    for canon in canon_nets:
        profile_nets.append(
            {
                "id": canon_to_display[canon],
                "desc": "net from Altium schematic (no documented limit)",
                "source": "altium_schdoc",
            }
        )

    # test points: every TP* designator
    test_points = [
        {"id": p["ref"], "desc": (p.get("description") or "test point")}
        for p in parts
        if categorise(p["ref"]) == "test_point"
    ]

    profile = {
        "board_profile": {
            "id": BOARD_ID,
            "description": BOARD_DESC,
            "provenance": provenance_note,
            "parts": profile_parts,
            "rails": [],            # none documented; do NOT invent
            "nets": profile_nets,   # no limit fields — real-data-only
            "test_points": test_points,
            "preconditions": {
                # HV precharge board — sensible-but-conservative; both default
                # True so SafetyGate forces PSU-off on flash/rework (matches the
                # bundled demo profile policy). These are operational defaults,
                # not documented electrical limits.
                "flash_requires_psu_off": True,
                "rework_requires_psu_off": True,
            },
            "procedures": [],       # none documented
        }
    }
    profile_path = out_dir / f"{BOARD_ID}.yaml"
    header = (
        f"# {BOARD_ID}.yaml — REAL 25E precharge board profile.\n"
        f"# {provenance_note}\n"
        f"#\n"
        f"# Select at runtime:  BOARD_PROFILE=bench_knowledge/{BOARD_ID}.yaml\n"
        f"# (Also auto-ingests bench_knowledge/{BOARD_ID}-schematic.json when\n"
        f"#  FORGE_BOARD=25e — see orchestrator/knowledge/__init__.py.)\n\n"
    )
    profile_path.write_text(header + yaml.safe_dump(profile, sort_keys=False, allow_unicode=True), encoding="utf-8")

    # ── SchematicJSON ─────────────────────────────────────────────────────────
    sch_nets = [
        {
            "id": canon_to_display[canon],
            "nodes": [],            # OLE fallback does not trace pin-level wires
            "classGuess": None,     # advisory only; left null (no guessing)
            "nominalVGuess": None,  # NEVER a documented limit
        }
        for canon in canon_nets
    ]
    warnings = []
    if not sch_available:
        warnings.append(
            "altium-schematic-parser unavailable; schematic not parsed."
        )
    else:
        warnings.append(
            "Nets are name-level only (net labels/ports/power-ports); pin-level "
            "connectivity (nodes) was not traced from the OLE fallback."
        )
    schematic = {
        "schematicId": BOARD_ID,
        "source": {
            "kind": "altium_schdoc",
            "uri": f"file://{BOMDOC_NAME} + 5x .SchDoc (25E_AMB_Rev2)",
            "model": None,
        },
        "confidence": None,
        "components": sch_components,
        "nets": sch_nets,
        "sheetCount": len(SCH_SHEETS),
        "warnings": warnings,
        "cite": (
            "25E_Precharge_Board_v2 — Altium .BomDoc + .SchDoc (real project, "
            "extracted via cloud _parse_bomdoc + olefile SchDoc fallback)"
        ),
    }
    schematic_path = out_dir / f"{BOARD_ID}-schematic.json"
    schematic_path.write_text(json.dumps(schematic, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # ── BOM seed YAML (lookup_bom shape) ──────────────────────────────────────
    # When the schematic join succeeded, items are REAL designators; otherwise
    # BomDoc-only rows with empty designators (designator_inferred stays False —
    # we never synthesise designators for this board).
    items: list[dict[str, Any]] = []
    if parts:
        for p in parts:
            items.append(
                {
                    "designator": p["ref"],
                    "designator_inferred": False,  # REAL designators from SchDoc
                    "bom_line": _to_int(bom_line_for(bom_components, p["design_item_id"])),
                    "unit_index": 0,
                    "name": p["mpn"] or p["type"],
                    "description": p["description"],
                    "value": p["value"] or None,
                    "package": None,
                    "type": p["type"],
                    "quantity": 1,
                    "manufacturer": p["manufacturer"] or None,
                    "mpn": p["mpn"] or None,
                    "lifecycle": lifecycle_for(bom_components, p["design_item_id"]),
                    "supplier": supplier_for(bom_components, p["design_item_id"]),
                    "supplier_pn": supplier_pn_for(bom_components, p["design_item_id"]),
                    "unit_price": None,
                }
            )
    else:
        for c in bom_components:
            items.append(
                {
                    "designator": "",
                    "designator_inferred": False,
                    "bom_line": _to_int(c.get("line_number")),
                    "unit_index": 0,
                    "name": c["mpn"] or c.get("category", "part"),
                    "description": c["description"],
                    "value": c["value"] or None,
                    "package": None,
                    "type": c.get("category", "other"),
                    "quantity": 1,
                    "manufacturer": c["manufacturer"] or None,
                    "mpn": c["mpn"] or None,
                    "lifecycle": c.get("lifecycle") or None,
                    "supplier": c.get("supplier") or None,
                    "supplier_pn": c.get("supplier_pn") or None,
                    "unit_price": None,
                }
            )

    bom_doc = {
        "bom": {
            "board_id": "25E_Precharge_Board_v2",
            "source_file": f"bench_knowledge/{BOARD_ID}-schematic.json",
            "note": provenance_note,
            "items": items,
        }
    }
    bom_path = out_dir / f"{BOARD_ID}-bom.yaml"
    bom_path.write_text(
        f"# {BOARD_ID}-bom.yaml — REAL 25E precharge BOM (BomDoc + SchDoc join).\n"
        f"# {provenance_note}\n\n"
        + yaml.safe_dump(bom_doc, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    return {
        "profile": str(profile_path),
        "schematic": str(schematic_path),
        "bom": str(bom_path),
    }


# small lookups into the BomDoc rows by DesignItemId
def _row(bom_components: list[dict], dii: str) -> dict:
    for c in bom_components:
        if c.get("design_item_id") == dii:
            return c
    return {}


def bom_line_for(bc, dii):  # noqa: ANN001
    return _row(bc, dii).get("line_number")


def lifecycle_for(bc, dii):  # noqa: ANN001
    return _row(bc, dii).get("lifecycle") or None


def supplier_for(bc, dii):  # noqa: ANN001
    return _row(bc, dii).get("supplier") or None


def supplier_pn_for(bc, dii):  # noqa: ANN001
    return _row(bc, dii).get("supplier_pn") or None


def _to_int(v: Any) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def main(argv: list[str]) -> int:
    project = argv[1] if len(argv) > 1 else str(pathlib.Path.home() / "Downloads" / "25E_AMB_Rev2")
    out_dir = pathlib.Path(__file__).resolve().parent
    data = build(project)
    paths = write_artifacts(out_dir, data)

    n_parts = len(data["parts"])
    n_nets = len(data["canon_nets"])
    n_bom = len(data["bom_components"])
    print(f"BomDoc CatalogItems: {n_bom}")
    print(f"Schematic available: {data['sch_available']}")
    print(f"Designated parts:    {n_parts}")
    print(f"Unique nets:         {n_nets}")
    print(f"Datasheet hints:     {len(data['datasheet_hints'])}")
    for k, v in paths.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

"""Deterministic post-parse normalizer — spec 09 §6 step 2.

Ports the cloud BOM-conversion node's normalization logic
(`cloud/pipeline/pipeline/nodes/conversion.py`: `_HEADER_MAP`,
`_PREFIX_CATEGORY`, `_categorise`) so a vision parse is tidied with the SAME
deterministic rules the cloud pipeline uses. The cloud repo is read-only, so
this is a COPY (not an import).

What it does to a parsed `SchematicJSON`:
  * fills `SchComponent.type` from the ref-des prefix when the model left it
    blank (U4 → ic, R5 → resistor, J3 → connector, …);
  * tidies whitespace on free-text value/package/description fields;
  * never touches `confidence` / `warnings` / `nominalVGuess` / `classGuess`
    (those stay model-derived + advisory — see 09 §4).

Pure + deterministic: same input → same output, no model, no network.
"""

from __future__ import annotations

import re

from orchestrator.schematic.schema import SchematicJSON

# ── designator-prefix → component category (cloud _PREFIX_CATEGORY) ──────────
# Order matters: longer / more-specific prefixes first so "SW" beats "S".
_PREFIX_CATEGORY: list[tuple[str, str]] = [
    ("SW", "switch"),
    ("TP", "test_point"),  # Forge boards carry test points (TP4, TP7, …)
    ("R", "resistor"),
    ("C", "capacitor"),
    ("L", "inductor"),
    ("U", "ic"),
    ("J", "connector"),
    ("P", "connector"),
    ("D", "diode"),
    ("Q", "transistor"),
    ("Y", "crystal"),
    ("X", "crystal"),
    ("F", "fuse"),
    ("T", "transformer"),
    ("K", "relay"),
]

# ── BOM header normalisation (cloud _HEADER_MAP) ─────────────────────────────
# Kept verbatim so a future CSV/BOM source path can normalise arbitrary headers
# to the same canonical keys the cloud pipeline uses. Exposed for that path +
# tests; the vision path fills the canonical fields directly.
_HEADER_MAP: dict[str, str] = {
    # designator variants
    "designator": "ref",
    "reference": "ref",
    "refdes": "ref",
    "ref des": "ref",
    "ref": "ref",
    # value / comment
    "comment": "value",
    "value": "value",
    "val": "value",
    # description
    "description": "description",
    "desc": "description",
    "part description": "description",
    # footprint / package
    "footprint": "package",
    "package": "package",
    "package/case": "package",
    "case/package": "package",
    # quantity
    "quantity": "quantity",
    "qty": "quantity",
    "count": "quantity",
    # mpn
    "mpn": "part",
    "manufacturer part": "part",
    "manufacturer part number": "part",
    "mfr part": "part",
    "mfr part number": "part",
    "mfg part": "part",
    "part number": "part",
    # manufacturer
    "manufacturer": "manufacturer",
    "mfr": "manufacturer",
    "mfg": "manufacturer",
}

_DESIGNATOR_RE = re.compile(r"^([A-Za-z]+)\d+$")


def categorise(ref: str) -> str:
    """Return a component category from the ref-des prefix (cloud `_categorise`).

    U4 → ic, R5 → resistor, J3 → connector, TP4 → test_point, … ; anything that
    doesn't start with a known prefix → "other"."""
    upper = (ref or "").upper().strip()
    for prefix, category in _PREFIX_CATEGORY:
        if upper.startswith(prefix):
            return category
    return "other"


def normalise_header(raw: str) -> str | None:
    """Normalise a raw CSV/BOM header to a canonical `SchComponent` key, or None
    (cloud `_normalise_header`). For the deferred CSV/BOM source path."""
    key = (raw or "").strip().lower()
    key = re.sub(r"[^a-z0-9 /]", "", key)
    return _HEADER_MAP.get(key)


def _tidy(value: str | None) -> str | None:
    """Collapse internal whitespace + strip a free-text field; None stays None."""
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or None


def normalize(sch: SchematicJSON) -> SchematicJSON:
    """Deterministically tidy a parsed `SchematicJSON` IN PLACE and return it.

    * fills `type` from the ref-des prefix when blank;
    * tidies whitespace on value/package/description/part;
    * leaves model-derived / advisory fields (confidence, warnings,
      nominalVGuess, classGuess) untouched.
    Never raises (09 §4: degrade gracefully)."""
    for c in sch.components:
        ref = (c.ref or "").strip()
        c.ref = ref
        if not (c.type or "").strip():
            c.type = categorise(ref)
        c.value = _tidy(c.value)
        c.package = _tidy(c.package)
        c.description = _tidy(c.description)
        c.part = _tidy(c.part)
        c.sheet = _tidy(c.sheet)
    return sch


__all__ = ["categorise", "normalise_header", "normalize", "_HEADER_MAP", "_PREFIX_CATEGORY"]

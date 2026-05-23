"""BOM knowledge lookup — `bench_knowledge/bq79616-bringup-bom.yaml` (AMB board).

`lookup_bom(query)` searches the real AMB BOM by:
  * designator  — inferred ref-des (R5, C1, U2, TP4, …)
  * value       — "4.99kΩ", "100k", "0.1uF", "5V", …
  * type        — "resistor", "capacitor", "comparator", "regulator", …
  * mpn         — "NCP785AH120T1G", "LM2903AVQDRQ1", "CRCW2512100KFKEG", …
  * name        — BOM Name column (e.g. "4.9k", "Comparator", "SSR")
  * description — any keyword from the rich description field

Result shape mirrors the DatasheetResult / BoardDocResult conventions used by
lookups.py: a pydantic model with a `cite` and a list of `BomMatch` items.

⚠ DESIGNATOR LIMITATION: The source BOM export has NO reference designator
column.  The `designator` on every item is INFERRED (designator_inferred=True)
using standard EE prefix conventions (R/C/D/U/K/F/J/TP/X) with deterministic
numbering by BOM Line # then unit index.  These inferred designators let demo
queries like lookup_bom("R5") work, but they are NOT authoritative schematic
ref-des — the real schematic may number differently.  A BOM export WITH a Ref
Des column would be required for authoritative designator-to-part binding.

Loading is lazy and the parsed index is cached on first call.
"""

from __future__ import annotations

import pathlib
import re
from functools import lru_cache
from typing import Any

import yaml
from pydantic import BaseModel, Field

# ── result shape ─────────────────────────────────────────────────────────────

class BomMatch(BaseModel):
    """A single physical unit from the BOM that matched the query."""
    designator: str
    designator_inferred: bool = True
    bom_line: int | None
    unit_index: int
    name: str
    description: str
    value: str | None
    package: str | None
    type: str
    quantity: int            # total qty on that BOM line (for context)
    manufacturer: str | None
    mpn: str | None
    lifecycle: str | None
    supplier: str | None
    supplier_pn: str | None
    unit_price: float | None
    score: float             # relevance rank (higher = more relevant)
    cite: str                # human-citable reference


class BomResult(BaseModel):
    """Result returned by lookup_bom(query)."""
    query: str
    matches: list[BomMatch] = Field(default_factory=list)
    cite: str  # top-level cite (e.g. "bq79616 bring-up BOM (bench_knowledge/…)")
    note: str = (
        "Designators are INFERRED (designator_inferred=True) — not schematic-"
        "authoritative. Source BOM had no Ref Des column. Queryable by value, "
        "part-type, MPN, name, description, or inferred designator."
    )


# ── BOM data path ────────────────────────────────────────────────────────────

_BOM_YAML_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "bench_knowledge"
    / "bq79616-bringup-bom.yaml"
)

_TOP_CITE = (
    "AMB BOM — bench_knowledge/bq79616-bringup-bom.yaml "
    "(source: BOM_[No Variations] (4).csv)"
)


# ── lazy-loaded + cached item index ─────────────────────────────────────────

@lru_cache(maxsize=1)
def _load_items() -> list[dict[str, Any]]:
    """Parse the YAML and return a flat list of item dicts. Cached forever."""
    if not _BOM_YAML_PATH.exists():
        return []
    with _BOM_YAML_PATH.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    bom = data.get("bom", {})
    items: list[dict] = bom.get("items", [])
    return items


# ── designator pattern ───────────────────────────────────────────────────────

_DESIGNATOR_RE = re.compile(
    r"^([A-Z]+)(\d+)$",  # e.g. R5, C12, TP4, U1, LED3
    re.IGNORECASE,
)


def _is_designator_query(q: str) -> bool:
    """True if the query looks like a reference designator (e.g. R5, C1, TP4)."""
    return bool(_DESIGNATOR_RE.match(q.strip()))


# ── scoring ──────────────────────────────────────────────────────────────────

_OMEGA_ALIASES = {"ohm", "ohms", "ω", "omega"}
_UF_ALIASES    = {"uf", "µf", "microfarad"}
_NF_ALIASES    = {"nf", "nanofarad"}
_PF_ALIASES    = {"pf", "picofarad"}


def _normalize_token(tok: str) -> str:
    """Lower-case, strip trailing Ω/ohm variants, normalise SI suffixes."""
    t = tok.casefold().strip()
    # "4.99kω" → "4.99k", "100kohm" → "100k", "0.1uf" → "0.1uf" (kept)
    for alias in _OMEGA_ALIASES:
        if t.endswith(alias):
            t = t[: -len(alias)]
    t = t.rstrip("ω")
    return t


def _score_item(item: dict, tokens: list[str], raw_query: str) -> float:
    """Return a relevance score for this BOM item against the tokenized query.

    Scoring tiers (additive):
      4.0  — exact designator match (R5 == R5)
      3.0  — exact MPN match (case-insensitive)
      2.0  — exact name match
      1.5  — value substring match (e.g. "4.99k" in "4.99kΩ")
      1.0  — type exact match (e.g. "resistor")
      0.5  — description keyword hit (per unique token)
    """
    score = 0.0
    rq = raw_query.casefold().strip()

    # ── designator exact ──────────────────────────────────────────────────
    des = (item.get("designator") or "").casefold()
    if des and des == rq:
        score += 4.0

    # ── MPN exact ─────────────────────────────────────────────────────────
    mpn = (item.get("mpn") or "").casefold()
    if mpn and mpn == rq:
        score += 3.0

    # ── MPN substring (partial MPN search) ────────────────────────────────
    if mpn and rq and rq in mpn:
        score += 1.5

    # ── name exact ────────────────────────────────────────────────────────
    name = (item.get("name") or "").casefold()
    if name and name == rq:
        score += 2.0

    # ── value substring ───────────────────────────────────────────────────
    val = (item.get("value") or "").casefold()
    for tok in tokens:
        nt = _normalize_token(tok)
        nv = _normalize_token(val)
        if nt and nv and (nt in nv or nv in nt):
            score += 1.5
            break

    # ── type match ────────────────────────────────────────────────────────
    typ = (item.get("type") or "").casefold()
    for tok in tokens:
        nt = _normalize_token(tok)
        if nt and (nt == typ or nt in typ or typ in nt):
            score += 1.0
            break

    # ── description keyword hits ─────────────────────────────────────────
    desc = (item.get("description") or "").casefold()
    desc_tokens = set(re.split(r"[\s,/\-]+", desc))
    for tok in tokens:
        nt = _normalize_token(tok)
        if nt and len(nt) > 1 and (nt in desc_tokens or any(nt in dt for dt in desc_tokens)):
            score += 0.5

    # ── name keyword hits ─────────────────────────────────────────────────
    name_tokens = set(re.split(r"[\s,/\-]+", name))
    for tok in tokens:
        nt = _normalize_token(tok)
        if nt and len(nt) > 1 and (nt in name_tokens or any(nt in nt2 for nt2 in name_tokens)):
            score += 0.3

    return score


def _item_to_match(item: dict, score: float) -> BomMatch:
    """Convert a raw YAML dict to a BomMatch pydantic model."""
    des = item.get("designator", "?")
    bom_line = item.get("bom_line")
    mpn = item.get("mpn") or None
    typ = item.get("type", "unknown")
    val = item.get("value") or None
    pkg = item.get("package") or None

    # Build a human-citable reference
    cite_parts = [f"AMB BOM {des}"]
    if mpn:
        cite_parts.append(f"MPN {mpn}")
    if val:
        cite_parts.append(val)
    if pkg:
        cite_parts.append(pkg)
    cite = " — ".join(cite_parts)

    return BomMatch(
        designator=des,
        designator_inferred=bool(item.get("designator_inferred", True)),
        bom_line=bom_line,
        unit_index=int(item.get("unit_index", 0)),
        name=str(item.get("name", "")),
        description=str(item.get("description", "")),
        value=val,
        package=pkg,
        type=typ,
        quantity=int(item.get("quantity", 1)),
        manufacturer=item.get("manufacturer") or None,
        mpn=mpn,
        lifecycle=item.get("lifecycle") or None,
        supplier=item.get("supplier") or None,
        supplier_pn=item.get("supplier_pn") or None,
        unit_price=item.get("unit_price"),
        score=score,
        cite=cite,
    )


# ── public API ───────────────────────────────────────────────────────────────

def lookup_bom(query: str, max_results: int = 10) -> BomResult:
    """Search the bq79616 bring-up BOM by any of:

      * Reference designator  — "R5", "C1", "TP4", "U5", "D2", …
        (inferred; designator_inferred=True)
      * Value                 — "4.99k", "100kΩ", "0.1uF", "2.2uF", "510Ω"
      * Component type        — "resistor", "capacitor", "comparator", "regulator",
                                "relay", "fuse", "LED", "diode", "connector", …
      * MPN                   — "NCP785AH120T1G", "LM2903AVQDRQ1", "NCP785", …
      * Name                  — "4.9k", "SSR", "Comparator", "866", …
      * Description keywords  — "thick film", "SOIC", "slo-blo", "X7R", …

    Returns matches ranked by relevance. Empty / whitespace query → empty result
    (never crashes).
    """
    q = (query or "").strip()
    if not q:
        return BomResult(query=query, matches=[], cite=_TOP_CITE)

    items = _load_items()
    if not items:
        return BomResult(
            query=query,
            matches=[],
            cite=_TOP_CITE,
            note=(
                "BOM YAML not found at expected path. "
                "Run from repo root or check bench_knowledge/ directory."
            ),
        )

    # Tokenise (split on whitespace + common separators, lower)
    tokens = [t for t in re.split(r"[\s,/\-]+", q.casefold()) if t]

    # Score all items
    scored: list[tuple[float, dict]] = []
    for item in items:
        s = _score_item(item, tokens, q)
        if s > 0:
            scored.append((s, item))

    # Sort descending by score, then by designator for determinism
    scored.sort(key=lambda x: (-x[0], x[1].get("designator", "")))

    # Cap results
    top = scored[:max_results]

    matches = [_item_to_match(item, score) for score, item in top]
    cite = _TOP_CITE
    return BomResult(query=query, matches=matches, cite=cite)

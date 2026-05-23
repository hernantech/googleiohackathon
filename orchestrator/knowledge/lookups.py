"""Knowledge-lookup tools — `specs/05_board_knowledge_api.md` §3.1, §3.2, §6.

Two read-only lookups SMEs call through the orchestrator side-channel:
  * `lookup_datasheet(part, query, maxPages)` — datasheet passages + a cite.
  * `lookup_board_doc(query)`                 — board-doc prose + profile hits.

Both surface as `ProposedAction(actor="guild")` / `ToolCall`; neither touches
hardware. With no datastore configured (no `VERTEX_SEARCH_DATASTORE_ID`) we run
in **stub mode** (§6) and serve canned excerpts from a hand-curated table
covering the demo parts (BQ79616 §7 power-up, ESP32 UART, AMS1117 dropout).

Result shapes follow §3 exactly. We model them as pydantic so they round-trip
to/from the JSON the wire carries; field names match the spec's TypeScript.
"""

from __future__ import annotations

import os

from pydantic import BaseModel, Field

from orchestrator.knowledge.board_profile import BoardProfile


# ───────────────────────── result shapes (§3.1, §3.2) ──────────────────────

class DatasheetPassage(BaseModel):
    text: str
    page: int
    sourceUri: str
    score: float


class DatasheetResult(BaseModel):
    part: str
    passages: list[DatasheetPassage] = Field(default_factory=list)
    cite: str  # human-citable, e.g. "bq79616 datasheet §7 p.41"


class BoardDocPassage(BaseModel):
    text: str
    section: str
    sourceUri: str


class ProfileMatch(BaseModel):
    kind: str  # "part" | "rail" | "net" | "procedure"
    id: str
    data: dict


class BoardDocResult(BaseModel):
    passages: list[BoardDocPassage] = Field(default_factory=list)
    profileMatches: list[ProfileMatch] = Field(default_factory=list)


# ───────────────────────── stub datasheet table (§6) ──────────────────────
# Hand-curated canned excerpts keyed by part. Each entry is a list of
# (keyword-set, passage, cite) so a query can be matched to the right passage.
# The absolute-maximum-ratings entry is what `get_documented_limit` falls
# through to for kind="part" (§3.3, BK-5).

_DatasheetEntry = dict


_STUB_DATASHEETS: dict[str, list[_DatasheetEntry]] = {
    "bq79616": [
        {
            "keywords": {"power-up", "powerup", "power", "wake", "stack", "cell", "comm"},
            "text": (
                "Power-Up: the BQ79616 device will not communicate on the daisy-chain "
                "until a valid cell stack is present on its VC pins. A cell stack must "
                "be applied before the host issues the wake tone; only then will the "
                "device respond on the comm bus."
            ),
            "page": 41,
            "section": "§7",
            "cite": "bq79616 datasheet §7 p.41",
        },
        {
            "keywords": {"absolute", "maximum", "rating", "ratings", "limit", "abs-max"},
            "text": (
                "Absolute Maximum Ratings: total stack voltage (BAT to VSS) 80 V max; "
                "per-cell VC input -0.3 V to 6 V; logic VIO -0.3 V to 5.5 V. Stresses "
                "beyond these ratings may cause permanent damage."
            ),
            "page": 8,
            "section": "§6.1",
            "cite": "bq79616 datasheet §6.1 (Absolute Maximum Ratings) p.8",
            "absoluteMax": {"voltageV": 80.0},
        },
    ],
    "esp32-wroom-32": [
        {
            "keywords": {"uart", "serial", "baud", "console", "tx", "rx"},
            "text": (
                "UART: the module exposes UART0 on GPIO1 (TXD0) / GPIO3 (RXD0). The "
                "default ROM bootloader and serial console run at 115200 baud, 8N1. "
                "IO pins are 3.3 V logic and are not 5 V tolerant."
            ),
            "page": 12,
            "section": "§4.3",
            "cite": "esp32-wroom-32 datasheet §4.3 (UART) p.12",
        },
        {
            "keywords": {"absolute", "maximum", "rating", "ratings", "limit"},
            "text": (
                "Recommended operating conditions: VDD 3.0 V to 3.6 V. Absolute maximum "
                "input voltage on any IO is 3.6 V; exceeding it may damage the pad."
            ),
            "page": 20,
            "section": "§5.2",
            "cite": "esp32-wroom-32 datasheet §5.2 p.20",
            "absoluteMax": {"voltageV": 3.6},
        },
    ],
    "ams1117": [
        {
            "keywords": {"dropout", "ldo", "regulator", "voltage", "drop", "3.3"},
            "text": (
                "Dropout Voltage: the AMS1117 is a fixed/adjustable LDO with a typical "
                "dropout of 1.1 V at 800 mA load. For a 3.3 V output the input must be "
                "at least ~4.4 V to stay in regulation."
            ),
            "page": 4,
            "section": "§Electrical",
            "cite": "ams1117 datasheet (Electrical Characteristics) p.4",
        },
        {
            "keywords": {"absolute", "maximum", "rating", "ratings", "limit", "input"},
            "text": (
                "Absolute Maximum Ratings: input voltage 15 V max; operating junction "
                "temperature 125 C max."
            ),
            "page": 2,
            "section": "§Abs Max",
            "cite": "ams1117 datasheet (Absolute Maximum Ratings) p.2",
            "absoluteMax": {"voltageV": 15.0},
        },
    ],
}

#: Canned board-doc prose returned in stub mode (§6).
_STUB_BOARD_DOC_TEXT = (
    "Bring-up board overview: ESP32 host (U1) talks to the BQ79616 16-cell "
    "monitor (U2) through the BQ79600 comm bridge (U3). Logic rails are derived "
    "from the AMS1117 LDO (U4). The emulated cell stack is applied at J3."
)


def _has_datastore() -> bool:
    """Stub mode unless a Vertex AI Search datastore is configured (§3.1, §6)."""
    return bool(os.environ.get("VERTEX_SEARCH_DATASTORE_ID"))


def _score_entry(entry: _DatasheetEntry, query: str) -> int:
    """Number of query tokens that hit the entry's keyword set."""
    tokens = {t for t in query.casefold().replace("-", " ").split() if t}
    kw = entry["keywords"]
    direct = sum(1 for t in tokens if t in kw)
    # also let a hyphenated query like "power-up" match the joined keyword
    joined = query.casefold().replace(" ", "-")
    if joined in kw:
        direct += 1
    return direct


def _normalize_part(part: str, profile: BoardProfile | None) -> str:
    """Resolve a part identifier to a datasheet key.

    Accepts the datasheet slug itself, a part number, or a component ref by
    consulting the profile's parts table.
    """
    key = part.casefold()
    if key in _STUB_DATASHEETS:
        return key
    if profile is not None:
        p = profile.part(part)
        if p is not None and p.datasheet and p.datasheet.casefold() in _STUB_DATASHEETS:
            return p.datasheet.casefold()
    return key


def lookup_datasheet(
    part: str,
    query: str,
    maxPages: int | None = None,
    *,
    profile: BoardProfile | None = None,
) -> DatasheetResult:
    """Find datasheet passages for `part` matching `query` (§3.1).

    Stub mode (no datastore): returns canned excerpts from `_STUB_DATASHEETS`,
    ranked by keyword overlap with `query`. Every result carries a `cite`.
    """
    key = _normalize_part(part, profile)
    entries = _STUB_DATASHEETS.get(key, [])

    if not entries:
        # Unknown part: graceful, empty-but-cited (still no crash).
        return DatasheetResult(
            part=part,
            passages=[],
            cite=f"{part} datasheet (no stub entry)",
        )

    ranked = sorted(entries, key=lambda e: _score_entry(e, query), reverse=True)
    if maxPages is not None and maxPages >= 0:
        ranked = ranked[:maxPages]
    elif not ranked:
        ranked = entries[:1]

    # If nothing matched the query, fall back to the first (most general) entry
    # so the SME still gets a cited passage rather than nothing.
    if ranked and _score_entry(ranked[0], query) == 0:
        ranked = [entries[0]] + [e for e in ranked if e is not entries[0]]
        if maxPages is not None and maxPages >= 0:
            ranked = ranked[:maxPages]

    src = f"datasheet://{key}"
    passages = [
        DatasheetPassage(
            text=e["text"],
            page=e["page"],
            sourceUri=f"{src}#p{e['page']}",
            score=1.0 / (i + 1),
        )
        for i, e in enumerate(ranked)
    ]
    cite = ranked[0]["cite"] if ranked else f"{part} datasheet"
    return DatasheetResult(part=part, passages=passages, cite=cite)


def datasheet_absolute_max(
    part: str,
    *,
    profile: BoardProfile | None = None,
) -> dict | None:
    """Return the absolute-maximum-ratings entry's `absoluteMax` + cite, if any.

    Used by `get_documented_limit` for the kind="part" fallback (§3.3, BK-5).
    """
    res = lookup_datasheet(part, "absolute maximum ratings", profile=profile)
    key = _normalize_part(part, profile)
    for entry in _STUB_DATASHEETS.get(key, []):
        if "absoluteMax" in entry:
            absmax = dict(entry["absoluteMax"])
            absmax["source"] = entry["cite"]
            return absmax
    # nothing richer; still surface the cite of whatever passage we found
    if res.passages:
        return None
    return None


def lookup_board_doc(
    query: str,
    *,
    profile: BoardProfile | None = None,
) -> BoardDocResult:
    """Query board documentation prose AND the structured profile (§3.2).

    Stub mode: returns a canned prose paragraph plus the bundled profile's
    structured matches (parts/rails/nets/procedures whose id/desc match query,
    or all of them for a broad query like "preconditions"/"overview").
    """
    q = query.casefold()
    passages = [
        BoardDocPassage(
            text=_STUB_BOARD_DOC_TEXT,
            section="overview",
            sourceUri="board_doc://demo",
        )
    ]

    matches: list[ProfileMatch] = []
    if profile is not None:
        # `preconditions` query: surface the documented preconditions (§4.3).
        if "precondition" in q:
            matches.append(
                ProfileMatch(
                    kind="procedure",
                    id="preconditions",
                    data=profile.preconditions.model_dump(),
                )
            )

        for p in profile.parts:
            if _matches(q, p.ref, p.part, p.role, p.datasheet):
                matches.append(ProfileMatch(kind="part", id=p.ref, data=p.model_dump()))
        for r in profile.rails:
            if _matches(q, r.id, r.note):
                matches.append(ProfileMatch(kind="rail", id=r.id, data=r.model_dump()))
        for n in profile.nets:
            if _matches(q, n.id, n.desc, n.test_point):
                matches.append(ProfileMatch(kind="net", id=n.id, data=n.model_dump()))
        for proc in profile.procedures:
            if _matches(q, proc.id, proc.summary, proc.cite):
                matches.append(ProfileMatch(kind="procedure", id=proc.id, data=proc.model_dump()))

    return BoardDocResult(passages=passages, profileMatches=matches)


def _matches(query: str, *fields: str | None) -> bool:
    """True if any query token appears in any of the (lowercased) fields."""
    tokens = [t for t in query.replace("-", " ").split() if len(t) > 1]
    if not tokens:
        return False
    hay = " ".join(f.casefold() for f in fields if f)
    return any(t in hay for t in tokens)

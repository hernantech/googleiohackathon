"""Knowledge-lookup tools — `specs/05_board_knowledge_api.md` §3.1, §3.2, §6.

Two read-only lookups SMEs call through the orchestrator side-channel:
  * `lookup_datasheet(part, query, maxPages)` — datasheet passages + a cite.
  * `lookup_board_doc(query)`                 — board-doc prose + profile hits.

Both surface as `ProposedAction(actor="guild")` / `ToolCall`; neither touches
hardware. With no datastore configured (no `VERTEX_SEARCH_DATASTORE_ID`) we run
in **stub mode** (§6) and serve canned excerpts from a hand-curated table
covering all four demo-board parts: the BQ79616 monitor (power-up/wake, VIO,
comm, abs-max), the BQ79600 comm bridge (host interface, wake, abs-max), the
ESP32-WROOM-32 host (UART, power, abs-max) and the AMS1117-3.3 LDO (dropout,
current/thermal, abs-max). Each entry is page-cited and grounded in the bundled
board profile (bench_knowledge/examples/bq79616-bringup-2026-05.yaml).

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
    # ── U2: TI BQ79616 16s battery monitor / AFE ───────────────────────────
    "bq79616": [
        {
            "keywords": {"power-up", "powerup", "power", "wake", "wakeup", "stack",
                         "cell", "comm", "daisy", "daisy-chain", "tone", "ping",
                         "sleep", "shutdown"},
            "text": (
                "Power-Up and Wake (Section 7.4): the BQ79616 powers from the cell "
                "stack applied to its VC0-VC16 / BAT pins, not from the logic VIO "
                "rail. With no stack present the device stays unpowered and is silent "
                "on the daisy-chain. The host wakes the stack by driving a wake ping "
                "(a long differential low) on the COMM bus; the device transitions "
                "SHUTDOWN -> WAKE -> ACTIVE within tWAKE (~4.3 ms per device). Only "
                "after a valid stack is present AND the wake tone has been issued will "
                "the device acknowledge on the comm bus. A common bring-up failure is "
                "expecting comm before the emulated stack is connected at J3."
            ),
            "page": 41,
            "section": "§7.4",
            "cite": "bq79616 datasheet §7.4 (Power-Up / Wake) p.41",
        },
        {
            "keywords": {"vio", "logic", "regulator", "ldo", "cvdd", "dvdd",
                         "supply", "3.3", "3v3", "rail", "io"},
            "text": (
                "Logic Supply VIO (Section 7.3): VIO is the digital I/O reference for "
                "the COMM and NFAULT pins and is internally regulated/derived for the "
                "device logic. Recommended VIO operating range is 3.3 V to 5.0 V; do "
                "NOT exceed the 5.5 V absolute maximum. On this board VIO is supplied "
                "from the 3V3 logic rail (the AMS1117-3.3 LDO) and is monitored at "
                "TP4. VIO does not power the cell-measurement front end — that comes "
                "from the stack. If VIO is absent the host UART/COMM levels float and "
                "the bridge sees no valid logic high."
            ),
            "page": 28,
            "section": "§7.3",
            "cite": "bq79616 datasheet §7.3 (Power Supply / VIO) p.28",
        },
        {
            "keywords": {"comm", "uart", "daisy", "tx", "rx", "nfault", "fault",
                         "bridge", "differential", "communication", "register"},
            "text": (
                "Communication (Section 8.3): the BQ79616 talks to the host through a "
                "differential daisy-chain (COMMH/COML), bridged to host UART by the "
                "companion BQ79600. Register reads/writes use the TI BQ7961x frame "
                "format with CRC; a single-device stack still requires the auto-address "
                "sequence to be run after wake before register access succeeds. NFAULT "
                "is an open-drain fault flag pulled to VIO."
            ),
            "page": 55,
            "section": "§8.3",
            "cite": "bq79616 datasheet §8.3 (Communication) p.55",
        },
        {
            "keywords": {"absolute", "maximum", "rating", "ratings", "limit",
                         "abs-max", "vc", "bat", "vio", "stack"},
            "text": (
                "Absolute Maximum Ratings (Section 6.1): total stack voltage BAT to "
                "VSS 80 V max; per-cell VC input differential -0.3 V to 6 V; logic VIO "
                "pin -0.3 V to 5.5 V; COMM pins -0.3 V to VIO+0.3 V. These are stress "
                "ratings; sustained operation at the limits is not implied and stresses "
                "beyond them may cause permanent damage."
            ),
            "page": 8,
            "section": "§6.1",
            "cite": "bq79616 datasheet §6.1 (Absolute Maximum Ratings) p.8",
            "absoluteMax": {"voltageV": 80.0},
        },
    ],
    # ── U3: TI BQ79600 host-side UART <-> daisy-chain comm bridge ──────────
    "bq79600": [
        {
            "keywords": {"bridge", "uart", "comm", "daisy", "host", "spi", "tx", "rx",
                         "communication", "interface", "baud"},
            "text": (
                "Host Interface (Section 7.2): the BQ79600 bridges a host UART (or "
                "SPI) to the differential BQ7961x daisy-chain. In UART mode it runs at "
                "1 Mbps half-duplex on the host side by default; the host (ESP32) must "
                "match this rate. The bridge is the only device on the host UART; it "
                "forwards framed transactions onto COMMH/COML to the BQ79616 stack and "
                "returns responses. If the stack is asleep the bridge still answers "
                "host pings but reports no downstream devices until a wake is sent."
            ),
            "page": 22,
            "section": "§7.2",
            "cite": "bq79600 datasheet §7.2 (Host Interface) p.22",
        },
        {
            "keywords": {"wake", "ping", "tone", "power", "power-up", "powerup",
                         "stack", "sleep", "nfault", "fault", "reset"},
            "text": (
                "Wake / Tone Generation (Section 7.4): the host asks the BQ79600 to "
                "emit the wake tone onto the daisy-chain to bring the BQ79616 stack out "
                "of SHUTDOWN/SLEEP. The bridge itself is powered from the host VIO/3V3 "
                "rail, so it is alive even when the stack is not. NFAULT from the stack "
                "is surfaced to the host through the bridge. The documented bring-up "
                "order is: power VIO -> host opens UART to bridge -> send wake -> run "
                "auto-address -> read cells."
            ),
            "page": 26,
            "section": "§7.4",
            "cite": "bq79600 datasheet §7.4 (Wake / Tone Generation) p.26",
        },
        {
            "keywords": {"absolute", "maximum", "rating", "ratings", "limit",
                         "vio", "supply", "input"},
            "text": (
                "Absolute Maximum Ratings (Section 6.1): VIO/logic supply -0.3 V to "
                "5.5 V; COMM pins -0.3 V to VIO+0.3 V; host UART I/O -0.3 V to "
                "VIO+0.3 V. Exceeding these may permanently damage the bridge."
            ),
            "page": 7,
            "section": "§6.1",
            "cite": "bq79600 datasheet §6.1 (Absolute Maximum Ratings) p.7",
            "absoluteMax": {"voltageV": 5.5},
        },
    ],
    # ── U1: Espressif ESP32-WROOM-32 host MCU ──────────────────────────────
    "esp32-wroom-32": [
        {
            "keywords": {"uart", "serial", "baud", "console", "tx", "rx", "txd0",
                         "rxd0", "gpio1", "gpio3", "bootloader", "flash"},
            "text": (
                "UART (Section 4.3): the module exposes UART0 on GPIO1 (TXD0) and "
                "GPIO3 (RXD0). The ROM bootloader and default serial console run at "
                "115200 baud, 8N1. Note this is the host console rate; the link to the "
                "BQ79600 bridge is a separate UART configured by firmware (commonly "
                "1 Mbps). All IO pins are 3.3 V logic and are NOT 5 V tolerant."
            ),
            "page": 12,
            "section": "§4.3",
            "cite": "esp32-wroom-32 datasheet §4.3 (UART) p.12",
        },
        {
            "keywords": {"power", "vdd", "supply", "3.3", "3v3", "current", "rail",
                         "brownout", "brown-out", "ldo", "regulator"},
            "text": (
                "Power Supply (Section 5.1): recommended VDD 3.0 V to 3.6 V, nominal "
                "3.3 V. Peak supply current during Wi-Fi TX can reach ~500 mA, so the "
                "3V3 rail and its LDO must source transient current without sagging "
                "below the ~2.7 V brown-out threshold. On this board Wi-Fi is unused "
                "during bring-up, so steady draw is well under 100 mA."
            ),
            "page": 18,
            "section": "§5.1",
            "cite": "esp32-wroom-32 datasheet §5.1 (Power Supply) p.18",
        },
        {
            "keywords": {"absolute", "maximum", "rating", "ratings", "limit", "input",
                         "io", "tolerant"},
            "text": (
                "Recommended operating conditions / Absolute Maximums (Section 5.2): "
                "VDD 3.0-3.6 V; absolute maximum input voltage on any IO pin is 3.6 V. "
                "Pins are not 5 V tolerant; applying >3.6 V may damage the pad and is a "
                "common cause of dead GPIO during bring-up."
            ),
            "page": 20,
            "section": "§5.2",
            "cite": "esp32-wroom-32 datasheet §5.2 (Abs Max) p.20",
            "absoluteMax": {"voltageV": 3.6},
        },
    ],
    # ── U4: AMS1117-3.3 fixed 3.3 V LDO (VIO/3V3 logic rail) ───────────────
    "ams1117": [
        {
            "keywords": {"dropout", "ldo", "regulator", "voltage", "drop", "3.3",
                         "3v3", "vout", "output", "regulation", "headroom"},
            "text": (
                "Dropout Voltage (Electrical Characteristics): the AMS1117 is a "
                "fixed/adjustable LDO with a typical dropout of 1.1 V (max 1.3 V) at "
                "800 mA load. The -3.3 fixed version outputs 3.3 V +/-1%. For 3.3 V "
                "out the input must be at least ~4.4-4.6 V to stay in regulation; "
                "below that the output simply follows Vin minus dropout. On this board "
                "it generates the 3V3/VIO logic rail measured at TP4."
            ),
            "page": 4,
            "section": "Electrical Characteristics",
            "cite": "ams1117 datasheet (Electrical Characteristics) p.4",
        },
        {
            "keywords": {"current", "limit", "max_current", "load", "thermal",
                         "dissipation", "heat", "junction", "ground"},
            "text": (
                "Current Limit & Thermal (Section: Protection): internal current limit "
                "is typically 1 A with thermal shutdown at ~165 C junction. Power "
                "dissipation is (Vin - Vout) x Iload; at Vin=5 V, Vout=3.3 V, 100 mA "
                "that is ~0.17 W. The board's 3V3 rail budget is 0.5 A, well inside "
                "the part's limit, but a sustained short pulls the device into thermal "
                "fold-back."
            ),
            "page": 5,
            "section": "Protection",
            "cite": "ams1117 datasheet (Protection) p.5",
        },
        {
            "keywords": {"absolute", "maximum", "rating", "ratings", "limit", "input",
                         "vin", "temperature"},
            "text": (
                "Absolute Maximum Ratings: input voltage 15 V max; operating junction "
                "temperature 125 C max (storage 150 C). Output is short-circuit and "
                "thermal-overload protected."
            ),
            "page": 2,
            "section": "Abs Max",
            "cite": "ams1117 datasheet (Absolute Maximum Ratings) p.2",
            "absoluteMax": {"voltageV": 15.0},
        },
    ],
}

#: Canned board-doc prose returned in stub mode (§6). Several richer passages,
#: ranked against the query so lookup_board_doc returns the most relevant first.
#: Grounded in bench_knowledge/examples/bq79616-bringup-2026-05.yaml.
_DOC_PASSAGE = dict
_STUB_BOARD_DOC_PASSAGES: list[_DOC_PASSAGE] = [
    {
        "keywords": {"overview", "board", "topology", "block", "what", "parts"},
        "section": "overview",
        "text": (
            "Bring-up board overview (bq79616-bringup-2026-05): an ESP32-WROOM-32 host "
            "(U1) talks to the BQ79616 16-cell monitor AFE (U2) through the BQ79600 "
            "host-side comm bridge (U3) over a UART<->daisy-chain link. All logic runs "
            "from the 3V3 rail produced by the AMS1117-3.3 LDO (U4), which also feeds "
            "the BQ79616 VIO. The 16-series cell stack is emulated by a resistor ladder "
            "(~1.875 V/cell, ~30 V top-of-stack) applied at connector J3."
        ),
    },
    {
        "keywords": {"power", "rail", "rails", "3v3", "vio", "cellstk", "psu",
                     "supply", "voltage", "net", "nets", "j3", "tp4", "tp7",
                     "max", "limit", "current"},
        "section": "power & nets",
        "text": (
            "Power rails and documented net limits: the 3V3 logic rail is nominal "
            "3.3 V, 0.5 A budget (powers U1, U3, and the AMS1117 output). VIO is the "
            "3.3 V logic reference at the BQ79616, 0.1 A, monitored at TP4 (documented "
            "max 5.5 V). The emulated CELLSTK rail is nominal 30 V, 0.5 A, applied at "
            "J3 (net J3 documented max 30.0 V, probed at J3-1). TP7 carries the U3 UART "
            "TX up to U1 and is limited to 3.6 V (ESP32 IO is not 5 V tolerant). "
            "Setpoints for any net must come from get_documented_limit, never a guess."
        ),
    },
    {
        "keywords": {"power-up", "powerup", "bring-up", "bringup", "wake", "stack",
                     "comm", "procedure", "sequence", "order", "no", "timeout",
                     "silent", "daisy"},
        "section": "bring-up procedure",
        "text": (
            "Documented bring-up order (procedure bq79616-power-up): (1) bring up the "
            "3V3/VIO logic rail so U1 and U3 are alive; (2) host opens its UART to the "
            "BQ79600 bridge; (3) apply the emulated cell stack at J3 BEFORE expecting "
            "any BQ79616 comm — the AFE is powered from the stack, not VIO; (4) send "
            "the wake tone via the bridge; (5) run auto-address; (6) read cells. The "
            "most common 'comm timeout' is doing step 4 before step 3 — see bq79616 "
            "datasheet §7.4."
        ),
    },
    {
        "keywords": {"precondition", "preconditions", "safety", "flash", "rework",
                     "psu", "off", "high"},
        "section": "preconditions",
        "text": (
            "Documented preconditions (SafetyGate, §4): flash_requires_psu_off=true "
            "and rework_requires_psu_off=true. Any flash_mcu or reflow/rework step must "
            "instruct the operator to power the PSU OFF first."
        ),
    },
]

#: Back-compat alias: the single most-general overview paragraph. Some callers/
#: tests reference the flat string; keep it pointing at the overview passage.
_STUB_BOARD_DOC_TEXT = _STUB_BOARD_DOC_PASSAGES[0]["text"]


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


def _score_doc_passage(passage: _DOC_PASSAGE, query: str) -> int:
    """Number of query tokens that hit a board-doc passage's keyword set."""
    tokens = {t for t in query.casefold().replace("-", " ").split() if t}
    kw = passage["keywords"]
    direct = sum(1 for t in tokens if t in kw)
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

    # Rank the canned prose passages by keyword overlap with the query so a
    # focused question (e.g. "max voltage on the 3V3 net") surfaces the
    # power & nets passage first rather than the generic overview. The overview
    # is always included as a fallback so the result is never empty.
    scored = sorted(
        _STUB_BOARD_DOC_PASSAGES,
        key=lambda p: _score_doc_passage(p, q),
        reverse=True,
    )
    top = [p for p in scored if _score_doc_passage(p, q) > 0]
    if not top:
        top = [_STUB_BOARD_DOC_PASSAGES[0]]  # overview fallback
    passages = [
        BoardDocPassage(
            text=p["text"],
            section=p["section"],
            sourceUri=f"board_doc://demo#{p['section'].replace(' ', '-')}",
        )
        for p in top[:3]
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

"""`SchematicJSON` Pydantic schema — spec 09 §4.

Aligned with (i) the cloud `schematic_data` shape (`components`/`nets`/
`sheet_count`), (ii) Forge's `BoardProfile` (`Part{ref,part,role,datasheet}`,
`Net{id,desc,max_voltage_v,...}`), and (iii) the `BomMatch` fields. Naming uses
Forge's `ref` (not Altium's `designator`) and `net.id` so the result drops
straight into profile-shaped consumers.

Provenance / safety rules baked in (09 §4, 03 §3.3.6):
  * `confidence` + `warnings` are always present so consumers treat this as
    model-derived, not authoritative.
  * `nominalVGuess` / `classGuess` are explicitly **advisory** and must NEVER be
    used as a setpoint — `get_documented_limit` stays the only limit source.

`extra="allow"` everywhere: forward-compatible, matching the proto / board-
profile policy (tolerate unknown keys the model may emit).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SchPin(BaseModel):
    model_config = ConfigDict(extra="allow")

    pin: str                       # pin number / designator on the part, e.g. "2"
    name: str | None = None        # pin function name if legible, e.g. "VOUT"
    net: str | None = None         # net id this pin connects to


class SchComponent(BaseModel):
    model_config = ConfigDict(extra="allow")

    ref: str                       # reference designator, e.g. "U4" (maps to Part.ref)
    part: str | None = None        # MPN / part name, e.g. "AMS1117-3.3" (maps to Part.part)
    type: str | None = None        # category: resistor|capacitor|ic|regulator|connector|...
    value: str | None = None       # "3.3V", "100kΩ", "0.1uF"
    package: str | None = None
    description: str | None = None
    sheet: str | None = None       # multi-sheet provenance (cloud schematic_data.sheet)
    pins: list[SchPin] = Field(default_factory=list)
    confidence: float | None = None


class SchNetNode(BaseModel):
    model_config = ConfigDict(extra="allow")

    ref: str                       # component ref
    pin: str                       # pin on that component


class SchNet(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str                                       # net name, e.g. "3V3" (maps to Net.id)
    nodes: list[SchNetNode] = Field(default_factory=list)  # pin-level endpoints
    classGuess: str | None = None                 # power|ground|signal|bus|clock|... (advisory)
    nominalVGuess: float | None = None            # advisory ONLY; never a documented limit


class SchematicSource(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: str                      # "image" | "pdf" | "csv" | "kicad_sch" | "altium_schdoc"
    uri: str | None = None         # snapshot://<frameId>, file path, etc.
    model: str | None = None       # vision model used, if any


class SchematicJSON(BaseModel):
    model_config = ConfigDict(extra="allow")

    schematicId: str | None = None
    source: SchematicSource
    confidence: float | None = None               # overall, model-derived
    components: list[SchComponent] = Field(default_factory=list)
    nets: list[SchNet] = Field(default_factory=list)
    sheetCount: int = 1
    warnings: list[str] = Field(default_factory=list)  # model fidelity warnings
    cite: str                                      # human-citable provenance string


__all__ = [
    "SchPin",
    "SchComponent",
    "SchNetNode",
    "SchNet",
    "SchematicSource",
    "SchematicJSON",
]

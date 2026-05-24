"""`parse_schematic()` — vision-first schematic → `SchematicJSON` (spec 09 §5.1).

A schematic photo / PDF page in, a validated + normalized `SchematicJSON` out.

The model call is INJECTED (`SchematicModelCall`) so this module is pure and
testable with no network — exactly the seam shape of
`orchestrator/snapshot/analyzer.py`. Production wires it to
`genai_seams.real_parse_schematic`, which reuses `_genai()` + the snapshot
vision model (gemini-3-pro-preview) with
`config={"response_mime_type":"application/json","response_schema": SchematicJSON}`.

Flow (09 §5.1, mirrors `managed_agents/structured_output.read_sme_response`):
  1. model_call(bytes, mime, hint, model) → a JSON string (forced JSON);
  2. validate against the §4 Pydantic model;
  3. on validation failure, ONE retry; still failing → a low-confidence STUB;
  4. run the deterministic normalizer (`normalize.normalize`) to fill `type`.

NEVER raises into the caller (01 §7) — every failure degrades to a cited,
low-confidence stub. The result is advisory context, NOT a limit source: a
parsed `nominalVGuess`/`classGuess` is never promoted to a setpoint (09 §4).
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from typing import Callable

from orchestrator.schematic.normalize import normalize
from orchestrator.schematic.schema import SchematicJSON, SchematicSource

log = logging.getLogger("forge.schematic.parser")

#: (image_bytes, mime_type, hint, model_name) -> JSON string (forced-JSON vision
#: answer). Injected so the parser is pure/testable; the real impl reuses the
#: snapshot vision model with response_mime_type=application/json.
SchematicModelCall = Callable[[bytes, str, "str | None", str], str]

#: Default vision model (the snapshot model — gemini-3-pro-preview). Kept here
#: as a fallback label; the real seam passes SNAPSHOT_MODEL explicitly.
DEFAULT_SCHEMATIC_MODEL = "gemini-3-pro-preview"

#: The structured-output instruction handed to the vision model. The schema is
#: ALSO enforced via response_schema at the real seam; this prose keeps a
#: stubbed / prose-only model honest about the shape + the advisory rules.
PARSE_PROMPT = (
    "You are Forge's schematic reader. Extract the components, reference "
    "designators, values, nets and pin↔net connections from this schematic "
    "image/page. Reply with ONE JSON object matching the SchematicJSON schema: "
    '{"schematicId": str|null, "source": {"kind","uri","model"}, '
    '"confidence": 0-1, "components": [{"ref","part","type","value","package",'
    '"description","sheet","pins":[{"pin","name","net"}],"confidence"}], '
    '"nets": [{"id","nodes":[{"ref","pin"}],"classGuess","nominalVGuess"}], '
    '"sheetCount": int, "warnings": [str], "cite": str}. '
    "Set per-component + overall `confidence` and list any legibility `warnings`. "
    "`nominalVGuess`/`classGuess` are ADVISORY only — they are NOT documented "
    "limits and must never be treated as a setpoint."
)


def _mime_for(image_bytes: bytes) -> str:
    """Best-effort MIME sniff: PDF magic → application/pdf, else image/jpeg.

    We don't decode the image (consistent with the snapshot path's no-decode
    policy); the bytes ride to the vision model as-is."""
    if image_bytes[:5] == b"%PDF-":
        return "application/pdf"
    return "image/jpeg"


def _kind_for(mime: str) -> str:
    return "pdf" if mime == "application/pdf" else "image"


def _cite(model: str, source_uri: str | None) -> str:
    today = _dt.date.today().isoformat()
    where = source_uri or "operator upload"
    return f"schematic image ({where}) · {model} · {today}"


def _stub(
    *, model: str, source_uri: str | None, mime: str, warning: str
) -> SchematicJSON:
    """A safe, valid, low-confidence fallback when the model output can't be read
    (mirrors structured_output._stub). Carries the failure as a warning so the
    consumer treats it as model-derived and effectively empty."""
    return SchematicJSON(
        source=SchematicSource(kind=_kind_for(mime), uri=source_uri, model=model),
        confidence=0.0,
        components=[],
        nets=[],
        sheetCount=1,
        warnings=[warning],
        cite=_cite(model, source_uri),
    )


def _validate(raw: str | None) -> SchematicJSON | None:
    """Parse + validate a JSON string into a `SchematicJSON`, tolerating a
    ```json fence / surrounding prose. None on any parse/validation failure."""
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip()
    # tolerate a fenced block / surrounding prose (the forced-JSON path is clean,
    # but a prose-y stub model may wrap it).
    try:
        data = json.loads(s)
    except Exception:  # noqa: BLE001
        lo, hi = s.find("{"), s.rfind("}")
        if not (0 <= lo < hi):
            return None
        try:
            data = json.loads(s[lo : hi + 1])
        except Exception:  # noqa: BLE001
            return None
    if not isinstance(data, dict):
        return None
    try:
        return SchematicJSON.model_validate(data)
    except Exception:  # noqa: BLE001 — never raise (01 §7)
        return None


def _stamp_provenance(
    sch: SchematicJSON, *, model: str, source_uri: str | None, mime: str
) -> SchematicJSON:
    """Backfill the provenance fields the orchestrator OWNS — source + cite are
    set by us, never trusted from the model (keeps the citation honest)."""
    sch.source = SchematicSource(kind=_kind_for(mime), uri=source_uri, model=model)
    sch.cite = _cite(model, source_uri)
    if sch.confidence is None:
        sch.confidence = 0.5
    return sch


def parse_schematic(
    image_bytes: bytes,
    hint: str | None = None,
    *,
    model_call: SchematicModelCall,
    model_name: str = DEFAULT_SCHEMATIC_MODEL,
    source_uri: str | None = None,
) -> SchematicJSON:
    """Parse a schematic image/PDF into a validated, normalized `SchematicJSON`.

    Args:
        image_bytes: the raw JPEG / PDF bytes (not decoded here).
        hint: optional operator hint (suspected board / part / section).
        model_call: INJECTED vision call returning a JSON string. Tests pass a
            fake; production passes `genai_seams.real_parse_schematic`.
        model_name: the vision model label to stamp into provenance.
        source_uri: e.g. "snapshot://<frameId>" or a file path, for provenance.

    Returns:
        A valid `SchematicJSON`. On any model/parse failure it degrades to a
        cited low-confidence stub — it NEVER raises (01 §7). The result is
        advisory context; `nominalVGuess`/`classGuess` are never setpoints (09 §4).
    """
    mime = _mime_for(image_bytes)
    prompt = PARSE_PROMPT
    if hint:
        prompt += f"\n\nOperator hint: {hint}"

    # ── 1) call the model (degrade to stub on any error) ──
    try:
        raw = model_call(image_bytes, mime, hint, model_name)
    except Exception as e:  # noqa: BLE001 — never fail-stop (01 §7)
        log.warning("parse_schematic model_call failed (%s); low-confidence stub", e)
        return _stamp_provenance(
            _stub(model=model_name, source_uri=source_uri, mime=mime,
                  warning=f"vision call failed: {e}"),
            model=model_name, source_uri=source_uri, mime=mime,
        )

    sch = _validate(raw)

    # ── 2) ONE retry on validation failure (09 §5.1) ──
    if sch is None:
        try:
            retry_prompt = (
                prompt + "\n\nYour previous reply did not validate against the "
                "SchematicJSON schema. Resend ONLY the JSON object, strictly valid."
            )
            raw_retry = model_call(image_bytes, mime, retry_prompt, model_name)
            sch = _validate(raw_retry)
        except Exception as e:  # noqa: BLE001
            log.warning("parse_schematic retry failed (%s)", e)
            sch = None

    # ── 3) still nothing → low-confidence stub ──
    if sch is None:
        log.warning("parse_schematic: model output failed validation; stub")
        return _stamp_provenance(
            _stub(model=model_name, source_uri=source_uri, mime=mime,
                  warning="model output failed SchematicJSON validation"),
            model=model_name, source_uri=source_uri, mime=mime,
        )

    # ── 4) deterministic normalize + provenance stamp ──
    sch = _stamp_provenance(sch, model=model_name, source_uri=source_uri, mime=mime)
    return normalize(sch)


__all__ = [
    "parse_schematic",
    "SchematicModelCall",
    "PARSE_PROMPT",
    "DEFAULT_SCHEMATIC_MODEL",
]

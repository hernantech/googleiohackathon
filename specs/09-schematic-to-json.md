# 09 — Schematic → JSON as a Managed Agent

> Investigation + design. **No code is implemented here** and the source pipeline (`~/work/galois/cloud`) is read-only. This spec answers: what the existing Galois Cloud "schematic/board → structured data" path actually does, and how to expose an equivalent capability to Forge's SME guild as a **managed agent** (Antigravity Interactions API, `antigravity-preview-05-2026`) and/or a Gemini vision call, with a JSON schema aligned to Forge's `bench_knowledge/` board profile + `KnowledgeAdapter`.
>
> Cross-refs: `05_board_knowledge_api.md` (board profile, the three lookups, documented-limit contract), `02_sme_persona_format.md` §4 + `orchestrator/managed_agents/structured_output.py` (SME structured-output reader), `orchestrator/genai_seams.py` (`_TOOL_SCHEMAS` / `_dispatch_tool`, `real_snapshot_model_call`, `build_real_deps`), `ROADMAP.md` "Managed Agents = Antigravity Interactions API". `orchestrator/proto/events.py` stays **frozen**.

---

## 1. Investigation — what the cloud pipeline actually does

There is **no single "schematic image → JSON" service** in `~/work/galois/cloud`. There are two distinct, real code paths that touch schematics/boards, plus one extraction pattern worth reusing. None of them use a vision model on a schematic image (confirmed: no `gemini.*schematic` / `vision.*pcb` references anywhere in the repo; `pdf_image.py` rasterizes datasheet *pages* for Anthropic image blocks, not schematics).

### 1.1 The BOM-analysis pipeline (`pipeline/`) — the real schematic→structured-data path

Service: a FastAPI app (`cloud/pipeline/pipeline/main.py`) exposing `POST /analyze` with `AnalyzeRequest{ analysis_id, project_id, bom_path, schematic_path?, instructions?, callback_url, auth_token? }`. It runs a LangGraph state machine (`cloud/pipeline/pipeline/graph.py`): `convert → search_datasheets → memory_lookup → parse_datasheets → generate_tests`, checkpointed in Postgres.

Stage 1, the conversion node (`cloud/pipeline/pipeline/nodes/conversion.py`), is where structured component + connectivity data is produced. It is a **deterministic parser, no LLM, no vision**:

- **BOM CSV** (`_parse_bom_csv`): normalizes arbitrary BOM headers to canonical keys via `_HEADER_MAP` (designator/value/description/package/quantity/mpn/manufacturer), categorizes by designator prefix via `_PREFIX_CATEGORY` (R→resistor, C→capacitor, U→ic, J/P→connector, D→diode, Q→transistor, Y/X→crystal, …), and flags passives to skip datasheet fetch.
- **Altium `.BomDoc`** (`_parse_bomdoc`): pipe-delimited procurement BOM state machine (CatalogItem + PartChoice records); emits components + per-MPN datasheet-URL hints. Note: BomDoc carries **no designators**.
- **Altium `.SchDoc` / `.PrjPcb`** (`_parse_altium_schematic`, `_parse_multi_schematics`, `_parse_prjpcb`): the **only connectivity path**. Uses the optional `altium-schematic-parser` library (declared as `altium = ["altium-schematic-parser>=1.0.0"]` in `cloud/pipeline/pyproject.toml`, **not installed by default**). Per component it pulls `designator`, `lib_ref`, `description`, `sheet`; per net it pulls a net name; merges multi-sheet projects and dedupes nets. If the lib is missing it logs a warning and returns `None` (the pipeline degrades gracefully to BOM-only).

The Stage-1 output shape is defined by `PipelineState` (`cloud/pipeline/pipeline/state.py`): `bom_components: list[dict]` (the per-row dicts above) and `schematic_data: dict` of shape:

```python
{
  "components": [ {"designator": "U1", "lib_ref": "...", "description": "...", "sheet": "power.SchDoc"}, ... ],
  "nets": ["3V3", "GND", "VIO", ...],   # net NAMES only — no pin endpoints
  "sheet_count": 2,
}
```

Downstream, Stage 4 (`test_generation.py`) reads `schematic_data["nets"]` only as a `{schematic_nets_summary}` string for the section-grouping LLM prompt (`BOM_ANALYZER_PLAN.md` §"Orchestrator prompt for section grouping"). The committed artifact is `bom-analyses/{id}/schematic.json` + `bom.json` (`BOM_ANALYZER_PLAN.md` §"Output Format").

**Limitation that matters for Forge:** the Altium path extracts component instances and *net names*, but **not pin-level endpoints** (which pin of U1 connects to which net) and not footprints. Connectivity is name-level, not graph-level.

### 1.2 The PCB Viewer worker (`pcb-viewer/`) — geometry converter, NOT JSON

`cloud/pcb-viewer/api/main.py` (FastAPI) accepts `.PcbDoc` / `.SchDoc` / `.zip` uploads, enqueues a Redis job (`pcb:queue`), and `pcb-worker/worker.py` runs `convert_pcb.py` as a subprocess. `convert_pcb.py` calls **KiCad 8 `pcbnew.PCB_IO_MGR.Load(ALTIUM_DESIGNER, …)` → `Save(KICAD_SEXP, …)`** — it converts Altium *board geometry* to a `.kicad_pcb` s-expression for the KiCanvas frontend renderer. Its only JSON output is a small fidelity summary (`{warnings, summary:{footprints,tracks,zones}}`). `.SchDoc` schematic conversion is explicitly **unsupported** (`convert_pcb.py` raises `unsupported_format` for schematics — "KiCad 8's Python schematic API is not yet stable for headless import"). This path produces **no component/net JSON** and is not reusable for Forge.

### 1.3 The datasheet extractor (`pipeline/agents/parser.py`) — reusable LLM extraction pattern

`extract_component_context(...)` uses a cheap LLM (Anthropic Haiku, OpenAI GPT-4o-mini fallback) at `temperature=0` to pull test-relevant structured info (Identification, Abs-Max Ratings, Pin Configuration, Test-Relevant Parameters, …) from datasheet markdown. This is the closest existing "free-form input → structured fields" precedent and validates the prompt-shape Forge would use for a vision pass.

### 1.4 Summary table

| Question | Answer (cloud) |
|---|---|
| What does schematic→structured-data do? | Deterministic parse of BOM CSV / Altium `.BomDoc` + (optional) `.SchDoc`/`.PrjPcb` into components + net **names** (`conversion.py`). |
| Input formats | BOM CSV, Altium `.BomDoc`, Altium `.SchDoc`/`.PrjPcb`. PCB Viewer also takes `.PcbDoc`/`.zip` (geometry only). **No** KiCad `.sch`, Gerber, netlist, or schematic-image/photo path. |
| Output schema | `bom_components[]` + `schematic_data{components[], nets[], sheet_count}` (`state.py`); committed `bom.json`/`schematic.json`. **Net names only, no pin endpoints, no footprints.** |
| Implementation | Deterministic parser (CSV + Altium binary parsers). **No vision, no OCR, no CV.** LLM appears only for *datasheet* extraction (Haiku/4o-mini) and section grouping/test-gen (Sonnet). |
| How invoked | HTTP `POST /analyze` (FastAPI) → LangGraph background task, Postgres checkpointer, callback URL. PCB Viewer is HTTP upload → Redis queue → `pcb-worker` subprocess. |
| Deps / runtime | Python ≥3.11; `langgraph`, `langchain-*`, `pdfplumber`, `pymupdf4llm`, `psycopg`, `pyyaml`; optional `altium-schematic-parser` (native-ish, not installed), and KiCad 8 `pcbnew` (heavy native, viewer only). |

**Implication for the design:** Forge cannot just "shell out to the cloud pipeline." The cloud path requires Postgres + a callback web service + (for any real connectivity) a not-installed Altium parser, and it never accepts the input Forge actually has at the bench — **a photo/PDF/screenshot of a schematic**. So the Forge-facing capability must be built around either a vision model or a pure-Python parser inside a sandbox, reusing the cloud pipeline's *schema and prompt shape* rather than its service.

---

## 2. Representative input → JSON example

Input the operator gives Forge at the bench: a **photo or PDF page of the schematic** for the bring-up board (the same board as `bench_knowledge/examples/bq79616-bringup-2026-05.yaml`). There is no existing image fixture in either repo, so the example below is the *target* output for the recommended Forge schema (§4), shown for the power section of that board:

```json
{
  "schematicId": "bq79616-bringup-2026-05",
  "source": {"kind": "image", "uri": "snapshot://<frameId>", "model": "gemini-3-pro-preview"},
  "confidence": 0.78,
  "components": [
    {"ref": "U4", "part": "AMS1117-3.3", "type": "regulator", "value": "3.3V",
     "package": "SOT-223", "description": "fixed 3.3V LDO", "sheet": "power",
     "pins": [
       {"pin": "1", "name": "GND", "net": "GND"},
       {"pin": "2", "name": "VOUT", "net": "3V3"},
       {"pin": "3", "name": "VIN", "net": "VIN_5V"}
     ]},
    {"ref": "U1", "part": "ESP32-WROOM-32", "type": "ic", "value": null,
     "package": "module", "description": "host MCU", "sheet": "digital",
     "pins": [{"pin": "2", "name": "3V3", "net": "3V3"}, {"pin": "1", "name": "GND", "net": "GND"}]}
  ],
  "nets": [
    {"id": "3V3", "nodes": [{"ref": "U4", "pin": "2"}, {"ref": "U1", "pin": "2"}],
     "classGuess": "power", "nominalVGuess": 3.3},
    {"id": "VIN_5V", "nodes": [{"ref": "U4", "pin": "3"}], "classGuess": "power"},
    {"id": "GND", "nodes": [{"ref": "U4", "pin": "1"}, {"ref": "U1", "pin": "1"}], "classGuess": "ground"}
  ],
  "warnings": ["pin numbers for U1 partially obscured; nets inferred from labels"],
  "cite": "schematic image (operator upload) · gemini-3-pro-preview · 2026-05-23"
}
```

This is **strictly richer** than the cloud `schematic_data` (which would only give `{"components":[{"designator":"U4","lib_ref":"AMS1117-3.3","description":"...","sheet":"power"}], "nets":["3V3","VIN_5V","GND"], "sheet_count":2}`) because a vision pass can recover pin↔net endpoints that the cloud net-name path drops — but every node carries `confidence`/`warnings` so consumers know it is model-derived, not authoritative.

---

## 3. Can a managed agent do this conversion? — recommendation

**Recommended: (c) hybrid, vision-first, with the sandbox doing validation/structuring — but ship the vision-only path (b) for the hackathon and treat the sandbox as the upgrade.**

Rationale, weighing accuracy / latency / dependency weight / reuse:

- **(b) Gemini vision call** (`gemini-3-pro-preview`, exactly Forge's existing `real_snapshot_model_call` path in `orchestrator/genai_seams.py`): this is the only option that accepts the input the bench actually has — a schematic **photo/PDF page**. The cloud pipeline literally cannot do this (it needs the native Altium `.SchDoc` binary). Latency is one `generate_content` call (~the snapshot path, seconds), it reuses wiring that already works (`build_snapshot_model_call`, `_genai()`), and structured output is enforced with `response_mime_type="application/json"` + the §4 schema. Accuracy is the risk: vision can misread pin numbers / dense nets. Mitigated by emitting per-node `confidence` + `warnings` and by **never letting it produce a documented limit** — limits still come only from `get_documented_limit` (05 §4, 03 §3.3.6).

- **(a) Antigravity managed agent running pipeline code:** the sandbox is Linux + Python 3.12 + `code_execution` (ROADMAP §"Managed Agents"). A pure-Python parser (CSV BOM, or a KiCad-`.kicad_sch`/netlist text parser) runs fine there. But it does **not** help with a schematic *image*, and the cloud's only connectivity parser (`altium-schematic-parser`) is an uninstalled optional dep, while `pcbnew` is too heavy/native for the sandbox. The sandbox's real value is **deterministic validation**: take the vision JSON, run code to check ref-des uniqueness, net-name sanity, reconcile against the loaded board profile / BOM (`lookup_bom`), and reject/repair low-confidence nodes — exactly the cloud `conversion.py` normalization logic, reimplemented small. Cost: ~70s cold per the ROADMAP SME decision, which is why it is the deferred half.

- **(c) hybrid = (b) then (a):** vision extracts → sandbox/code validates+structures+reconciles → emit final JSON. This is the accuracy/robustness sweet spot and matches Forge's own SME structured-output philosophy (02 §4: prefer the committed `/workspace/output.json`, validate with Pydantic, one retry). It is also a natural fit for the SME structured-output reader already in `orchestrator/managed_agents/structured_output.py`.

**Net recommendation:** implement **`parse_schematic(image|pdf) → SchematicJSON` as a Gemini-vision tool now** (reusing `real_snapshot_model_call`'s client + model), validated by a new Pydantic model and a deterministic post-process that reuses cloud `conversion.py`'s `_HEADER_MAP`/`_PREFIX_CATEGORY` normalization. Wire the Antigravity sandbox as the validator behind the same function signature later (matching the `summon_one` "swap the path, keep the seam" pattern in `genai_seams.build_real_deps`). Do **not** call the cloud service.

---

## 4. Forge-facing JSON schema (`SchematicJSON`)

Aligned with (i) the cloud `schematic_data` shape (`components`/`nets`/`sheet_count`), (ii) Forge's `BoardProfile` (`orchestrator/knowledge/board_profile.py`: `Part{ref,part,role,datasheet}`, `Net{id,desc,max_voltage_v,...}`, `Rail`, `TestPoint`), and (iii) the `BomMatch` fields (`orchestrator/knowledge/bom.py`). Naming uses Forge's `ref` (not Altium's `designator`) and `net.id` so the result drops straight into profile-shaped consumers. Defined as Pydantic with `extra="allow"` (forward-compatible, matches the proto/profile policy).

```python
class SchPin(BaseModel):
    pin: str                  # pin number/designator on the part, e.g. "2"
    name: str | None = None   # pin function name if legible, e.g. "VOUT"
    net: str | None = None    # net id this pin connects to

class SchComponent(BaseModel):
    ref: str                       # reference designator, e.g. "U4" (maps to Part.ref)
    part: str | None = None        # MPN / part name, e.g. "AMS1117-3.3" (maps to Part.part)
    type: str | None = None        # category: resistor|capacitor|ic|regulator|connector|... (cloud _PREFIX_CATEGORY)
    value: str | None = None       # "3.3V", "100kΩ", "0.1uF"
    package: str | None = None
    description: str | None = None
    sheet: str | None = None       # multi-sheet provenance (cloud schematic_data.sheet)
    pins: list[SchPin] = []
    confidence: float | None = None

class SchNetNode(BaseModel):
    ref: str                  # component ref
    pin: str                  # pin on that component

class SchNet(BaseModel):
    id: str                          # net name, e.g. "3V3" (maps to Net.id)
    nodes: list[SchNetNode] = []     # pin-level endpoints (RICHER than cloud's name-only nets)
    classGuess: str | None = None    # power|ground|signal|bus|clock|... (advisory)
    nominalVGuess: float | None = None  # advisory ONLY; never a documented limit

class SchematicSource(BaseModel):
    kind: str                 # "image" | "pdf" | "csv" | "kicad_sch" | "altium_schdoc"
    uri: str | None = None    # snapshot://<frameId>, file path, etc.
    model: str | None = None  # vision model used, if any

class SchematicJSON(BaseModel):
    model_config = ConfigDict(extra="allow")
    schematicId: str | None = None
    source: SchematicSource
    confidence: float | None = None   # overall, model-derived
    components: list[SchComponent] = []
    nets: list[SchNet] = []
    sheetCount: int = 1
    warnings: list[str] = []          # model fidelity warnings (mirrors convert_pcb.py warnings)
    cite: str                          # human-citable provenance string
```

**Provenance / safety rules baked in:** `confidence` + `warnings` are always present so consumers treat this as model-derived; `nominalVGuess`/`classGuess` are explicitly advisory and **must not** be used as setpoints — `get_documented_limit` remains the only source of limits (05 §4). A `SchematicJSON` can be lowered into the existing `BoardProfile` shape (ref→`parts`, net.id→`nets`) by a deterministic mapper, but only with `designator_inferred`-style honesty: profile entries minted from a schematic image carry a `source: "schematic_image"` marker and no limit fields.

---

## 5. How the JSON reaches the SME agents — integration points

Three hooks, smallest-blast-radius first. All live in `orchestrator/`; `proto/events.py` is untouched.

### 5.1 New SME function-calling tool `parse_schematic` (primary)

- **Where:** add a 4th entry to `_TOOL_SCHEMAS` in `orchestrator/genai_seams.py` and a branch in `_dispatch_tool(name, args, knowledge)`. The SME loop (`_run_sme_tool_loop`) already executes declared tools and streams each call — no engine change needed.
- **Schema:** `parse_schematic(source_uri: str, hint?: str) -> SchematicJSON-as-dict`. `source_uri` is a `snapshot://<frameId>` (resolve via `orchestrator/storage/frame_store.py`, same store `analyze_snapshot` uses) or an uploaded file path. `hint` lets the SME pass the suspected board/part.
- **Implementation module:** new `orchestrator/schematic/parser.py` exposing `parse_schematic(jpeg_or_pdf_bytes, hint, *, model_call) -> SchematicJSON`. It reuses the **injected `ModelCall` pattern** from `orchestrator/snapshot/analyzer.py` (pure + testable, no network in tests) and the real call reuses `genai_seams._genai()` + `SNAPSHOT_MODEL` exactly like `real_snapshot_model_call`, but with `config={"response_mime_type":"application/json","response_schema": SchematicJSON}`. Post-parse: validate with the §4 Pydantic model; run the deterministic normalizer (ported `_categorise`/`_HEADER_MAP` logic) to fill `type`; on validation failure do ONE retry then return a low-confidence stub (mirrors `managed_agents/structured_output.read_sme_response`). Never raises (01 §7).

### 5.2 New retrieval tool `lookup_schematic(query)` over a cached parse

- **Where:** companion `_TOOL_SCHEMAS`/`_dispatch_tool` entry, backed by `KnowledgeAdapter`.
- **What:** once a schematic has been parsed in a session, cache the `SchematicJSON` on the adapter and let SMEs query it by ref / net / part without re-running vision — e.g. "what connects to net 3V3?", "which pin of U4 is VOUT?". Returns the matching `SchComponent`/`SchNet` subset with the `cite`. This is the `lookup_bom`-style read path (`orchestrator/knowledge/bom.py`).

### 5.3 Ingest into the `KnowledgeAdapter` / board profile (so existing lookups answer from it)

- **Where:** `orchestrator/knowledge/__init__.py` (`KnowledgeAdapter`) + `orchestrator/knowledge/board_profile.py`.
- **What:** add `KnowledgeAdapter.ingest_schematic(sch: SchematicJSON)` that merges parsed components/nets into the in-memory `BoardProfile` (additively, marked `source="schematic_image"`, no limit fields). After ingest, the **existing** `lookup_board_doc` (its `profileMatches` loop over `profile.parts`/`nets`, `orchestrator/knowledge/lookups.py`) and `get_documented_limit` (`limits.LimitResolver`, structured-first) answer board-topology questions from the parsed schematic with **zero SME-side changes** — the SMEs keep calling the same three tools. Limits still resolve only from documented profile/datasheet values, never from `nominalVGuess`.

### 5.4 Optional Antigravity validator (deferred upgrade)

- **Where:** behind the `parse_schematic` seam, selected like `build_real_deps` does for `summon_one`.
- **What:** `client.interactions.create(agent="antigravity-preview-05-2026", input=<validate-prompt + vision JSON>, environment=<kept-warm env_id>)`, then `managed_agents.read_sme_response`-style parse of `/workspace/output.json`. Sandbox runs Python to dedupe refs, sanity-check nets, reconcile against `lookup_bom`. Same function signature, so 5.1's tool body swaps without touching the SME loop.

---

## 6. Implementation checklist (ordered, for the follow-up coding pass)

1. **Schema module** — add `orchestrator/schematic/__init__.py` + `orchestrator/schematic/schema.py` with the §4 Pydantic models (`SchematicJSON`, `SchComponent`, `SchNet`, `SchPin`, `SchematicSource`). `extra="allow"`. Unit test: round-trips the §2 example; rejects a missing `ref`.
2. **Normalizer** — add `orchestrator/schematic/normalize.py` porting cloud `conversion.py`'s `_HEADER_MAP` + `_PREFIX_CATEGORY`/`_categorise` (copy, do not import — cloud is read-only) to fill `SchComponent.type` from `ref` prefix and tidy values. Test: U4→ic/regulator, R5→resistor, J3→connector.
3. **Vision parser (b)** — add `orchestrator/schematic/parser.py::parse_schematic(bytes, hint, *, model_call)` using the injected-`ModelCall` pattern from `orchestrator/snapshot/analyzer.py`; validate → normalize → one-retry → low-confidence stub. Pure/testable with a fake `model_call`. Tests under `orchestrator/schematic/tests/` (no network); a `@live` variant (08 §5 marker) for the real Gemini call, excluded from CI.
4. **Real model wiring** — in `orchestrator/genai_seams.py` add `real_parse_schematic(...)` reusing `_genai()` + `SNAPSHOT_MODEL` with `response_mime_type=application/json` (+ `response_schema`); add `build_parse_schematic()` to `orchestrator/seams.py` mirroring `build_snapshot_model_call` (real when `GEMINI_API_KEY`, else stub). No change to `proto/events.py`.
5. **SME tool `parse_schematic`** — append to `_TOOL_SCHEMAS` and add the `_dispatch_tool` branch in `genai_seams.py`; resolve `snapshot://` URIs via `orchestrator/storage/frame_store.py`. Test: the tool loop executes it and the call is captured in `tool_calls` / streamed via `on_tool_call`.
6. **`lookup_schematic` + session cache** — add the cache slot + `lookup_schematic(query)` tool (5.2). Test ref/net/part queries return the right subset with a `cite`.
7. **KnowledgeAdapter ingest** — add `KnowledgeAdapter.ingest_schematic(...)` and `BoardProfile` additive-merge helper (5.3), `source="schematic_image"`, no limit fields. Test: after ingest, `lookup_board_doc("3V3")` surfaces the parsed net in `profileMatches`, and `get_documented_limit("3V3","net")` still returns `found=False` (no invented limit) unless the YAML profile documents it.
8. **Safety regression** — assert that `nominalVGuess`/`classGuess` never become a `documentedLimitRef`; reuse the 03/05 gate tests. SafetyGate path unchanged.
9. **(Deferred) Antigravity validator** — `orchestrator/schematic/sandbox.py` with the `interactions.create` validate pass (5.4) + kept-warm `environment` reuse; same signature, gated behind an env flag.
10. **Docs** — note the new tools in `README.md`/`ROADMAP.md` Phase 4; keep `08_test_plan.md`'s contract matrix in sync.

**Frozen / do-not-touch:** `orchestrator/proto/events.py`; the existing three lookup tools' behavior; the documented-limit contract (every operator-facing value carries a `documentedLimitRef`). The schematic JSON is advisory context, never a limit source.

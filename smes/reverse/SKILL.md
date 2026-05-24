# Skills index for @reverse

| Skill | When to use | Tools |
|---|---|---|
| read-marking | identify a chip from its visible marking | lookup_datasheet (confirm), snapshot |
| orientation-check | verify pin-1 / package orientation | lookup_datasheet, snapshot |
| infer-topology | map connector/header pinout from a photo | lookup_board_doc, snapshot |
| assembly-fault | spot tombstone/bridge/missing/rotated parts | snapshot (then flag to domain SME) |
| request-closeup | the marking is illegible at current resolution | inspect_closeup step |

## Tools — which and WHEN
- The latest camera snapshot arrives IN the briefing (vision text). Read it first.
- `lookup_datasheet` — to CONFIRM a marking you read maps to a real part, and to
  get its package/pin-1 reference. Use after you have a candidate marking.
- `lookup_board_doc` — to cross-check inferred topology against the documented
  board profile (refs, nets, connectors).
- `get_documented_limit` — only if confirming a value tied to an identification.
- `run_analysis` — not used. You read images; you do not compute.

## Reasoning pattern (retrieve → reason → retrieve, bounded)
1. Read the snapshot in the briefing; extract the candidate marking/observation.
2. `lookup_datasheet` to confirm the marking → part, and pin-1/package.
3. `lookup_board_doc` to cross-check topology against the documented board.
4. If illegible, conclude low-confidence and propose `inspect_closeup`. Hand the
   identified part to @librarian/@sourcing. Within the round cap.

## Output contract — SmeResponse (forced JSON)
```json
{"confidence": <0-1>,
 "claim": "<the identification / observation, one sentence>",
 "rationale": "<2-3 sentences; what the image shows + CITE any confirming source>",
 "proposedAction": null OR {"tool":"inspect_closeup",
   "args":{"target":"<chip/area>"},
   "instruction":"Photograph <area> at higher resolution.","risk":"LOW"}}
```
Use `proposedAction` only to request a closeup; else null.

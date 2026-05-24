# Skills index for @layout

| Skill | When to use | Tools |
|---|---|---|
| decoupling-loop | a cap is too far / loop too long for a transient | lookup_board_doc, run_analysis |
| return-path-check | ground bounce / reference discontinuity | lookup_board_doc |
| impedance-parasitic | a reflection traces to a stub / mismatch | lookup_datasheet, run_analysis |
| thermal-copper | a hot-spot from under-sized copper | lookup_board_doc, run_analysis |
| photo-topology | read placement/routing from a board image | lookup_board_doc (+ snapshot in briefing) |

## Tools — which and WHEN
- `lookup_board_doc` — the structured board profile: parts/refs, net names, test
  points, the placement context. Your primary source for "what is where".
- `lookup_datasheet` — a part's recommended layout / pad / impedance guidance
  when a specific component is implicated.
- `get_documented_limit` — the cited limit when you must reference a documented
  geometry/electrical bound. Never invent it.
- `run_analysis` — REAL Python, ONLY to CALCULATE from known geometry: a trace
  parasitic L/R, a loop inductance, copper-area thermal rise. Pass concrete
  dimensions + units. Not for lookups.

## Reasoning pattern (retrieve → reason → retrieve, bounded)
1. `lookup_board_doc` to locate the implicated refs/nets and read the snapshot.
2. `lookup_datasheet` for the part's layout guidance if one is named.
3. `run_analysis` only to compute a parasitic/thermal number from dimensions.
4. Conclude with the geometric cause; hand the electrical verdict to @power /
   @signal. Stay within the round cap.

## Output contract — SmeResponse (forced JSON)
```json
{"confidence": <0-1>,
 "claim": "<one-sentence answer>",
 "rationale": "<2-3 sentences; CITE the board-doc section / datasheet page / photo>",
 "proposedAction": null OR {"tool":"probe_net|inspect_closeup",
   "args":{"target":"<net/area>"},
   "instruction":"<imperative step for the operator>","risk":"LOW|MEDIUM|HIGH"}}
```
Use `proposedAction` only when a concrete operator step is warranted; else null.

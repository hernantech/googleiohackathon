# Skills index for @signal

| Skill | When to use | Tools |
|---|---|---|
| capture-readout | a scope/logic capture must be interpreted | lookup_board_doc, run_analysis |
| termination-check | reflections / ringing on a bus | lookup_datasheet, lookup_board_doc |
| level-compatibility | two parts on one bus disagree on logic levels | lookup_datasheet, get_documented_limit |
| timing-budget | setup/hold or baud margin from edge data | lookup_datasheet, run_analysis |
| reflection-vs-droop | tell a power problem from a SI problem | lookup_board_doc (then defer to @power) |

## Tools — which and WHEN
- `lookup_board_doc` — which net is which bus, test-point names, the documented
  bus topology. Ground the capture against the board first.
- `lookup_datasheet` — a transceiver/MCU pin's VIH/VIL, max baud, drive strength,
  termination guidance. Use when a bus part is named.
- `get_documented_limit` — the cited max voltage for a bus net before you tell
  the operator any level/threshold. Never invent it.
- `run_analysis` — REAL Python, ONLY to CALCULATE from captured numbers you have:
  a timing/baud margin, an RC settling time, a reflection coefficient. Pass the
  concrete edge times / impedances + units. Not for lookups.

## Reasoning pattern (retrieve → reason → retrieve, bounded)
1. `lookup_board_doc` to map the net/bus and find the capture's context.
2. `lookup_datasheet` for the transceiver/MCU electrical + timing spec.
3. `run_analysis` only to compute a margin from the captured values.
4. `get_documented_limit` before recommending any concrete level/rate.
5. Conclude once the capture is explained; stay within the round cap.

## Output contract — SmeResponse (forced JSON)
```json
{"confidence": <0-1>,
 "claim": "<one-sentence answer>",
 "rationale": "<2-3 sentences; CITE the datasheet page / board-doc section / capture>",
 "proposedAction": null OR {"tool":"probe_net|inspect_closeup",
   "args":{"target":"<net>"},
   "instruction":"<imperative step for the operator>","risk":"LOW|MEDIUM|HIGH"}}
```
Use `proposedAction` only when a concrete operator step is warranted; else null.

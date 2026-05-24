# Skills index for @power

| Skill | When to use | Tools |
|---|---|---|
| rail-droop-diagnosis | a rail sags under load / brown-out | lookup_board_doc, lookup_datasheet, run_analysis |
| rail-current-budget | dial in a PSU current limit from a parts list | lookup_board_doc, run_analysis |
| ldo-vs-buck-selection | choose a regulator for a Vin/Vout/Iload | lookup_datasheet, run_analysis |
| psu-setpoint | propose a concrete PSU voltage/current | get_documented_limit (REQUIRED) |
| thermal-headroom | check a linear reg's dissipation at worst-case Vin | lookup_datasheet, run_analysis |

## Tools — which and WHEN
- `lookup_board_doc` — board-level facts: which rail powers what, net/test-point
  names, the documented bring-up order. Start here for "what is on this board".
- `lookup_datasheet` — a part's electrical behavior: dropout, Iq, load-transient
  response curve, abs-max. Use when a regulator/load part is named.
- `get_documented_limit` — the deterministic cited max V/I for a net/rail/part.
  ALWAYS call before proposing any `set_psu` value. Never invent a setpoint.
- `run_analysis` — REAL Python compute, ONLY to CALCULATE from numbers you
  already have: worst-case rail current from a load list, droop magnitude, an RC
  constant, a thermal budget. Pass concrete values + units. Not for lookups.

## Reasoning pattern (retrieve → reason → retrieve, bounded)
1. `lookup_board_doc` to ground the rail/topology.
2. `lookup_datasheet` for any named regulator/load part's spec.
3. `get_documented_limit` before any numeric setpoint.
4. `run_analysis` only when a number must be computed from retrieved inputs.
5. Stop calling tools once grounded; conclude. Keep it within the round cap.

## Output contract — SmeResponse (forced JSON)
```json
{"confidence": <0-1>,
 "claim": "<one-sentence answer>",
 "rationale": "<2-3 sentences; CITE the datasheet page / board-doc section / limit>",
 "proposedAction": null OR {"tool":"set_psu|probe_net|disable_psu_output",
   "args":{"target":"<net>","voltage_v":<n>,"current_limit_a":<n>},
   "instruction":"<imperative step for the operator>","risk":"LOW|MEDIUM|HIGH"}}
```
Use `proposedAction` only when a concrete operator step is warranted; else null.

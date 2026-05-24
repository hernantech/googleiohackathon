# Skills index for @sentinel

| Skill | When to use | Tools |
|---|---|---|
| hazard-detection-vision | smoke/flame/melting/hot-iron in the frame | snapshot (in briefing) |
| panic-keyword-detection | "ow"/"fire"/"smoke"/"shock"/panicked tone in transcript | briefing transcript |
| overcurrent-trip-policy | operator reads a current clearly out of range | get_documented_limit |
| thermal-runaway-detection | a component reported too-hot-to-touch | get_documented_limit |

## Tools — which and WHEN
- The live snapshot + transcript arrive IN the briefing. Read them first; that is
  your primary sensor. You DO NOT respond to every cue — only on hazard detection.
- `get_documented_limit` — to confirm a reading the operator stated is actually
  out of range against the documented bound before escalating to HALT/WARN.
- `lookup_board_doc` — rarely; to confirm which rail/net a hazard cue maps to.
- `run_analysis` — not used. Hazard detection is real-time; do not compute.

## Reasoning pattern (detect → confirm → flag, bounded)
1. Scan the snapshot/transcript in the briefing for a hazard cue.
2. If the cue is a numeric reading, `get_documented_limit` to confirm it is truly
   out of range.
3. Classify the tier (HALT / WARN / INFO) and emit ONCE. Do not diagnose; do not
   re-scan looking for more. Within the round cap.

## Output contract — SmeResponse (forced JSON)
```json
{"confidence": <hazard 0-1>,
 "claim": "<hazard headline, one sentence>",
 "rationale": "<2-3 sentences; cite the frame timestamp / transcript line / reading>",
 "proposedAction": null OR {"tool":"disable_psu_output",
   "args":{"channel":1},
   "instruction":"Turn the PSU output OFF now.","risk":"LOW"}}
```
Emit `proposedAction` only on a hazard (HALT → power-down step); else null. On no
hazard, return a low-confidence null-action response.

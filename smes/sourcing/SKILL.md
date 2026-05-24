# Skills index for @sourcing

| Skill | When to use | Tools |
|---|---|---|
| identify-part | what populates this ref / what is this part | lookup_board_doc, lookup_datasheet |
| find-substitute | original is out-of-stock / damaged | lookup_datasheet (compare specs) |
| compat-check | does a candidate match footprint/pinout/spec | lookup_datasheet, get_documented_limit |
| bom-readout | consult documented BOM quantities | lookup_board_doc |
| availability-flag | flag a hard-to-source part | lookup_board_doc, lookup_datasheet |

## Tools — which and WHEN
- `lookup_board_doc` — the structured board profile + BOM: which ref is which
  part, documented quantities. Start here to identify the part in question.
- `lookup_datasheet` — the original's and any candidate's package, ratings, and
  pinout, to judge a substitute's compatibility.
- `get_documented_limit` — the cited rating when a substitute's envelope must be
  bounded against the net it sits on. Never invent a rating.
- `run_analysis` — rarely; only to CALCULATE a derating/margin from cited specs.
  Pass concrete numbers + units. Not for lookups.

## Reasoning pattern (retrieve → reason → retrieve, bounded)
1. `lookup_board_doc` to identify the original part at the ref.
2. `lookup_datasheet` for the original's key specs, then the candidate's.
3. `get_documented_limit` to confirm the candidate fits the net's envelope.
4. Conclude with original→candidate and the differing spec; hand the
   electrical-fitness call to the domain SME. Within the round cap.

## Output contract — SmeResponse (forced JSON)
```json
{"confidence": <0-1>,
 "claim": "<the part identification or substitute, one sentence>",
 "rationale": "<2-3 sentences; original vs candidate, key spec, CITE the datasheet>",
 "proposedAction": null}
```
`proposedAction` is null — sourcing is procurement advice, not a bench operation.

# Skills index for @firmware

| Skill | When to use | Tools |
|---|---|---|
| bringup-sequence | MCU won't boot / wrong init order | lookup_board_doc, lookup_datasheet |
| console-baud | garbled or silent serial console | lookup_datasheet, get_documented_limit |
| register-write | a specific register must be set | lookup_datasheet (REQUIRED for value) |
| flash-procedure | flashing fails or image unknown | lookup_board_doc, lookup_datasheet |
| wake-precondition | check a wake/reset/VIO line before blaming FW | lookup_datasheet, get_documented_limit |

## Tools — which and WHEN
- `lookup_board_doc` — the documented bring-up order, which MCU/companion parts
  are present, console net/test-point names. Ground the sequence first.
- `lookup_datasheet` — the chip's power-up/wake requirement, register map,
  console baud, flashing notes (e.g. BQ79616 wake/VIO, ESP32 UART/IO levels).
- `get_documented_limit` — the cited limit for any wake/VIO/level you reference
  before recommending it. Never invent a register value or voltage.
- `run_analysis` — rarely; only to CALCULATE (e.g. a baud divisor from a clock).
  Pass concrete numbers + units. Not for lookups.

## Reasoning pattern (retrieve → reason → retrieve, bounded)
1. `lookup_board_doc` for the documented bring-up order + parts.
2. `lookup_datasheet` for the exact wake/register/baud spec of the chip.
3. `get_documented_limit` before any numeric VIO/level/value you recommend.
4. Conclude with the ORDERED steps; stay within the round cap. If a rail
   precondition is unverified, hand off to @power instead of assuming it.

## Output contract — SmeResponse (forced JSON)
```json
{"confidence": <0-1>,
 "claim": "<one-sentence answer>",
 "rationale": "<2-3 sentences; CITE the datasheet page / board-doc section>",
 "proposedAction": null OR {"tool":"serial_send|flash_mcu|probe_net",
   "args":{"target":"<net/console>","payload":"<bytes/cmd>"},
   "instruction":"<imperative step for the operator>","risk":"LOW|MEDIUM|HIGH"}}
```
Use `proposedAction` only when a concrete operator step is warranted; else null.

"""Regenerate the golden wire corpus from the canonical instances.

Run from the repo root:  python testdata/wire/_generate.py
Writes one pretty-printed JSON file per canonical type into this directory.
The committed JSON is the single source of truth for cross-language parity
(WP-6) and the system-level contract test (08 §3.1)."""

from __future__ import annotations

import json
import pathlib

from orchestrator.proto.examples import canonical

OUT = pathlib.Path(__file__).parent


def main() -> None:
    for name, instance in canonical().items():
        payload = instance.model_dump(mode="json")
        (OUT / f"{name}.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"wrote {len(canonical())} golden files to {OUT}")


if __name__ == "__main__":
    main()

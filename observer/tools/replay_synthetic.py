"""Seed a local observer DB with a synthetic rework session, then (optionally)
keep dribbling events so you can watch the dashboard update live — all offline,
no orchestrator, no Gemini key required.

Usage (from observer/):
    python -m tools.replay_synthetic --db /tmp/observer.db          # one shot
    python -m tools.replay_synthetic --db /tmp/observer.db --loop   # keep adding

Then point the dashboard at the same DB:
    OBSERVER_DB_PATH=/tmp/observer.db OBSERVER_BUS_URL=ws://disabled \
      uvicorn observer.main:app --port 8090
(The ingest loop will just keep retrying the bogus bus URL harmlessly while the
 web view serves the seeded rows.)
"""

from __future__ import annotations

import argparse
import time

from observer.distill import distill_once
from observer.ingest import persist_event
from observer.store import Store
from tests import synthetic as S


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/tmp/observer.db")
    ap.add_argument("--session", default="op-bench-01")
    ap.add_argument("--loop", action="store_true", help="keep emitting chatter")
    args = ap.parse_args()

    store = Store(args.db)
    for ev in S.scenario():
        persist_event(store, ev, default_session_id=args.session)
    distill_once(store, window_s=3600, max_events=200, model_call=None)  # heuristic
    print(f"seeded {store.event_count()} events into {args.db} for session {args.session}")

    if args.loop:
        i = 0
        msgs = [
            "Probing the 3V3 rail now.",
            "@power, can you confirm the short location?",
            "Reflowing U4 pin 12.",
            "Rail is holding 3.30 V under load.",
        ]
        try:
            while True:
                persist_event(
                    store, S.chat(msgs[i % len(msgs)], mid=f"loop-{i}"),
                    default_session_id=args.session,
                )
                distill_once(store, window_s=3600, max_events=200, model_call=None)
                print("…emitted", msgs[i % len(msgs)])
                i += 1
                time.sleep(5)
        except KeyboardInterrupt:
            print("\nstopped")


if __name__ == "__main__":
    main()

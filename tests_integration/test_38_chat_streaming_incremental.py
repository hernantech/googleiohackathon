"""/v2/chat streams deliberation INCREMENTALLY (not one batched burst at the end).

Regression for the MEDIUM found on PR #12: the /v2/chat handler ran the
synchronous GraphEngine ON the event loop, so the per-session WS writer task
could not flush queued events until the whole run returned — every streamed
frame arrived in one burst at the very end.

The fix runs the engine via asyncio.to_thread with a loop-bound publisher
(call_soon_threadsafe), so the writer gets loop time to flush each event as the
guild deliberates. Here we inject a SLOW summon_one (a real per-SME delay) and
assert the per-SME ChatMessages arrive SPREAD OVER TIME — i.e. the @power frame
lands well before @signal finishes, which is only possible if the writer flushed
mid-run.

Deterministic + offline: stub seams, a fake clock-free delay, no network.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from orchestrator.chat_bus.bus import ChatBus
from orchestrator.graph.state import RouteDecision
from orchestrator.proto.events import SmeResponse, now_ns


_FAST_SME_DELAY_S = 0.05   # @power concludes quickly...
_SLOW_SME_DELAY_S = 0.40   # ...while @signal is still thinking


def _drain_replay(ws) -> None:
    while True:
        if ws.receive_json().get("kind") == "ReplayDone":
            return


def test_chat_streams_per_sme_messages_spread_over_time(monkeypatch):
    """The #power message must arrive well BEFORE #signal finishes — proving the
    writer flushed the @power frame mid-run (incremental), not in one end-of-run
    burst.

    The guild now fans out CONCURRENTLY (Part C.1), so this no longer relies on
    sequential execution: @power is a FAST SME and @signal a SLOW one running in
    parallel, and the engine flushes each SME's frame in roster order as soon as
    it resolves. The fast @power frame therefore reaches the WS a clear margin
    before the slow @signal frame — only possible if the writer got loop time
    mid-run (the PR #12 fix), and only this ordering is possible because flushing
    is roster-ordered + incremental, not batched."""
    import orchestrator.main as main_mod

    # Hermetic bus/sessions so this test owns the replay buffer + fan-out.
    monkeypatch.setattr(main_mod, "bus", ChatBus())
    monkeypatch.setattr(main_mod, "_sessions", {})

    # Force a deterministic two-SME guild: @power fast, @signal slow. They run
    # concurrently, so wall-clock ≈ the slow SME — but the fast SME's frame must
    # still stream out first (incremental, roster-ordered flush).
    delays = {"@power": _FAST_SME_DELAY_S, "@signal": _SLOW_SME_DELAY_S}

    def staggered_summon(sme, summon):
        time.sleep(delays[sme])
        return SmeResponse(smeId=sme, callId=summon.callId, confidence=0.8,
                           claim=f"{sme} claim", rationale="r", ts=now_ns())

    monkeypatch.setattr(main_mod.deps, "classify",
                        lambda t, r: RouteDecision(True, ["@power", "@signal"], "topic"))
    monkeypatch.setattr(main_mod.deps, "summon_one", staggered_summon)

    client = TestClient(main_mod.app)
    with client.websocket_connect("/v2/chat?sessionId=stream-timing") as ws:
        _drain_replay(ws)
        ws.send_text(json.dumps({"kind": "ChatMessage", "body": "diagnose the guild"}))

        arrivals: dict[str, float] = {}
        order: list[str] = []
        # Read until the final LiveSpeaker Transcript (end of run).
        for _ in range(30):
            e = ws.receive_json()
            now = time.monotonic()
            if e.get("kind") == "ChatMessage" and e.get("authorKind") == "sme":
                arrivals.setdefault(e["channelId"], now)
                order.append(e["channelId"])
            if e.get("kind") == "Transcript" and not e.get("partial", True):
                break

    assert "#power" in arrivals and "#signal" in arrivals, arrivals
    # The fast @power frame flushed well before the slow @signal frame → the
    # writer flushed mid-run (incremental), not in one end-of-run burst.
    gap = arrivals["#signal"] - arrivals["#power"]
    assert gap >= (_SLOW_SME_DELAY_S - _FAST_SME_DELAY_S) * 0.5, (
        f"per-SME frames arrived in a burst (gap={gap:.3f}s); not streaming")
    # Order is preserved: @power before @signal.
    assert order.index("#power") < order.index("#signal")

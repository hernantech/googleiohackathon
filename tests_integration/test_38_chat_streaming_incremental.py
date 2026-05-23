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


_PER_SME_DELAY_S = 0.30  # each SME "thinks" this long inside the worker thread


def _drain_replay(ws) -> None:
    while True:
        if ws.receive_json().get("kind") == "ReplayDone":
            return


def test_chat_streams_per_sme_messages_spread_over_time(monkeypatch):
    """With a slow two-SME guild, the #power message must arrive at least one
    per-SME delay BEFORE the #signal message — proving incremental flush."""
    import orchestrator.main as main_mod

    # Hermetic bus/sessions so this test owns the replay buffer + fan-out.
    monkeypatch.setattr(main_mod, "bus", ChatBus())
    monkeypatch.setattr(main_mod, "_sessions", {})

    # Force a deterministic two-SME guild with a measurable per-SME delay.
    def slow_summon(sme, summon):
        time.sleep(_PER_SME_DELAY_S)
        return SmeResponse(smeId=sme, callId=summon.callId, confidence=0.8,
                           claim=f"{sme} claim", rationale="r", ts=now_ns())

    monkeypatch.setattr(main_mod.deps, "classify",
                        lambda t, r: RouteDecision(True, ["@power", "@signal"], "topic"))
    monkeypatch.setattr(main_mod.deps, "summon_one", slow_summon)

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
    # #power finished a full SME-delay before #signal → the writer flushed the
    # @power frame mid-run (incremental), not in one end-of-run burst.
    gap = arrivals["#signal"] - arrivals["#power"]
    assert gap >= _PER_SME_DELAY_S * 0.5, (
        f"per-SME frames arrived in a burst (gap={gap:.3f}s); not streaming")
    # Order is preserved: @power before @signal.
    assert order.index("#power") < order.index("#signal")

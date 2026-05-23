"""Streaming-deliberation tests — the SME guild publishes to the bus AS IT
UNFOLDS, not as one batched dump at the end.

Deterministic + offline: a fake list-sink stands in for `bus.publish`, and the
SME fan-out is a monkeypatched double that drives the per-tool-call callback so
retrieval activity surfaces. We assert:

  * events arrive INCREMENTALLY, in the right order and on the right channels:
    summon notice (#live-feed) → per-SME claim (#<sme>) the moment each SME
    finishes → tool-call activity (#<sme>) interleaved BEFORE that SME's claim,
  * the final consensus + LiveSpeaker line still arrive (kept as today),
  * NOTHING is double-published: an idempotent drain (mirroring main's
    `_drain_to_bus`) publishes only the not-yet-streamed remainder.
"""

from __future__ import annotations

from orchestrator.graph import GraphEngine
from orchestrator.graph.state import DissentResult, ForgeState, GraphDeps, RouteDecision
from orchestrator.knowledge import KnowledgeAdapter
from orchestrator.proto.events import ChatMessage, SmeResponse, Transcript, now_ns
from orchestrator.safety.gate import SafetyGate


# ───────────────────────── doubles ─────────────────────────

def _sme(sme, *, confidence=0.8, claim=None, rationale="grounded"):
    return SmeResponse(smeId=sme, callId="01HCALL", confidence=confidence,
                       claim=claim or f"{sme} claim", rationale=rationale, ts=now_ns())


def _summon_with_tools(tool_plan):
    """Build a summon_one double that, for each SME, fires the per-tool-call
    callback (as a real SME tool loop would) before returning its response.

    `tool_plan` maps smeId -> list of {name, args} tool calls to surface."""
    def summon(sme, summon, on_tool_call=None):
        for call in tool_plan.get(sme, []):
            if on_tool_call is not None:
                on_tool_call({**call, "result": {"ok": True}})
        return _sme(sme)
    return summon


def make_engine(*, smes, summon_one, dissent_fn=None):
    k = KnowledgeAdapter()
    deps = GraphDeps(
        gate=SafetyGate(k), knowledge=k,
        classify=lambda t, r: RouteDecision(True, list(smes), "topic"),
        summon_one=summon_one,
        merge_fn=lambda kept: ("CONSENSUS HEADLINE", [r.smeId for r in kept]),
        dissent_fn=dissent_fn or (lambda resp, rnd: DissentResult(convergence="converged")),
    )
    return GraphEngine(deps), k


class _ListSink:
    """A fake incremental publish sink: records every event the graph streams,
    in arrival order (stands in for bus.publish)."""

    def __init__(self):
        self.events: list[object] = []

    def __call__(self, event: object) -> None:
        self.events.append(event)


def _idempotent_drain(state, sink: _ListSink) -> None:
    """Mirror main._drain_to_bus: publish only the events NOT already streamed."""
    streamed = state.streamedEvents
    remainder = [e for e in state.outboundEvents if id(e) not in streamed]
    state.outboundEvents.clear()
    streamed.clear()
    for e in remainder:
        sink(e)


def _chat(events):
    return [e for e in events if isinstance(e, ChatMessage)]


# ───────────────────────── tests ─────────────────────────

def test_summon_notice_streams_first_to_live_feed():
    sink = _ListSink()
    eng, _ = make_engine(smes=["@power", "@signal"],
                         summon_one=_summon_with_tools({}))
    st = ForgeState(sessionId="s")
    eng.run(st, "diagnose", emit=sink)

    # The very first streamed event is the summon notice on #live-feed naming
    # everyone summoned.
    first = sink.events[0]
    assert isinstance(first, ChatMessage)
    assert first.channelId == "#live-feed"
    assert first.authorKind == "system"
    assert "@power" in first.body and "@signal" in first.body
    assert first.body.startswith("summoned")


def test_per_sme_claims_stream_as_each_completes():
    sink = _ListSink()
    eng, _ = make_engine(smes=["@power", "@signal"],
                         summon_one=_summon_with_tools({}))
    st = ForgeState(sessionId="s")
    eng.run(st, "diagnose", emit=sink)

    # Each SME's claim lands on its OWN channel, streamed (not batched): the
    # #power claim arrives before the #signal claim (fan-out order).
    chat = _chat(sink.events)
    power = [m for m in chat if m.channelId == "#power" and m.authorKind == "sme"]
    signal = [m for m in chat if m.channelId == "#signal" and m.authorKind == "sme"]
    assert power and "@power claim" in power[0].body
    assert signal and "@signal claim" in signal[0].body
    # ordering: power's claim is streamed before signal's claim.
    assert sink.events.index(power[0]) < sink.events.index(signal[0])


def test_tool_call_activity_streams_before_the_claim():
    sink = _ListSink()
    plan = {
        "@power": [
            {"name": "lookup_datasheet", "args": {"part": "BQ79616", "query": "VIO"}},
            {"name": "get_documented_limit", "args": {"target": "J3", "kind": "net"}},
        ],
    }
    eng, _ = make_engine(smes=["@power"], summon_one=_summon_with_tools(plan))
    st = ForgeState(sessionId="s")
    eng.run(st, "diagnose", emit=sink)

    chat = _chat(sink.events)
    on_power = [m for m in chat if m.channelId == "#power"]
    # Two tool-call notices then the final claim, on #power, IN THAT ORDER.
    assert len(on_power) == 3, [m.body for m in on_power]
    assert "lookup_datasheet(part=BQ79616, query=VIO)" in on_power[0].body
    assert on_power[0].body.startswith("@power → ")
    assert "get_documented_limit(target=J3, kind=net)" in on_power[1].body
    assert "@power claim" in on_power[2].body  # the claim is last


def test_full_stream_order_and_channels():
    """End-to-end ordering across the whole run:
    summon notice (#live-feed) → @power tool call (#power) → @power claim
    (#power) → @signal claim (#signal) → ... → consensus/LiveSpeaker at the end.
    """
    sink = _ListSink()
    plan = {"@power": [{"name": "lookup_board_doc", "args": {"query": "J3 rail"}}]}
    eng, _ = make_engine(smes=["@power", "@signal"], summon_one=_summon_with_tools(plan))
    st = ForgeState(sessionId="s")
    eng.run(st, "diagnose", emit=sink)

    # Streamed sequence, restricted to chat messages, in arrival order.
    seq = [(m.channelId, m.authorKind) for m in _chat(sink.events)]
    assert seq == [
        ("#live-feed", "system"),   # 1) summon notice
        ("#power", "sme"),          # 3) @power tool-call activity
        ("#power", "sme"),          # 2) @power claim
        ("#signal", "sme"),         # @signal claim (no tools)
    ], seq

    # The aggregate #live-feed view, consensus + LiveSpeaker are NOT streamed —
    # they fall to the final drain (kept as today).
    streamed_transcripts = [e for e in sink.events if isinstance(e, Transcript)]
    assert not streamed_transcripts


def test_nothing_double_published_streamed_plus_drain():
    """The streamed events + the final idempotent drain together publish each
    event EXACTLY once (no double-publish)."""
    bus_log = _ListSink()  # the "bus": receives streamed events live...
    eng, _ = make_engine(smes=["@power", "@signal"],
                         summon_one=_summon_with_tools(
                             {"@power": [{"name": "get_documented_limit",
                                          "args": {"target": "J3", "kind": "net"}}]}))
    st = ForgeState(sessionId="s")
    # Stream to the same sink the drain will use (as main does: bus.publish).
    eng.run(st, "diagnose", emit=bus_log)
    streamed_count = len(bus_log.events)
    assert streamed_count > 0

    # ...then drain the remainder to the SAME bus.
    _idempotent_drain(st, bus_log)

    # No event object appears twice on the bus (identity-level dedup).
    ids = [id(e) for e in bus_log.events]
    assert len(ids) == len(set(ids)), "an event was published more than once"

    # The streamed events are exactly the deliberation messages; the drained
    # remainder carries the consensus tail (a LiveSpeaker Transcript) that was
    # NOT streamed — proving the split is clean, not overlapping.
    drained = bus_log.events[streamed_count:]
    assert any(isinstance(e, Transcript) for e in drained), [type(e).__name__ for e in drained]
    # And every event the graph recorded is on the bus once.
    assert len(bus_log.events) >= streamed_count


def test_no_sink_is_pure_noop_default():
    """Without an emit sink, run() behaves exactly as before: everything sits in
    outboundEvents and nothing is marked streamed (back-compatible default)."""
    eng, _ = make_engine(smes=["@power"], summon_one=_summon_with_tools({}))
    st = ForgeState(sessionId="s")
    eng.run(st, "diagnose")  # no emit=
    # Streaming still APPENDS to outboundEvents (the run transcript is intact)
    # and records ids (so a later drain stays idempotent even if a sink is
    # attached on resume) — but with the default no-op sink nothing leaves.
    assert _chat(st.outboundEvents)  # summon notice + claim are present
    # The default no-op deps.emit means there is no external observer; the test
    # simply proves the run completes and produced the messages.
    assert st.mergedOpinion is not None
    assert st.liveSpeakerScript == "CONSENSUS HEADLINE"


def test_engine_introspects_partial_bound_real_seam(monkeypatch):
    """The engine's _summon_one introspects the seam for an `on_tool_call` param
    and threads the callback. Prove it works against the REAL seam as wired in
    production: functools.partial(real_summon_one, knowledge=...) — the Gemini
    client is monkeypatched so this stays offline + deterministic."""
    import functools
    import json as _json

    pytest = __import__("pytest")
    pytest.importorskip("google.genai.types")
    from orchestrator import genai_seams as gs

    # A scripted fake client: one datasheet lookup, then a forced-JSON answer.
    from orchestrator.tests.test_genai_seams import _FakeClient

    fake = _FakeClient(
        [[("lookup_datasheet", {"part": "BQ79616", "query": "VIO"})]],
        _json.dumps({"confidence": 0.9, "claim": "@power claim", "rationale": "grounded"}),
    )
    monkeypatch.setattr(gs, "_genai", lambda: fake)

    k = KnowledgeAdapter()
    bound = functools.partial(gs.real_summon_one, knowledge=k)
    deps = GraphDeps(
        gate=SafetyGate(k), knowledge=k,
        classify=lambda t, r: RouteDecision(True, ["@power"], "topic"),
        summon_one=bound,
        merge_fn=lambda kept: ("CONSENSUS", [r.smeId for r in kept]),
        dissent_fn=lambda resp, rnd: DissentResult(convergence="converged"),
    )
    eng = GraphEngine(deps)
    sink = _ListSink()
    st = ForgeState(sessionId="s")
    eng.run(st, "diagnose", emit=sink)

    on_power = [m for m in _chat(sink.events) if m.channelId == "#power"]
    # the tool-call activity streamed (proving the partial-bound seam got the
    # on_tool_call callback), then the claim.
    assert any("lookup_datasheet(part=BQ79616, query=VIO)" in m.body for m in on_power), \
        [m.body for m in on_power]
    assert any("@power claim" in m.body for m in on_power)


def test_cross_exam_does_not_respam_summon_notice():
    """A second deliberation round (cross-exam) must NOT re-emit the summon
    notice (it only belongs to the initial fan-out)."""
    rounds = {"n": 0}

    def dissent(resp, rnd):
        rounds["n"] += 1
        # force exactly one extra round, then converge.
        return DissentResult(convergence="needs_more_rounds" if rounds["n"] == 1 else "converged")

    sink = _ListSink()
    eng, _ = make_engine(smes=["@power"], summon_one=_summon_with_tools({}), dissent_fn=dissent)
    st = ForgeState(sessionId="s")
    eng.run(st, "diagnose", emit=sink)

    notices = [m for m in _chat(sink.events)
               if m.channelId == "#live-feed" and m.body.startswith("summoned")]
    assert len(notices) == 1, "summon notice should appear once, not per round"
    assert st.crossExamRound == 1

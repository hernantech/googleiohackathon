"""Concurrent SME fan-out (Part C.1) — _parallel_summon runs all SMEs in
parallel while preserving deterministic roster order, the live streaming sink,
and a per-SME wall-clock timeout (GR-5).

Deterministic + offline: summon_one is a double. We assert:
  * the guild runs CONCURRENTLY (wall-clock ≈ slowest SME, not the sum);
  * state.smeResponses is written in deterministic ROSTER order regardless of
    completion order;
  * streamed events stay in roster order on the bus, with each SME's tool-call
    activity BEFORE its claim (identical ordering contract to the sequential
    version, just faster);
  * a per-SME timeout → confidence-0 <timeout> placeholder, others still land;
  * the streaming sink still fires for every SME even though the work happens in
    worker threads (marshalled back through the main-thread _emit in roster order).
"""

from __future__ import annotations

import threading
import time

from orchestrator.graph import GraphEngine
from orchestrator.graph import engine as engine_mod
from orchestrator.graph.state import DissentResult, ForgeState, GraphDeps, RouteDecision
from orchestrator.knowledge import KnowledgeAdapter
from orchestrator.proto.events import ChatMessage, SmeResponse, now_ns
from orchestrator.safety.gate import SafetyGate


def _sme(sme, *, confidence=0.8, claim=None, rationale="grounded"):
    return SmeResponse(smeId=sme, callId="01HCALL", confidence=confidence,
                       claim=claim or f"{sme} claim", rationale=rationale, ts=now_ns())


def make_engine(*, smes, summon_one):
    k = KnowledgeAdapter()
    deps = GraphDeps(
        gate=SafetyGate(k), knowledge=k,
        classify=lambda t, r: RouteDecision(True, list(smes), "topic"),
        summon_one=summon_one,
        merge_fn=lambda kept: ("CONSENSUS", [r.smeId for r in kept]),
        dissent_fn=lambda resp, rnd: DissentResult(convergence="converged"),
    )
    return GraphEngine(deps), k


class _ListSink:
    def __init__(self):
        self.events: list[object] = []

    def __call__(self, event: object) -> None:
        self.events.append(event)


def _chat(events):
    return [e for e in events if isinstance(e, ChatMessage)]


# ───────────────────────── concurrency ─────────────────────────────────────

def test_smes_run_concurrently_not_sequentially():
    """N SMEs each sleeping D run in ~D total, not ~N*D — proving real fan-out."""
    delay = 0.15
    n = 5

    def summon(sme, summon, on_tool_call=None):
        time.sleep(delay)
        return _sme(sme)

    smes = [f"@s{i}" for i in range(n)]
    eng, _ = make_engine(smes=smes, summon_one=summon)
    st = ForgeState(sessionId="s")
    t0 = time.monotonic()
    eng.run(st, "diagnose")
    elapsed = time.monotonic() - t0
    # sequential would be n*delay = 0.75s; concurrent ≈ delay. Allow headroom.
    assert elapsed < delay * (n - 1), f"fan-out not concurrent: {elapsed:.3f}s"
    assert len(st.smeResponses) == n


def test_max_workers_covers_full_roster(monkeypatch):
    """The pool is sized to the roster so all SMEs start together (no queueing)."""
    seen_concurrent = {"max": 0}
    live = {"n": 0}
    lock = threading.Lock()
    barrier_release = threading.Event()

    def summon(sme, summon, on_tool_call=None):
        with lock:
            live["n"] += 1
            seen_concurrent["max"] = max(seen_concurrent["max"], live["n"])
        barrier_release.wait(timeout=2)
        with lock:
            live["n"] -= 1
        return _sme(sme)

    # release everyone shortly after they've all clocked in.
    def releaser():
        time.sleep(0.2)
        barrier_release.set()
    threading.Thread(target=releaser, daemon=True).start()

    smes = [f"@s{i}" for i in range(6)]
    eng, _ = make_engine(smes=smes, summon_one=summon)
    eng.run(ForgeState(sessionId="s"), "diagnose")
    assert seen_concurrent["max"] == 6  # all ran at once


# ───────────────────────── deterministic order ─────────────────────────────

def test_responses_in_roster_order_regardless_of_completion():
    """Even when SMEs complete in REVERSE order, state.smeResponses and the
    streamed claims are in roster order."""
    smes = ["@power", "@signal", "@firmware"]
    # later-in-roster SMEs finish FIRST (reverse completion).
    delays = {"@power": 0.30, "@signal": 0.15, "@firmware": 0.01}

    def summon(sme, summon, on_tool_call=None):
        time.sleep(delays[sme])
        return _sme(sme)

    sink = _ListSink()
    eng, _ = make_engine(smes=smes, summon_one=summon)
    st = ForgeState(sessionId="s")
    eng.run(st, "diagnose", emit=sink)

    # state written in roster order
    assert list(st.smeResponses.keys()) == smes

    # streamed claims in roster order (power, then signal, then firmware), even
    # though firmware finished first.
    claims = [m for m in _chat(sink.events) if m.authorKind == "sme"]
    assert [m.channelId for m in claims] == ["#power", "#signal", "#firmware"]


def test_tool_calls_stream_before_claim_per_sme():
    """Within each SME the buffered tool-call activity streams BEFORE its claim,
    and SMEs stay in roster order on the bus."""
    plan = {
        "@power": [{"name": "lookup_datasheet", "args": {"part": "BQ79616", "query": "VIO"}}],
        "@signal": [{"name": "lookup_board_doc", "args": {"query": "J3 net"}}],
    }

    def summon(sme, summon, on_tool_call=None):
        # signal is faster, but ordering on the bus must still be roster order.
        time.sleep(0.10 if sme == "@power" else 0.01)
        for call in plan.get(sme, []):
            if on_tool_call is not None:
                on_tool_call({**call, "result": {"ok": True}})
        return _sme(sme)

    sink = _ListSink()
    eng, _ = make_engine(smes=["@power", "@signal"], summon_one=summon)
    eng.run(ForgeState(sessionId="s"), "diagnose", emit=sink)

    seq = [(m.channelId, m.body) for m in _chat(sink.events) if m.authorKind == "sme"]
    # #power tool-call then #power claim, THEN #signal tool-call then #signal claim.
    assert seq[0][0] == "#power" and "lookup_datasheet" in seq[0][1]
    assert seq[1][0] == "#power" and "@power claim" in seq[1][1]
    assert seq[2][0] == "#signal" and "lookup_board_doc" in seq[2][1]
    assert seq[3][0] == "#signal" and "@signal claim" in seq[3][1]


# ───────────────────────── per-SME timeout (GR-5) ──────────────────────────

def test_per_sme_timeout_yields_placeholder_others_land(monkeypatch):
    """A hung SME hits SME_WAIT_S → confidence-0 <timeout> placeholder; the other
    SMEs still produce real responses."""
    monkeypatch.setattr(engine_mod, "SME_WAIT_S", 0.2)

    def summon(sme, summon, on_tool_call=None):
        if sme == "@signal":
            time.sleep(5)  # hang past the timeout
        return _sme(sme)

    sink = _ListSink()
    eng, _ = make_engine(smes=["@power", "@signal", "@firmware"], summon_one=summon)
    st = ForgeState(sessionId="s")
    t0 = time.monotonic()
    eng.run(st, "diagnose", emit=sink)
    elapsed = time.monotonic() - t0

    assert elapsed < 3, f"timeout did not bound the hung SME: {elapsed:.2f}s"
    assert st.smeResponses["@signal"].claim == "<timeout>"
    assert st.smeResponses["@signal"].confidence == 0.0
    # the others landed with real claims, in roster order.
    assert st.smeResponses["@power"].claim == "@power claim"
    assert st.smeResponses["@firmware"].claim == "@firmware claim"
    assert list(st.smeResponses.keys()) == ["@power", "@signal", "@firmware"]
    # a claim still streamed for the timed-out SME (the <timeout> placeholder).
    sig = [m for m in _chat(sink.events) if m.channelId == "#signal"]
    assert sig and "<timeout>" in sig[0].body


def test_summon_raising_yields_placeholder():
    """An SME whose summon_one RAISES → placeholder, not a fail-stop."""
    def summon(sme, summon, on_tool_call=None):
        if sme == "@power":
            raise RuntimeError("boom")
        return _sme(sme)

    eng, _ = make_engine(smes=["@power", "@signal"], summon_one=summon)
    st = ForgeState(sessionId="s")
    eng.run(st, "diagnose")
    assert st.smeResponses["@power"].claim == "<timeout>"
    assert "boom" in st.smeResponses["@power"].rationale
    assert st.smeResponses["@signal"].claim == "@signal claim"


# ───────────────────────── streaming sink fires from threads ───────────────

def test_streaming_sink_fires_for_every_sme():
    """Every SME's claim reaches the streaming sink even though the work runs in
    worker threads (marshalled through the main-thread _emit)."""
    smes = ["@power", "@signal", "@firmware", "@layout"]

    def summon(sme, summon, on_tool_call=None):
        if on_tool_call is not None:
            on_tool_call({"name": "lookup_board_doc", "args": {"query": "x"},
                          "result": {"ok": True}})
        return _sme(sme)

    sink = _ListSink()
    eng, _ = make_engine(smes=smes, summon_one=summon)
    eng.run(ForgeState(sessionId="s"), "diagnose", emit=sink)

    for sme in smes:
        ch = "#" + sme.lstrip("@")
        msgs = [m for m in _chat(sink.events) if m.channelId == ch]
        # one tool-call activity + one claim per SME.
        assert len(msgs) == 2, f"{sme}: {[m.body for m in msgs]}"
        assert "lookup_board_doc" in msgs[0].body
        assert f"{sme} claim" in msgs[1].body


def test_two_arg_summon_one_still_works():
    """A back-compatible 2-arg summon_one (no on_tool_call) runs concurrently and
    still streams claims — the engine introspects and omits the callback."""
    def summon(sme, summon):  # no on_tool_call param
        return _sme(sme)

    sink = _ListSink()
    eng, _ = make_engine(smes=["@power", "@signal"], summon_one=summon)
    st = ForgeState(sessionId="s")
    eng.run(st, "diagnose", emit=sink)
    assert list(st.smeResponses.keys()) == ["@power", "@signal"]
    claims = [m for m in _chat(sink.events) if m.authorKind == "sme"]
    assert [m.channelId for m in claims] == ["#power", "#signal"]

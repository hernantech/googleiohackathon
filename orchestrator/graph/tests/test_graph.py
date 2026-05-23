"""GR-1..GR-15 — per-node graph tests (01 §8).

SMEs / Live / model steps are faked with deterministic doubles; SafetyGate +
KnowledgeAdapter are the real P2/P1 components. No network.
"""

from __future__ import annotations

import json

from orchestrator.graph import GraphEngine
from orchestrator.graph.state import DissentResult, ForgeState, GraphDeps, RouteDecision
from orchestrator.knowledge import KnowledgeAdapter
from orchestrator.proto.events import (
    ConfirmationRequest,
    ConfirmationResponse,
    DissentPair,
    DissentReport,
    EvidenceRef,
    FrameRef,
    Goodbye,
    ProposedAction,
    SafetyInterrupt,
    SmeResponse,
    SnapshotAnalysis,
    now_ns,
)
from orchestrator.safety.gate import SafetyGate


# ───────────────────────── doubles ─────────────────────────

def _sme(sme, *, confidence=0.8, actions=None, callId="01HCALL"):
    return SmeResponse(smeId=sme, callId=callId, confidence=confidence,
                       claim=f"{sme} claim", rationale="r",
                       proposedActions=actions or [], ts=now_ns())


def _set_psu_action(v=30.0, target="J3", ref="board_doc p.4"):
    return ProposedAction(
        actor="operator", tool="set_psu",
        argsJson=json.dumps({"voltage_v": v, "current_limit_a": 0.5, "target": target}),
        rationale="apply stack", risk="MEDIUM",
        instruction=f"Set PSU to {v} V across {target}.", documentedLimitRef=ref)


def make_engine(*, classify=None, summon_one=None, merge_fn=None, dissent_fn=None, cap=64):
    k = KnowledgeAdapter()
    deps = GraphDeps(
        gate=SafetyGate(k), knowledge=k,
        classify=classify or (lambda t, r: RouteDecision(True, ["@power"], "topic")),
        summon_one=summon_one or (lambda sme, s: _sme(sme)),
        merge_fn=merge_fn or (lambda kept: ("headline", [r.smeId for r in kept])),
        dissent_fn=dissent_fn or (lambda resp, rnd: DissentResult(convergence="converged")),
        aggregator_queue_max=cap,
    )
    return GraphEngine(deps), k


def _events(state, kind):
    return [e for e in state.outboundEvents if type(e).__name__ == kind]


# ───────────────────────── tests ─────────────────────────

def test_gr1_perception_snapshot():
    eng, _ = make_engine()
    st = ForgeState(sessionId="s")
    snap = SnapshotAnalysis(
        jobId="j", frame=FrameRef(uri="mem:f1", width=1920, height=1080, ts=1, sourceSeq=1),
        model="gemini-3-pro", analysis="cell-stack unplugged",
        cites=[EvidenceRef(kind="datasheet", uri="datasheet://bq79616", note="§7")], ts=1)
    eng.ingest_snapshot(st, snap)
    assert st.latestFrame.uri == "mem:f1"
    assert st.latestSnapshot is snap
    cms = [e for e in _events(st, "ChatMessage") if e.channelId == "#live-feed"]
    assert cms and cms[0].bodyContentType == "application/json"
    assert _events(st, "CheckpointMarker")


def test_gr1b_snapshot_threaded_into_summon():
    eng, _ = make_engine()
    st = ForgeState(sessionId="s")
    eng.ingest_snapshot(st, SnapshotAnalysis(
        jobId="j", frame=FrameRef(uri="mem:f1", width=1, height=1, ts=1, sourceSeq=1),
        model="m", analysis="a", ts=1))
    eng.run(st, "what's wrong here")
    assert "mem:f1" in st.pendingSummon.contextRefs


def test_gr2_malformed_transcript_survives():
    eng, _ = make_engine()
    st = ForgeState(sessionId="s")
    eng.ingest_malformed(st)  # must not raise
    assert _events(st, "Goodbye")
    assert "perception_invalid" in st.errors


def test_gr3_mention_forces_sme():
    eng, _ = make_engine(classify=lambda t, r: RouteDecision(True, ["@power"], "t"))
    st = ForgeState(sessionId="s")
    eng.run(st, "hey @signal can you look at this")
    assert "@signal" in st.pendingSummon.smes


def test_gr4_bad_routing_falls_back():
    def boom(t, r):
        raise ValueError("bad json")
    eng, _ = make_engine(classify=boom)
    st = ForgeState(sessionId="s")
    res = eng.run(st, "chitchat with no mention")
    assert res.status == "direct_reply"
    assert "routing_failed" in st.errors


def test_gr5_sme_timeout_partial():
    def summon(sme, s):
        if sme == "@signal":
            raise TimeoutError("deadline")
        return _sme(sme)
    eng, _ = make_engine(
        classify=lambda t, r: RouteDecision(True, ["@power", "@signal", "@firmware"], "t"),
        summon_one=summon)
    st = ForgeState(sessionId="s")
    eng.run(st, "diagnose")
    assert st.smeResponses["@signal"].confidence == 0.0
    assert st.smeResponses["@signal"].claim == "<timeout>"
    assert st.smeResponses["@power"].confidence == 0.8


def test_gr6_aggregator_backpressure_coalesces():
    eng, _ = make_engine(
        classify=lambda t, r: RouteDecision(True, ["@power", "@signal", "@firmware"], "t"),
        cap=2)
    st = ForgeState(sessionId="s")
    eng.run(st, "diagnose")  # 3 SMEs, cap 2 → coalesce, no exception
    feed = [e for e in _events(st, "ChatMessage") if e.channelId == "#live-feed"]
    assert feed  # aggregator still produced a consolidated view


def test_gr7_merge_excludes_low_confidence():
    def summon(sme, s):
        return _sme(sme, confidence=0.1) if sme == "@signal" else _sme(sme, confidence=0.8)
    eng, _ = make_engine(
        classify=lambda t, r: RouteDecision(True, ["@power", "@signal"], "t"), summon_one=summon)
    st = ForgeState(sessionId="s")
    eng.run(st, "diagnose")
    assert any("@signal" in q for q in st.mergedOpinion.openQuestions)
    assert "@signal" not in st.mergedOpinion.supportingSmes


def test_gr8_dissent_loop_cap():
    def dissent(resp, rnd):
        return DissentResult(
            pairwise=[DissentPair(a="@power", b="@firmware", aClaim="x", bClaim="y", crux="root")],
            convergence="needs_more_rounds")
    eng, _ = make_engine(
        classify=lambda t, r: RouteDecision(True, ["@power", "@firmware"], "t"), dissent_fn=dissent)
    st = ForgeState(sessionId="s")
    res = eng.run(st, "diagnose")
    assert st.crossExamRound == 2  # bounced exactly twice
    assert res.status == "complete"
    assert _events(st, "DissentReport")


def test_gr9_guild_lookup_auto_allowed():
    lookup = ProposedAction(actor="guild", tool="lookup_datasheet",
                            argsJson='{"part":"bq79616"}', rationale="ground", risk="LOW")
    eng, _ = make_engine(summon_one=lambda sme, s: _sme(sme, actions=[lookup]))
    st = ForgeState(sessionId="s")
    eng.run(st, "diagnose")
    assert not st.pendingConfirmations
    assert lookup in st.approvedActions


def test_gr10_over_limit_denied_warn():
    over = _set_psu_action(v=35.0)  # > J3's 30 V
    eng, _ = make_engine(summon_one=lambda sme, s: _sme("@power", actions=[over])
                         if sme == "@power" else _sme(sme))
    st = ForgeState(sessionId="s")
    eng.run(st, "fix")
    warns = [e for e in _events(st, "SafetyInterrupt") if e.severity == "WARN"]
    assert warns
    assert not st.pendingConfirmations  # no card for a denied step


def test_gr11_high_step_confirm_then_done():
    step = _set_psu_action(v=30.0)  # HIGH, within J3 limit
    eng, _ = make_engine(summon_one=lambda sme, s: _sme("@power", actions=[step])
                         if sme == "@power" else _sme(sme))
    st = ForgeState(sessionId="s")
    res = eng.run(st, "fix")
    assert res.status == "paused"
    reqs = _events(st, "ConfirmationRequest")
    assert reqs and reqs[0].actionCardJson and "30" in reqs[0].actionCardJson
    call_id = reqs[0].callId
    res2 = eng.resume(st, ConfirmationResponse(callId=call_id, approved=True, approverChannel="voice"))
    assert res2.status == "complete"
    assert step in st.approvedActions
    assert any(a.get("operatorOutcome") == "done" for a in st.audit)


def test_gr12_skip_drops_step():
    step = _set_psu_action(v=30.0)
    eng, _ = make_engine(summon_one=lambda sme, s: _sme("@power", actions=[step])
                         if sme == "@power" else _sme(sme))
    st = ForgeState(sessionId="s")
    eng.run(st, "fix")
    call_id = _events(st, "ConfirmationRequest")[0].callId
    eng.resume(st, ConfirmationResponse(callId=call_id, approved=False, approverChannel="chat"))
    assert step not in st.approvedActions
    assert any(a.get("operatorOutcome") == "skipped" for a in st.audit)


def test_gr13_sentinel_halt_no_actuation():
    eng, _ = make_engine()
    st = ForgeState(sessionId="s")
    eng.sentinel_observe(st, "hot iron over a powered board", severity="HALT")
    halts = [e for e in _events(st, "SafetyInterrupt") if e.severity == "HALT"]
    assert halts
    # only a power-down *instruction* — no actuation symbol anywhere on the path
    assert halts[0].suggestedRecoverActions[0].tool == "disable_psu_output"


def test_gr14_error_envelope_no_fail_stop():
    def boom_merge(kept):
        raise RuntimeError("merge exploded")
    eng, _ = make_engine(merge_fn=boom_merge)
    st = ForgeState(sessionId="s")
    res = eng.run(st, "diagnose")
    assert res.status == "complete"  # did not fail-stop
    warns = [e for e in _events(st, "SafetyInterrupt")
             if e.severity == "WARN" and "internal error" in e.reason]
    assert warns


def test_briefing_carries_full_context():
    # The fix for "SMEs have no proper context": the summon briefing must carry
    # the question, the board facts + a documented limit, and the snapshot vision.
    eng, _ = make_engine(classify=lambda t, r: RouteDecision(True, ["@power", "@signal"], "comm-timeout"))
    st = ForgeState(sessionId="s")
    eng.ingest_snapshot(st, SnapshotAnalysis(
        jobId="j", frame=FrameRef(uri="mem:f1", width=1, height=1, ts=1, sourceSeq=1),
        model="m", analysis="cell-stack lead unplugged", ts=1))
    eng.run(st, "ESP32 can't read the BQ79616 — comm timeout")
    b = st.pendingSummon.briefing
    assert b
    assert "ESP32 can't read" in b                      # operator question
    assert "BQ79616" in b                                # board facts
    assert "J3" in b                                     # documented net limit
    assert "cell-stack lead unplugged" in b              # snapshot vision evidence


def test_gr15_replay_reproduces_pending_card():
    step = _set_psu_action(v=30.0)
    eng, _ = make_engine(summon_one=lambda sme, s: _sme("@power", actions=[step])
                         if sme == "@power" else _sme(sme))
    st = ForgeState(sessionId="s")
    eng.run(st, "fix")
    before = len(_events(st, "ConfirmationRequest"))
    eng.replay_pending(st)  # client reconnects
    after = len(_events(st, "ConfirmationRequest"))
    assert after == before + 1  # pending card re-emitted
    call_id = _events(st, "ConfirmationRequest")[0].callId
    res = eng.resume(st, ConfirmationResponse(callId=call_id, approved=True))
    assert res.status == "complete"

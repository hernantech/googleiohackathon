"""System-level integration tests (08 §3) — cross-process flows proving the
contracts and endpoints align end to end. Doubles for the model/SME/Live steps;
real P1 KnowledgeAdapter + P2 SafetyGate + P4 snapshot + P5 graph + chat bus.
"""

from __future__ import annotations

import json
import pathlib

from orchestrator.graph import GraphEngine
from orchestrator.graph.state import DissentResult, ForgeState, GraphDeps, RouteDecision
from orchestrator.knowledge import KnowledgeAdapter
from orchestrator.proto import events as E
from orchestrator.proto.events import (
    ConfirmationResponse,
    DissentPair,
    ProposedAction,
    SmeResponse,
    now_ns,
)
from orchestrator.safety.gate import SafetyGate
from orchestrator.snapshot.endpoint import handle_snapshot
from orchestrator.storage.frame_store import InMemoryFrameStore

WIRE_DIR = pathlib.Path(__file__).resolve().parents[1] / "testdata" / "wire"
_JPEG = b"\xff\xd8fake\xff\xd9"


class _FakeBus:
    def __init__(self):
        self.published: list = []

    def publish(self, event):
        self.published.append(event)


def _sme(sme, *, confidence=0.8, actions=None):
    return SmeResponse(smeId=sme, callId="01HCALL", confidence=confidence,
                       claim=f"{sme} claim", rationale="r", proposedActions=actions or [],
                       ts=now_ns())


def _set_psu(v=30.0, target="J3"):
    return ProposedAction(
        actor="operator", tool="set_psu",
        argsJson=json.dumps({"voltage_v": v, "current_limit_a": 0.5, "target": target}),
        rationale="apply stack", risk="MEDIUM",
        instruction=f"Set PSU to {v} V across {target}.", documentedLimitRef="board_doc p.4")


def _engine(**kw):
    k = KnowledgeAdapter()
    deps = GraphDeps(
        gate=SafetyGate(k), knowledge=k,
        classify=kw.get("classify", lambda t, r: RouteDecision(True, ["@power"], "t")),
        summon_one=kw.get("summon_one", lambda sme, s: _sme(sme)),
        merge_fn=kw.get("merge_fn", lambda kept: ("headline", [r.smeId for r in kept])),
        dissent_fn=kw.get("dissent_fn", lambda resp, rnd: DissentResult(convergence="converged")),
    )
    return GraphEngine(deps), k


def _kinds(state):
    return [type(e).__name__ for e in state.outboundEvents]


# ── §3.1 — wire-contract alignment (Python side; Kotlin parity is WP-6's job) ──
def test_31_contract_alignment():
    # every golden file parses; union members dispatch through the adapter.
    from orchestrator.proto.examples import UNION_MEMBER_NAMES, canonical
    files = sorted(WIRE_DIR.glob("*.json"))
    assert len(files) == len(canonical())
    for name in UNION_MEMBER_NAMES:
        blob = (WIRE_DIR / f"{name}.json").read_text()
        assert type(E.parse_agent_event(blob)).__name__ == name

    # every event the graph emits is renderable: an AgentEvent union member
    # (15) is parseable; ChatMessage/ConfirmationRequest/etc. are union members too.
    eng, _ = _engine(summon_one=lambda sme, s: _sme("@power", actions=[_set_psu()])
                     if sme == "@power" else _sme(sme))
    st = ForgeState(sessionId="s")
    eng.run(st, "fix the rail")
    union_names = set(E.AGENT_EVENT_TYPES and [t.__name__ for t in E.AGENT_EVENT_TYPES])
    for ev in st.outboundEvents:
        # each emitted event is an AgentEvent variant the client can render.
        assert type(ev).__name__ in union_names, type(ev).__name__


# ── §3.4 — safety end to end (SME → merge → gate → KnowledgeAdapter → audit) ──
def test_34a_within_limit_confirm_then_done():
    eng, _ = _engine(summon_one=lambda sme, s: _sme("@power", actions=[_set_psu(30.0)])
                     if sme == "@power" else _sme(sme))
    st = ForgeState(sessionId="s")
    res = eng.run(st, "set the cell-sim supply")
    assert res.status == "paused"
    req = [e for e in st.outboundEvents if type(e).__name__ == "ConfirmationRequest"][0]
    assert req.actionCardJson and "30" in req.actionCardJson  # documented limit cited
    res2 = eng.resume(st, ConfirmationResponse(callId=req.callId, approved=True))
    assert res2.status == "complete"
    assert any(a.get("operatorOutcome") == "done" for a in st.audit)


def test_34b_over_limit_denied():
    eng, _ = _engine(summon_one=lambda sme, s: _sme("@power", actions=[_set_psu(35.0)])
                     if sme == "@power" else _sme(sme))
    st = ForgeState(sessionId="s")
    eng.run(st, "set it to 35")
    warns = [e for e in st.outboundEvents
             if type(e).__name__ == "SafetyInterrupt" and e.severity == "WARN"]
    assert warns and not st.pendingConfirmations


def test_34c_sentinel_halt_never_actuates():
    eng, _ = _engine()
    st = ForgeState(sessionId="s")
    eng.sentinel_observe(st, "smoke", severity="HALT")
    halt = [e for e in st.outboundEvents
            if type(e).__name__ == "SafetyInterrupt" and e.severity == "HALT"][0]
    # the only "recovery" is an instruction to the human — no actuation tool runs.
    assert halt.suggestedRecoverActions[0].tool == "disable_psu_output"


# ── §3.5b — snapshot → strong model → evidence → next summon includes it ──
def test_35b_snapshot_flows_into_guild():
    k = KnowledgeAdapter()
    bus = _FakeBus()
    snap = handle_snapshot(
        session_id="s", jpeg_bytes=_JPEG, width=1920, height=1080,
        note="bq79616 wiring", knowledge=k,
        model_call=lambda j, c, m: "cell-stack lead unplugged",
        store=InMemoryFrameStore(), bus=bus)
    # the snapshot path posted a SnapshotAnalysis card to the chat bus
    assert bus.published and bus.published[0].channelId == "#live-feed"
    assert bus.published[0].bodyContentType == "application/json"

    # feed it into the graph; the next summon carries the frame as evidence
    eng, _ = _engine()
    st = ForgeState(sessionId="s")
    eng.ingest_snapshot(st, snap)
    eng.run(st, "what's wrong")
    assert snap.frame.uri in st.pendingSummon.contextRefs


# ── §3.6 — the demo flow as one scripted integration test ──
def test_36_demo_flow_bq79616():
    bounce = {"n": 0}

    def dissent(resp, rnd):
        if rnd == 0:  # one round of visible disagreement, then resolve
            return DissentResult(
                pairwise=[DissentPair(a="@power", b="@firmware",
                                      aClaim="no stack", bClaim="comm bus", crux="root cause")],
                convergence="needs_more_rounds")
        return DissentResult(convergence="converged")

    def summon(sme, s):
        if sme == "@power":
            return _sme("@power", actions=[_set_psu(30.0)])
        return _sme(sme)

    eng, _ = _engine(
        classify=lambda t, r: RouteDecision(True, ["@firmware", "@signal", "@power"], "comm-timeout"),
        summon_one=summon, dissent_fn=dissent)
    st = ForgeState(sessionId="demo")

    # 📷 snapshot evidence first
    bus = _FakeBus()
    snap = handle_snapshot(session_id="demo", jpeg_bytes=_JPEG, width=1920, height=1080,
                           note="bq79616 wiring", knowledge=KnowledgeAdapter(),
                           model_call=lambda j, c, m: "cell-stack lead unplugged",
                           store=InMemoryFrameStore(), bus=bus)
    eng.ingest_snapshot(st, snap)

    res = eng.run(st, "ESP32 can't read the BQ79616 — comm timeout")
    assert res.status == "paused"  # waiting on the set_psu confirmation
    assert st.crossExamRound == 1  # bounced once

    kinds = _kinds(st)
    # snapshot evidence appears before the dissent resolves (08 §3.6)
    assert kinds.index("ChatMessage") < kinds.index("DissentReport")
    assert "DissentReport" in kinds and "ConfirmationRequest" in kinds
    # no internal-error surfaced anywhere
    assert not any(type(e).__name__ == "SafetyInterrupt" and "internal error" in e.reason
                   for e in st.outboundEvents)

    req = [e for e in st.outboundEvents if type(e).__name__ == "ConfirmationRequest"][0]
    res2 = eng.resume(st, ConfirmationResponse(callId=req.callId, approved=True, approverChannel="voice"))
    assert res2.status == "complete"
    assert st.liveSpeakerScript  # Forge voiced the close
    assert any(a.get("operatorOutcome") == "done" for a in st.audit)

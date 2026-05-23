"""SG-1..SG-12 — SafetyGate truth-table tests (03 §10).

Matrix loaded as data; documented-limit check uses the real P1 KnowledgeAdapter
with the bundled fixture board.yaml (J3 max 30 V). No graph, no SMEs, no network.
"""

from __future__ import annotations

import json

import pytest

from orchestrator.knowledge import KnowledgeAdapter
from orchestrator.proto.events import ProposedAction
from orchestrator.safety.gate import SafetyGate, GateSession


def _k_profile() -> KnowledgeAdapter:
    return KnowledgeAdapter()  # bundled bq79616 fixture (J3 max 30 V)


def _k_empty() -> KnowledgeAdapter:
    return KnowledgeAdapter(profile_path="/nonexistent/board.yaml")  # → empty profile


def _set_psu(v, i, target="J3", risk="MEDIUM", ref="board_doc p.4"):
    return ProposedAction(
        actor="operator", tool="set_psu",
        argsJson=json.dumps({"voltage_v": v, "current_limit_a": i, "target": target}),
        rationale="apply stack", risk=risk,
        instruction=f"Set PSU to {v} V / {i} A across {target}.",
        documentedLimitRef=ref,
    )


# SG-1 — every (step, invoker) resolves to one deterministic decision.
def test_sg1_total_and_deterministic():
    gate = SafetyGate(_k_profile())
    cases = [
        (_set_psu(30, 0.5), "@power"),
        (ProposedAction(actor="operator", tool="probe_net",
                        argsJson='{"net":"VIO","test_point":"TP4"}', rationale="m", risk="LOW"), "@power"),
        (ProposedAction(actor="guild", tool="lookup_datasheet", argsJson="{}", rationale="r", risk="LOW"), "@signal"),
        (ProposedAction(actor="operator", tool="flash_mcu",
                        argsJson='{"image":"a.bin","expected_sha256":"x"}', rationale="f", risk="HIGH"), "@firmware"),
    ]
    for action, invoker in cases:
        d1 = gate.evaluate(action, invoker, GateSession())
        d2 = gate.evaluate(action, invoker, GateSession())
        assert d1.decision == d2.decision
        assert d1.decision in {"allow", "confirm", "deny", "halt_bypass", "queued", "suppressed"}


# SG-2 — set_psu(30 V) within J3's 30 V → HIGH confirm, NOT denied.
def test_sg2_set_psu_within_limit_high_confirm():
    gate = SafetyGate(_k_profile())
    d = gate.evaluate(_set_psu(30.0, 0.5), "@power", GateSession())
    assert d.decision == "confirm"
    assert d.risk == "HIGH"
    assert d.card is not None and "30" in (d.card.documentedLimit or "")


# SG-3 — set_psu(35 V) > J3's 30 V → DENY + WARN citing the limit.
def test_sg3_set_psu_over_limit_denied():
    gate = SafetyGate(_k_profile())
    d = gate.evaluate(_set_psu(35.0, 0.5), "@power", GateSession())
    assert d.decision == "deny"
    assert d.emit_warn is True
    assert "limit" in d.reason.lower()


# SG-4 — set_psu by @firmware (out of lane) → DENY out of scope + WARN.
def test_sg4_out_of_lane_denied():
    gate = SafetyGate(_k_profile())
    d = gate.evaluate(_set_psu(5.0, 0.5), "@firmware", GateSession())
    assert d.decision == "deny"
    assert "out of scope" in d.reason
    assert d.emit_warn is True


# SG-5 — risk elevation: LOW default + declared HIGH → HIGH.
def test_sg5_risk_elevation():
    gate = SafetyGate(_k_profile())
    probe = ProposedAction(actor="operator", tool="probe_net",
                           argsJson='{"net":"VIO","test_point":"TP4"}', rationale="m", risk="HIGH")
    d = gate.evaluate(probe, "@power", GateSession())
    assert d.decision == "allow"
    assert d.risk == "HIGH"  # max(LOW table default, HIGH declared)


# SG-6 / SG-7 — sentinel HALT bypass + 60 s coalescing.
def test_sg6_sg7_sentinel_halt_bypass_and_coalesce():
    clock = {"t": 1000.0}
    gate = SafetyGate(_k_profile(), now=lambda: clock["t"])
    session = GateSession()
    disable = ProposedAction(actor="operator", tool="disable_psu_output",
                             argsJson='{"channel":1}', rationale="kill", risk="LOW")

    d1 = gate.evaluate(disable, "@sentinel", session, sentinel_halt=True)
    assert d1.decision == "halt_bypass" and d1.risk == "HALT" and d1.needs_confirmation is False

    clock["t"] += 10  # within 60 s
    d2 = gate.evaluate(disable, "@sentinel", session, sentinel_halt=True)
    assert d2.decision == "suppressed"  # SG-7 coalesced

    clock["t"] += 60  # past the window
    d3 = gate.evaluate(disable, "@sentinel", session, sentinel_halt=True)
    assert d3.decision == "halt_bypass"


# SG-8 — value-bearing set_psu without a citation → downgraded to confirm.
def test_sg8_provenance_downgrade():
    gate = SafetyGate(_k_profile())
    action = _set_psu(30.0, 0.5, ref=None)  # no documentedLimitRef
    d = gate.evaluate(action, "@power", GateSession())
    assert d.decision == "confirm"
    assert "no documented source" in d.reason


# SG-9 — no profile → defaults; set_psu(30 V) DENY; small value forced ≥ MEDIUM.
def test_sg9_no_profile_defaults():
    gate = SafetyGate(_k_empty())
    d_over = gate.evaluate(_set_psu(30.0, 0.5), "@power", GateSession())
    assert d_over.decision == "deny"  # 30 > default 12 V

    d_small = gate.evaluate(_set_psu(3.0, 0.1), "@power", GateSession())
    assert d_small.decision == "confirm"
    assert d_small.risk == "MEDIUM"  # forced up from LOW because limit not found


# SG-10 — repeated skip of identical (tool,args) → third proposal suppressed.
def test_sg10_skip_denylist():
    gate = SafetyGate(_k_profile())
    session = GateSession()
    action = _set_psu(30.0, 0.5)
    gate.evaluate(action, "@power", session)
    gate.record_skip(session, action)
    gate.evaluate(action, "@power", session)
    gate.record_skip(session, action)
    d = gate.evaluate(action, "@power", session)
    assert d.decision == "suppressed"


# SG-11 — flash precondition: PSU on → DENY; PSU off → HIGH confirm.
def test_sg11_flash_precondition():
    gate = SafetyGate(_k_profile())
    flash = ProposedAction(actor="operator", tool="flash_mcu",
                           argsJson='{"image":"fw.bin","expected_sha256":"abc"}',
                           rationale="flash", risk="HIGH")
    d_on = gate.evaluate(flash, "@firmware", GateSession(psu_output_on=True))
    assert d_on.decision == "deny" and d_on.emit_warn is True

    d_off = gate.evaluate(flash, "@firmware", GateSession(psu_output_on=False))
    assert d_off.decision == "confirm" and d_off.risk == "HIGH"


# SG-12 — guild lookups always allow, no card, no pending entry.
def test_sg12_lookups_ungated():
    gate = SafetyGate(_k_profile())
    session = GateSession()
    lookup = ProposedAction(actor="guild", tool="lookup_datasheet",
                            argsJson='{"part":"bq79616","query":"power-up"}',
                            rationale="ground", risk="LOW")
    d = gate.evaluate(lookup, "@power", session)
    assert d.decision == "allow"
    assert d.needs_confirmation is False
    assert d.card is None
    assert session.pending_count == 0

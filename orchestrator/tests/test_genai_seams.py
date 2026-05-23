"""real_summon_one as a bounded tool-calling SME (managed-agents finalize).

The Gemini *client* is monkeypatched (no network); we use the real
``google.genai.types`` so the fake responses are shaped exactly like the SDK's
(``response.function_calls`` is read off real Content/Part objects). These tests
assert the SME PULLS knowledge before concluding, that the retrieved text
influences the final SmeResponse, that citations come from the orchestrator's
``get_documented_limit`` (never the model), and that the stub fallback holds.
"""

from __future__ import annotations

import json
import types as _pytypes

import pytest

from orchestrator import genai_seams as gs
from orchestrator.knowledge import KnowledgeAdapter
from orchestrator.proto.events import SummonGuild

# google-genai is the [live] extra; these tests require it (real types, faked
# client). Skip cleanly if it is absent so the stub-only environment still runs.
genai_types = pytest.importorskip("google.genai.types")


# ───────────────────────── faked genai client ─────────────────────────────
#
# Drives a scripted tool-calling loop:
#   * a call carrying `tools` in its config returns the next queued *turn* —
#     either a list of function calls (model wants to retrieve) or None
#     (model is ready to conclude);
#   * a call carrying `response_mime_type` (the forced-JSON final answer)
#     returns the queued final JSON string.

def _fc_response(calls: list[tuple[str, dict]]):
    """Build a real GenerateContentResponse whose .function_calls are `calls`."""
    parts = [
        genai_types.Part(
            function_call=genai_types.FunctionCall(name=name, args=args)
        )
        for name, args in calls
    ]
    content = genai_types.Content(role="model", parts=parts)
    cand = genai_types.Candidate(content=content)
    return genai_types.GenerateContentResponse(candidates=[cand])


class _FakeModels:
    def __init__(self, tool_turns: list, final_json: str):
        # tool_turns: list of (list[(name,args)] | None); consumed per loop round
        self._tool_turns = list(tool_turns)
        self._final_json = final_json
        self.calls_seen: list[tuple[str, dict]] = []
        self.final_contents = None

    def generate_content(self, model, contents, config=None):
        is_final = bool(getattr(config, "response_mime_type", None)) or (
            isinstance(config, dict) and config.get("response_mime_type")
        )
        if is_final:
            self.final_contents = contents
            return _pytypes.SimpleNamespace(text=self._final_json)

        # tool-loop round: pop the next scripted turn (default: conclude)
        turn = self._tool_turns.pop(0) if self._tool_turns else None
        if turn:
            self.calls_seen.extend(turn)
            return _fc_response(turn)
        return _fc_response([])  # no function calls -> loop breaks


class _FakeClient:
    def __init__(self, tool_turns, final_json):
        self.models = _FakeModels(tool_turns, final_json)


def _summon(briefing: str) -> SummonGuild:
    return SummonGuild(callId="c", topic="comm-timeout",
                       smes=["@power", "@signal"], briefing=briefing)


# ─────────────────── PART A: the SME pulls knowledge ───────────────────────

def test_sme_calls_lookup_datasheet_and_text_influences_answer(monkeypatch):
    """A turn that needs a datasheet actually CALLS a tool, and the retrieved
    text drives the final SmeResponse."""
    # round 1: the model asks for the BQ79616 VIO datasheet passage.
    tool_turns = [[("lookup_datasheet", {"part": "BQ79616", "query": "VIO logic supply"})]]
    final = json.dumps({
        "confidence": 0.95,
        "claim": "VIO max is 5.5 V per the BQ79616 datasheet.",
        "rationale": "Per bq79616 datasheet §7.3 (VIO) the logic supply runs 3.3-5.0 V, abs-max 5.5 V.",
    })
    fake = _FakeClient(tool_turns, final)
    monkeypatch.setattr(gs, "_genai", lambda: fake)

    knowledge = KnowledgeAdapter()
    resp = gs.real_summon_one("@power", _summon("what is VIO max?"), knowledge=knowledge)

    # 1) a tool was actually invoked
    names = [n for n, _ in fake.models.calls_seen]
    assert "lookup_datasheet" in names

    # 2) the retrieved passage was fed back into the model before the final call
    fed_text = json.dumps(_serialize(fake.models.final_contents))
    assert "VIO" in fed_text  # the datasheet passage text round-tripped in
    assert "datasheet" in fed_text.lower()

    # 3) the final SmeResponse reflects the retrieved knowledge
    assert "5.5" in resp.claim
    assert "§7.3" in resp.rationale or "VIO" in resp.rationale
    assert abs(resp.confidence - 0.95) < 1e-6


def test_sme_can_chain_lookups_before_concluding(monkeypatch):
    """retrieve -> reason -> retrieve: multiple tool rounds are allowed."""
    tool_turns = [
        [("lookup_board_doc", {"query": "VIO net"})],
        [("get_documented_limit", {"target": "TP4", "kind": "net"})],
    ]
    final = json.dumps({"confidence": 0.8, "claim": "TP4/VIO max 5.5 V.", "rationale": "r"})
    fake = _FakeClient(tool_turns, final)
    monkeypatch.setattr(gs, "_genai", lambda: fake)

    resp = gs.real_summon_one("@power", _summon("VIO?"), knowledge=KnowledgeAdapter())
    names = [n for n, _ in fake.models.calls_seen]
    assert names == ["lookup_board_doc", "get_documented_limit"]
    assert resp.claim == "TP4/VIO max 5.5 V."


def test_tool_rounds_are_capped(monkeypatch):
    """The loop never runs more than SME_MAX_TOOL_ROUNDS retrieval rounds."""
    monkeypatch.setattr(gs, "SME_MAX_TOOL_ROUNDS", 2)
    # script more turns than the cap; the loop must stop and force the final JSON.
    tool_turns = [[("lookup_board_doc", {"query": "x"})]] * 10
    final = json.dumps({"confidence": 0.5, "claim": "capped.", "rationale": "r"})
    fake = _FakeClient(tool_turns, final)
    monkeypatch.setattr(gs, "_genai", lambda: fake)

    resp = gs.real_summon_one("@power", _summon("b"), knowledge=KnowledgeAdapter())
    assert len(fake.models.calls_seen) <= 2
    assert resp.claim == "capped."


# ─────────── citations come from get_documented_limit, not the model ───────

def test_proposed_action_citation_comes_from_orchestrator(monkeypatch):
    """Even if the model emits a setpoint, the documentedLimitRef is attached by
    the orchestrator from get_documented_limit — never invented by the model."""
    pa = {"tool": "set_psu",
          "args": {"target": "J3", "voltage_v": 30.0, "current_limit_a": 0.5},
          "instruction": "Set PSU to 30 V across J3.", "risk": "HIGH",
          # a hostile/bogus citation the model tried to invent — must be ignored:
          "documentedLimitRef": "I made this up"}
    final = json.dumps({
        "confidence": 0.92, "claim": "Apply the cell stack.", "rationale": "r",
        "proposedAction": pa,
    })
    fake = _FakeClient([], final)  # no tool rounds needed
    monkeypatch.setattr(gs, "_genai", lambda: fake)

    resp = gs.real_summon_one("@power", _summon("b"), knowledge=KnowledgeAdapter())
    assert len(resp.proposedActions) == 1
    a = resp.proposedActions[0]
    assert a.tool == "set_psu" and a.actor == "operator" and a.risk == "HIGH"
    assert a.instruction == "Set PSU to 30 V across J3."
    # the citation is the orchestrator's, derived from the real board profile.
    assert a.documentedLimitRef == "board_profile.nets[J3]"
    assert "I made this up" not in (a.documentedLimitRef or "")


def test_malformed_proposed_action_is_dropped(monkeypatch):
    final = json.dumps({
        "confidence": 0.5, "claim": "c", "rationale": "r",
        "proposedAction": {"no_tool": True},  # malformed
    })
    fake = _FakeClient([], final)
    monkeypatch.setattr(gs, "_genai", lambda: fake)
    resp = gs.real_summon_one("@power", _summon("b"), knowledge=KnowledgeAdapter())
    assert resp.proposedActions == []


def test_persona_and_briefing_are_system_and_context(monkeypatch):
    """The persona is the system-instruction and the briefing is the context."""
    final = json.dumps({"confidence": 0.9, "claim": "ok.", "rationale": "r"})
    fake = _FakeClient([], final)
    monkeypatch.setattr(gs, "_genai", lambda: fake)

    brief = "Operator said: comm timeout\nDocumented net limits: J3<=30.0V."
    resp = gs.real_summon_one("@power", _summon(brief))

    blob = json.dumps(_serialize(fake.models.final_contents))
    assert "comm timeout" in blob       # the grounded briefing reached the SME
    assert "J3<=30.0V" in blob          # documented limit context present
    assert "@signal" in blob            # sibling SMEs named
    assert resp.smeId == "@power" and resp.claim == "ok."


# ─────────────────────── stub fallback (no key / lib) ──────────────────────

def test_falls_back_to_stub_on_error(monkeypatch):
    class _Boom:
        @property
        def models(self):
            raise RuntimeError("no client")

    monkeypatch.setattr(gs, "_genai", lambda: _Boom())
    resp = gs.real_summon_one("@power", _summon("b"))
    assert "[stub]" in resp.claim  # never-fail-stop (01 §7)


def test_robust_json_parse_tolerates_fenced_final(monkeypatch):
    """A final answer wrapped in a ```json fence still parses (never-fail-stop)."""
    fenced = "```json\n" + json.dumps({"confidence": 0.7, "claim": "fenced ok.", "rationale": "r"}) + "\n```"
    fake = _FakeClient([], fenced)
    monkeypatch.setattr(gs, "_genai", lambda: fake)
    resp = gs.real_summon_one("@power", _summon("b"))
    assert resp.claim == "fenced ok."


# ────────────────────────────── helpers ───────────────────────────────────

def _serialize(contents) -> object:
    """Best-effort flatten of the contents list (real Content/Part objects) to
    something json.dumps can stringify for substring assertions."""
    out = []
    for c in contents or []:
        parts = getattr(c, "parts", None) or []
        for p in parts:
            t = getattr(p, "text", None)
            if t:
                out.append(t)
            fr = getattr(p, "function_response", None)
            if fr is not None:
                out.append(str(getattr(fr, "response", "")))
    return out

"""parse_schematic / lookup_schematic SME tools (spec 09 §5.1, §5.2; §6 step 5).

Two layers:
  * the _dispatch_tool branches (no network, no google-genai) — resolve bytes →
    vision parse → ingest into the session adapter → JSON-able dict;
  * the bounded SME tool-loop actually executing the tools and streaming each
    call (requires the [live] google.genai types; skipped if absent).
"""

from __future__ import annotations

import json

import pytest

from orchestrator import genai_seams as gs
from orchestrator.knowledge import EXAMPLE_PROFILE_PATH, KnowledgeAdapter
from orchestrator.storage.frame_store import InMemoryFrameStore

_JPEG = b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9"

# a minimal valid SchematicJSON the faked vision call returns.
_PARSED = json.dumps({
    "source": {"kind": "image", "model": "x"},
    "confidence": 0.7,
    "components": [{"ref": "Q1", "part": "2N7002",
                    "pins": [{"pin": "1", "net": "VSENSE"}]}],
    "nets": [{"id": "VSENSE", "classGuess": "signal", "nominalVGuess": 3.3}],
    "warnings": [],
    "cite": "model-cite",
})


# ── tool schemas are registered + the existing tools survive ────────────────

def test_schematic_tools_registered_alongside_existing():
    names = [t["name"] for t in gs._TOOL_SCHEMAS]
    assert "parse_schematic" in names
    assert "lookup_schematic" in names
    # the existing tools are intact (no regression)
    for keep in ("lookup_datasheet", "lookup_board_doc",
                 "get_documented_limit", "run_analysis"):
        assert keep in names


# ── _dispatch_tool: parse_schematic resolves, parses, ingests ───────────────

def test_dispatch_parse_schematic_resolves_parses_and_ingests(monkeypatch):
    store = InMemoryFrameStore()
    ref = store.put(_JPEG, 800, 600, ts=1)
    # the resolver reads bytes from the shared store; point it at our store.
    monkeypatch.setattr(gs, "_resolve_schematic_bytes",
                        lambda uri: store.get_jpeg(ref.uri))
    # fake the vision model so no network is touched.
    monkeypatch.setattr(gs, "real_parse_schematic",
                        lambda b, m, h, mn: _PARSED)

    ka = KnowledgeAdapter(EXAMPLE_PROFILE_PATH)
    out = gs._dispatch_tool(
        "parse_schematic",
        {"source_uri": f"snapshot://{ref.uri}", "hint": "power section"},
        ka,
    )
    # returns the SchematicJSON-as-dict + the merge counts
    assert out["components"][0]["ref"] == "Q1"
    assert out["components"][0]["type"] == "transistor"   # normalizer ran
    assert out["_ingest"]["parts_added"] == 1
    # the parse is now cached AND merged into the profile (existing lookups work)
    assert ka.schematic is not None
    assert ka.board_profile.net("VSENSE") is not None
    # safety: the guessed value did NOT become a documented limit.
    assert ka.get_documented_limit("VSENSE", "net").found is False


def test_dispatch_parse_schematic_unresolvable_source_is_graceful(monkeypatch):
    monkeypatch.setattr(gs, "_resolve_schematic_bytes", lambda uri: None)
    ka = KnowledgeAdapter(EXAMPLE_PROFILE_PATH)
    out = gs._dispatch_tool("parse_schematic", {"source_uri": "snapshot://nope"}, ka)
    assert "error" in out and "could not resolve" in out["error"]
    assert ka.schematic is None  # nothing ingested


def test_dispatch_lookup_schematic_reads_session_cache(monkeypatch):
    monkeypatch.setattr(gs, "_resolve_schematic_bytes", lambda uri: _JPEG)
    monkeypatch.setattr(gs, "real_parse_schematic", lambda b, m, h, mn: _PARSED)
    ka = KnowledgeAdapter(EXAMPLE_PROFILE_PATH)
    gs._dispatch_tool("parse_schematic", {"source_uri": "snapshot://f1"}, ka)

    out = gs._dispatch_tool("lookup_schematic", {"query": "VSENSE"}, ka)
    assert "VSENSE" in [n["id"] for n in out["nets"]]
    assert "Q1" in [c["ref"] for c in out["components"]]
    assert out["cite"]


# ── resolver: file path + missing frame degrade to None (never raise) ───────

def test_resolve_bytes_from_file_and_missing(tmp_path):
    f = tmp_path / "sch.jpg"
    f.write_bytes(_JPEG)
    assert gs._resolve_schematic_bytes(str(f)) == _JPEG
    assert gs._resolve_schematic_bytes("") is None
    assert gs._resolve_schematic_bytes("/no/such/file.jpg") is None


# ── the SME tool-loop actually executes parse_schematic (needs [live] types) ─

genai_types = pytest.importorskip("google.genai.types")


def _fc_response(calls):
    parts = [genai_types.Part(function_call=genai_types.FunctionCall(name=n, args=a))
             for n, a in calls]
    content = genai_types.Content(role="model", parts=parts)
    return genai_types.GenerateContentResponse(
        candidates=[genai_types.Candidate(content=content)])


class _FakeModels:
    def __init__(self, tool_turns, final_json):
        self._turns = list(tool_turns)
        self._final = final_json
        self.calls_seen = []

    def generate_content(self, model, contents, config=None):
        is_final = bool(getattr(config, "response_mime_type", None)) or (
            isinstance(config, dict) and config.get("response_mime_type"))
        if is_final:
            import types as _t
            return _t.SimpleNamespace(text=self._final)
        turn = self._turns.pop(0) if self._turns else None
        if turn:
            self.calls_seen.extend(turn)
            return _fc_response(turn)
        return _fc_response([])


class _FakeClient:
    def __init__(self, tool_turns, final_json):
        self.models = _FakeModels(tool_turns, final_json)


def test_tool_loop_executes_parse_schematic_and_streams_it(monkeypatch):
    from orchestrator.proto.events import SummonGuild

    # the SME asks to parse the schematic, then concludes.
    tool_turns = [[("parse_schematic", {"source_uri": "snapshot://f1",
                                        "hint": "power"})]]
    final = json.dumps({"confidence": 0.8, "claim": "ok.", "rationale": "r"})
    fake = _FakeClient(tool_turns, final)
    monkeypatch.setattr(gs, "_genai", lambda: fake)
    monkeypatch.setattr(gs, "_resolve_schematic_bytes", lambda uri: _JPEG)
    monkeypatch.setattr(gs, "real_parse_schematic", lambda b, m, h, mn: _PARSED)

    ka = KnowledgeAdapter(EXAMPLE_PROFILE_PATH)
    seen: list[dict] = []
    summon = SummonGuild(callId="c", topic="t", smes=["@power"], briefing="parse it")
    resp = gs.real_summon_one("@power", summon, knowledge=ka, on_tool_call=seen.append)

    # the tool was executed via the loop AND streamed through on_tool_call
    assert any(c["name"] == "parse_schematic" for c in seen)
    parse_call = next(c for c in seen if c["name"] == "parse_schematic")
    assert parse_call["result"]["components"][0]["ref"] == "Q1"
    # the parse was ingested into the session adapter as a side effect
    assert ka.board_profile.net("VSENSE") is not None
    assert resp.claim == "ok."

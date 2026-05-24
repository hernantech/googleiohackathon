"""Gemini Live can invoke the schematic pipeline as a function/tool call (09 §5).

Proves the SECOND entry point: a Live `function_call` for parse_schematic /
lookup_schematic is routed by the LiveDuplexBridge into the schematic handler
(orchestrator.live.schematic_tools), which dispatches through the SAME shared
seam the SME tool-loop uses (genai_seams.dispatch_schematic_tool), and the
structured result is injected back into the session. All offline: a stubbed
LiveSession + a faked vision call, no network.
"""

from __future__ import annotations

import asyncio
import json

from orchestrator import genai_seams as gs
from orchestrator.knowledge import EXAMPLE_PROFILE_PATH, KnowledgeAdapter
from orchestrator.live.bridge import LiveDuplexBridge, LiveEvent, MediaKind
from orchestrator.live.schematic_tools import (
    is_schematic_tool,
    make_live_schematic_handler,
)

_JPEG = b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9"
_PARSED = json.dumps({
    "source": {"kind": "image", "model": "x"},
    "confidence": 0.7,
    "components": [{"ref": "Q1", "part": "2N7002",
                    "pins": [{"pin": "1", "net": "VSENSE"}]}],
    "nets": [{"id": "VSENSE", "classGuess": "signal", "nominalVGuess": 3.3}],
    "warnings": [],
    "cite": "model-cite",
})


class FakeLiveSession:
    """A scripted LiveSession (no network) that emits the scripted events and
    records injected function-responses + text."""

    def __init__(self, scripted: list[LiveEvent]):
        self._scripted = scripted
        self.injected_responses: list[tuple[str, dict]] = []
        self.sent_text: list[str] = []
        self.closed = False

    async def send_media(self, chunk: bytes, kind: MediaKind = MediaKind.AUDIO) -> None:
        pass

    async def receive(self):
        for ev in self._scripted:
            yield ev
            await asyncio.sleep(0)

    async def inject_function_response(self, call_id: str, payload: dict) -> None:
        self.injected_responses.append((call_id, payload))

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)

    async def close(self) -> None:
        self.closed = True


class _Client:
    def __init__(self):
        self.audio_frames: list[bytes] = []

    async def send_bytes(self, chunk: bytes) -> None:
        self.audio_frames.append(chunk)


def test_is_schematic_tool_recognizes_both_tools():
    assert is_schematic_tool("parse_schematic")
    assert is_schematic_tool("lookup_schematic")
    assert not is_schematic_tool("summon_guild")


def test_live_parse_schematic_routed_through_shared_seam(monkeypatch):
    """A Live `parse_schematic` function-call reaches the parse pipeline via the
    shared seam, ingests into the per-session adapter, and the SchematicJSON is
    injected back as the function-response."""
    monkeypatch.setattr(gs, "_resolve_schematic_bytes", lambda uri: _JPEG)
    monkeypatch.setattr(gs, "real_parse_schematic", lambda b, m, h, mn: _PARSED)

    knowledge = KnowledgeAdapter(EXAMPLE_PROFILE_PATH)
    handler = make_live_schematic_handler(knowledge)

    async def run():
        session = FakeLiveSession(scripted=[
            LiveEvent(tool_call="parse_schematic",
                      tool_args={"source_uri": "snapshot://f1", "hint": "power"},
                      tool_call_id="call-1"),
            LiveEvent(turn_complete=True),
        ])
        client = _Client()
        bridge = LiveDuplexBridge(session, audio_out=client.send_bytes,
                                  on_tool_call=handler)
        await bridge.receive_loop()
        return session, bridge

    session, bridge = asyncio.run(run())

    # the Live tool-call was routed and a function-response injected back
    assert bridge.tool_calls_routed == 1
    assert len(session.injected_responses) == 1
    call_id, payload = session.injected_responses[0]
    assert call_id == "call-1"
    assert payload["components"][0]["ref"] == "Q1"
    assert payload["components"][0]["type"] == "transistor"   # normalizer ran
    # the same shared pipeline ingested into the session adapter
    assert knowledge.schematic is not None
    assert knowledge.board_profile.net("VSENSE") is not None
    # safety: the guessed value did NOT become a documented limit.
    assert knowledge.get_documented_limit("VSENSE", "net").found is False


def test_live_lookup_schematic_after_parse(monkeypatch):
    """Voice 'what's connected to net VSENSE?' → lookup_schematic over the cached
    parse, injected back."""
    monkeypatch.setattr(gs, "_resolve_schematic_bytes", lambda uri: _JPEG)
    monkeypatch.setattr(gs, "real_parse_schematic", lambda b, m, h, mn: _PARSED)

    knowledge = KnowledgeAdapter(EXAMPLE_PROFILE_PATH)
    handler = make_live_schematic_handler(knowledge)

    async def run():
        session = FakeLiveSession(scripted=[
            LiveEvent(tool_call="parse_schematic",
                      tool_args={"source_uri": "snapshot://f1"},
                      tool_call_id="c1"),
            LiveEvent(tool_call="lookup_schematic",
                      tool_args={"query": "VSENSE"}, tool_call_id="c2"),
            LiveEvent(turn_complete=True),
        ])
        bridge = LiveDuplexBridge(session, audio_out=_Client().send_bytes,
                                  on_tool_call=handler)
        await bridge.receive_loop()
        return session

    session = asyncio.run(run())
    assert [cid for cid, _ in session.injected_responses] == ["c1", "c2"]
    lookup_payload = session.injected_responses[1][1]
    assert "VSENSE" in [n["id"] for n in lookup_payload["nets"]]
    assert "Q1" in [c["ref"] for c in lookup_payload["components"]]


def test_live_handler_passes_through_non_schematic_calls():
    """The handler returns None for a non-schematic call so it can be composed
    ahead of main.py's guild router (additive, no regression)."""
    knowledge = KnowledgeAdapter(EXAMPLE_PROFILE_PATH)
    handler = make_live_schematic_handler(knowledge)
    out = asyncio.run(handler("summon_guild", {"topic": "x"}, "c0"))
    assert out is None


def test_live_handler_graceful_when_source_unresolvable(monkeypatch):
    monkeypatch.setattr(gs, "_resolve_schematic_bytes", lambda uri: None)
    knowledge = KnowledgeAdapter(EXAMPLE_PROFILE_PATH)
    handler = make_live_schematic_handler(knowledge)
    out = asyncio.run(handler("parse_schematic", {"source_uri": "snapshot://x"}, "c"))
    assert "error" in out  # graceful, no raise
    assert knowledge.schematic is None


# ── Live config DECLARES summon_guild + the schematic tools + a system prompt ─

def test_live_config_declares_all_three_tools():
    """The Live config offers BOTH summon_guild AND the schematic tools, so the
    Gemini Live agent can deliberately call either by voice. We assert on the
    declaration content (works with no google-genai) + the combined Tool builder
    via a fake `types`."""
    from orchestrator.live import session as sess
    from orchestrator.live.schematic_tools import _LIVE_FUNCTION_DECLS

    # the schematic declarations + the guild declaration, by name
    sch_names = {d["name"] for d in _LIVE_FUNCTION_DECLS}
    assert sch_names == {"parse_schematic", "lookup_schematic"}
    guild = sess._live_guild_tool_decl()
    assert guild["name"] == "summon_guild"
    assert "topic" in guild["parameters"]["properties"]

    # system instruction mentions each tool so the agent knows WHEN to call it
    si = sess.LIVE_SYSTEM_INSTRUCTION
    assert "summon_guild" in si and "parse_schematic" in si and "lookup_schematic" in si

    # _live_tool() assembles all three into one Tool (fake the genai types module)
    import sys
    import types as _t

    captured = {}

    class _FakeTool:
        def __init__(self, function_declarations):
            captured["decls"] = function_declarations

    fake_genai = _t.ModuleType("google.genai")
    fake_types = _t.ModuleType("google.genai.types")
    fake_types.Tool = _FakeTool
    fake_genai.types = fake_types
    sys.modules["google.genai"] = fake_genai
    sys.modules["google.genai.types"] = fake_types
    try:
        tool = sess._live_tool()
    finally:
        sys.modules.pop("google.genai", None)
        sys.modules.pop("google.genai.types", None)
    assert tool is not None
    names = [d["name"] for d in captured["decls"]]
    assert names == ["summon_guild", "parse_schematic", "lookup_schematic"]


# ── main.py on_tool_call now DISPATCHES BY NAME ───────────────────────────────

def test_main_on_tool_call_dispatches_parse_schematic(monkeypatch):
    """main._make_live_graph_hooks.on_tool_call routes a Live parse_schematic call
    to the schematic parser (the SAME shared seam) — NOT through the guild — and
    injects the SchematicJSON back. summon_guild still routes to the guild
    (covered by test_main_live_tool_call_injects_function_response)."""
    monkeypatch.setattr(gs, "_resolve_schematic_bytes", lambda uri: _JPEG)
    monkeypatch.setattr(gs, "real_parse_schematic", lambda b, m, h, mn: _PARSED)

    async def run():
        import orchestrator.main as main_mod
        from orchestrator.chat_bus.bus import ChatBus

        monkeypatch.setattr(main_mod, "_sessions", {})
        monkeypatch.setattr(main_mod, "bus", ChatBus())
        # fresh adapter so this test does not leak schematic state into the
        # process singleton other tests share.
        monkeypatch.setattr(main_mod, "knowledge",
                            KnowledgeAdapter(EXAMPLE_PROFILE_PATH))

        _on_transcript, on_tool_call = main_mod._make_live_graph_hooks("live-sch")
        session = FakeLiveSession(scripted=[
            LiveEvent(tool_call="parse_schematic",
                      tool_args={"source_uri": "snapshot://f1", "hint": "power"},
                      tool_call_id="call-sch-1"),
        ])
        bridge = LiveDuplexBridge(session, audio_out=_Client().send_bytes,
                                  on_tool_call=on_tool_call)
        await bridge.receive_loop()
        return main_mod, session

    main_mod, session = asyncio.run(run())
    assert len(session.injected_responses) == 1
    call_id, payload = session.injected_responses[0]
    assert call_id == "call-sch-1"
    # routed to the parser, NOT the guild (no "headline" key; has components)
    assert "headline" not in payload
    assert payload["components"][0]["ref"] == "Q1"
    # ingested into main's process knowledge adapter
    assert main_mod.knowledge.board_profile.net("VSENSE") is not None

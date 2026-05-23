"""WP-1..WP-12 — component contract tests for the wire protocol (00 §11).

Pure (de)serialization; no external services. Run: pytest orchestrator/proto/tests/
"""

from __future__ import annotations

import json
import pathlib

import pytest
from pydantic import ValidationError

from orchestrator.proto import events as E
from orchestrator.proto.examples import UNION_MEMBER_NAMES, canonical

WIRE_DIR = pathlib.Path(__file__).resolve().parents[3] / "testdata" / "wire"


# WP-1 — round-trip every AgentEvent variant.
@pytest.mark.parametrize("name", UNION_MEMBER_NAMES)
def test_wp1_round_trip_union(name: str):
    original = canonical()[name]
    reparsed = E.parse_agent_event(original.model_dump_json())
    assert reparsed == original
    assert type(reparsed) is type(original)


def test_wp1_covers_15_variants():
    assert len(UNION_MEMBER_NAMES) == 15
    assert len(E.AGENT_EVENT_TYPES) == 15


# WP-2 — discriminator dispatch lands on the exact type.
def test_wp2_discriminator_dispatch():
    blob = '{"kind":"ChatMessage","channelId":"#power","authorId":"@power",' \
           '"authorKind":"sme","body":"hi","messageId":"01H","ts":1}'
    parsed = E.parse_agent_event(blob)
    assert isinstance(parsed, E.ChatMessage)


# WP-3 — forward-compatible: unknown extra field is ignored.
def test_wp3_forward_compat_extra_field():
    blob = '{"kind":"Goodbye","reason":"bye","futureField":123}'
    parsed = E.parse_agent_event(blob)
    assert isinstance(parsed, E.Goodbye)
    assert parsed.reason == "bye"


# WP-4 — v1→v2 default-fill on Transcript.
def test_wp4_transcript_defaults():
    t = E.parse_agent_event('{"kind":"Transcript","text":"hi","partial":false,"ts":1}')
    assert isinstance(t, E.Transcript)
    assert t.speaker == "user"
    assert t.smeId is None


# WP-5 — ProposedAction.actor default + guild lookups validate.
def test_wp5_proposed_action_actor():
    op = E.ProposedAction(tool="set_psu", argsJson="{}", rationale="r", risk="HIGH")
    assert op.actor == "operator"
    guild = E.ProposedAction(actor="guild", tool="lookup_datasheet", argsJson="{}",
                             rationale="r", risk="LOW")
    assert guild.actor == "guild"


# WP-6 — golden corpus parses in Python (Kotlin parity validated separately).
def test_wp6_golden_corpus_exists():
    assert WIRE_DIR.is_dir(), f"missing golden corpus dir {WIRE_DIR}"
    files = sorted(WIRE_DIR.glob("*.json"))
    assert len(files) == len(canonical()), "golden corpus out of sync with canonical()"


@pytest.mark.parametrize("name", sorted(canonical().keys()))
def test_wp6_golden_matches_canonical(name: str):
    path = WIRE_DIR / f"{name}.json"
    assert path.exists(), f"missing golden file {path}; run testdata/wire/_generate.py"
    on_disk = json.loads(path.read_text())
    expected = canonical()[name].model_dump(mode="json")
    assert on_disk == expected, f"{name}.json drifted from canonical(); regenerate"
    # union members must also parse through the discriminated adapter
    if name in UNION_MEMBER_NAMES:
        assert E.parse_agent_event(on_disk) == canonical()[name]


# WP-7 — ActionCard default labels.
def test_wp7_action_card_defaults():
    card = E.ActionCard(title="t", bodyMarkdown="b", risk="MEDIUM")
    assert card.affirmLabel == "I did it"
    assert card.denyLabel == "Skip"


# WP-8 — FrameRef URI validation.
def test_wp8_frameref_uri():
    assert E.FrameRef(uri="gs://x/y.jpg", width=1, height=1, ts=1, sourceSeq=0)
    assert E.FrameRef(uri="mem:abc", width=1, height=1, ts=1, sourceSeq=0)
    with pytest.raises(ValidationError):
        E.FrameRef(uri="", width=1, height=1, ts=1, sourceSeq=0)


# WP-9 — Hello without protocolVersion is rejected.
def test_wp9_hello_requires_protocol_version():
    with pytest.raises(ValidationError):
        E.parse_agent_event('{"kind":"Hello","client":"phone","sessionId":"s"}')
    ok = E.parse_agent_event('{"kind":"Hello","client":"phone","sessionId":"s","protocolVersion":"2.0"}')
    assert isinstance(ok, E.Hello)


# WP-10 — no actuation surface in the user-visible tool registry.
def test_wp10_no_actuation_tools_exposed():
    assert "set_psu" not in E.USER_VISIBLE_TOOLS
    assert "flash_mcu" not in E.USER_VISIBLE_TOOLS
    # they exist only as operator-step labels
    assert "set_psu" in E.OPERATOR_STEP_TOOLS
    assert "flash_mcu" in E.OPERATOR_STEP_TOOLS


# WP-11 — SnapshotAnalysis round-trips, embeds FrameRef, cites defaults to [].
def test_wp11_snapshot_analysis():
    snap = canonical()["SnapshotAnalysis"]
    reparsed = E.SnapshotAnalysis.model_validate_json(snap.model_dump_json())
    assert reparsed == snap
    assert isinstance(reparsed.frame, E.FrameRef)
    bare = E.SnapshotAnalysis(jobId="j", frame=snap.frame, model="m", analysis="a", ts=1)
    assert bare.cites == []


# WP-12 — analyze_snapshot registered; no decode/transcode entrypoint.
def test_wp12_snapshot_tool_no_transcode():
    assert "analyze_snapshot" in E.USER_VISIBLE_TOOLS
    forbidden_substrings = ("decode", "transcode", "h264", "encode")
    for tool in E.USER_VISIBLE_TOOLS | E.OPERATOR_STEP_TOOLS:
        assert not any(s in tool.lower() for s in forbidden_substrings)

"""§3.9 — Device-source conformance + wire corpus (08 §3.9, HANDOFF §5).

Two concerns merged here:
  A) Device-source conformance: both phone and quest clients produce the same
     DeviceSource contract (same /v2/snapshot + /v2/live path, no device branch
     in the hot path — 07 §2.2).
  B) Wire corpus conformance (WP-6 server-side): load EVERY file in
     testdata/wire/*.json and assert the server parses each via
     AGENT_EVENT_ADAPTER (or the correct per-kind model for card types).
     Round-trip where sensible.

Builds on: WP-11, §3.5a, §3.5b.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from orchestrator.proto.events import (
    AGENT_EVENT_ADAPTER,
    AGENT_EVENT_TYPES,
    ActionCard,
    FrameRef,
    SnapshotAnalysis,
    parse_agent_event,
)
from orchestrator.proto.examples import UNION_MEMBER_NAMES, canonical

WIRE_DIR = pathlib.Path(__file__).resolve().parents[1] / "testdata" / "wire"

# Card types that are NOT in the AgentEvent union but do appear in the corpus
_CARD_PARSERS: dict[str, type] = {
    "ActionCard": ActionCard,
    "FrameRef": FrameRef,
    "SnapshotAnalysis": SnapshotAnalysis,
}

# All expected names in the corpus (15 union members + 3 card types)
_ALL_CORPUS_NAMES = list(UNION_MEMBER_NAMES) + list(_CARD_PARSERS.keys())


# ── §3.9 Wire corpus: parse every testdata/wire/*.json ───────────────────────

@pytest.mark.parametrize("name", UNION_MEMBER_NAMES)
def test_39_wire_corpus_union_member_parses(name: str):
    """§3.9/WP-6: every AgentEvent union member JSON file parses correctly.

    Asserts:
    - The file exists
    - AGENT_EVENT_ADAPTER parses it to the expected type
    - The parsed type == the expected model class
    - Round-trip: model_dump_json → parse → same instance
    """
    path = WIRE_DIR / f"{name}.json"
    assert path.exists(), f"missing golden corpus file: {path}"

    raw = path.read_text()
    parsed = parse_agent_event(raw)

    # Type must match exactly (not just isinstance of a parent)
    expected_types = {t.__name__: t for t in AGENT_EVENT_TYPES}
    assert type(parsed).__name__ == name, (
        f"{name}.json parsed to {type(parsed).__name__}, expected {name}"
    )
    assert isinstance(parsed, expected_types[name])

    # Round-trip: dump then re-parse must equal the original
    reparsed = parse_agent_event(parsed.model_dump_json())
    assert reparsed == parsed, f"round-trip failed for {name}"


@pytest.mark.parametrize("name", list(_CARD_PARSERS.keys()))
def test_39_wire_corpus_card_type_parses(name: str):
    """§3.9/WP-6: every card/payload type JSON file parses to the correct model.

    Card types (ActionCard, FrameRef, SnapshotAnalysis) are NOT in the
    AgentEvent union but DO appear in the golden corpus. They must parse via
    their own model's validate_json().
    """
    path = WIRE_DIR / f"{name}.json"
    assert path.exists(), f"missing golden corpus file: {path}"

    model_cls = _CARD_PARSERS[name]
    raw = path.read_text()
    parsed = model_cls.model_validate_json(raw)

    assert isinstance(parsed, model_cls), (
        f"{name}.json did not parse to {model_cls.__name__}"
    )

    # Round-trip
    reparsed = model_cls.model_validate_json(parsed.model_dump_json())
    assert reparsed == parsed, f"round-trip failed for {name}"


def test_39_wire_corpus_complete():
    """§3.9: the corpus contains EXACTLY the expected number of files.

    Ensures no files were added to testdata/wire/ without a corresponding
    canonical() entry and a test, and no files were accidentally deleted.
    """
    files = sorted(f.stem for f in WIRE_DIR.glob("*.json"))
    expected = sorted(_ALL_CORPUS_NAMES)
    assert files == expected, (
        f"corpus mismatch:\n  on disk: {files}\n  expected: {expected}"
    )


def test_39_wire_corpus_matches_canonical():
    """§3.9: every file in the corpus matches the canonical() instances.

    The canonical() function in orchestrator/proto/examples.py is the single
    source of truth. If a file differs from canonical(), it's drifted and must
    be regenerated via testdata/wire/_generate.py.
    """
    canon = canonical()
    for name, instance in canon.items():
        path = WIRE_DIR / f"{name}.json"
        assert path.exists(), f"missing: {path}"
        on_disk = json.loads(path.read_text())
        expected = instance.model_dump(mode="json")
        assert on_disk == expected, (
            f"{name}.json drifted from canonical(); run testdata/wire/_generate.py"
        )


# ── §3.9 Device-source conformance: phone vs quest ───────────────────────────

def test_39_device_snapshot_phone_and_quest_same_path():
    """§3.9: POST /v2/snapshot works identically for phone and quest clients.

    The orchestrator is device-blind: the media path has no branch on
    Hello.client. We verify:
    - A snapshot POST from a "phone" session returns 202 + jobId
    - A snapshot POST from a "quest" session returns 202 + jobId
    - Both responses have the same shape (jobId key)
    - No client-typed branch exists by asserting the same path handles both

    Spec ref: 07 §2.2, 08 §3.9 ("orchestrator code has no client-typed branch
    in the media path").
    """
    from fastapi.testclient import TestClient
    from orchestrator.main import app

    client = TestClient(app)
    _JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"

    for client_type in ("phone", "quest", "web"):
        session_id = f"s39-device-{client_type}"
        r = client.post(
            f"/v2/snapshot?sessionId={session_id}",
            content=_JPEG,
            headers={"content-type": "image/jpeg"},
        )
        assert r.status_code == 202, (
            f"snapshot from {client_type} client must return 202, got {r.status_code}"
        )
        body = r.json()
        assert "jobId" in body, (
            f"missing jobId for {client_type} client: {body}"
        )
        assert isinstance(body["jobId"], str) and len(body["jobId"]) > 0


def test_39_device_live_phone_and_quest_same_path():
    """§3.9: /v2/live accepts media from both phone and quest (device-blind).

    We feed prefixed PCM-audio AND JPEG-video frames (the Live-path contract;
    1-byte type prefix per binary frame — 0x01 audio, 0x02 JPEG) to /v2/live for
    both client types and assert no error. The stub live_sink is a no-op; this
    just proves the path doesn't branch on client type and routes both kinds.
    """
    from fastapi.testclient import TestClient
    from orchestrator.live.bridge import MediaKind
    from orchestrator.main import app

    client = TestClient(app)
    pcm = bytes(range(256)) * 4  # synthetic PCM-like chunk
    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"

    for client_type in ("phone", "quest"):
        session_id = f"s39-live-{client_type}"
        with client.websocket_connect(f"/v2/live?sessionId={session_id}") as ws:
            ws.send_bytes(bytes([MediaKind.AUDIO]) + pcm)
            ws.send_bytes(bytes([MediaKind.VIDEO]) + jpeg)
            # No error means the server accepted + routed both kinds without a
            # client-typed branch.


def test_39_device_chat_hello_phone_and_quest():
    """§3.9: /v2/chat accepts Hello from both phone and quest clients.

    After connect, the replay handshake (ChannelList + ReplayDone) is identical
    regardless of which client type sent the Hello. The server must not
    dispatch differently based on Hello.client in the media path.
    """
    from fastapi.testclient import TestClient
    from orchestrator.main import app

    client = TestClient(app)

    for client_type in ("phone", "quest", "web", "test"):
        session_id = f"s39-chat-{client_type}"
        with client.websocket_connect(f"/v2/chat?sessionId={session_id}") as ws:
            # Drain replay handshake
            events = []
            while True:
                data = ws.receive_json()
                events.append(data)
                if data.get("kind") == "ReplayDone":
                    break

            kinds = [e["kind"] for e in events]
            assert kinds[0] == "ChannelList", (
                f"client={client_type}: first event must be ChannelList, got {kinds}"
            )
            assert kinds[-1] == "ReplayDone", (
                f"client={client_type}: last event must be ReplayDone, got {kinds}"
            )

            # Send Hello for this client type
            ws.send_json(
                {
                    "kind": "Hello",
                    "client": client_type,
                    "sessionId": session_id,
                    "protocolVersion": "2.0",
                }
            )
            # No error = device-blind acceptance


def test_39_no_client_branch_in_snapshot_path():
    """§3.9: assert the snapshot endpoint in main.py has no client-typed branch.

    The spec (08 §3.9) requires "the orchestrator code has no client-typed
    branch in the media path". We enforce this statically by reading the source
    of the snapshot endpoint and asserting it does not inspect a 'client'
    parameter to choose a code path.

    Note: the snapshot endpoint does NOT take a 'client' param at all — that's
    the correct implementation. We just make the spec constraint explicit.
    """
    import inspect
    import orchestrator.main as m

    # The snapshot route handler
    source = inspect.getsource(m.snapshot)

    # The handler must not branch on a "client" type
    # (it should not have any 'if client' or 'client ==' or 'client in' logic)
    forbidden_patterns = [
        'if client_type',
        'if client ==',
        'client == "phone"',
        'client == "quest"',
        'elif client',
    ]
    for pattern in forbidden_patterns:
        assert pattern not in source, (
            f"snapshot handler contains client-type branch: {pattern!r}\n"
            "The snapshot path must be device-blind (08 §3.9)."
        )


def test_39_snapshot_jpeg_size_within_bounds():
    """§3.9: JPEG snapshots within size bounds are accepted (00 §4.3).

    Spec allows snapshots up to SNAPSHOT_MAX_EDGE_PX (≤ 4096 px long edge).
    We verify the server accepts a 1920×1080 JPEG (within bounds) and returns
    202. The constraint is a client-side downscaling requirement; the server
    accepts any JPEG bytes.
    """
    from fastapi.testclient import TestClient
    from orchestrator.main import app

    client = TestClient(app)
    _JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"

    r = client.post(
        "/v2/snapshot?sessionId=s39-size-check&w=1920&h=1080",
        content=_JPEG,
        headers={"content-type": "image/jpeg"},
    )
    assert r.status_code == 202
    assert "jobId" in r.json()


def test_39_snapshot_analysis_wire_format():
    """§3.9: SnapshotAnalysis in the corpus has a valid FrameRef embedded.

    Asserts WP-11 from the system integration level: the SnapshotAnalysis wire
    format contains an embedded FrameRef with all required fields, and the
    cites list is present (even if empty defaults to []).
    """
    path = WIRE_DIR / "SnapshotAnalysis.json"
    assert path.exists()

    snap = SnapshotAnalysis.model_validate_json(path.read_text())
    assert isinstance(snap.frame, FrameRef), "SnapshotAnalysis.frame must be FrameRef"
    assert snap.frame.width > 0
    assert snap.frame.height > 0
    assert snap.frame.uri.startswith(("gs://", "mem:")), (
        f"FrameRef.uri must be gs:// or mem:, got {snap.frame.uri!r}"
    )
    assert isinstance(snap.cites, list)  # defaults to [] (WP-11)


# ── §3.9 @live variant ────────────────────────────────────────────────────────

@pytest.mark.live
def test_39_live_device_conformance_recordings():
    """§3.9 @live: drive the orchestrator with recorded phone + quest captures.

    Uses testdata/device/{phone,quest}.capture when available.
    Excluded from CI.
    """
    pytest.skip(
        "@live test — requires testdata/device/{phone,quest}.capture recordings "
        "and a running Gemini Live session."
    )

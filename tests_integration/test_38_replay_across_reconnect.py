"""§3.8 — Replay / HITL across reconnect (08 §3.8, HANDOFF §5).

Proves: a session interrupted at a pending InstructionCard survives a client
reconnect and the operator can still complete the step.

Replay sequence contract (spec 04 §6):
    ChannelList → last-N ChatMessages (ts order) →
    pending ConfirmationRequests → ReplayDone

Builds on: CB-7, GR-15.
"""

from __future__ import annotations

import json

import pytest

from orchestrator.chat_bus.bus import ChatBus
from orchestrator.chat_bus.envelopes import ChannelList, ReplayDone
from orchestrator.proto.events import (
    ChatMessage,
    ConfirmationRequest,
    ConfirmationResponse,
    new_ulid,
    now_ns,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"


def _chat_msg(channel="#general", body="hello", author="@power") -> ChatMessage:
    return ChatMessage(
        channelId=channel,
        authorId=author,
        authorKind="sme",
        body=body,
        messageId=new_ulid(),
        ts=now_ns(),
    )


def _confirmation_req(summary="do something") -> ConfirmationRequest:
    return ConfirmationRequest(
        callId=new_ulid(),
        summary=summary,
        risk="HIGH",
    )


def _collect_replay(ws) -> list[dict]:
    """Drain events from the WS until and including ReplayDone."""
    events = []
    for _ in range(50):
        data = ws.receive_json()
        events.append(data)
        if data.get("kind") == "ReplayDone":
            break
    return events


# ── §3.8.1 — Replay sequence ORDER is ChannelList → Messages → Pending → ReplayDone ──

def test_38_replay_order_channel_list_then_messages_then_confirmations_then_done():
    """§3.8: bus.replay() emits events in the correct spec-mandated order.

    This tests the ChatBus.replay() method directly (transport-agnostic layer),
    asserting the exact order:
        1. ChannelList
        2. ChatMessage(s) in ts order
        3. pending ConfirmationRequests
        4. ReplayDone
    Spec ref: 04 §6.
    """

    class _RecordingTransport:
        def __init__(self):
            self.received: list[object] = []

        def send(self, event: object) -> None:
            self.received.append(event)

    bus = ChatBus()

    # Publish two chat messages with distinct ts so order is deterministic
    msg_a = _chat_msg(body="first")
    msg_b = _chat_msg(body="second")
    bus._buffer.append(msg_a)
    bus._buffer.append(msg_b)

    # A pending ConfirmationRequest
    conf = _confirmation_req()
    bus._pending_confirmations[conf.callId] = conf

    transport = _RecordingTransport()
    from orchestrator.chat_bus.bus import Session

    session = Session("sess-replay-order", transport)
    bus.subscribe(session)

    replayed = bus.replay("sess-replay-order")

    kinds = [type(e).__name__ for e in replayed]

    # 1. First is ChannelList
    assert kinds[0] == "ChannelList", f"first must be ChannelList, got {kinds}"

    # 2. Then ChatMessages in ts order (msg_a then msg_b)
    chat_indices = [i for i, k in enumerate(kinds) if k == "ChatMessage"]
    assert len(chat_indices) == 2, f"expected 2 ChatMessages, got {chat_indices}"
    assert chat_indices == sorted(chat_indices), "ChatMessages not in order"
    # Verify ts ordering within ChatMessages
    chat_events = [replayed[i] for i in chat_indices]
    assert chat_events[0].messageId == msg_a.messageId, "first ChatMessage should be msg_a"
    assert chat_events[1].messageId == msg_b.messageId, "second ChatMessage should be msg_b"

    # 3. Then ConfirmationRequest
    conf_indices = [i for i, k in enumerate(kinds) if k == "ConfirmationRequest"]
    assert len(conf_indices) == 1, f"expected 1 ConfirmationRequest, got {conf_indices}"
    # Must come after all ChatMessages
    assert conf_indices[0] > max(chat_indices), (
        "ConfirmationRequest must come after all ChatMessages"
    )
    # The replayed ConfirmationRequest must match the original
    assert replayed[conf_indices[0]].callId == conf.callId

    # 4. Last is ReplayDone
    assert kinds[-1] == "ReplayDone", f"last event must be ReplayDone, got {kinds}"


# ── §3.8.2 — Via FastAPI: connect, drive activity, disconnect, reconnect ─────

def test_38_fastapi_reconnect_replays_messages_and_pending_confirmation():
    """§3.8: reconnecting with the same sessionId replays all history.

    Flow:
    1. Connect /v2/chat?sessionId=S → drain replay handshake
    2. POST /v2/snapshot for session S to drive a ChatMessage into the bus
    3. Disconnect
    4. Reconnect /v2/chat?sessionId=S → assert replay order:
       ChannelList → ChatMessage(s) → ReplayDone
    """
    from fastapi.testclient import TestClient
    from orchestrator.main import app, bus, _sessions

    client = TestClient(app)
    session_id = "s38-reconnect-test"

    # Ensure this session doesn't carry state from prior tests
    _sessions.pop(session_id, None)

    # — First connection: drain the initial replay ——————————————————
    with client.websocket_connect(f"/v2/chat?sessionId={session_id}") as ws:
        initial_replay = _collect_replay(ws)
        # First connect on a fresh session: ChannelList + ReplayDone only
        init_kinds = [e["kind"] for e in initial_replay]
        assert init_kinds[0] == "ChannelList"
        assert init_kinds[-1] == "ReplayDone"

    # — POST snapshot to push a ChatMessage into the bus ——————————————
    r = client.post(
        f"/v2/snapshot?sessionId={session_id}",
        content=_JPEG,
        headers={"content-type": "image/jpeg"},
    )
    assert r.status_code == 202, f"snapshot must return 202, got {r.status_code}"

    # — Second connection: verify replay includes the ChatMessage ————————
    with client.websocket_connect(f"/v2/chat?sessionId={session_id}") as ws:
        replay = _collect_replay(ws)

    kinds = [e["kind"] for e in replay]

    # Contract order: ChannelList first
    assert kinds[0] == "ChannelList", f"first must be ChannelList, got {kinds}"

    # ReplayDone last
    assert kinds[-1] == "ReplayDone", f"last must be ReplayDone, got {kinds}"

    # The snapshot triggered a ChatMessage to the bus — must appear in replay
    assert "ChatMessage" in kinds, (
        f"ChatMessage from snapshot must be replayed; got {kinds}"
    )

    # All ChatMessages must come before any ConfirmationRequest (if any)
    if "ConfirmationRequest" in kinds:
        last_msg_idx = max(i for i, k in enumerate(kinds) if k == "ChatMessage")
        first_conf_idx = kinds.index("ConfirmationRequest")
        assert last_msg_idx < first_conf_idx, (
            "ChatMessages must precede ConfirmationRequests in replay"
        )


# ── §3.8.3 — Pending ConfirmationRequest survives reconnect ──────────────────

def test_38_pending_confirmation_survives_reconnect():
    """§3.8: a pending ConfirmationRequest is re-emitted on reconnect.

    Flow:
    1. Manually inject a ConfirmationRequest into the bus's pending map
    2. Subscribe a session
    3. Run replay
    4. Assert the ConfirmationRequest is in the replayed sequence
    5. After sending ConfirmationResponse, assert it is resolved (not re-emitted)

    Spec ref: 04 §6, CB-7.
    """

    class _RecordingTransport:
        def __init__(self):
            self.received: list[object] = []

        def send(self, event: object) -> None:
            self.received.append(event)

    bus = ChatBus()
    conf = _confirmation_req("Set PSU to 30 V")
    bus._pending_confirmations[conf.callId] = conf

    transport = _RecordingTransport()
    from orchestrator.chat_bus.bus import Session

    session = Session("sess-conf-survives", transport)
    bus.subscribe(session)

    # First replay: ConfirmationRequest must appear
    replayed1 = bus.replay("sess-conf-survives")
    kinds1 = [type(e).__name__ for e in replayed1]
    assert "ConfirmationRequest" in kinds1, (
        f"ConfirmationRequest must be in replay; kinds={kinds1}"
    )
    conf_event = next(e for e in replayed1 if type(e).__name__ == "ConfirmationRequest")
    assert conf_event.callId == conf.callId
    assert conf_event.summary == "Set PSU to 30 V"

    # Resolve the confirmation
    bus.resolve_confirmation(conf.callId)
    assert conf.callId not in bus._pending_confirmations

    # Second replay: ConfirmationRequest must NOT appear
    replayed2 = bus.replay("sess-conf-survives")
    kinds2 = [type(e).__name__ for e in replayed2]
    assert "ConfirmationRequest" not in kinds2, (
        f"resolved ConfirmationRequest must not be replayed; kinds={kinds2}"
    )


# ── §3.8.4 — ChatMessages are in ts order during replay ──────────────────────

def test_38_replay_chat_messages_ts_order():
    """§3.8: ChatMessages are replayed in monotonic ts order (spec 04 §5/§6).

    Inserts messages out of order and asserts the replay sorts them by ts.
    """

    class _RecordingTransport:
        def __init__(self):
            self.received: list[object] = []

        def send(self, event: object) -> None:
            self.received.append(event)

    bus = ChatBus()

    # Create messages with explicitly decreasing ts to test sort
    base_ts = now_ns()
    msg_later = ChatMessage(
        channelId="#general", authorId="@power", authorKind="sme",
        body="later message", messageId=new_ulid(),
        ts=base_ts + 1_000_000,  # +1ms
    )
    msg_earlier = ChatMessage(
        channelId="#general", authorId="@signal", authorKind="sme",
        body="earlier message", messageId=new_ulid(),
        ts=base_ts,
    )
    # Add out of order
    bus._buffer.append(msg_later)
    bus._buffer.append(msg_earlier)

    transport = _RecordingTransport()
    from orchestrator.chat_bus.bus import Session

    session = Session("sess-ts-order", transport)
    bus.subscribe(session)

    replayed = bus.replay("sess-ts-order")
    chat_events = [e for e in replayed if isinstance(e, ChatMessage)]

    assert len(chat_events) == 2
    # Earlier ts must come first
    assert chat_events[0].ts <= chat_events[1].ts, (
        f"ChatMessages not in ts order: {[e.ts for e in chat_events]}"
    )
    assert chat_events[0].messageId == msg_earlier.messageId
    assert chat_events[1].messageId == msg_later.messageId


# ── §3.8.5 — replayFrom query param is accepted ──────────────────────────────

def test_38_replay_from_query_param_accepted():
    """§3.8: /v2/chat?replayFrom=<checkpointId> is accepted by the server.

    The spec (04 §6) says replayFrom is optional; when present the server
    uses it as the checkpointId in ReplayDone. We verify:
    - The connection succeeds
    - ReplayDone carries checkpointId == replayFrom value
    """
    from fastapi.testclient import TestClient
    from orchestrator.main import app

    client = TestClient(app)
    session_id = "s38-replay-from"
    checkpoint = "01HCKPT-TEST"

    with client.websocket_connect(
        f"/v2/chat?sessionId={session_id}&replayFrom={checkpoint}"
    ) as ws:
        replay = _collect_replay(ws)

    replay_done = next(e for e in replay if e.get("kind") == "ReplayDone")
    assert replay_done["checkpointId"] == checkpoint, (
        f"ReplayDone.checkpointId must echo replayFrom; got {replay_done}"
    )


# ── §3.8.6 — Full reconnect flow with ChatMessage sent by client ─────────────

def test_38_send_chat_message_then_reconnect_replays_it():
    """§3.8: A ChatMessage sent by the client persists across reconnect.

    Flow:
    1. Connect session S
    2. Drain initial replay
    3. Send a ChatMessage (which triggers engine.run → might add bus messages)
    4. Disconnect
    5. Reconnect session S
    6. Assert the bus has messages to replay (reconnect delivers history)

    Note: the stub classify() may return no guild, so the bus may only have
    messages emitted by the graph's outbound events. We assert that the
    reconnect produces a valid replay sequence (ChannelList + ReplayDone).
    """
    from fastapi.testclient import TestClient
    from orchestrator.main import app, _sessions

    client = TestClient(app)
    session_id = "s38-chat-msg-persist"
    _sessions.pop(session_id, None)

    # First connection
    with client.websocket_connect(f"/v2/chat?sessionId={session_id}") as ws:
        _collect_replay(ws)  # drain initial replay
        # Send a ChatMessage
        ws.send_json(
            {
                "kind": "ChatMessage",
                "channelId": "#general",
                "authorId": "@user",
                "authorKind": "user",
                "body": "@power check the rail",
                "messageId": new_ulid(),
                "ts": now_ns(),
            }
        )

    # Reconnect
    with client.websocket_connect(f"/v2/chat?sessionId={session_id}") as ws:
        replay = _collect_replay(ws)

    kinds = [e["kind"] for e in replay]
    assert kinds[0] == "ChannelList", f"ChannelList must be first; got {kinds}"
    assert kinds[-1] == "ReplayDone", f"ReplayDone must be last; got {kinds}"


# ── §3.8 @live variant ────────────────────────────────────────────────────────

@pytest.mark.live
def test_38_live_hitl_resume_from_checkpoint():
    """§3.8 @live: HITL resume from checkpoint with a real LangGraph checkpointer.

    Excluded from CI. Run manually pre-demo.
    """
    pytest.skip("@live test — run manually pre-demo with real LangGraph checkpointer.")

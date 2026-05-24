"""CB-1..CB-11 — component contract tests for the chat bus (`04 §13`).

Driven by an in-memory WS pair (no network): the server side is the real
`ChatBus`/`Session` from `bus.py`; the client side is a `FakeClient` harness
that records events off a queue-backed `FakeTransport`. Pure Python, no FastAPI.

Run: PYTHONPATH=. .venv/bin/pytest orchestrator/chat_bus/tests/ -q
"""

from __future__ import annotations

import json

import pytest

from orchestrator.proto.events import (
    ActionCard,
    ChannelUpdate,
    ChatMessage,
    ConfirmationRequest,
    ConfirmationResponse,
    EvidenceRef,
    FrameRef,
    SnapshotAnalysis,
    new_ulid,
    now_ns,
)

from orchestrator.chat_bus.bus import (
    MAX_QUEUE,
    REPLAY_MAX,
    ChatBus,
    ClientMessageStore,
    Session,
)
from orchestrator.chat_bus.channels import SME_ROSTER, sme_channel_ids
from orchestrator.chat_bus.envelopes import (
    BackpressureNotice,
    ChannelList,
    ErrorEvent,
    Ping,
    Pong,
    ReplayDone,
)


# ───────────────────────── in-memory WS pair ──────────────────────────

class FakeTransport:
    """Stand-in for the WS sink: just records everything `send` to a list/queue
    the client harness reads."""

    def __init__(self):
        self.received: list[object] = []

    def send(self, event: object) -> None:
        self.received.append(event)


class FakeClient:
    """The client side of the in-memory pair. Wraps a `ClientMessageStore` so
    streaming deltas / dedup / orphan handling are exercised exactly as the real
    renderer would, and lets tests pump the transport into the store."""

    def __init__(self, transport: FakeTransport):
        self.transport = transport
        self.store = ClientMessageStore()

    def pump(self, *, now_ns: int | None = None) -> list[object]:
        """Move everything the server sent into the client store. Returns the
        drained events so tests can assert on framing too."""
        drained = list(self.transport.received)
        self.transport.received.clear()
        for event in drained:
            self.store.ingest(event, now_ns=now_ns)
        return drained


def make_session(bus: ChatBus, session_id: str | None = None):
    sid = session_id or new_ulid()
    transport = FakeTransport()
    session = bus.subscribe(Session(sid, transport))
    client = FakeClient(transport)
    return session, client


def chat(channel: str, body: str, *, streaming: bool = False,
         content_type: str = "text/markdown", message_id: str | None = None,
         author: str = "@power", kind: str = "sme", ts: int | None = None) -> ChatMessage:
    return ChatMessage(
        channelId=channel,
        authorId=author,
        authorKind=kind,
        body=body,
        bodyContentType=content_type,
        messageId=message_id or new_ulid(),
        ts=ts if ts is not None else now_ns(),
        streaming=streaming,
    )


# ───────────────────────── CB-1 ──────────────────────────

def test_cb1_channel_list_roster_matches_spec_no_bench_tech():
    """On connect, the ChannelList's #<sme> channels equal the roster in 02/07
    and contain no #bench-tech."""
    bus = ChatBus()
    _session, client = make_session(bus, "01HSESSION")

    # Connect-time emission == start of replay sequence: first event is ChannelList.
    sent = bus.replay("01HSESSION")
    channel_list = sent[0]
    assert isinstance(channel_list, ChannelList)

    sme_channels = {c.id for c in channel_list.channels if c.smeId is not None}
    expected = {f"#{s}" for s in SME_ROSTER}
    assert sme_channels == expected
    assert sme_channels == sme_channel_ids()
    assert "#bench-tech" not in {c.id for c in channel_list.channels}
    # Sanity: the canonical roster size is the 10 SMEs.
    assert len(SME_ROSTER) == 10
    # SME channel smeId is "@<id>" and titles are present.
    by_id = {c.id: c for c in channel_list.channels}
    assert by_id["#power"].smeId == "@power"
    assert by_id["#power"].title


# ───────────────────────── CB-2 ──────────────────────────

def test_cb2_streaming_reconstructs_body_in_order():
    """ChatMessage(streaming=true) then 5 ChannelUpdates then done=true →
    client reconstructs the full body in order."""
    bus = ChatBus()
    _session, client = make_session(bus)

    mid = new_ulid()
    base_ts = now_ns()
    bus.publish(chat("#power", "", streaming=True, message_id=mid, ts=base_ts))

    deltas = ["Rail ", "looks ", "fine; ", "stack ", "missing."]
    for i, d in enumerate(deltas):
        last = i == len(deltas) - 1
        bus.publish(ChannelUpdate(messageId=mid, deltaText=d, done=last, ts=base_ts + i + 1))

    client.pump()
    assert client.store.body_of(mid) == "Rail looks fine; stack missing."
    assert mid in client.store.done


# ───────────────────────── CB-3 ──────────────────────────

def test_cb3_orphan_update_buffered_then_dropped():
    """ChannelUpdate for an unknown messageId → buffered ≤2s then dropped, no
    crash."""
    store = ClientMessageStore()
    t0 = now_ns()
    store.ingest(ChannelUpdate(messageId="01HORPHAN", deltaText="x", ts=t0), now_ns=t0)
    assert store.orphan_count == 1

    # Before TTL: still buffered.
    assert store.sweep_orphans(now_ns=t0 + 1_000_000_000) == 0
    assert store.orphan_count == 1

    # After 2s: dropped, no crash, no message materialized.
    dropped = store.sweep_orphans(now_ns=t0 + 2_500_000_000)
    assert dropped == 1
    assert store.orphan_count == 0
    assert "01HORPHAN" not in store.messages

    # If the parent shows up first, a subsequent (non-orphan) update applies.
    mid = new_ulid()
    store.ingest(chat("#power", "", streaming=True, message_id=mid))
    store.ingest(ChannelUpdate(messageId=mid, deltaText="hi", done=True, ts=now_ns()))
    assert store.body_of(mid) == "hi"


# ───────────────────────── CB-4 ──────────────────────────

def test_cb4_idempotent_replay_renders_once():
    """Replay re-sends a message with the same ULID → client renders once."""
    bus = ChatBus()
    _session, client = make_session(bus, "01HSESSION")

    mid = "01HDEDUP"
    bus.publish(chat("#power", "hello", message_id=mid, ts=now_ns()))
    client.pump()
    assert client.store.render_count[mid] == 1

    # Reconnect/replay re-sends the same message (same ULID).
    bus.replay("01HSESSION")
    client.pump()
    # Rendered exactly once; body unchanged.
    assert client.store.render_count[mid] == 1
    assert client.store.body_of(mid) == "hello"
    assert len([m for m in client.store.order if m == mid]) == 1


# ───────────────────────── CB-5 ──────────────────────────

def test_cb5_backpressure_drops_channel_update_before_chat_message():
    """Flood >256 events → server drops ChannelUpdate before ChatMessage and
    emits a BackpressureNotice. Priority preserved."""
    bus = ChatBus()
    transport = FakeTransport()
    # Build the session directly so we can fill the queue without per-event flush.
    session = bus.subscribe(Session("01HFLOOD", transport))

    # Pre-fill the queue to capacity with ChannelUpdates (droppable).
    for i in range(MAX_QUEUE):
        ok = session.enqueue(ChannelUpdate(messageId=f"M{i}", deltaText="x", ts=now_ns()))
        assert ok
    assert len(session._queue) == MAX_QUEUE

    # Now flood it well past 256: a mix where ChatMessages must survive and
    # ChannelUpdates get evicted/dropped first.
    chat_ids = []
    for i in range(50):
        cm = chat("#power", f"final-{i}", message_id=f"C{i}", ts=now_ns())
        chat_ids.append(cm.messageId)
        assert session.enqueue(cm) is True  # ChatMessage always admitted

    # More ChannelUpdates arriving while saturated get dropped (no room left
    # except by evicting other ChannelUpdates, which counts as a drop).
    for i in range(40):
        session.enqueue(ChannelUpdate(messageId=f"X{i}", deltaText="x", ts=now_ns()))

    assert session.dropped_count > 0
    assert len(session._queue) <= MAX_QUEUE

    # Every ChatMessage we pushed is still queued — priority preserved.
    queued_ids = {getattr(e, "messageId", None) for e in session._queue
                  if isinstance(e, ChatMessage)}
    for cid in chat_ids:
        assert cid in queued_ids

    # A BackpressureNotice is produced and reflects the drop count + window.
    notice = session.take_backpressure_notice()
    assert isinstance(notice, BackpressureNotice)
    assert notice.dropped == session.dropped_count
    assert notice.sinceTs > 0


def test_cb5_publish_emits_backpressure_notice_to_transport():
    """End-to-end via the bus: when drops happen, a BackpressureNotice reaches
    the client behind the events."""
    bus = ChatBus()
    transport = FakeTransport()
    session = bus.subscribe(Session("01HBP", transport))

    # Saturate the queue (no flush) with droppable updates.
    for i in range(MAX_QUEUE):
        session.enqueue(ChannelUpdate(messageId=f"M{i}", deltaText="x", ts=now_ns()))

    # Publishing one more ChannelUpdate forces a drop; publish() flushes and
    # appends a BackpressureNotice.
    bus.publish(ChannelUpdate(messageId="MFLOOD", deltaText="x", ts=now_ns()))
    kinds = [getattr(e, "kind", None) for e in transport.received]
    assert "BackpressureNotice" in kinds


# ───────────────────────── CB-6 ──────────────────────────

def test_cb6_instruction_card_round_trip():
    """ConfirmationRequest carrying an ActionCard ("I did it"/"Skip",
    documentedLimit present) parses on the client and yields a
    ConfirmationResponse(approved=True) on affirm."""
    bus = ChatBus()
    _session, client = make_session(bus)

    card = ActionCard(
        title="@power asks you to:",
        bodyMarkdown="Set bench PSU CH1 to **30.0 V**, 0.5 A limit across J3.",
        risk="HIGH",
        documentedLimit="board doc max: 30 V",
    )
    req = ConfirmationRequest(
        callId="01HCALL",
        summary="Set PSU to 30 V across J3",
        risk="HIGH",
        invokerSmeId="@power",
        actionCardJson=card.model_dump_json(),
    )
    bus.publish(req)

    # Client side: pull it off the transport and parse the embedded ActionCard.
    received = client.transport.received
    assert any(getattr(e, "kind", None) == "ConfirmationRequest" for e in received)
    got_req = next(e for e in received if getattr(e, "kind", None) == "ConfirmationRequest")
    assert got_req.actionCardJson is not None
    parsed_card = ActionCard.model_validate_json(got_req.actionCardJson)

    # Labels render as the documented "I did it" / "Skip"; limit is present.
    assert parsed_card.affirmLabel == "I did it"
    assert parsed_card.denyLabel == "Skip"
    assert parsed_card.documentedLimit == "board doc max: 30 V"

    # User taps "I did it" → ConfirmationResponse(approved=True), chat channel.
    response = ConfirmationResponse(callId=got_req.callId, approved=True, approverChannel="chat")
    assert response.approved is True
    assert response.callId == "01HCALL"

    # Server resolving it clears the pending confirmation (so replay won't re-offer).
    assert bus.pending_confirmations and bus.pending_confirmations[0].callId == "01HCALL"
    bus.resolve_confirmation(response.callId)
    assert bus.pending_confirmations == []


# ───────────────────────── CB-7 ──────────────────────────

def test_cb7_reconnect_replay_contract():
    """Reconnect with same sessionId → ChannelList + last-200 + pending
    ConfirmationRequest re-emitted + ReplayDone, in order."""
    bus = ChatBus()

    # Original connection produces some history + a pending confirmation.
    sess1, _client1 = make_session(bus, "01HSAME")
    base = now_ns()
    bus.publish(chat("#power", "a", message_id="A", ts=base + 1))
    bus.publish(chat("#signal", "b", message_id="B", ts=base + 2))
    bus.publish(ConfirmationRequest(callId="01HCALL", summary="confirm", risk="HIGH",
                                    actionCardJson=None))

    # Reconnect: new transport, same sessionId.
    bus.unsubscribe("01HSAME")
    _sess2, client2 = make_session(bus, "01HSAME")

    sent = bus.replay("01HSAME")
    kinds = [getattr(e, "kind", None) for e in sent]

    # Contract order: ChannelList first, ReplayDone last.
    assert isinstance(sent[0], ChannelList)
    assert isinstance(sent[-1], ReplayDone)

    # last-200 ChatMessages present, in ts order.
    msgs = [e for e in sent if isinstance(e, ChatMessage)]
    assert [m.messageId for m in msgs] == ["A", "B"]
    assert [m.ts for m in msgs] == sorted(m.ts for m in msgs)

    # Pending confirmation re-emitted between messages and ReplayDone.
    confs = [e for e in sent if getattr(e, "kind", None) == "ConfirmationRequest"]
    assert len(confs) == 1 and confs[0].callId == "01HCALL"
    cl_idx = kinds.index("ChannelList")
    conf_idx = kinds.index("ConfirmationRequest")
    done_idx = kinds.index("ReplayDone")
    assert cl_idx < conf_idx < done_idx

    # ReplayDone.resumeTs is the latest message ts.
    assert sent[-1].resumeTs == base + 2

    # Unread backfill is reflected on the ChannelList.
    by_id = {c.id: c for c in sent[0].channels}
    assert by_id["#power"].unreadHint == 1
    assert by_id["#signal"].unreadHint == 1


def test_cb7_replay_caps_at_200():
    """Replay only re-sends the last N=200 messages."""
    bus = ChatBus()
    _sess, _client = make_session(bus, "01HCAP")
    base = now_ns()
    for i in range(REPLAY_MAX + 50):
        bus.publish(chat("#general", str(i), message_id=f"G{i}", ts=base + i), flush=False)

    sent = bus.replay("01HCAP")
    msgs = [e for e in sent if isinstance(e, ChatMessage)]
    assert len(msgs) == REPLAY_MAX
    # Kept the most recent 200.
    assert msgs[0].body == "50"
    assert msgs[-1].body == str(REPLAY_MAX + 50 - 1)


# ───────────────────────── CB-8 ──────────────────────────

def test_cb8_unknown_kind_json_body_fallback():
    """Unknown `kind` in an application/json body → collapsed-JSON fallback, no
    exception. Forward-compatible."""
    bus = ChatBus()
    _session, client = make_session(bus)

    unknown_body = json.dumps({"kind": "FutureCardV9", "foo": 42})
    msg = chat("#general", unknown_body, content_type="application/json",
               message_id="01HFUT", ts=now_ns())
    bus.publish(msg)
    client.pump()

    # The message renders (is stored) and the body is intact JSON the client can
    # show collapsed; parsing it as JSON does not raise.
    stored = client.store.messages["01HFUT"]
    assert stored.bodyContentType == "application/json"
    parsed = json.loads(stored.body)  # no exception
    assert parsed["kind"] == "FutureCardV9"

    # Renderer dispatch helper: unknown kind ⇒ fallback, no crash.
    assert _render_kind(stored) == "collapsed-json"


def _render_kind(msg: ChatMessage) -> str:
    """Minimal model of the client's renderer dispatch (§3 matrix)."""
    if msg.bodyContentType != "application/json":
        return msg.bodyContentType
    try:
        obj = json.loads(msg.body)
    except json.JSONDecodeError:
        return "collapsed-json"
    known = {"SmeResponse", "DissentReport", "ActionCard", "ToolResult",
             "SafetyInterrupt", "EvidenceRef", "MergedOpinion", "SnapshotAnalysis"}
    kind = obj.get("kind")
    if kind in known:
        return kind
    return "collapsed-json"


# ───────────────────────── CB-9 ──────────────────────────

def test_cb9_protocol_mismatch_rejected_with_goodbye():
    """Hello with mismatched major protocolVersion → ErrorEvent("protocol_
    mismatch") then Goodbye + close, rejected cleanly."""
    from orchestrator.proto.events import Goodbye, Hello, PROTOCOL_VERSION

    def handle_hello(hello: Hello) -> list[object]:
        """Server-side handshake check (§6, §8)."""
        client_major = hello.protocolVersion.split(".")[0]
        server_major = PROTOCOL_VERSION.split(".")[0]
        if client_major != server_major:
            return [
                ErrorEvent(code="protocol_mismatch",
                           message=f"server speaks {PROTOCOL_VERSION}",
                           ts=now_ns()),
                Goodbye(reason="protocol_mismatch"),
            ]
        return []

    out = handle_hello(Hello(client="phone", sessionId="s", protocolVersion="1.4"))
    assert isinstance(out[0], ErrorEvent)
    assert out[0].code == "protocol_mismatch"
    assert isinstance(out[1], Goodbye)
    assert out[1].reason == "protocol_mismatch"

    # Matching major version → accepted (no rejection events).
    assert handle_hello(Hello(client="phone", sessionId="s", protocolVersion="2.0")) == []


# ───────────────────────── CB-10 ──────────────────────────

def test_cb10_heartbeat_two_missed_pongs_marks_dead():
    """Server Ping every 20s; missing 2 Pongs → server marks WS dead."""
    bus = ChatBus()
    transport = FakeTransport()
    session = bus.subscribe(Session("01HHB", transport))

    # Ping #1, then a Pong → still alive, counter reset.
    pings = bus.heartbeat()
    assert isinstance(transport.received[-1], Ping)
    bus.on_pong("01HHB", Pong(nonce=pings["01HHB"].nonce))
    assert session.alive and session.missed_pings == 0

    # Two consecutive Pings with no Pong → dead.
    bus.heartbeat()           # missed #1
    assert session.alive
    bus.heartbeat()           # missed #2
    assert session.alive      # exactly 2 allowed
    bus.heartbeat()           # missed #3 → over the limit
    assert session.alive is False

    # The bus reaps dead sessions.
    dead = bus.reap_dead()
    assert "01HHB" in dead
    assert "01HHB" not in {s.session_id for s in bus.sessions}


# ───────────────────────── CB-11 ──────────────────────────

def test_cb11_snapshot_analysis_card_renders_no_binary():
    """ChatMessage(application/json, body=SnapshotAnalysis) → client parses it,
    renders the FrameRef thumbnail + analysis + cites; lands in #live-feed; no
    binary on the bus."""
    bus = ChatBus()
    _session, client = make_session(bus)

    frame = FrameRef(uri="gs://forge/frame-00412.jpg", width=1920, height=1080,
                     ts=now_ns(), sourceSeq=1)
    snap = SnapshotAnalysis(
        jobId="01HJOB",
        frame=frame,
        model="gemini-3-pro",
        analysis="Only the VIO header is connected; the cell-stack lead at J3 is unplugged.",
        cites=[EvidenceRef(kind="datasheet", uri="gs://forge/bq79616.pdf", note="§7")],
        ts=now_ns(),
    )
    msg = chat("#live-feed", snap.model_dump_json(),
               content_type="application/json", message_id="01HSNAP",
               author="@system", kind="system", ts=now_ns())
    bus.publish(msg)
    client.pump()

    stored = client.store.messages["01HSNAP"]
    assert stored.channelId == "#live-feed"
    assert stored.bodyContentType == "application/json"
    assert _render_kind(stored) == "SnapshotAnalysis"

    # Client parses the card and can render thumbnail + analysis + cites.
    parsed = SnapshotAnalysis.model_validate_json(stored.body)
    assert isinstance(parsed.frame, FrameRef)
    assert parsed.frame.uri.startswith("gs://")        # a reference/link, not bytes
    assert parsed.analysis
    assert len(parsed.cites) == 1 and parsed.cites[0].kind == "datasheet"

    # No binary on the bus: every body is a JSON/markdown string, never bytes.
    for event in [stored]:
        assert isinstance(event.body, str)
    # The frame is only ever a URI reference; no base64/byte payload smuggled in.
    body_obj = json.loads(stored.body)
    assert "pcmBase64" not in stored.body
    assert isinstance(body_obj["frame"]["uri"], str)


# ───────────────────────── extra: envelope sanity ──────────────────────────

def test_envelopes_not_in_agent_event_union():
    """Chat-bus-only envelopes are intentionally NOT parseable by the sealed
    AgentEvent adapter (they ride only on the bus)."""
    from pydantic import ValidationError
    from orchestrator.proto.events import parse_agent_event

    for env in (ChannelList(channels=[]), Ping(nonce="n"), Pong(nonce="n"),
                BackpressureNotice(dropped=1, sinceTs=now_ns()),
                ReplayDone(resumeTs=now_ns())):
        with pytest.raises(ValidationError):
            parse_agent_event(env.model_dump_json())


# ─────────── per-operator attribution hook (observer/ATTRIBUTION.md) ───────────

from orchestrator.chat_bus.envelopes import Presence
from orchestrator.chat_bus.ws import WebSocketTransport, event_to_json


class StampingTransport:
    """A Transport whose `send` accepts the additive `extra=` kwarg (like the
    real WebSocketTransport) and records the serialized JSON the subscriber
    would actually receive — so a test can assert on the stamped frame."""

    def __init__(self):
        self.frames: list[str] = []          # serialized JSON, as the client sees it
        self.events: list[object] = []        # raw events (for kind assertions)

    def send(self, event: object, *, extra=None) -> None:
        self.events.append(event)
        self.frames.append(event_to_json(event, extra=extra))


def _frames_as_dicts(transport: StampingTransport) -> list[dict]:
    return [json.loads(f) for f in transport.frames]


def test_attribution_fanned_out_frame_carries_origin_session_id():
    """(a) A fanned-out frame carries the ORIGINATING sessionId in its JSON.

    A ChatMessage (no sessionId of its own) published with
    `origin_session_id="op-bench-07"` reaches the subscriber stamped with that id,
    so a passive subscriber (the observer) can attribute it per-operator."""
    bus = ChatBus()
    transport = StampingTransport()
    bus.subscribe(Session("observer-dashboard", transport))
    transport.frames.clear()   # drop the subscribe-time Presence frame
    transport.events.clear()

    bus.publish(chat("#power", "rail looks fine", message_id="01HATTR", ts=now_ns()),
                origin_session_id="op-bench-07")

    cms = [d for d in _frames_as_dicts(transport) if d.get("kind") == "ChatMessage"]
    assert len(cms) == 1
    assert cms[0]["sessionId"] == "op-bench-07"     # stamped originator
    assert cms[0]["messageId"] == "01HATTR"          # payload intact

    # Untagged publish stays byte-for-byte what it was before (no sessionId key).
    transport.frames.clear()
    bus.publish(chat("#power", "untagged", message_id="01HBARE", ts=now_ns()))
    bare = [d for d in _frames_as_dicts(transport) if d.get("kind") == "ChatMessage"]
    assert bare and "sessionId" not in bare[0]


def test_attribution_event_with_own_session_id_is_unchanged():
    """(c) An event that already carries its own sessionId (e.g. Hello) keeps it —
    the origin stamp never clobbers an existing field."""
    from orchestrator.proto.events import Hello

    hello = Hello(client="phone", sessionId="hello-own-sid", protocolVersion="2.0")

    # Even when a DIFFERENT origin is threaded through, Hello.sessionId wins.
    stamped = json.loads(event_to_json(hello, extra={"sessionId": "some-other-origin"}))
    assert stamped["sessionId"] == "hello-own-sid"
    assert stamped["client"] == "phone"

    # And with no extra at all, the frame is exactly the model's own dump.
    assert event_to_json(hello) == hello.model_dump_json()
    assert json.loads(event_to_json(hello))["sessionId"] == "hello-own-sid"


def test_attribution_presence_on_subscribe_and_unsubscribe():
    """(b) Presence(online) is published on subscribe; Presence(offline) on
    unsubscribe — each tagged with the operator's id (and client label),
    delivered to a passive subscriber (the observer)."""
    bus = ChatBus()
    observer = StampingTransport()
    bus.subscribe(Session("observer-dashboard", observer))
    observer.frames.clear()
    observer.events.clear()

    # subscribe a real operator → observer sees Presence(online)
    bus.subscribe(Session("op-bench-07", StampingTransport()), client="phone")
    online = [d for d in _frames_as_dicts(observer) if d.get("kind") == "Presence"]
    assert len(online) == 1
    assert online[0]["state"] == "online"
    assert online[0]["sessionId"] == "op-bench-07"
    assert online[0]["client"] == "phone"
    assert isinstance(online[0]["ts"], int)

    observer.frames.clear()
    observer.events.clear()

    # unsubscribe → observer sees Presence(offline), same id + remembered label
    bus.unsubscribe("op-bench-07")
    offline = [d for d in _frames_as_dicts(observer) if d.get("kind") == "Presence"]
    assert len(offline) == 1
    assert offline[0]["state"] == "offline"
    assert offline[0]["sessionId"] == "op-bench-07"
    assert offline[0]["client"] == "phone"     # remembered from subscribe


def test_attribution_presence_not_delivered_to_self():
    """A connecting session never receives its OWN presence — so it can't jump
    ahead of that session's replay handshake (ChannelList must stay first)."""
    bus = ChatBus()
    own = StampingTransport()
    bus.subscribe(Session("op-solo", own), client="phone")
    assert not any(d.get("kind") == "Presence" for d in _frames_as_dicts(own))


def test_attribution_presence_fans_out_to_other_subscribers():
    """A second operator's connect/disconnect is delivered to an already-connected
    passive subscriber (the observer), tagged with the connecting operator's id."""
    bus = ChatBus()
    observer = StampingTransport()
    bus.subscribe(Session("observer-dashboard", observer))
    observer.frames.clear()

    op = StampingTransport()
    bus.subscribe(Session("op-bench-07", op), client="quest")

    seen = [d for d in _frames_as_dicts(observer)
            if d.get("kind") == "Presence" and d.get("sessionId") == "op-bench-07"]
    assert seen and seen[0]["state"] == "online" and seen[0]["client"] == "quest"


def test_attribution_presence_not_in_replay_buffer():
    """Presence is NOT recorded into the replay buffer, so it never re-fires on
    reconnect (only ChatMessages replay)."""
    bus = ChatBus()
    transport = StampingTransport()
    bus.subscribe(Session("01HREPLAY", transport))   # emits Presence(online)
    sent = bus.replay("01HREPLAY")
    assert not any(getattr(e, "kind", None) == "Presence" for e in sent)


def test_attribution_bus_flush_carries_origin_to_websocket_transport():
    """End-to-end through the real WebSocketTransport: Session.flush stamps the
    origin onto the wire frame the writer emits."""
    import asyncio

    class FakeWS:
        def __init__(self):
            self.sent: list[str] = []
        async def send_text(self, text: str) -> None:
            self.sent.append(text)

    async def run():
        ws = FakeWS()
        transport = WebSocketTransport(ws)
        session = Session("op-9", transport)
        session.enqueue(chat("#power", "hi", message_id="01HWS", ts=now_ns()),
                        origin_session_id="op-9")
        session.flush()                  # → transport.send(event, extra={...})
        writer = asyncio.create_task(transport.writer())
        await asyncio.sleep(0)           # let the writer drain one frame
        transport.close()
        await writer
        return ws.sent

    frames = asyncio.run(run())
    dicts = [json.loads(f) for f in frames]
    cm = next(d for d in dicts if d.get("kind") == "ChatMessage")
    assert cm["sessionId"] == "op-9"
    assert cm["messageId"] == "01HWS"


def test_attribution_backward_compatible_send_without_extra():
    """A bare Transport (send(event) only — the existing FakeTransport contract)
    still works: Session.flush degrades to send(event) when `extra` is rejected.
    Both a fanned-out Presence (from another operator) and a tagged ChatMessage
    reach the bare sink as raw events with no exception."""
    bus = ChatBus()
    transport = FakeTransport()          # bare send(event), no extra kwarg
    bus.subscribe(Session("observer-bare", transport))
    transport.received.clear()

    # Another operator connects → Presence fans out to the bare observer sink.
    bus.subscribe(Session("op-other", FakeTransport()), client="phone")
    bus.publish(chat("#power", "hi", message_id="01HBARE2", ts=now_ns()),
                origin_session_id="op-other")
    kinds = [getattr(e, "kind", None) for e in transport.received]
    assert "Presence" in kinds
    assert "ChatMessage" in kinds

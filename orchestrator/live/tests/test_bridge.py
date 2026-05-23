"""§3.5a — always-on media path: H.264 + audio → Live, byte pass-through, no
transcode, exactly one media socket, single-lifecycle reconnect."""

from __future__ import annotations

from orchestrator.live.bridge import LivePassthrough


def test_passthrough_byte_identical_one_socket():
    received: list[bytes] = []
    bridge = LivePassthrough(live_sink=received.append)

    chunks = [b"\x00\x00\x00\x01h264-nal-a", b"audio-pcm-b", b"\x00\x00\x00\x01h264-nal-c"]
    for c in chunks:
        bridge.forward(c)

    assert b"".join(received) == b"".join(chunks)        # byte-for-byte pass-through
    assert bridge.media_sockets == 1                     # one persistent socket
    assert bridge.bytes_forwarded == sum(len(c) for c in chunks)


def test_no_decode_or_transcode_method():
    # Mirrors WP-12: the relay must expose no codec entrypoint.
    forbidden = ("decode", "encode", "transcode")
    for attr in dir(LivePassthrough):
        assert not any(f in attr.lower() for f in forbidden), attr


def test_reconnect_single_lifecycle():
    sink_a: list[bytes] = []
    sink_b: list[bytes] = []
    bridge = LivePassthrough(live_sink=sink_a.append)
    bridge.forward(b"before")
    # uplink drops, client reconnects → same single socket, new sink
    bridge.reconnect(live_sink=sink_b.append)
    bridge.forward(b"after")

    assert sink_a == [b"before"]
    assert sink_b == [b"after"]
    assert bridge.media_sockets == 1  # never spawned a second feed to reconcile

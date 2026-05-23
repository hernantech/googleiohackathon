"""§3.5a — always-on media path: PCM audio + JPEG frames → Live, byte
pass-through, no transcode, exactly one media socket, single-lifecycle
reconnect. The sink now carries the chunk's MediaKind (audio vs JPEG) so it can
route to the right Live realtime-input slot; the bytes are still verbatim."""

from __future__ import annotations

from orchestrator.live.bridge import LivePassthrough, MediaKind


def test_passthrough_byte_identical_one_socket():
    received: list[bytes] = []

    def sink(chunk: bytes, kind: MediaKind) -> None:
        received.append(chunk)

    bridge = LivePassthrough(live_sink=sink)

    chunks = [b"pcm-a", b"\xff\xd8jpeg-b\xff\xd9", b"pcm-c"]
    bridge.forward(chunks[0], MediaKind.AUDIO)
    bridge.forward(chunks[1], MediaKind.VIDEO)
    bridge.forward(chunks[2], MediaKind.AUDIO)

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
    bridge = LivePassthrough(live_sink=lambda c, k: sink_a.append(c))
    bridge.forward(b"before")
    # uplink drops, client reconnects → same single socket, new sink
    bridge.reconnect(live_sink=lambda c, k: sink_b.append(c))
    bridge.forward(b"after")

    assert sink_a == [b"before"]
    assert sink_b == [b"after"]
    assert bridge.media_sockets == 1  # never spawned a second feed to reconcile

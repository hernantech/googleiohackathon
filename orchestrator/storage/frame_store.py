"""FrameStore — stores snapshot JPEGs with the v1 `FRAM` binary header (00 §4.3).

In-memory by default (a `mem:` URI ring); a GCS-backed variant can swap in later
behind the same `put`/`get` surface. We never decode the JPEG — width/height are
provided by the caller (the client knows them), consistent with "no server-side
decode/transcode" (08 §3.5a)."""

from __future__ import annotations

import struct

from orchestrator.proto.events import FrameRef

FRAM_MAGIC = b"FRAM"
HEADER_LEN = 20  # magic(4) + width(4) + height(4) + ts(8)


def wrap_fram(jpeg: bytes, width: int, height: int, ts: int) -> bytes:
    """Prepend the FRAM header (00 §4.3) to raw JPEG bytes."""
    return FRAM_MAGIC + struct.pack("<II", width, height) + struct.pack("<Q", ts) + jpeg


def has_fram_header(blob: bytes) -> bool:
    return blob[:4] == FRAM_MAGIC


def unwrap_fram(blob: bytes) -> bytes:
    """Return the JPEG payload from a FRAM blob."""
    if not has_fram_header(blob):
        raise ValueError("not a FRAM blob")
    return blob[HEADER_LEN:]


class InMemoryFrameStore:
    """A bounded in-memory store of FRAM-wrapped snapshots."""

    def __init__(self, capacity: int = 256):
        self._blobs: dict[str, bytes] = {}
        self._order: list[str] = []
        self._seq = 0
        self._capacity = capacity

    def put(self, jpeg: bytes, width: int, height: int, ts: int) -> FrameRef:
        self._seq += 1
        uri = f"mem:frame-{self._seq:05d}"
        self._blobs[uri] = wrap_fram(jpeg, width, height, ts)
        self._order.append(uri)
        if len(self._order) > self._capacity:
            old = self._order.pop(0)
            self._blobs.pop(old, None)
        return FrameRef(uri=uri, width=width, height=height, ts=ts, sourceSeq=self._seq)

    def get(self, uri: str) -> bytes:
        return self._blobs[uri]

    def get_jpeg(self, uri: str) -> bytes:
        return unwrap_fram(self._blobs[uri])

"""Forge Observer — a decoupled, read-only manager dashboard.

The observer is a WebSocket *client* of the orchestrator's /v2/chat bus. It
never imports from ``orchestrator/`` (the wire shapes are mirrored here on
purpose) so it can build + ship in its own container without coupling to the
orchestrator's source tree or release cadence.

Three concerns, three modules, one process (see README for the one-container
rationale):

  - ingest    : WS client → normalize → persist raw events to SQLite
  - distill   : periodic "managed agent" (Gemini) → per-session STATUS rows
  - web       : FastAPI app polling SQLite → the manager view
"""

__all__ = ["__version__"]

__version__ = "0.1.0"

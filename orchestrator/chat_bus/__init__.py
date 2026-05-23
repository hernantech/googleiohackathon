"""Chat Bus (P3) — WebSocket protocol between the orchestrator and the
Discord-style multi-channel client. See specs/04_chat_bus_protocol.md.

The testable core is transport-agnostic:
- `ChatBus` — pub/sub fan-out, replay buffer, per-session bounded queues.
- chat-bus-only envelopes in `envelopes` (not in the sealed AgentEvent union).
- the server-defined channel roster in `channels`.
"""

from orchestrator.chat_bus.bus import (  # noqa: F401
    ChatBus,
    Session,
)
from orchestrator.chat_bus.channels import (  # noqa: F401
    SME_ROSTER,
    build_channel_list,
    channel_ids,
)
from orchestrator.chat_bus.envelopes import (  # noqa: F401
    BackpressureNotice,
    ChannelHint,
    ChannelInfo,
    ChannelList,
    ErrorEvent,
    Ping,
    Pong,
    ReplayDone,
    Subscribe,
    Unsubscribe,
)

"""Server-defined channel roster + the `ChannelList` builder (§2).

Channels are server-defined: the client can subscribe/mute but never create
them. There is deliberately NO `#bench-tech` — that SME was removed because
nothing actuates the bench (cross-checked against `02 §3`/`§9` and `07 §7`).

The roster the orchestrator advertises at connection time is:
  - one `#<sme-id>` channel per SME in `SME_ROSTER`, and
  - the fixed system channels (`#live-feed #user #actions #dissent #general`).

`#sentinel` and `#scribe` are SME channels (they appear in `SME_ROSTER`); §2
also lists them as system-fed channels, so they are NOT duplicated — the SME
channel is the canonical home for each.
"""

from __future__ import annotations

from orchestrator.chat_bus.envelopes import ChannelInfo, ChannelList

#: The SME roster, in declaration order. Mirrors `02 §9` / `07 §7`.
#: Explicitly contains no `bench-tech`.
SME_ROSTER: tuple[str, ...] = (
    "power",
    "signal",
    "firmware",
    "layout",
    "librarian",
    "sourcing",
    "reverse",
    "sentinel",
    "scribe",
    "tutor",
)

#: Human-readable titles + icons for the SME channels.
_SME_TITLES: dict[str, tuple[str, str]] = {
    "power": ("Power", "⚡"),
    "signal": ("Signal", "📶"),
    "firmware": ("Firmware", "💾"),
    "layout": ("Layout", "📐"),
    "librarian": ("Librarian", "📚"),
    "sourcing": ("Sourcing", "🛒"),
    "reverse": ("Reverse", "🔍"),
    "sentinel": ("Sentinel", "🛡️"),
    "scribe": ("Scribe", "✍️"),
    "tutor": ("Tutor", "🎓"),
}

#: Fixed, non-SME system channels (§2 table). `id -> (title, icon, alwaysVisible)`.
#: `#user` and `#general` are `alwaysVisible` (always-on, can't be muted) per §2.
_SYSTEM_CHANNELS: tuple[tuple[str, str, str, bool], ...] = (
    ("#live-feed", "Live Feed", "📡", False),
    ("#user", "You", "🗣️", True),
    ("#actions", "Actions", "✅", False),
    ("#dissent", "Dissent", "⚔️", False),
    ("#general", "General", "💬", True),
)


def sme_channel_id(sme_id: str) -> str:
    """`"power"` -> `"#power"`; tolerates an already-prefixed `@power`/`#power`."""
    bare = sme_id.lstrip("@#")
    return f"#{bare}"


def build_channel_list(unread: dict[str, int] | None = None) -> ChannelList:
    """Construct the `ChannelList` the server emits at connect / replay (§2).

    `unread` optionally backfills `unreadHint` per channel id (used at replay,
    §6). Ordering: SME channels first (roster order), then system channels.
    """
    unread = unread or {}
    channels: list[ChannelInfo] = []

    for sme in SME_ROSTER:
        cid = sme_channel_id(sme)
        title, icon = _SME_TITLES[sme]
        channels.append(
            ChannelInfo(
                id=cid,
                title=title,
                smeId=f"@{sme}",
                icon=icon,
                alwaysVisible=False,
                unreadHint=unread.get(cid, 0),
            )
        )

    for cid, title, icon, always in _SYSTEM_CHANNELS:
        channels.append(
            ChannelInfo(
                id=cid,
                title=title,
                smeId=None,
                icon=icon,
                alwaysVisible=always,
                unreadHint=unread.get(cid, 0),
            )
        )

    return ChannelList(channels=channels)


def channel_ids() -> set[str]:
    """Set of every server-defined channel id (SME + system)."""
    return {c.id for c in build_channel_list().channels}


def sme_channel_ids() -> set[str]:
    """Set of just the `#<sme-id>` channel ids — used by CB-1's roster check."""
    return {sme_channel_id(s) for s in SME_ROSTER}

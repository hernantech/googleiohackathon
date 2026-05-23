"""Safety matrix as data — `specs/03_safety_gate_matrix.md` §3.

Pure tables + tiny predicates. No model, no I/O. The dynamic risk for
value-bearing steps (set_psu / serial_send / flash_mcu) lives in `gate.py`
because it depends on the documented limit and the args.
"""

from __future__ import annotations

#: Sentinel meaning "any SME may invoke".
ANY: frozenset[str] = frozenset({"*"})

#: SMEs known to the roster. Steps from anyone else are DENIED (§3.3 rule 2).
DEFAULT_ROSTER: frozenset[str] = frozenset({
    "@power", "@signal", "@firmware", "@layout", "@librarian",
    "@sourcing", "@reverse", "@sentinel", "@scribe", "@tutor",
    "LiveSpeaker", "@user", "user",
})

#: Step → who may recommend it (§3.1 / §3.2 "Invokable by"). Empty/ANY = anyone.
INVOKABLE_BY: dict[str, frozenset[str]] = {
    # physical operator steps (§3.1)
    "probe_net": frozenset({"@power", "@signal", "@firmware", "@sentinel"}),
    "inspect_closeup": frozenset({"@reverse", "@signal", "@power"}),
    "set_psu": frozenset({"@power"}),
    "enable_psu_output": frozenset({"@power"}),
    "disable_psu_output": frozenset({"@power", "@sentinel"}),
    "serial_send": frozenset({"@firmware"}),
    "flash_mcu": frozenset({"@firmware"}),
    "reflow_pin": frozenset({"@reverse", "@signal"}),
    # non-physical steps (§3.2)
    "summon_guild": frozenset({"LiveSpeaker"}),
    "request_snapshot": ANY,
    "lookup_datasheet": ANY,
    "lookup_board_doc": ANY,
    "get_documented_limit": ANY,
    "web_fetch": ANY,
    "request_human_confirmation": ANY,
    "publish_report": frozenset({"@scribe", "@user", "user"}),
    "sourcing.order_parts": frozenset({"@sourcing"}),
}

#: LOW, read-only / trivially-reversible → auto-shown, no confirm (§1).
AUTO_ALLOW: frozenset[str] = frozenset({
    "probe_net", "inspect_closeup", "disable_psu_output",
    "summon_guild", "request_snapshot", "web_fetch",
    "lookup_datasheet", "lookup_board_doc", "get_documented_limit",
})

#: Always require confirmation regardless of value (§3.1 / §3.2).
ALWAYS_CONFIRM: frozenset[str] = frozenset({
    "enable_psu_output", "request_human_confirmation",
})

#: Denied in hackathon scope → surfaced as request_human_confirmation (§3.2).
DENY_TOOLS: frozenset[str] = frozenset({"sourcing.order_parts"})

#: Steps that may carry a numeric setpoint (§3.3 rule 6 provenance).
VALUE_BEARING: frozenset[str] = frozenset({"set_psu", "serial_send", "flash_mcu"})

#: serial_send payloads matching these → HIGH (§3.1).
DANGEROUS_SERIAL_PATTERNS: frozenset[str] = frozenset({
    "reset", "calibrate", "format", "erase", "shutdown",
})

_RISK_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "HALT": 3}


def risk_max(*risks: str) -> str:
    """`max(table_default, sme_declared, …)` over the risk ladder (§3.3 rule 1)."""
    return max(risks, key=lambda r: _RISK_RANK[r])


def invoker_allowed(tool: str, invoker: str) -> bool:
    """True if `invoker` is on the step's Invokable-by list (or it's ANY)."""
    allowed = INVOKABLE_BY.get(tool)
    if allowed is None:
        return False  # unknown tool → not invokable
    return allowed is ANY or invoker in allowed

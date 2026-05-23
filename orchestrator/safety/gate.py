"""SafetyGate evaluator — `specs/03_safety_gate_matrix.md` §3-§6.

Gates *operator instructions* (and ungates read-only guild lookups). It NEVER
executes anything — the decision tells the orchestrator whether to show the
step, ask the human to confirm, or refuse. Two layers: this gate, and the
documented board limits (§6) fetched from the P1 KnowledgeAdapter.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Literal

from orchestrator.proto.events import ActionCard, ProposedAction
from orchestrator.knowledge import KnowledgeAdapter
from orchestrator.knowledge.limits import DocumentedLimit
from orchestrator.safety import matrix as M

Decision = Literal["allow", "confirm", "deny", "halt_bypass", "queued", "suppressed"]

#: Conservative fallback limits when the board profile is absent (07 §2.1, 03 §6).
DEFAULT_MAX_VOLTAGE_V = 12.0
DEFAULT_MAX_CURRENT_A = 1.0

#: Max simultaneous pending confirmations (§3.3 rule 4).
MAX_PENDING = 3


@dataclass
class GateDecision:
    decision: Decision
    risk: str
    reason: str
    needs_confirmation: bool
    emit_warn: bool = False
    documented_limit: DocumentedLimit | None = None
    card: ActionCard | None = None


@dataclass
class GateSession:
    """Per-session mutable safety state."""
    psu_output_on: bool = False
    pending_count: int = 0
    _skip_counts: dict[str, int] = field(default_factory=dict)
    _last_halt_ts: float | None = None


def _args(action: ProposedAction) -> dict:
    try:
        return json.loads(action.argsJson) if action.argsJson else {}
    except (ValueError, TypeError):
        return {}


def _has_numeric_setpoint(tool: str, args: dict) -> bool:
    if tool == "set_psu":
        return any(k in args for k in ("voltage_v", "current_limit_a"))
    if tool == "serial_send":
        return "baud" in args
    return False


class SafetyGate:
    """Evaluate one ProposedAction at a time (table-driven; §3)."""

    def __init__(
        self,
        knowledge: KnowledgeAdapter,
        roster: frozenset[str] = M.DEFAULT_ROSTER,
        defaults: tuple[float, float] = (DEFAULT_MAX_VOLTAGE_V, DEFAULT_MAX_CURRENT_A),
        now: Callable[[], float] | None = None,
    ):
        self._k = knowledge
        self._roster = roster
        self._defaults = defaults
        import time as _t
        self._now = now or _t.monotonic

    # ── public API ──────────────────────────────────────────────
    def evaluate(
        self,
        action: ProposedAction,
        invoker: str,
        session: GateSession,
        *,
        sentinel_halt: bool = False,
    ) -> GateDecision:
        tool = action.tool
        args = _args(action)

        # @sentinel HALT bypass (§5) — only for disable_psu_output.
        if sentinel_halt and invoker == "@sentinel" and tool == "disable_psu_output":
            return self._halt(session)

        # guild (read-only) lookups are never gated (§3.2, SG-12).
        if action.actor == "guild" or tool in {
            "lookup_datasheet", "lookup_board_doc", "get_documented_limit",
            "web_fetch", "summon_guild", "request_snapshot",
        }:
            return GateDecision("allow", "LOW", "read-only / internal", needs_confirmation=False)

        # unknown SME (§3.3 rule 2).
        if invoker not in self._roster:
            return GateDecision("deny", action.risk, f"unknown invoker {invoker}",
                                needs_confirmation=False, emit_warn=True)

        # forbidden invoker (§3.3 rule 3).
        if not M.invoker_allowed(tool, invoker):
            return GateDecision("deny", action.risk, "out of scope for invoker",
                                needs_confirmation=False, emit_warn=True)

        # session skip-denylist (§3.3 rule 5).
        sig = self._sig(tool, args)
        if session._skip_counts.get(sig, 0) >= 2:
            return GateDecision("suppressed", action.risk,
                                "operator already skipped this", needs_confirmation=False)

        # denied-in-scope tools → surface as request_human_confirmation (§3.2).
        if tool in M.DENY_TOOLS:
            return self._confirm(session, "MEDIUM",
                                 "ordering disabled in hackathon scope; complete in your browser")

        # the load-bearing branch: classify + (for value-bearing) limit-check.
        return self._classify(action, tool, args, session)

    # ── internals ───────────────────────────────────────────────
    def _classify(self, action, tool, args, session) -> GateDecision:
        # provenance rule (§3.3 rule 6): a numeric setpoint without a citation
        # is downgraded to request_human_confirmation.
        if tool in M.VALUE_BEARING and _has_numeric_setpoint(tool, args) \
                and not action.documentedLimitRef:
            return self._confirm(session, M.risk_max("MEDIUM", action.risk),
                                 "no documented source for this value — verify before doing it")

        if tool == "set_psu":
            return self._set_psu(action, args, session)
        if tool == "serial_send":
            return self._serial_send(action, args, session)
        if tool == "flash_mcu":
            return self._flash_mcu(action, session)
        if tool == "reflow_pin":
            return self._precondition_high(action, session, "reflow")
        if tool in M.AUTO_ALLOW:
            return GateDecision("allow", M.risk_max("LOW", action.risk),
                                "auto-shown", needs_confirmation=False)
        if tool in M.ALWAYS_CONFIRM:
            return self._confirm(session, M.risk_max("MEDIUM", action.risk), "confirm required")
        # unknown but invokable tool → conservative confirm.
        return self._confirm(session, M.risk_max("MEDIUM", action.risk), "default confirm")

    def _set_psu(self, action, args, session) -> GateDecision:
        v = float(args.get("voltage_v", 0.0))
        i = float(args.get("current_limit_a", 0.0))
        target = args.get("target") or args.get("net") or ""
        limit = self._k.get_documented_limit(target, "net") if target else \
            DocumentedLimit(target=target, found=False)

        max_v = limit.maxVoltageV if (limit.found and limit.maxVoltageV is not None) else self._defaults[0]
        max_i = limit.maxCurrentA if (limit.found and limit.maxCurrentA is not None) else self._defaults[1]

        if v > max_v or i > max_i:
            src = limit.source if limit.found else f"default {max_v} V / {max_i} A"
            return GateDecision("deny", "HIGH",
                                f"exceeds documented limit ({src}): {v} V / {i} A",
                                needs_confirmation=False, emit_warn=True, documented_limit=limit)

        if v > 12 or i > 1:
            base = "HIGH"
        elif v > 5 or i > 0.5:
            base = "MEDIUM"
        else:
            base = "LOW"
        # no documented source → force at least MEDIUM (§6 fallback, SG-9).
        if not limit.found:
            base = M.risk_max(base, "MEDIUM")
        risk = M.risk_max(base, action.risk)

        if risk == "LOW":
            return GateDecision("allow", "LOW", "within low band", needs_confirmation=False,
                                documented_limit=limit)
        card = self._card(action, risk, limit)
        return self._confirm(session, risk, "set_psu confirm", limit=limit, card=card)

    def _serial_send(self, action, args, session) -> GateDecision:
        payload = str(args.get("payload", "")).lower()
        if any(p in payload for p in M.DANGEROUS_SERIAL_PATTERNS):
            return self._confirm(session, M.risk_max("HIGH", action.risk), "dangerous serial pattern")
        return GateDecision("allow", M.risk_max("LOW", action.risk), "serial ok",
                            needs_confirmation=False)

    def _flash_mcu(self, action, session) -> GateDecision:
        # precondition: PSU output must be off (§3.1, board preconditions).
        requires_off = getattr(
            getattr(self._k.board_profile, "preconditions", None),
            "flash_requires_psu_off", True,
        )
        if requires_off and session.psu_output_on:
            return GateDecision("deny", "HIGH", "psu_must_be_off before flashing",
                                needs_confirmation=False, emit_warn=True)
        return self._confirm(session, M.risk_max("HIGH", action.risk), "flash confirm")

    def _precondition_high(self, action, session, what) -> GateDecision:
        if session.psu_output_on:
            return GateDecision("deny", "HIGH", f"power down before {what}",
                                needs_confirmation=False, emit_warn=True)
        return self._confirm(session, M.risk_max("HIGH", action.risk), f"{what} confirm")

    def _halt(self, session) -> GateDecision:
        now = self._now()
        if session._last_halt_ts is not None and (now - session._last_halt_ts) < 60.0:
            # coalesce within the 60 s window (§5, SG-7) — no new card.
            return GateDecision("suppressed", "HALT", "HALT coalesced (rate-limited 1/60s)",
                                needs_confirmation=False)
        session._last_halt_ts = now
        return GateDecision("halt_bypass", "HALT", "sentinel HALT — power down now",
                            needs_confirmation=False)

    def _confirm(self, session, risk, reason, *, limit=None, card=None) -> GateDecision:
        if session.pending_count >= MAX_PENDING:
            return GateDecision("queued", risk, "pending limit reached", needs_confirmation=False,
                                documented_limit=limit, card=card)
        session.pending_count += 1
        return GateDecision("confirm", risk, reason, needs_confirmation=True,
                            documented_limit=limit, card=card)

    def _card(self, action, risk, limit: DocumentedLimit) -> ActionCard:
        doc = None
        if limit.found and limit.maxVoltageV is not None:
            doc = f"board doc max: {limit.maxVoltageV} V ({limit.source})"
        return ActionCard(
            title="confirm operator step",
            bodyMarkdown=action.instruction or action.rationale,
            risk=risk if risk in ("LOW", "MEDIUM", "HIGH") else "HIGH",
            documentedLimit=doc,
        )

    @staticmethod
    def _sig(tool: str, args: dict) -> str:
        return tool + "|" + json.dumps(args, sort_keys=True)

    # convenience for SG-10
    def record_skip(self, session: GateSession, action: ProposedAction) -> None:
        sig = self._sig(action.tool, _args(action))
        session._skip_counts[sig] = session._skip_counts.get(sig, 0) + 1
        session.pending_count = max(0, session.pending_count - 1)

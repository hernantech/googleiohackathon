"""GraphEngine — faithful runner of the 01 §2 topology.

Entry ticks: a final transcript (runs the main pipeline), a SnapshotAnalysis
(perception evidence), or a sentinel hazard. Each node is wrapped in the
never-fail-stop error envelope (01 §7): an exception becomes a
SafetyInterrupt(WARN) and the graph continues. HITL: when SafetyGate needs a
confirmation the run returns `paused`; `resume()` applies the operator's answer.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import re

from orchestrator.graph.state import (
    DissentResult,
    ForgeState,
    GraphDeps,
    MergedOpinion,
    RouteDecision,
    RunResult,
)
from orchestrator.proto.events import (
    ChatMessage,
    CheckpointMarker,
    ConfirmationRequest,
    ConfirmationResponse,
    DissentReport,
    SafetyInterrupt,
    SmeResponse,
    SnapshotAnalysis,
    SummonGuild,
    new_ulid,
    now_ns,
)
from orchestrator.safety.gate import GateSession

_MENTION = re.compile(r"@([a-z0-9\-]+)", re.I)
CROSS_EXAM_CAP = 2


class GraphEngine:
    def __init__(self, deps: GraphDeps):
        self.deps = deps
        self._gate_session = GateSession()
        #: Active incremental sink for the in-flight run. None ⇒ fall back to
        #: deps.emit (the default no-op). Set by run()/resume()'s `emit=` kwarg
        #: and torn down in a finally so it never leaks across runs/sessions.
        self._emit_sink = None

    # ───────────────────────── entry ticks ─────────────────────────
    def ingest_snapshot(self, state: ForgeState, snap: SnapshotAnalysis) -> None:
        """PerceptionGate for a 📷 snapshot (01 §3.1): set latestFrame, post the
        analysis to #live-feed, checkpoint."""
        state.latestFrame = snap.frame
        state.latestSnapshot = snap
        state.outboundEvents.append(ChatMessage(
            channelId="#live-feed", authorId="@forge", authorKind="system",
            body=snap.model_dump_json(), bodyContentType="application/json",
            messageId=new_ulid(), ts=snap.ts,
        ))
        state.outboundEvents.append(CheckpointMarker(
            checkpointId=new_ulid(), graphNodeName="PerceptionGate", ts=now_ns()))

    def ingest_malformed(self, state: ForgeState, channel: str = "#live-feed") -> None:
        """Malformed transcript → Goodbye on that channel only; graph survives (GR-2)."""
        from orchestrator.proto.events import Goodbye
        state.errors.append("perception_invalid")
        state.outboundEvents.append(Goodbye(reason="perception_invalid"))

    def sentinel_observe(self, state: ForgeState, reason: str, *, severity: str = "HALT") -> None:
        """Sentinel hazard (01 §4.1): emit SafetyInterrupt; NEVER actuate (GR-13)."""
        recover = []
        if severity == "HALT":
            from orchestrator.proto.events import ProposedAction
            recover = [ProposedAction(
                actor="operator", tool="disable_psu_output", argsJson='{"channel":1}',
                rationale="power down now", risk="LOW",
                instruction="Turn the PSU output OFF now.")]
        state.outboundEvents.append(SafetyInterrupt(
            severity=severity, reason=reason, suggestedRecoverActions=recover, ts=now_ns()))

    # ───────────────────────── main pipeline ─────────────────────────
    def run(self, state: ForgeState, transcript: str, *, emit=None) -> RunResult:
        """Run the main pipeline. `emit`, when given, is a per-run incremental
        publish sink (callable taking one event): summon notice, per-SME claims
        and per-tool-call activity stream to it AS THEY HAPPEN. Streamed events
        are also recorded in state.streamedEvents so the caller's final drain is
        idempotent. Defaults to GraphDeps.emit (a no-op) → unchanged for tests
        that don't inject a sink."""
        with self._streaming(emit):
            return self._run(state, transcript)

    def _run(self, state: ForgeState, transcript: str) -> RunResult:
        state.latestTranscriptFinal = transcript
        state.outboundEvents.append(CheckpointMarker(
            checkpointId=new_ulid(), graphNodeName="PerceptionGate", ts=now_ns()))

        decision = self._supervisor(state, transcript)
        if not decision.needs_guild:
            self._live_speaker(state, transcript)
            return RunResult("direct_reply", state)

        state.pendingSummon = SummonGuild(
            callId=new_ulid(), topic=decision.topic or "guild",
            smes=decision.smes, deadlineMs=decision.deadline_ms,
            contextRefs=[state.latestFrame.uri] if state.latestFrame else [],
            briefing=self._build_briefing(state))
        return self._deliberate(state)

    def _build_briefing(self, state: ForgeState) -> str:
        """Assemble the grounding every SME needs (01 §3.3): the operator's
        question, the board under test + documented limits, a board-doc passage
        for the question, and the latest camera snapshot. This is the fix for
        'SMEs have no proper context' — without it each SME sees only an 8-word
        topic + opaque frame URIs."""
        lines: list[str] = []
        if state.latestTranscriptFinal:
            lines.append(f"Operator said: {state.latestTranscriptFinal}")
        bp = getattr(self.deps.knowledge, "board_profile", None)
        if bp is not None and not getattr(bp, "is_empty", True):
            parts = ", ".join(f"{p.ref} {p.part}" for p in bp.parts)
            lines.append(f"Board under test ({bp.id}): {parts}.")
            limits = "; ".join(
                f"{n.id}≤{n.max_voltage_v}V" for n in bp.nets
                if getattr(n, "max_voltage_v", None) is not None)
            if limits:
                lines.append(f"Documented net limits: {limits}.")
        if state.latestTranscriptFinal:
            try:
                doc = self.deps.knowledge.lookup_board_doc(state.latestTranscriptFinal)
                if doc.passages:
                    lines.append(f"Board doc: {doc.passages[0].text}")
            except Exception:  # noqa: BLE001 — grounding is best-effort
                pass
        if state.latestSnapshot is not None:
            lines.append(f"Latest camera snapshot (vision): {state.latestSnapshot.analysis}")
        return "\n".join(lines)

    def _deliberate(self, state: ForgeState) -> RunResult:
        while True:
            self._parallel_summon(state)
            self._aggregate(state)
            self._safe(state, self._merge, "MergeOpinion")
            convergence = self._dissent(state)
            if convergence == "needs_more_rounds" and state.crossExamRound < CROSS_EXAM_CAP:
                state.crossExamRound += 1
                continue
            break

        result = self._safety(state)
        if result == "paused":
            return RunResult("paused", state)
        self._live_speaker(state, (state.mergedOpinion.headline if state.mergedOpinion else ""))
        return RunResult("complete", state)

    # ───────────────────────── nodes ─────────────────────────
    def _supervisor(self, state: ForgeState, transcript: str) -> RouteDecision:
        """SupervisorRouter (01 §3.2): @-mentions force-include; bad model output
        retries once then falls back to no_guild (GR-3/GR-4)."""
        forced = ["@" + m for m in _MENTION.findall(transcript)]
        attempts = 0
        decision = None
        while attempts < 2 and decision is None:
            attempts += 1
            try:
                decision = self.deps.classify(transcript, [])
            except Exception:  # noqa: BLE001 — malformed model output
                decision = None
        if decision is None:
            if forced:
                return RouteDecision(needs_guild=True, smes=forced, topic="mention")
            state.errors.append("routing_failed")
            return RouteDecision(needs_guild=False)
        # merge forced mentions as hard hints
        for m in forced:
            if m not in decision.smes:
                decision.smes.append(m)
        if forced:
            decision.needs_guild = True
        return decision

    def _parallel_summon(self, state: ForgeState) -> None:
        """Fan out; per-SME timeout → confidence-0 placeholder, others continue (GR-5).

        Streams deliberation AS IT HAPPENS (not batched at the end): a summon
        notice to #live-feed up front, then — per SME, the moment it completes —
        its claim+rationale to #<sme>, plus a short #<sme> note for each knowledge
        tool the SME called (so retrieval is visible). All via self._emit so the
        chat bus sees them live while state.outboundEvents stays the full record."""
        summon = state.pendingSummon
        state.activeSmes = list(summon.smes)

        # 1) summon notice → #live-feed, immediately (only on the first round so
        #    cross-exam re-summons don't spam the feed).
        if state.crossExamRound == 0:
            roster = ", ".join(summon.smes)
            self._emit(state, ChatMessage(
                channelId="#live-feed", authorId="@forge", authorKind="system",
                body=f"summoned {roster}", messageId=new_ulid(), ts=now_ns()))

        for sme in summon.smes:
            # 3) surface each knowledge tool call to the SME's channel AS IT
            #    happens. summon_one captures the calls in real_summon_one's loop;
            #    we pass a per-SME emit so they stream rather than vanish. The
            #    callback is opt-in: only wired when summon_one accepts `emit`.
            def _on_tool_call(call: dict, _sme: str = sme) -> None:
                self._emit(state, ChatMessage(
                    channelId=_sme_channel(_sme), authorId=_sme, authorKind="sme",
                    body=f"{_sme} → {_format_tool_call(call)}",
                    messageId=new_ulid(), ts=now_ns()))

            try:
                resp = self._summon_one(sme, summon, _on_tool_call)
            except Exception as e:  # noqa: BLE001 — timeout / cold-start
                resp = SmeResponse(
                    smeId=sme, callId=summon.callId, confidence=0.0,
                    claim="<timeout>", rationale=f"{e!r}", ts=now_ns())
            state.smeResponses[sme] = resp
            for action in resp.proposedActions:
                state.invokerOf[id(action)] = sme

            # 2) per-SME completion → claim+rationale to #<sme>, RIGHT AWAY (not
            #    batched after every SME finishes).
            self._emit(state, ChatMessage(
                channelId=_sme_channel(sme), authorId=sme, authorKind="sme",
                body=_format_claim(resp), messageId=new_ulid(), ts=now_ns()))

    def _summon_one(self, sme: str, summon, on_tool_call) -> SmeResponse:
        """Call deps.summon_one, threading a per-tool-call emit when the seam
        supports it (real_summon_one does; stubs / test doubles need not). We
        introspect the callable so the GraphDeps contract stays back-compatible:
        a 2-arg summon_one keeps working unchanged."""
        fn = self.deps.summon_one
        try:
            sig = inspect.signature(fn)
            accepts_emit = "on_tool_call" in sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        except (TypeError, ValueError):  # builtins / un-introspectable
            accepts_emit = False
        if accepts_emit:
            return fn(sme, summon, on_tool_call=on_tool_call)
        return fn(sme, summon)

    def _aggregate(self, state: ForgeState) -> None:
        """StreamingAggregator (01 §3.4): bounded queue, coalesce on overflow (GR-6)."""
        q: list[str] = []
        cap = self.deps.aggregator_queue_max
        for sme, resp in state.smeResponses.items():
            q.append(f"{sme}: {resp.claim}")
            if len(q) > cap:
                q = q[-cap:]  # coalesce to most-recent
        state.outboundEvents.append(ChatMessage(
            channelId="#live-feed", authorId="@forge", authorKind="system",
            body="\n".join(q), messageId=new_ulid(), ts=now_ns(), streaming=True))

    def _merge(self, state: ForgeState) -> None:
        """MergeOpinion (01 §3.5): drop confidence<0.2 → openQuestions; dedup actions."""
        responses = list(state.smeResponses.values())
        kept = [r for r in responses if r.confidence >= 0.2]
        open_qs = [f"{r.smeId}: {r.claim}" for r in responses if r.confidence < 0.2]
        headline, supporting = self.deps.merge_fn(kept)  # may raise → error envelope

        seen: set[tuple[str, str]] = set()
        actions = []
        for r in kept:
            for a in r.proposedActions:
                key = (a.tool, a.argsJson)
                if key in seen:
                    continue
                seen.add(key)
                actions.append(a)
                state.invokerOf.setdefault(id(a), r.smeId)
        state.mergedOpinion = MergedOpinion(
            headline=headline or "Inconclusive; need more evidence.",
            supportingSmes=supporting, openQuestions=open_qs, proposedActions=actions)

    def _dissent(self, state: ForgeState) -> str:
        responses = list(state.smeResponses.values())
        try:
            res: DissentResult = self.deps.dissent_fn(responses, state.crossExamRound)
        except Exception:  # noqa: BLE001 — malformed → assume converged (01 §3.6)
            return "converged"
        if res.pairwise:
            state.dissentReport = DissentReport(
                callId=state.pendingSummon.callId, parties=[p.a for p in res.pairwise] +
                [p.b for p in res.pairwise], axis="root_cause",
                summary="; ".join(p.crux for p in res.pairwise),
                pairwise=res.pairwise, ts=now_ns())
            state.outboundEvents.append(state.dissentReport)
        return res.convergence

    def _safety(self, state: ForgeState) -> str:
        """SafetyGate node (01 §3.7): gate each proposed action; pending → pause."""
        merged = state.mergedOpinion
        if not merged or not merged.proposedActions:
            return "go"
        for action in merged.proposedActions:
            invoker = state.invokerOf.get(id(action), action_invoker_default(action))
            decision = self.deps.gate.evaluate(action, invoker, self._gate_session)
            self._audit(state, action, invoker, decision)
            if decision.decision == "deny":
                state.outboundEvents.append(SafetyInterrupt(
                    severity="WARN", reason=decision.reason, ts=now_ns()))
            elif decision.decision == "allow":
                state.approvedActions.append(action)
            elif decision.decision == "confirm":
                call_id = new_ulid()
                card_json = decision.card.model_dump_json() if decision.card else None
                req = ConfirmationRequest(
                    callId=call_id, summary=action.instruction or action.rationale,
                    risk=decision.risk if decision.risk in ("LOW", "MEDIUM", "HIGH") else "HIGH",
                    invokerSmeId=invoker, actionCardJson=card_json)
                state.pendingConfirmations[call_id] = req
                state.outboundEvents.append(req)
        return "paused" if state.pendingConfirmations else "go"

    def resume(self, state: ForgeState, response: ConfirmationResponse, *, emit=None) -> RunResult:
        """HITL resume (GR-11/GR-12): apply 'I did it' / 'Skip'. `emit` mirrors
        run(): a per-run incremental sink for any events resume produces."""
        with self._streaming(emit):
            return self._resume(state, response)

    def _resume(self, state: ForgeState, response: ConfirmationResponse) -> RunResult:
        req = state.pendingConfirmations.pop(response.callId, None)
        if req is None:
            return RunResult("paused", state)
        # find the action behind this confirmation by summary match
        action = next((a for a in state.mergedOpinion.proposedActions
                       if (a.instruction or a.rationale) == req.summary), None)
        outcome = "done" if response.approved else "skipped"
        if response.approved and action is not None:
            state.approvedActions.append(action)
        else:
            if action is not None:
                self.deps.gate.record_skip(self._gate_session, action)
            state.outboundEvents.append(ChatMessage(
                channelId="#actions", authorId="@forge", authorKind="system",
                body=f"operator skipped {req.summary}", messageId=new_ulid(), ts=now_ns()))
        state.audit.append({"callId": response.callId, "operatorOutcome": outcome,
                            "approverChannel": response.approverChannel})
        if state.pendingConfirmations:
            return RunResult("paused", state)
        self._live_speaker(state, state.mergedOpinion.headline if state.mergedOpinion else "")
        return RunResult("complete", state)

    def replay_pending(self, state: ForgeState) -> None:
        """Reconnect/replay (GR-15): re-emit any pending ConfirmationRequest."""
        for req in state.pendingConfirmations.values():
            state.outboundEvents.append(req)

    def _live_speaker(self, state: ForgeState, text: str) -> None:
        state.liveSpeakerScript = text
        from orchestrator.proto.events import Transcript
        state.outboundEvents.append(Transcript(
            text=text, partial=False, ts=now_ns(), speaker="live"))

    # ───────────────────────── helpers ─────────────────────────
    @contextlib.contextmanager
    def _streaming(self, emit):
        """Scope a per-run incremental sink. Restores the prior sink on exit so
        a sink never leaks across runs even if the run raises."""
        prev = self._emit_sink
        if emit is not None:
            self._emit_sink = emit
        try:
            yield
        finally:
            self._emit_sink = prev

    def _emit(self, state: ForgeState, event: object) -> None:
        """Stream one event NOW: append to state.outboundEvents (so the run's
        full transcript is preserved + replayable) AND push it to the incremental
        sink (GraphDeps.emit) so subscribers see deliberation as it unfolds.

        Records id(event) in state.streamedEvents so the final drain
        (main._drain_to_bus) skips it — streamed events are never re-published
        (idempotent drain). The sink is never allowed to fail-stop the run
        (01 §7): a raising sink is swallowed and logged into state.errors."""
        state.outboundEvents.append(event)
        state.streamedEvents.add(id(event))
        sink = self._emit_sink if self._emit_sink is not None else self.deps.emit
        try:
            sink(event)
        except Exception as e:  # noqa: BLE001 — a bad sink must not fail-stop
            state.errors.append(f"emit: {e!r}")

    def _audit(self, state, action, invoker, decision) -> None:
        state.audit.append({
            "tool": action.tool, "invokerSmeId": invoker,
            "gateDecision": decision.decision, "riskAssigned": decision.risk,
            "documentedLimit": decision.documented_limit.model_dump() if decision.documented_limit else None,
        })

    def _safe(self, state: ForgeState, fn, node_name: str) -> None:
        """Error envelope (01 §7): never fail-stop (GR-14)."""
        try:
            fn(state)
        except Exception as e:  # noqa: BLE001
            state.errors.append(f"{node_name}: {e!r}")
            state.outboundEvents.append(SafetyInterrupt(
                severity="WARN", reason=f"internal error in {node_name}", ts=now_ns()))
            if state.mergedOpinion is None:
                state.mergedOpinion = MergedOpinion(headline="Inconclusive; internal error.")


def action_invoker_default(action) -> str:
    return "@power"


def _sme_channel(sme_id: str) -> str:
    """`@power`/`power`/`#power` → `#power` (per-SME chat-bus channel, spec 04 §2)."""
    return "#" + sme_id.lstrip("@#")


def _format_tool_call(call: dict) -> str:
    """One-line `tool(arg=val, …)` for a captured knowledge-tool call, e.g.
    `lookup_datasheet(part=BQ79616, query=VIO)`. Defensive: a missing/odd shape
    degrades to just the tool name."""
    name = str(call.get("name") or call.get("tool") or "tool")
    args = call.get("args")
    if isinstance(args, dict) and args:
        inner = ", ".join(f"{k}={v}" for k, v in args.items())
        return f"{name}({inner})"
    return f"{name}()"


def _format_claim(resp: SmeResponse) -> str:
    """The SME's streamed bubble: a confidence-tagged claim headline plus its
    rationale underneath (markdown). Mirrors the SmeResponse card (04 §3)."""
    head = f"**{resp.claim}** _(confidence {resp.confidence:.2f})_"
    return f"{head}\n\n{resp.rationale}" if resp.rationale else head

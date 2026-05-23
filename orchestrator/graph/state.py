"""ForgeState + the small result/dep types the engine threads (01 §1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

from orchestrator.knowledge import KnowledgeAdapter
from orchestrator.proto.events import (
    DissentPair,
    DissentReport,
    FrameRef,
    ProposedAction,
    SmeResponse,
    SnapshotAnalysis,
    SummonGuild,
)
from orchestrator.safety.gate import SafetyGate


@dataclass
class RouteDecision:
    needs_guild: bool
    smes: list[str] = field(default_factory=list)
    topic: str = ""
    deadline_ms: int = 30_000


@dataclass
class MergedOpinion:
    headline: str
    supportingSmes: list[str] = field(default_factory=list)
    openQuestions: list[str] = field(default_factory=list)
    proposedActions: list[ProposedAction] = field(default_factory=list)


@dataclass
class DissentResult:
    pairwise: list[DissentPair] = field(default_factory=list)
    convergence: Literal["converged", "needs_more_rounds"] = "converged"
    crossExamPrompt: str | None = None


@dataclass
class GraphDeps:
    """Injected collaborators. Real components for safety/knowledge; callables
    for the model-ish steps + SME fan-out so the graph is deterministic in tests."""
    gate: SafetyGate
    knowledge: KnowledgeAdapter
    #: SupervisorRouter model call: (transcript, recent) -> decision | None (bad output).
    classify: Callable[[str, list[str]], RouteDecision | None]
    #: ParallelSummonSMEs per-SME call: (smeId, summon) -> SmeResponse; may raise.
    summon_one: Callable[[str, SummonGuild], SmeResponse]
    #: MergeOpinion synthesis over the surviving responses (may raise → error envelope).
    merge_fn: Callable[[list[SmeResponse]], tuple[str, list[str]]]  # (headline, supportingSmes)
    #: DissentDetector over responses + cross-exam round.
    dissent_fn: Callable[[list[SmeResponse], int], DissentResult]
    #: invoker resolution for a SmeResponse (defaults to its smeId).
    aggregator_queue_max: int = 64
    #: Incremental publish sink: called with each outbound event AS IT IS
    #: produced during a run (summon notice, per-SME claim, per-tool-call
    #: activity). Default no-op keeps the graph fully deterministic + offline:
    #: tests inject a list-sink, main.py injects a per-session bus.publish.
    #: Streamed events are still appended to state.outboundEvents; the final
    #: drain is idempotent (see GraphEngine._emit / ForgeState.streamedEvents).
    emit: Callable[[object], None] = lambda _event: None


@dataclass
class ForgeState:
    sessionId: str
    userId: str | None = None
    # perception
    latestFrame: FrameRef | None = None
    latestSnapshot: SnapshotAnalysis | None = None
    latestTranscriptFinal: str | None = None
    # routing
    pendingSummon: SummonGuild | None = None
    activeSmes: list[str] = field(default_factory=list)
    smeResponses: dict[str, SmeResponse] = field(default_factory=dict)
    # decision
    mergedOpinion: MergedOpinion | None = None
    dissentReport: DissentReport | None = None
    crossExamRound: int = 0
    invokerOf: dict[int, str] = field(default_factory=dict)  # id(action) -> smeId
    pendingConfirmations: dict[str, object] = field(default_factory=dict)  # callId -> ConfirmationRequest
    approvedActions: list[ProposedAction] = field(default_factory=list)
    audit: list[dict] = field(default_factory=list)
    # output
    liveSpeakerScript: str | None = None
    outboundEvents: list = field(default_factory=list)
    #: id()s of events already pushed to the incremental sink (GraphDeps.emit),
    #: so the final drain can skip them and never double-publish (idempotent
    #: drain). Populated by GraphEngine._emit; read by main._drain_to_bus.
    streamedEvents: set[int] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    status: Literal["complete", "paused", "direct_reply"]
    state: ForgeState

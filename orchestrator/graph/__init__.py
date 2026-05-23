"""LangGraph state machine (P5) — `specs/01_langgraph_state_machine.md`.

The node functions are langgraph-shaped (state in, delta out) and wire the
real P1 KnowledgeAdapter + P2 SafetyGate + P6 reader, with the model-ish steps
(SupervisorRouter / MergeOpinion / DissentDetector) and the SME fan-out
injected as callables so the graph is deterministic under test. `GraphEngine`
is a faithful runner of the §2 topology (conditional edges, HITL interrupt,
cross-exam loop cap, never-fail-stop error envelope); a `StateGraph` assembly
can wrap the same nodes for production (langgraph checkpointer/interrupts).
"""

from orchestrator.graph.state import (  # noqa: F401
    DissentResult,
    ForgeState,
    GraphDeps,
    MergedOpinion,
    RouteDecision,
    RunResult,
)
from orchestrator.graph.engine import GraphEngine  # noqa: F401

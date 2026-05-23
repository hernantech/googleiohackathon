"""SafetyGate (P2) — gates operator instructions; actuates nothing.

See `specs/03_safety_gate_matrix.md`. The matrix is data (`matrix.py`); the
evaluator (`gate.py`) layers the documented-limit check (via the P1
KnowledgeAdapter), `max(risk)` elevation, the provenance rule, the @sentinel
HALT bypass, and per-session skip denylist / pending limits.
"""

from orchestrator.safety.gate import (  # noqa: F401
    GateDecision,
    GateSession,
    SafetyGate,
)
from orchestrator.safety.matrix import (  # noqa: F401
    DANGEROUS_SERIAL_PATTERNS,
    DEFAULT_ROSTER,
    INVOKABLE_BY,
    invoker_allowed,
)

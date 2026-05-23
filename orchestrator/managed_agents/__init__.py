"""Managed-Agents SME integration — see specs/02_sme_persona_format.md.

P6: the SME structured-output reader (strategy + fallback chain, 02 §4).
"""

from orchestrator.managed_agents.structured_output import (  # noqa: F401
    read_sme_response,
    RETRY_PROMPT_TEMPLATE,
)

__all__ = ["read_sme_response", "RETRY_PROMPT_TEMPLATE"]

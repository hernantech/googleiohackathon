"""Schematic tools for the Gemini Live path (spec 09 §5.1 + Live function-calling).

This is the Live-session counterpart to the SME tool-loop's schematic tools: it
declares the SAME `parse_schematic` / `lookup_schematic` function tools to the
Live model and dispatches a Live function-call through the SAME shared seam the
SME loop uses (`genai_seams.dispatch_schematic_tool`) — so the parse pipeline is
reached identically from voice and from a specialist SME, with NO duplicated
logic.

Two pieces:
  * `live_schematic_tool()` — a `google.genai.types.Tool` carrying the two
    function declarations, folded into the LiveConnectConfig (session.py) so the
    Live model can EMIT a `function_call` for them. Built lazily; if google-genai
    is absent it simply isn't added (graceful, 01 §7).
  * `make_live_schematic_handler(knowledge)` — an async `OnToolCall`-shaped
    handler that recognizes the schematic tools, runs the (sync) shared dispatch
    off the event loop, and returns the function-response payload to inject back
    into the Live conversation. Returns None for non-schematic calls so a caller
    can chain it ahead of its own routing.
"""

from __future__ import annotations

import asyncio
import logging

from orchestrator.genai_seams import SCHEMATIC_TOOL_NAMES, dispatch_schematic_tool
from orchestrator.knowledge import KnowledgeAdapter

log = logging.getLogger("forge.live.schematic_tools")

#: The function declarations the Live model is offered. Reuses the SME-tool
#: parameter shapes verbatim (kept here in google-genai's declaration form so the
#: Live config builds without importing the SME _TOOL_SCHEMAS' OBJECT dialect).
_LIVE_FUNCTION_DECLS = [
    {
        "name": "parse_schematic",
        "description": (
            "Parse a schematic photo/PDF the operator has supplied into "
            "structured JSON (components, reference designators, values, nets, "
            "pin↔net connections). Use when the operator asks to 'pull up the "
            "schematic' or about board topology from an image. The result is "
            "advisory model-derived context (confidence/warnings), never a "
            "documented limit."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source_uri": {
                    "type": "string",
                    "description": "snapshot:// frame ref, mem: frame uri, or file path",
                },
                "hint": {
                    "type": "string",
                    "description": "optional: suspected board / part / section",
                },
            },
            "required": ["source_uri"],
        },
    },
    {
        "name": "lookup_schematic",
        "description": (
            "Query the schematic parsed earlier this session by reference "
            "designator, net, or part — e.g. 'what's connected to net 3V3?', "
            "'which pin of U4 is VOUT?'. Advisory only; limits still come from "
            "documented values."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "a ref (U4), net (3V3), or part"},
            },
            "required": ["query"],
        },
    },
]


def live_schematic_tool():
    """Return a `google.genai.types.Tool` declaring the schematic functions, or
    None if google-genai is unavailable (so the Live config degrades gracefully).
    Never raises (01 §7)."""
    try:
        from google.genai import types  # optional [live] dep

        return types.Tool(function_declarations=_LIVE_FUNCTION_DECLS)
    except Exception as e:  # noqa: BLE001 — Live just won't offer the tool
        log.warning("live_schematic_tool unavailable (%s); Live runs without it", e)
        return None


def is_schematic_tool(name: str) -> bool:
    return name in SCHEMATIC_TOOL_NAMES


def make_live_schematic_handler(knowledge: KnowledgeAdapter):
    """Build an async handler (OnToolCall-shaped) that dispatches a Live
    schematic function-call through the shared seam and returns the
    function-response payload to inject back into the session.

    Signature: `async (name, args, call_id) -> dict | None`. Returns None when
    `name` is not a schematic tool, so this can be composed ahead of another
    on_tool_call router (e.g. main.py's guild handler). The (synchronous) shared
    dispatch runs in a worker thread so the Live receive loop never blocks. Never
    fail-stops the live loop (01 §7)."""

    async def handler(name: str, args: dict, call_id: str) -> "dict | None":
        if not is_schematic_tool(name):
            return None  # let the caller route non-schematic calls
        try:
            payload = await asyncio.to_thread(
                dispatch_schematic_tool, name, args or {}, knowledge)
        except Exception as e:  # noqa: BLE001 — never wedge the live loop
            log.warning("live schematic dispatch(%s) failed (%s)", name, e)
            return {"error": f"schematic tool {name!r} failed: {e}"}
        # dispatch_schematic_tool returns None only for a non-schematic name,
        # which we've already excluded — but be defensive.
        return payload if payload is not None else {"error": f"unhandled {name!r}"}

    return handler


__all__ = [
    "live_schematic_tool",
    "make_live_schematic_handler",
    "is_schematic_tool",
]

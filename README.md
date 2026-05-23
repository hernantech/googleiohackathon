# Forge

A voice + vision **multi-agent advisor for a human at an electronics bench**. You hold the probes, turn the PSU knob, and wield the iron; Forge watches through your phone/Quest camera, summons a guild of specialist SME agents that deliberate visibly and in parallel, surfaces their disagreements, and hands you precise, safety-gated, step-by-step instructions — every value cited against the board's own documentation.

Forge actuates nothing. There is no bench daemon; the human is the operator and the final authority.

## Where to start

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — the shared mental model (topology, orchestrator internals, the LangGraph state machine, the demo sequence, UI, safety tree, design patterns).
- **[specs/](specs/)** — the contracts, numbered and cross-referenced:
  - `00_wire_protocol.md` — frozen event vocabulary across processes
  - `01_langgraph_state_machine.md` — the orchestrator graph, node by node
  - `02_sme_persona_format.md` — the SME sandbox layout (AGENTS.md / SKILL.md)
  - `03_safety_gate_matrix.md` — operator-instruction gating, two safety layers
  - `04_chat_bus_protocol.md` — the Discord-style client protocol
  - `05_board_knowledge_api.md` — board profile + read-only knowledge lookups
  - `06_demo_script.md` — the 3-minute BQ79616 bring-up demo
  - `07_environment_setup.md` — accounts, env vars, repo layout, pre-warm
  - `08_test_plan.md` — build-order gates + cross-process integration tests

Each spec ends with a component-level **Test cases** section; `08` owns the system-level tests that prove the contracts line up end-to-end.

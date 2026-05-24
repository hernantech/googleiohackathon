"""Startup wiring for the per-SME managed-agent lifecycle (main.py).

The per-SME Antigravity managed agent is the default summon path, so its envs
must be PROVISIONED up front (off the critical path) and KEPT WARM. main.py wires
two background tasks at startup:

  * _sme_prewarm()      → calls genai_seams.prewarm_smes() once in a worker thread;
  * _sme_keepwarm_loop() → pings every provisioned per-SME env on the keep-warm
                           cadence (genai_seams.keepwarm_sme_envs()).

Both no-op offline (no GEMINI_API_KEY / google-genai) and are cancelled cleanly
on shutdown. These tests drive the helpers directly (no network) and assert the
startup task wiring + the offline no-op, without standing up a real Gemini.
"""

from __future__ import annotations

import asyncio
import contextlib
import types as _pytypes

import pytest

from orchestrator import main as m


def _settings(**over):
    """A minimal stand-in for the frozen `settings` singleton (which is a frozen
    dataclass and so cannot be monkeypatched field-by-field). Carries just the
    attributes the lifecycle helpers read (`gemini_api_key`)."""
    base = {"gemini_api_key": ""}
    base.update(over)
    return _pytypes.SimpleNamespace(**base)


# ───────────────────────── prewarm at startup ──────────────────────────────

@pytest.mark.asyncio
async def test_sme_prewarm_runs_prewarm_smes_when_keyed(monkeypatch):
    """_sme_prewarm calls genai_seams.prewarm_smes() (in a worker thread) when
    a key is present."""
    monkeypatch.setattr(m, "settings", _settings(gemini_api_key="test-key"))

    called = {"n": 0}

    def fake_prewarm():
        called["n"] += 1
        return {"@power": "env-1", "@signal": "env-2"}

    import orchestrator.genai_seams as gs
    monkeypatch.setattr(gs, "prewarm_smes", fake_prewarm)

    await m._sme_prewarm()
    assert called["n"] == 1


@pytest.mark.asyncio
async def test_sme_prewarm_no_op_offline(monkeypatch):
    """With no key, _sme_prewarm never touches genai_seams (offline boot)."""
    monkeypatch.setattr(m, "settings", _settings(gemini_api_key=""))

    import orchestrator.genai_seams as gs

    def _explode():
        raise AssertionError("prewarm_smes touched offline")
    monkeypatch.setattr(gs, "prewarm_smes", _explode)

    await m._sme_prewarm()  # returns immediately, no exception


# ───────────────────────── keep-warm loop ──────────────────────────────────

@pytest.mark.asyncio
async def test_sme_keepwarm_loop_pings_envs_then_cancels(monkeypatch):
    """The keep-warm loop pings the per-SME envs (keepwarm_sme_envs) on each tick
    and cancels cleanly."""
    monkeypatch.setattr(m, "settings", _settings(gemini_api_key="test-key"))

    import orchestrator.genai_seams as gs
    pings = {"n": 0}

    def fake_keepwarm():
        pings["n"] += 1
        return 2
    monkeypatch.setattr(gs, "keepwarm_sme_envs", fake_keepwarm)
    # tiny cadence so the loop ticks promptly under test.
    monkeypatch.setattr(gs, "SANDBOX_KEEPWARM_INTERVAL_S", 0.01)

    task = asyncio.create_task(m._sme_keepwarm_loop())
    # let it tick at least once.
    for _ in range(50):
        await asyncio.sleep(0.01)
        if pings["n"] >= 1:
            break
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert pings["n"] >= 1


@pytest.mark.asyncio
async def test_sme_keepwarm_loop_no_op_offline(monkeypatch):
    """With no key, the keep-warm loop returns immediately without importing the
    keep-warm helper (offline boot)."""
    monkeypatch.setattr(m, "settings", _settings(gemini_api_key=""))

    import orchestrator.genai_seams as gs

    def _explode():
        raise AssertionError("keepwarm_sme_envs touched offline")
    monkeypatch.setattr(gs, "keepwarm_sme_envs", _explode)

    # should return promptly (no infinite loop, no ping).
    await asyncio.wait_for(m._sme_keepwarm_loop(), timeout=1.0)


# ───────────────────────── startup registers both tasks ────────────────────

@pytest.mark.asyncio
async def test_startup_registers_sme_prewarm_and_keepwarm_tasks(monkeypatch):
    """_startup creates the sme_prewarm + sme_keepwarm background tasks (alongside
    the existing heartbeat + sandbox keep-warm); _shutdown cancels them all."""
    # make every background coroutine a quick no-op so _startup just registers them.
    async def _noop():
        return None

    monkeypatch.setattr(m, "_heartbeat_loop", _noop)
    monkeypatch.setattr(m, "_sandbox_keepwarm_loop", _noop)
    monkeypatch.setattr(m, "_sme_prewarm", _noop)
    monkeypatch.setattr(m, "_sme_keepwarm_loop", _noop)

    await m._startup()
    for name in ("heartbeat", "sandbox_keepwarm", "sme_prewarm", "sme_keepwarm"):
        assert getattr(m.app.state, name, None) is not None, f"{name} task not created"

    await m._shutdown()  # cancels cleanly, no exception

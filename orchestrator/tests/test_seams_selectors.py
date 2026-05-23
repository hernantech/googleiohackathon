"""seams selectors: real-vs-stub model_call wiring (07 §2.4 zero-config boot).

Deterministic, no network: we only assert WHICH callable the selector returns
based on GEMINI_API_KEY presence. The real branch imports
orchestrator.genai_seams.real_snapshot_model_call (no model call is made here).
"""

from __future__ import annotations

from orchestrator import seams


def test_snapshot_model_call_is_stub_without_key(monkeypatch):
    """Zero-config boot: no GEMINI_API_KEY → the stub vision model_call."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert seams.build_snapshot_model_call() is seams.stub_snapshot_model_call


def test_snapshot_model_call_is_real_with_key(monkeypatch):
    """Keyed: GEMINI_API_KEY set + google-genai importable → real vision model_call
    (so /v2/snapshot uses real Gemini vision, matching chat/live), not the stub."""
    from orchestrator import genai_seams

    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
    selected = seams.build_snapshot_model_call()
    assert selected is genai_seams.real_snapshot_model_call
    assert selected is not seams.stub_snapshot_model_call


def test_snapshot_model_call_falls_back_to_stub_on_import_error(monkeypatch):
    """Keyed but google-genai/import unavailable → stub fallback (never fail-stop)."""
    import builtins

    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name == "orchestrator.genai_seams" or name.endswith("genai_seams"):
            raise ImportError("simulated missing google-genai")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _boom)
    assert seams.build_snapshot_model_call() is seams.stub_snapshot_model_call

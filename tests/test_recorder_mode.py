"""Tests for RECORDER_MODE dispatch in oracle_ai_agent.run_agent.

Strategy
--------
- Monkeypatch config.RECORDER_MODE before calling run_agent.
- Replace _run_snapshot_recorder and _run_computer_use_recorder with spies.
- Mock everything else run_agent needs (Playwright MCP client, login, dispatch)
  so no real network or Oracle connections are made.
- Assert exactly one of the two recorders was called, and the other was not.

These tests confirm the architectural invariant:
  "Normal replay is AI-free and the coordinate path is healing-fallback only."
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest


# ── Shared fixture helpers ────────────────────────────────────────────────────

class _FakePwClient:
    """Minimal async Playwright MCP client stub."""
    async def list_tools(self):
        return []

    async def call_tool(self, name, args=None):
        return ""


class _FakeMCPClient:
    """Minimal async context-manager stub for fastmcp.Client."""
    def __init__(self, transport):
        pass

    async def __aenter__(self):
        return _FakePwClient()

    async def __aexit__(self, *args):
        pass


def _patch_run_agent_infrastructure(monkeypatch, tmp_path):
    """Apply all mocks that make run_agent work without real I/O."""
    import config
    import oracle_ai_agent

    monkeypatch.setattr(config, "RECORDINGS_DIR", tmp_path)
    monkeypatch.setattr(oracle_ai_agent, "close_existing_oracle_windows", lambda: [])
    monkeypatch.setattr(oracle_ai_agent, "_make_pw_transport", lambda: None)
    monkeypatch.setattr(oracle_ai_agent, "dispatch", AsyncMock(return_value="ok"))
    monkeypatch.setattr(oracle_ai_agent, "_deterministic_login", AsyncMock())
    monkeypatch.setattr("oracle_ai_agent.Client", _FakeMCPClient)


# ── Config default ────────────────────────────────────────────────────────────

class TestRecorderModeConfig:
    def test_default_is_snapshot(self):
        """RECORDER_MODE must default to 'snapshot' so Approach B is the active path."""
        import importlib
        import os
        # Reload config without QCS_RECORDER_MODE in the environment
        saved = os.environ.pop("QCS_RECORDER_MODE", None)
        try:
            import config
            importlib.reload(config)
            assert config.RECORDER_MODE == "snapshot"
        finally:
            if saved is not None:
                os.environ["QCS_RECORDER_MODE"] = saved
            importlib.reload(config)

    def test_overridable_via_env(self, monkeypatch):
        import importlib, os, config  # noqa: E401
        monkeypatch.setenv("QCS_RECORDER_MODE", "coordinate")
        importlib.reload(config)
        assert config.RECORDER_MODE == "coordinate"
        importlib.reload(config)  # restore


# ── Mode dispatch in run_agent ────────────────────────────────────────────────

class TestRecorderModeDispatch:
    @pytest.mark.asyncio
    async def test_snapshot_mode_calls_snapshot_recorder(self, tmp_path, monkeypatch):
        """RECORDER_MODE='snapshot' → _run_snapshot_recorder called once."""
        import config
        import oracle_ai_agent

        monkeypatch.setattr(config, "RECORDER_MODE", "snapshot")
        _patch_run_agent_infrastructure(monkeypatch, tmp_path)

        snapshot_calls: list[str] = []
        coord_calls: list[str] = []

        async def _fake_snapshot(session, pw_client, instructions):
            snapshot_calls.append(instructions)

        async def _fake_coord(session, pw_client, instructions):
            coord_calls.append(instructions)

        monkeypatch.setattr(oracle_ai_agent, "_run_snapshot_recorder", _fake_snapshot)
        monkeypatch.setattr(oracle_ai_agent, "_run_computer_use_recorder", _fake_coord)

        await oracle_ai_agent.run_agent("1. Open PO form", "rt_snap")

        assert snapshot_calls == ["1. Open PO form"]
        assert coord_calls == [], "coordinate recorder must NOT be called in snapshot mode"

    @pytest.mark.asyncio
    async def test_snapshot_mode_coordinate_executor_never_invoked(self, tmp_path, monkeypatch):
        """In snapshot mode _execute_recording_action (coordinate executor) is never called."""
        import config
        import oracle_ai_agent

        monkeypatch.setattr(config, "RECORDER_MODE", "snapshot")
        _patch_run_agent_infrastructure(monkeypatch, tmp_path)

        coord_executor_calls: list = []

        def _spy_coord_executor(*args, **kwargs):
            coord_executor_calls.append(args)
            return None

        monkeypatch.setattr(oracle_ai_agent, "_execute_recording_action", _spy_coord_executor)
        monkeypatch.setattr(oracle_ai_agent, "_run_snapshot_recorder", AsyncMock())
        # Do NOT replace _run_computer_use_recorder so we confirm it's simply not called.

        await oracle_ai_agent.run_agent("1. Open PO form", "rt_snap_exec")

        assert coord_executor_calls == [], (
            "_execute_recording_action (coordinate executor) must not be called "
            "when RECORDER_MODE='snapshot'"
        )

    @pytest.mark.asyncio
    async def test_coordinate_mode_calls_coordinate_recorder(self, tmp_path, monkeypatch):
        """RECORDER_MODE='coordinate' → _run_computer_use_recorder called once."""
        import config
        import oracle_ai_agent

        monkeypatch.setattr(config, "RECORDER_MODE", "coordinate")
        _patch_run_agent_infrastructure(monkeypatch, tmp_path)

        snapshot_calls: list[str] = []
        coord_calls: list[str] = []

        async def _fake_snapshot(session, pw_client, instructions):
            snapshot_calls.append(instructions)

        async def _fake_coord(session, pw_client, instructions):
            coord_calls.append(instructions)

        monkeypatch.setattr(oracle_ai_agent, "_run_snapshot_recorder", _fake_snapshot)
        monkeypatch.setattr(oracle_ai_agent, "_run_computer_use_recorder", _fake_coord)

        await oracle_ai_agent.run_agent("1. Open PO form", "rt_coord")

        assert coord_calls == ["1. Open PO form"]
        assert snapshot_calls == [], "snapshot recorder must NOT be called in coordinate mode"

    @pytest.mark.asyncio
    async def test_unknown_mode_falls_back_to_snapshot(self, tmp_path, monkeypatch):
        """Any unrecognised RECORDER_MODE value falls back to snapshot (Approach B)."""
        import config
        import oracle_ai_agent

        monkeypatch.setattr(config, "RECORDER_MODE", "typo_mode")
        _patch_run_agent_infrastructure(monkeypatch, tmp_path)

        snapshot_calls: list[str] = []
        coord_calls: list[str] = []

        async def _fake_snapshot(session, pw_client, instructions):
            snapshot_calls.append(instructions)

        async def _fake_coord(session, pw_client, instructions):
            coord_calls.append(instructions)

        monkeypatch.setattr(oracle_ai_agent, "_run_snapshot_recorder", _fake_snapshot)
        monkeypatch.setattr(oracle_ai_agent, "_run_computer_use_recorder", _fake_coord)

        await oracle_ai_agent.run_agent("1. Open PO form", "rt_unknown")

        assert snapshot_calls == ["1. Open PO form"], "unknown mode must fall back to snapshot"
        assert coord_calls == []


# ── Structural guard: healing files are the only coordinate consumers ─────────

class TestHealingFilesAreOnlyCoordinateConsumers:
    """Verify the healing module docstrings carry the canonical HEALING FALLBACK ONLY marker."""

    def test_computer_use_module_marked_healing_only(self):
        from qcs_replay.healing import computer_use
        assert "HEALING FALLBACK ONLY" in computer_use.__doc__

    def test_coord_to_locator_module_marked_healing_only(self):
        from qcs_replay.healing import coord_to_locator
        assert "HEALING FALLBACK ONLY" in coord_to_locator.__doc__

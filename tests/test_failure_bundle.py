"""Tests for qcs_replay.failure_bundle — deterministic failure bundle capture.

All tests use spy/stub backends; no Playwright, Java agent, or AI/LLM is invoked.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from qcs_replay.dsl import (
    ReplayBackend,
    ReplayLogger,
    ReplayRefNotFoundError,
    RepositoryResolver,
    ResolvedTarget,
)
from qcs_replay.failure_bundle import BundleWriter, FailureBundle, _apply_redaction
from qcs_replay.script import OracleReplay


# -- Test doubles -------------------------------------------------------------


class _AlwaysFailBackend(ReplayBackend):
    """Backend stub that raises RuntimeError on every action."""

    def click(self, ref: str, descriptor: dict) -> None:
        raise RuntimeError(f"click failed on {ref!r}")

    def double_click(self, ref: str, descriptor: dict) -> None:
        raise RuntimeError("double_click failed")

    def set_text(self, ref: str, descriptor: dict, value: str) -> None:
        raise RuntimeError(f"set_text failed on {ref!r}")

    def select_value(self, ref: str, descriptor: dict, value: str) -> None:
        raise RuntimeError("select_value failed")

    def press_key(self, key: str) -> None:
        raise RuntimeError(f"press_key failed: {key!r}")

    def wait_for(self, ref: str, descriptor: dict, timeout_ms: int = 10_000) -> None:
        raise RuntimeError("wait_for failed")

    def assert_visible(self, ref: str, descriptor: dict) -> None:
        raise RuntimeError("assert_visible failed")

    def get_text(self, ref: str, descriptor: dict) -> str:
        return ""

    def get_value(self, ref: str, descriptor: dict) -> str:
        return ""


class _SucceedBackend(ReplayBackend):
    """Backend stub that silently succeeds on every action."""

    def click(self, ref: str, descriptor: dict) -> None: ...
    def double_click(self, ref: str, descriptor: dict) -> None: ...
    def set_text(self, ref: str, descriptor: dict, value: str) -> None: ...
    def select_value(self, ref: str, descriptor: dict, value: str) -> None: ...
    def press_key(self, key: str) -> None: ...
    def wait_for(self, ref: str, descriptor: dict, timeout_ms: int = 10_000) -> None: ...
    def assert_visible(self, ref: str, descriptor: dict) -> None: ...
    def get_text(self, ref: str, descriptor: dict) -> str: return ""
    def get_value(self, ref: str, descriptor: dict) -> str: return ""


def _make_replay(artifact_dir: Path, backend: ReplayBackend, **kwargs: Any) -> OracleReplay:
    """Create an OracleReplay with a single java_forms backend and a BundleWriter."""
    resolver = RepositoryResolver()
    resolver.register(
        "java_demo_form",
        "order_type",
        ResolvedTarget(ref="order_type", surface="java_forms", form_id="java_demo_form", descriptor={}),
    )
    return OracleReplay(
        page=None,
        resolver=resolver,
        java_backend=backend,
        artifact_dir=artifact_dir,
        run_id="test_run",
        test_name="test_demo",
        **kwargs,
    )


# -- Tests --------------------------------------------------------------------


def test_bundle_created_on_replay_error(tmp_path: Path) -> None:
    """A failure_bundle.json must be written when a backend action raises."""
    replay = _make_replay(tmp_path, _AlwaysFailBackend())

    with pytest.raises(RuntimeError):
        replay.form("java_demo_form").click("order_type")

    bundle_file = tmp_path / "failure_bundle.json"
    assert bundle_file.exists(), "failure_bundle.json was not created after replay failure"


def test_bundle_created_on_ref_not_found(tmp_path: Path) -> None:
    """A failure_bundle.json must be written even when the element_ref is missing from repo."""
    replay = _make_replay(tmp_path, _SucceedBackend())

    with pytest.raises(ReplayRefNotFoundError):
        replay.form("java_demo_form").click("does_not_exist")

    bundle_file = tmp_path / "failure_bundle.json"
    assert bundle_file.exists(), "failure_bundle.json was not created on ReplayRefNotFoundError"


def test_bundle_includes_replay_log(tmp_path: Path) -> None:
    """Bundle must include a replay_log list (may be empty or populated)."""
    replay = _make_replay(tmp_path, _AlwaysFailBackend())

    with pytest.raises(RuntimeError):
        replay.form("java_demo_form").click("order_type")

    data = json.loads((tmp_path / "failure_bundle.json").read_text(encoding="utf-8"))
    assert "replay_log" in data, "Bundle missing replay_log key"
    assert isinstance(data["replay_log"], list), "replay_log must be a list"
    # The failed action should appear in the log
    assert len(data["replay_log"]) >= 1
    assert data["replay_log"][-1]["status"] == "failed"


def test_bundle_includes_form_ref_and_element_ref(tmp_path: Path) -> None:
    """Bundle's failed_step must contain form_ref and element_ref."""
    replay = _make_replay(tmp_path, _AlwaysFailBackend())

    with pytest.raises(RuntimeError):
        replay.form("java_demo_form").click("order_type")

    data = json.loads((tmp_path / "failure_bundle.json").read_text(encoding="utf-8"))
    step = data["failed_step"]
    assert step["form_ref"] == "java_demo_form"
    assert step["element_ref"] == "order_type"
    assert step["action"] == "click"
    assert step["surface"] == "java_forms"


def test_bundle_includes_exception_info(tmp_path: Path) -> None:
    """Bundle must record exception type and message."""
    replay = _make_replay(tmp_path, _AlwaysFailBackend())

    with pytest.raises(RuntimeError):
        replay.form("java_demo_form").set_text("order_type", "STANDARD")

    data = json.loads((tmp_path / "failure_bundle.json").read_text(encoding="utf-8"))
    exc = data["exception"]
    assert exc["type"] == "RuntimeError"
    assert "set_text" in exc["message"] or "order_type" in exc["message"]


def test_bundle_includes_repo_entry_when_resolved(tmp_path: Path) -> None:
    """When the ref was resolved before backend failure, repo_entry must be populated."""
    replay = _make_replay(tmp_path, _AlwaysFailBackend())

    with pytest.raises(RuntimeError):
        replay.form("java_demo_form").click("order_type")

    data = json.loads((tmp_path / "failure_bundle.json").read_text(encoding="utf-8"))
    repo_entry = data.get("repo_entry")
    assert repo_entry is not None, "repo_entry should be present when ref resolved"
    assert repo_entry["form_ref"] == "java_demo_form"
    assert repo_entry["element_ref"] == "order_type"
    assert repo_entry["surface"] == "java_forms"


def test_no_bundle_created_on_success(tmp_path: Path) -> None:
    """No failure_bundle.json must be written when all actions succeed."""
    replay = _make_replay(tmp_path, _SucceedBackend())
    replay.form("java_demo_form").click("order_type")

    bundle_file = tmp_path / "failure_bundle.json"
    assert not bundle_file.exists(), "failure_bundle.json must NOT be created on success"


def test_redaction_hook_is_invoked(tmp_path: Path) -> None:
    """redact_fn must be called for leaf values in the bundle dict."""
    calls: list[tuple[str, Any]] = []

    def spy_redact(key: str, value: Any) -> Any:
        calls.append((key, value))
        return value  # pass-through

    replay = _make_replay(tmp_path, _AlwaysFailBackend(), redact_fn=spy_redact)

    with pytest.raises(RuntimeError):
        replay.form("java_demo_form").click("order_type")

    assert len(calls) > 0, "redact_fn was never called"
    # Ensure the bundle was still written despite redaction pass-through
    assert (tmp_path / "failure_bundle.json").exists()


def test_repository_not_modified_after_failure(tmp_path: Path) -> None:
    """Replay failure must not alter the repository entry."""
    import config as _cfg  # noqa: PLC0415

    resolver = RepositoryResolver()
    resolver.register(
        "java_demo_form",
        "order_type",
        ResolvedTarget(ref="order_type", surface="java_forms", form_id="java_demo_form", descriptor={}),
    )
    replay = OracleReplay(
        page=None,
        resolver=resolver,
        java_backend=_AlwaysFailBackend(),
        artifact_dir=tmp_path,
        run_id="test_repo_guard",
        test_name="test_repository_not_modified_after_failure",
    )

    # Capture the pre-failure state of the repo for the registered element.
    from qcs_repo import store as repo_store  # noqa: PLC0415
    before = repo_store.load_entry("java_demo_form", "order_type", _cfg.REPO_DIR)

    with pytest.raises(RuntimeError):
        replay.form("java_demo_form").click("order_type")

    after = repo_store.load_entry("java_demo_form", "order_type", _cfg.REPO_DIR)
    # Both should be None (not in the disk repo) or identical — no mutation occurred.
    assert before == after, "Repository entry was modified by replay failure"


def test_bundle_metadata_fields(tmp_path: Path) -> None:
    """Bundle must include run_id, test_name, timestamp, and environment."""
    replay = _make_replay(tmp_path, _AlwaysFailBackend())

    with pytest.raises(RuntimeError):
        replay.form("java_demo_form").click("order_type")

    data = json.loads((tmp_path / "failure_bundle.json").read_text(encoding="utf-8"))
    assert data["run_id"] == "test_run"
    assert data["test_name"] == "test_demo"
    assert data["timestamp"]  # non-empty ISO string
    assert "python_version" in data["environment"]
    assert "platform" in data["environment"]


def test_apply_redaction_replaces_leaf_values() -> None:
    """_apply_redaction must call redact_fn for every leaf and preserve structure."""
    seen: list[str] = []

    def redact(key: str, value: Any) -> Any:
        seen.append(key)
        return f"REDACTED:{value}" if key == "message" else value

    data = {
        "exception": {"type": "RuntimeError", "message": "secret detail"},
        "replay_log": [{"action": "click", "surface": "java_forms"}],
        "run_id": "r1",
    }

    result = _apply_redaction(data, redact)
    assert result["exception"]["message"] == "REDACTED:secret detail"
    assert result["exception"]["type"] == "RuntimeError"
    assert result["run_id"] == "r1"
    assert "message" in seen

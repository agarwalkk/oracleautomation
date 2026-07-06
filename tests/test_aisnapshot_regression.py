"""Regression tests for build_action_context against approved baselines.

Every subfolder under tests/testdata/aisnapshot/approved/ contains a
pair: java_scan_dump.json (raw DOM) and ai_snapshot.txt (expected AI
snapshot text).  This test loads each pair, runs build_action_context on
the JSON, and asserts the output matches the approved baseline exactly.

If a snapshot.py change alters the output, the diff is shown so you can
either fix the code or update the baseline with the new expected text.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from qcs_java_agent.snapshot import build_action_context

# ── Discovery ────────────────────────────────────────────────────────────

_APPROVED_DIR = Path(__file__).parent / "testdata" / "aisnapshot" / "approved"


def _discover_cases() -> list[Path]:
    """Return all approved subfolders that contain both required files."""
    if not _APPROVED_DIR.is_dir():
        return []
    return sorted(
        d for d in _APPROVED_DIR.iterdir()
        if d.is_dir()
        and (d / "java_scan_dump.json").exists()
        and (d / "ai_snapshot.txt").exists()
    )


_CASES = _discover_cases()


# ── Parametrized test ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "case_dir",
    _CASES,
    ids=[c.name for c in _CASES],
)
def test_ai_snapshot_matches_baseline(case_dir: Path):
    scan_path = case_dir / "java_scan_dump.json"
    baseline_path = case_dir / "ai_snapshot.txt"

    with open(scan_path, encoding="utf-8") as f:
        scan = json.load(f)

    all_tabs = scan.get("multi_tab", True)
    actual_text, _ = build_action_context(scan, all_tabs=all_tabs)
    expected_text = baseline_path.read_text(encoding="utf-8")

    if actual_text != expected_text:
        # Build a readable line-by-line diff for the failure message
        import difflib

        diff = difflib.unified_diff(
            expected_text.splitlines(keepends=True),
            actual_text.splitlines(keepends=True),
            fromfile=f"approved/{case_dir.name}/ai_snapshot.txt",
            tofile="build_action_context() output",
            lineterm="",
        )
        diff_text = "".join(diff)
        pytest.fail(
            f"Snapshot mismatch for {case_dir.name}.\n"
            f"To update the baseline, copy the actual output to:\n"
            f"  {baseline_path}\n\n"
            f"{diff_text}"
        )


def test_recognition_of_single_vs_multi_tab(monkeypatch):
    import io
    import builtins
    import sys
    
    called_all_tabs = []
    
    def mock_build_action_context(scan, all_tabs=False):
        called_all_tabs.append(all_tabs)
        return "mocked snapshot text", {"some_key": {}}
        
    module = sys.modules[test_ai_snapshot_matches_baseline.__module__]
    monkeypatch.setattr(module, "build_action_context", mock_build_action_context)
    
    # Mocking Path objects so we don't hit the filesystem
    class MockPath:
        def __init__(self, name):
            self.name = name
            
        def __truediv__(self, other):
            return self
            
        def read_text(self, encoding="utf-8"):
            return "mocked snapshot text"
            
    # Case 1: multi_tab is False in the scan dump
    def mock_open_single(*args, **kwargs):
        return io.StringIO('{"multi_tab": false}')
        
    monkeypatch.setattr(builtins, "open", mock_open_single)
    test_ai_snapshot_matches_baseline(MockPath("mock_single_tab"))
    
    # Case 2: multi_tab is True in the scan dump
    def mock_open_multi(*args, **kwargs):
        return io.StringIO('{"multi_tab": true}')
        
    monkeypatch.setattr(builtins, "open", mock_open_multi)
    test_ai_snapshot_matches_baseline(MockPath("mock_multi_tab"))
    
    # Case 3: multi_tab is missing in the scan dump (should default to True for backward compatibility)
    def mock_open_missing(*args, **kwargs):
        return io.StringIO('{}')
        
    monkeypatch.setattr(builtins, "open", mock_open_missing)
    test_ai_snapshot_matches_baseline(MockPath("mock_missing_tab"))
    
    assert called_all_tabs == [False, True, True]



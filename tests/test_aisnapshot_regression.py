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

    actual_text, _ = build_action_context(scan)
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

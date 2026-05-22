from __future__ import annotations

import json
from pathlib import Path

from generator.build_test import generate_test
from qcs_manifest import normalize_recording


def _write_generation_sample_jsonl(path: Path, run_id: str = "rec_gen") -> None:
    rows = [
        {"ts": "2026-05-21T10:00:00.000+00:00", "run_id": run_id, "surface": "unknown", "op": "session_start"},
        {
            "ts": "2026-05-21T10:00:01.000+00:00",
            "run_id": run_id,
            "surface": "html",
            "op": "ebs_login",
            "url": "https://example.local/login",
            "user_env": "EBS_USER",
            "password_env": "EBS_PASSWORD",
        },
        {
            "ts": "2026-05-21T10:00:02.000+00:00",
            "run_id": run_id,
            "surface": "html",
            "op": "oracle_form_open",
            "url": "https://example.local/form",
            "search_text": "Open Demo Form",
        },
        {
            "ts": "2026-05-21T10:00:03.000+00:00",
            "run_id": run_id,
            "surface": "java",
            "op": "java_form_launch",
            "url": "https://example.local/form",
            "form_id": "java_demo_form",
            "form_name": "Demo Form",
        },
        {
            "ts": "2026-05-21T10:00:04.000+00:00",
            "run_id": run_id,
            "surface": "java",
            "op": "java_send_text",
            "target": {"form_id": "java_demo_form", "friendly_name": "order_type"},
            "text": "STANDARD",
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _assert_generated_pytest_structure(out_dir: Path, test_name: str) -> None:
    test_file = out_dir / f"test_{test_name}.py"
    assert test_file.exists()

    content = test_file.read_text(encoding="utf-8")

    # Header
    header_lines = content.splitlines()
    assert header_lines[0].startswith("# AUTO-GENERATED")
    assert "# Source mode: manifest-native" in content
    assert "# Manifest steps are the generation source of truth." in content

    # Imports and structure
    assert "import pytest" in content
    assert "from qcs_replay.script import OracleReplay" in content
    assert f"def test_{test_name}(oracle_replay: OracleReplay):" in content

    # Markers
    assert "pytestmark" in content
    assert "pytest.mark.oracle" in content
    assert "pytest.mark.ebs" in content
    assert "pytest.mark.generated" in content

    # Business steps present — must use business/sanitised form refs (no java_ prefix)
    assert "oracle_replay.form(" in content
    import re as _re
    form_refs = [m[1] for m in _re.findall(r"oracle_replay\.form\(\s*(['\"])(.*?)\1", content)]
    for ref in form_refs:
        assert not ref.startswith("java_"), f"Technical java_ form_ref emitted: {ref!r}"

    # Lifecycle must NOT be in test file (moved to conftest)
    assert "oracle_replay.login(" not in content
    assert "oracle_replay.open_form(" not in content
    assert "sync_playwright" not in content

    # No internal/technical details in generated test
    assert ".textbox(" not in content
    assert "xpath" not in content.lower()
    assert "selector" not in content.lower()
    assert "descriptor" not in content.lower()


def _assert_conftest_structure(out_dir: Path) -> None:
    conftest = out_dir / "conftest.py"
    assert conftest.exists()
    content = conftest.read_text(encoding="utf-8")

    # Lifecycle constants
    assert "_EBS_LOGIN_URL" in content
    assert "_INITIAL_FORM_URL" in content

    # Fixtures present
    assert "def oracle_replay" in content
    assert "def _artifact_dir" in content
    assert "request" in content

    # Bundle writer wired up
    assert "artifact_dir" in content
    assert "test_name" in content

    # Login and form open in fixture
    assert "replay.login(" in content
    assert "replay.open_form(" in content

    # No business step calls in conftest
    assert ".set_text(" not in content
    assert ".click(" not in content
    assert "oracle_replay.form(" not in content


def test_generate_from_recording_directory(tmp_path: Path) -> None:
    run_dir = tmp_path / "rec_dir"
    run_dir.mkdir(parents=True)
    _write_generation_sample_jsonl(run_dir / "recording.jsonl", run_id="rec_dir")

    out_dir = tmp_path / "out_dir"
    generate_test(run_dir, out_dir, "rec_dir")

    _assert_generated_pytest_structure(out_dir, "rec_dir")
    _assert_conftest_structure(out_dir)


def test_generate_from_direct_jsonl_path(tmp_path: Path) -> None:
    run_dir = tmp_path / "rec_jsonl"
    run_dir.mkdir(parents=True)
    jsonl_path = run_dir / "recording.jsonl"
    _write_generation_sample_jsonl(jsonl_path, run_id="rec_jsonl")

    out_dir = tmp_path / "out_jsonl"
    generate_test(jsonl_path, out_dir, "rec_jsonl")

    _assert_generated_pytest_structure(out_dir, "rec_jsonl")
    _assert_conftest_structure(out_dir)


def test_generate_from_direct_manifest_path(tmp_path: Path) -> None:
    run_dir = tmp_path / "rec_manifest"
    run_dir.mkdir(parents=True)
    jsonl_path = run_dir / "recording.jsonl"
    _write_generation_sample_jsonl(jsonl_path, run_id="rec_manifest")
    manifest_path = normalize_recording(run_dir)

    out_dir = tmp_path / "out_manifest"
    generate_test(manifest_path, out_dir, "rec_manifest")

    _assert_generated_pytest_structure(out_dir, "rec_manifest")
    _assert_conftest_structure(out_dir)


def test_generated_test_has_no_lifecycle_or_selectors(tmp_path: Path) -> None:
    """Generated test file must be clean: no login, no browser setup, no selectors."""
    run_dir = tmp_path / "rec_clean"
    run_dir.mkdir(parents=True)
    _write_generation_sample_jsonl(run_dir / "recording.jsonl", run_id="rec_clean")
    generate_test(run_dir, tmp_path / "out_clean", "rec_clean")

    content = (tmp_path / "out_clean" / "test_rec_clean.py").read_text(encoding="utf-8")

    assert "oracle_replay.login(" not in content
    assert "oracle_replay.open_form(" not in content
    assert "sync_playwright" not in content
    assert "playwright" not in content.lower()
    assert "xpath" not in content.lower()
    assert "selector" not in content.lower()
    assert "descriptor" not in content.lower()
    assert "pytestmark" in content
    assert "pytest.mark.oracle" in content
    assert "pytest.mark.ebs" in content
    assert "pytest.mark.generated" in content
    assert "pytest.mark.browser" in content
    assert "pytest.mark.java_forms" in content


def test_conftest_contains_lifecycle_not_business_steps(tmp_path: Path) -> None:
    """Conftest must own login/open_form lifecycle; no business step calls."""
    run_dir = tmp_path / "rec_conf"
    run_dir.mkdir(parents=True)
    _write_generation_sample_jsonl(run_dir / "recording.jsonl", run_id="rec_conf")
    generate_test(run_dir, tmp_path / "out_conf", "rec_conf")

    _assert_conftest_structure(tmp_path / "out_conf")

    conftest = (tmp_path / "out_conf" / "conftest.py").read_text(encoding="utf-8")
    assert "https://example.local/login" in conftest
    assert "https://example.local/form" in conftest
    assert "_browser_page" in conftest
    assert "_artifact_dir" in conftest


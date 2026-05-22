from __future__ import annotations

import json

import pytest

from qcs_manifest import (
    ManifestValidationError,
    STEP_REQUIRED_FIELDS,
    TOP_LEVEL_REQUIRED_FIELDS,
    load_manifest,
    normalize_recording,
    validate_manifest_dict,
)
from qcs_manifest.schema import MANIFEST_SCHEMA


def _write_sample_jsonl(path) -> None:
    rows = [
        {
            "ts": "2026-05-11T15:15:57.605+00:00",
            "run_id": "rec_test",
            "surface": "unknown",
            "op": "session_start",
        },
        {
            "ts": "2026-05-11T15:16:12.023+00:00",
            "run_id": "rec_test",
            "surface": "html",
            "op": "ebs_login",
            "url": "https://example.local/login",
            "user_env": "EBS_USER",
            "password_env": "EBS_PASSWORD",
        },
        {
            "ts": "2026-05-11T15:18:28.184+00:00",
            "run_id": "rec_test",
            "surface": "java",
            "op": "java_send_text",
            "target": {"form_id": "java_find_orders", "friendly_name": "order_type"},
            "text": "STANDARD",
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_normalize_recording_builds_manifest(tmp_path) -> None:
    run_dir = tmp_path / "rec_test"
    run_dir.mkdir(parents=True)
    jsonl_path = run_dir / "recording.jsonl"
    _write_sample_jsonl(jsonl_path)

    manifest_path = normalize_recording(run_dir)
    manifest = load_manifest(manifest_path)

    assert manifest["schema_version"] == "1.0"
    assert manifest["run_id"] == "rec_test"
    assert manifest["flow_name"] == "rec_test"
    assert manifest["source_recording_path"].endswith("recording.jsonl")
    assert len(manifest["steps"]) == 3

    step = manifest["steps"][2]
    assert step["step_id"] == "s0003"
    assert step["surface"] == "java_forms"
    assert step["action"] == "java_send_text"
    assert step["form_ref"] == "java_find_orders"
    assert step["element_ref"] == "order_type"
    assert step["input"] == {"text": "STANDARD"}


def test_manifest_validation_reports_pathful_errors() -> None:
    broken = {
        "schema_version": "1.0",
        "run_id": "run_x",
        "flow_name": "flow_x",
        "app_context": {},
        "recorded_at": "2026-05-11T00:00:00Z",
        "source_recording_path": "recordings/run_x/recording.jsonl",
        "steps": [{"intent": "missing id", "surface": "wrong", "action": "x", "form_ref": ""}],
    }

    with pytest.raises(ManifestValidationError) as exc:
        validate_manifest_dict(broken)

    message = str(exc.value)
    assert "$.steps[0].step_id is required" in message
    assert "$.steps[0].input is required" in message
    assert "$.steps[0].surface must be one of" in message


def test_manifest_step_missing_form_ref_raises_validation_error() -> None:
    broken = {
        "schema_version": "1.0",
        "run_id": "run_z",
        "flow_name": "flow_z",
        "app_context": {},
        "recorded_at": "2026-05-11T00:00:00Z",
        "source_recording_path": "recordings/run_z/recording.jsonl",
        "steps": [
            {
                "step_id": "s0001",
                "intent": "do thing",
                "surface": "java_forms",
                "action": "java_click",
                "input": None,
                "assertions": [],
                "diagnostics": {},
                "metadata": {},
            }
        ],
    }

    with pytest.raises(ManifestValidationError) as exc:
        validate_manifest_dict(broken)

    assert "form_ref" in str(exc.value)


def test_normalize_recording_missing_jsonl_raises(tmp_path) -> None:
    run_dir = tmp_path / "missing_run"
    run_dir.mkdir(parents=True)

    with pytest.raises(FileNotFoundError):
        normalize_recording(run_dir)


def test_normalize_recording_malformed_jsonl_raises(tmp_path) -> None:
    run_dir = tmp_path / "bad_run"
    run_dir.mkdir(parents=True)
    (run_dir / "recording.jsonl").write_text('{"ok": 1}\n{bad json\n', encoding="utf-8")

    with pytest.raises(ValueError) as exc:
        normalize_recording(run_dir)

    assert "Invalid JSON" in str(exc.value)
    assert "line 2" in str(exc.value)


def test_normalize_recording_empty_jsonl_creates_manifest(tmp_path) -> None:
    run_dir = tmp_path / "empty_run"
    run_dir.mkdir(parents=True)
    (run_dir / "recording.jsonl").write_text("\n", encoding="utf-8")

    manifest_path = normalize_recording(run_dir)
    manifest = load_manifest(manifest_path)

    assert manifest["run_id"] == "empty_run"
    assert manifest["steps"] == []


def test_normalize_recording_explicit_out_path(tmp_path) -> None:
    run_dir = tmp_path / "out_run"
    run_dir.mkdir(parents=True)
    jsonl_path = run_dir / "recording.jsonl"
    _write_sample_jsonl(jsonl_path)

    out_path = tmp_path / "custom" / "manifest.json"
    written = normalize_recording(run_dir, out_path=out_path)

    assert written == out_path
    assert out_path.exists()
    manifest = load_manifest(out_path)
    assert manifest["run_id"] == "rec_test"



def test_required_fields_contract_stays_aligned() -> None:
    schema_top_required = tuple(MANIFEST_SCHEMA.get("required", []))
    schema_step_required = tuple(
        MANIFEST_SCHEMA["properties"]["steps"]["items"].get("required", [])
    )

    assert schema_top_required == TOP_LEVEL_REQUIRED_FIELDS
    assert schema_step_required == STEP_REQUIRED_FIELDS

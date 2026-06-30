from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qcs_manifest.model import RecordingManifest, RecordingManifestStep, SCHEMA_VERSION
from qcs_manifest.validate import validate_manifest_dict

MANIFEST_FILE_NAME = "recording.manifest.json"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        raw = line.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {path} at line {line_no}: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"Invalid row in {path} at line {line_no}: expected object")
        rows.append(row)
    return rows


def _surface_from_row(row: dict[str, Any]) -> str:
    op = str(row.get("op") or "")
    raw_surface = str(row.get("surface") or "").strip().lower()
    if op == "assertion":
        return "assertion"
    if raw_surface in {"html", "browser"}:
        return "browser"
    if raw_surface in {"java", "java_forms"}:
        return "java_forms"
    return "system"


def _form_ref_from_row(row: dict[str, Any]) -> str:
    target = row.get("target")
    if isinstance(target, dict):
        form_id = str(target.get("form_id") or "").strip()
        if form_id:
            return form_id
    # Prefer explicit top-level form_ref added by the Approach B recorder,
    # then fall back to legacy form_id.
    for key in ("form_ref", "form_id"):
        fid = row.get(key)
        if isinstance(fid, str) and fid.strip():
            return fid.strip()
    return ""


def _element_ref_from_row(row: dict[str, Any]) -> str | None:
    # Prefer the explicit stable repo key added by the Approach B recorder.
    explicit = str(row.get("element_ref") or "").strip()
    if explicit:
        return explicit
    # Fall back to target.friendly_name for legacy rows.
    target = row.get("target")
    if isinstance(target, dict):
        name = (
            target.get("friendly_name")
            or target.get("ref")
            or target.get("name")
            or ""
        )
        name = str(name).strip()
        if name:
            return name
    return None


def _input_from_row(row: dict[str, Any]) -> dict[str, Any] | None:
    payload: dict[str, Any] = {}
    for key in ("text", "value", "checked", "key", "url", "user_env", "password_env",
                "form_name", "locator_params"):
        if key in row:
            payload[key] = row[key]
    return payload or None


def _metadata_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Extract supplementary fields for the manifest step metadata dict.

    Captures the stable ``semantic_ref`` (gold/display name) and the volatile
    ``element_id`` (scan-time token like ``e10``) for diagnostics and healing.
    """
    meta: dict[str, Any] = {}
    for key in ("semantic_ref", "element_id"):
        val = row.get(key)
        if val is not None:
            meta[key] = val
    return meta


def _assertions_from_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    if row.get("op") != "assertion":
        return []
    assertion = {
        key: value
        for key in ("kind", "target", "expected", "actual", "operator", "message")
        if (value := row.get(key)) is not None
    }
    if not assertion:
        assertion = {"raw": row}
    return [assertion]


def _intent_from_row(row: dict[str, Any], target_ref: str) -> str:
    if isinstance(row.get("intent"), str) and row["intent"].strip():
        return row["intent"].strip()

    op = str(row.get("op") or "action")
    if op == "java_send_text":
        return f"Enter text into {target_ref or 'target'}"
    if op == "java_click":
        return f"Click {target_ref or 'target'}"
    if op == "java_press_key":
        return f"Press key {row.get('key') or ''}".strip()
    if op == "ebs_login":
        return "Login to Oracle EBS"
    if op == "oracle_form_open":
        return "Open Oracle form"
    if op == "java_form_launch":
        return "Launch Oracle Java form"
    if op == "java_double_click":
        return f"Double-click {target_ref or 'target'}"
    if op == "java_select_value":
        return f"Select a value in {target_ref or 'target'}"
    if op == "java_set_check":
        return f"Set checkbox {target_ref or 'target'}"
    if op == "java_expand_tree":
        return f"Expand {target_ref or 'target'}"
    if op == "java_collapse_tree":
        return f"Collapse {target_ref or 'target'}"
    return op.replace("_", " ")


def normalize_rows_to_manifest(
    rows: list[dict[str, Any]],
    *,
    run_id: str,
    source_recording_path: str,
    flow_name: str,
) -> dict[str, Any]:
    recorded_at = rows[0].get("ts") if rows else ""
    if not recorded_at:
        recorded_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    app_context: dict[str, Any] = {
        "source": "recording.jsonl",
        "surfaces_seen": sorted({_surface_from_row(row) for row in rows}),
    }

    steps: list[RecordingManifestStep] = []
    for idx, row in enumerate(rows, start=1):
        form_ref = _form_ref_from_row(row)
        element_ref = _element_ref_from_row(row)
        intent = _intent_from_row(row, f"{form_ref}.{element_ref}" if form_ref and element_ref else form_ref or "")
        step = RecordingManifestStep(
            step_id=f"s{idx:04d}",
            intent=intent,
            surface=_surface_from_row(row),
            action=str(row.get("op") or "action"),
            form_ref=form_ref,
            element_ref=element_ref,
            input=_input_from_row(row),
            assertions=_assertions_from_row(row),
            diagnostics={"ts": row.get("ts", "")},
            metadata=_metadata_from_row(row),
        )
        steps.append(step)

    manifest = RecordingManifest(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        flow_name=flow_name,
        app_context=app_context,
        recorded_at=str(recorded_at),
        source_recording_path=source_recording_path,
        steps=steps,
    )
    payload = manifest.to_dict()
    validate_manifest_dict(payload)
    return payload


def normalize_recording(
    recording_path: Path,
    out_path: Path | None = None,
    *,
    flow_name: str | None = None,
) -> Path:
    """Convert recording.jsonl (or containing run dir) into normalized manifest."""
    recording_path = Path(recording_path)

    if recording_path.is_dir():
        run_dir = recording_path
        jsonl_path = run_dir / "recording.jsonl"
    else:
        jsonl_path = recording_path
        run_dir = jsonl_path.parent

    if not jsonl_path.exists():
        raise FileNotFoundError(f"recording.jsonl not found: {jsonl_path}")

    rows = _load_jsonl(jsonl_path)
    derived_run_id = run_dir.name
    if rows and isinstance(rows[0].get("run_id"), str) and rows[0]["run_id"].strip():
        derived_run_id = rows[0]["run_id"].strip()

    manifest_payload = normalize_rows_to_manifest(
        rows,
        run_id=derived_run_id,
        source_recording_path=jsonl_path.as_posix(),
        flow_name=flow_name or derived_run_id,
    )

    output = out_path or (run_dir / MANIFEST_FILE_NAME)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Manifest root must be an object: {manifest_path}")
    validate_manifest_dict(payload)
    return payload

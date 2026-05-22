from __future__ import annotations

from typing import Any

from qcs_manifest.model import (
    MANIFEST_SURFACES,
    SCHEMA_VERSION,
    STEP_REQUIRED_FIELDS,
    TOP_LEVEL_REQUIRED_FIELDS,
)


class ManifestValidationError(ValueError):
    """Raised when a manifest fails schema/contract validation."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        message = "Manifest validation failed:\n" + "\n".join(f"- {err}" for err in errors)
        super().__init__(message)


def _is_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "null":
        return value is None
    return True


def validate_manifest_dict(payload: dict[str, Any]) -> None:
    """Validate normalized manifest payload.

    Keeps validation local and dependency-free while still following the JSON
    schema contract in ``qcs_manifest.schema``.
    """
    errors: list[str] = []

    if not isinstance(payload, dict):
        raise ManifestValidationError(["$ must be an object"])

    for key in TOP_LEVEL_REQUIRED_FIELDS:
        if key not in payload:
            errors.append(f"$.{key} is required")

    if errors:
        raise ManifestValidationError(errors)

    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"$.schema_version must be '{SCHEMA_VERSION}'")

    string_fields = ["run_id", "flow_name", "recorded_at", "source_recording_path"]
    for field in string_fields:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"$.{field} must be a non-empty string")

    if not isinstance(payload.get("app_context"), dict):
        errors.append("$.app_context must be an object")

    steps = payload.get("steps")
    if not isinstance(steps, list):
        errors.append("$.steps must be an array")
        raise ManifestValidationError(errors)

    allowed_surfaces = set(MANIFEST_SURFACES)
    seen_step_ids: set[str] = set()

    for index, step in enumerate(steps):
        location = f"$.steps[{index}]"
        if not isinstance(step, dict):
            errors.append(f"{location} must be an object")
            continue

        for key in STEP_REQUIRED_FIELDS:
            if key not in step:
                errors.append(f"{location}.{key} is required")

        step_id = step.get("step_id")
        if not isinstance(step_id, str) or not step_id.strip():
            errors.append(f"{location}.step_id must be a non-empty string")
        elif step_id in seen_step_ids:
            errors.append(f"{location}.step_id duplicates existing value {step_id!r}")
        else:
            seen_step_ids.add(step_id)

        intent = step.get("intent")
        if not isinstance(intent, str) or not intent.strip():
            errors.append(f"{location}.intent must be a non-empty string")

        surface = step.get("surface")
        if not isinstance(surface, str) or surface not in allowed_surfaces:
            errors.append(
                f"{location}.surface must be one of {sorted(allowed_surfaces)}"
            )

        action = step.get("action")
        if not isinstance(action, str) or not action.strip():
            errors.append(f"{location}.action must be a non-empty string")

        form_ref = step.get("form_ref")
        if not isinstance(form_ref, str):
            errors.append(f"{location}.form_ref must be a string")

        element_ref = step.get("element_ref")
        if element_ref is not None and not isinstance(element_ref, str):
            errors.append(f"{location}.element_ref must be a string or null")

        if "input" in step:
            value = step.get("input")
            if not (_is_type(value, "object") or _is_type(value, "null")):
                errors.append(f"{location}.input must be an object or null")

        if "assertions" in step and not isinstance(step.get("assertions"), list):
            errors.append(f"{location}.assertions must be an array")

        if "diagnostics" in step and not isinstance(step.get("diagnostics"), dict):
            errors.append(f"{location}.diagnostics must be an object")

        if "metadata" in step and not isinstance(step.get("metadata"), dict):
            errors.append(f"{location}.metadata must be an object")

    if errors:
        raise ManifestValidationError(errors)

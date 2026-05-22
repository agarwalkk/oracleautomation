from __future__ import annotations

from qcs_manifest.model import (
    MANIFEST_SURFACES,
    SCHEMA_VERSION,
    STEP_REQUIRED_FIELDS,
    TOP_LEVEL_REQUIRED_FIELDS,
)


MANIFEST_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://qcs.local/schemas/recording-manifest-1.0.json",
    "title": "QCS Recording Manifest",
    "type": "object",
    "required": list(TOP_LEVEL_REQUIRED_FIELDS),
    "properties": {
        "schema_version": {"type": "string", "const": SCHEMA_VERSION},
        "run_id": {"type": "string", "minLength": 1},
        "flow_name": {"type": "string", "minLength": 1},
        "app_context": {"type": "object"},
        "recorded_at": {"type": "string", "minLength": 1},
        "source_recording_path": {"type": "string", "minLength": 1},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "required": list(STEP_REQUIRED_FIELDS),
                "properties": {
                    "step_id": {"type": "string", "minLength": 1},
                    "intent": {"type": "string", "minLength": 1},
                    "surface": {
                        "type": "string",
                        "enum": list(MANIFEST_SURFACES),
                    },
                    "action": {"type": "string", "minLength": 1},
                    "form_ref": {"type": "string"},
                    "element_ref": {"type": ["string", "null"]},
                    "input": {"type": ["object", "null"]},
                    "assertions": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                    "diagnostics": {"type": "object"},
                    "metadata": {"type": "object"},
                },
                "additionalProperties": True,
            },
        },
    },
    "additionalProperties": True,
}

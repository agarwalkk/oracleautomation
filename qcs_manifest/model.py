from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


SCHEMA_VERSION = "1.0"
MANIFEST_SURFACES = ("browser", "java_forms", "system", "assertion")
MANIFEST_ACTIONS = (
    "ebs_login",
    "oracle_form_open",
    "java_form_launch",
    "java_send_text",
    "java_click",
    "java_double_click",
    "java_select_value",
    "java_set_check",
    "java_expand_tree",
    "java_collapse_tree",
    "java_activate_tab",
    "java_press_key",
    "assertion",
    "java_form_close",
    "step_note",
    "session_start",
    "repo_register_element",
    "repo_invoke_flow",
    "flow_boundary",
    "data_placeholder",
    "java_get_page_snapshot",
)
TOP_LEVEL_REQUIRED_FIELDS = (
    "schema_version",
    "run_id",
    "flow_name",
    "app_context",
    "recorded_at",
    "source_recording_path",
    "steps",
)
STEP_REQUIRED_FIELDS = (
    "step_id",
    "intent",
    "surface",
    "action",
    "form_ref",
    "element_ref",
    "input",
    "assertions",
    "diagnostics",
    "metadata",
)

ManifestSurface = Literal["browser", "java_forms", "system", "assertion"]


@dataclass(slots=True)
class RecordingManifestStep:
    step_id: str
    intent: str
    surface: ManifestSurface
    action: str
    form_ref: str = ""
    element_ref: str | None = None
    input: dict[str, Any] | None = None
    assertions: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RecordingManifest:
    schema_version: str
    run_id: str
    flow_name: str
    app_context: dict[str, Any]
    recorded_at: str
    source_recording_path: str
    steps: list[RecordingManifestStep] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["steps"] = [step.to_dict() for step in self.steps]
        return payload

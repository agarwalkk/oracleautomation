from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


SCHEMA_VERSION = "1.0"
MANIFEST_SURFACES = ("browser", "java_forms", "system", "assertion")
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

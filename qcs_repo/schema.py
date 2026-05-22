"""qcs_repo.schema — Validated object-repository entry model.

Each entry is uniquely identified by the (form_ref, element_ref) primary key.
``qualified_ref`` = form_ref + "." + element_ref is derived for display, logging,
and diagnostics only — it is not used as a primary lookup key.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


VALID_SURFACES: frozenset[str] = frozenset({"browser", "java_forms"})
VALID_SOURCES: frozenset[str] = frozenset({"recording", "manual", "healing", "imported"})
VALID_STATUSES: frozenset[str] = frozenset({"active", "deprecated", "candidate"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class RepoEntry:
    """A validated, form-scoped object repository entry.

    Primary key: (form_ref, element_ref).
    ``qualified_ref`` is derived as ``form_ref + "." + element_ref`` — for display
    and diagnostics only, not for primary lookup.
    """

    form_ref: str
    element_ref: str
    qualified_ref: str = ""
    friendly_name: str = ""
    surface: str = ""               # "browser" | "java_forms"
    object_type: str = ""
    descriptor: dict = field(default_factory=dict)
    fallback_descriptors: list = field(default_factory=list)
    source: str = "recording"       # "recording" | "manual" | "healing" | "imported"
    confidence: float = 1.0
    status: str = "active"          # "active" | "deprecated" | "candidate"
    last_validated_run: str | None = None
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.qualified_ref:
            self.qualified_ref = f"{self.form_ref}.{self.element_ref}"
        if not self.friendly_name:
            self.friendly_name = self.element_ref

    # ── Serialisation ──────────────────────────────────────────────────────

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RepoEntry":
        now = _now_iso()
        raw_conf = data.get("confidence")
        try:
            confidence = float(raw_conf) if raw_conf is not None else 1.0
        except (TypeError, ValueError):
            confidence = 1.0
        return cls(
            form_ref=str(data.get("form_ref") or ""),
            element_ref=str(data.get("element_ref") or ""),
            qualified_ref=str(data.get("qualified_ref") or ""),
            friendly_name=str(data.get("friendly_name") or ""),
            surface=str(data.get("surface") or ""),
            object_type=str(data.get("object_type") or ""),
            descriptor=dict(data.get("descriptor") or {}),
            fallback_descriptors=list(data.get("fallback_descriptors") or []),
            source=str(data.get("source") or "recording"),
            confidence=confidence,
            status=str(data.get("status") or "active"),
            last_validated_run=data.get("last_validated_run"),
            created_at=str(data.get("created_at") or now),
            updated_at=str(data.get("updated_at") or now),
            metadata=dict(data.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "form_ref": self.form_ref,
            "element_ref": self.element_ref,
            "qualified_ref": self.qualified_ref,
            "friendly_name": self.friendly_name,
            "surface": self.surface,
            "object_type": self.object_type,
            "descriptor": self.descriptor,
            "fallback_descriptors": self.fallback_descriptors,
            "source": self.source,
            "confidence": self.confidence,
            "status": self.status,
            "last_validated_run": self.last_validated_run,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    # ── Status helpers ─────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def is_deprecated(self) -> bool:
        return self.status == "deprecated"

    @property
    def is_candidate(self) -> bool:
        return self.status == "candidate"


# ── Validation ─────────────────────────────────────────────────────────────────

class RepoValidationError(ValueError):
    """Raised when one or more repository entries fail validation."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("\n".join(errors))


def validate_entry(entry: dict[str, Any]) -> list[str]:
    """Return JSON-path-style validation errors for a raw entry dict.

    Returns an empty list when the entry is valid.

    Example errors::

        "$.form_ref must be a non-empty string"
        "$.surface must be one of ['browser', 'java_forms'] — got 'web'"
    """
    errors: list[str] = []

    form_ref = entry.get("form_ref")
    if not isinstance(form_ref, str) or not form_ref.strip():
        errors.append("$.form_ref must be a non-empty string")

    element_ref = entry.get("element_ref")
    if not isinstance(element_ref, str) or not element_ref.strip():
        errors.append("$.element_ref must be a non-empty string")

    surface = entry.get("surface", "")
    if surface not in VALID_SURFACES:
        errors.append(
            f"$.surface must be one of {sorted(VALID_SURFACES)} — got {surface!r}"
        )

    source = entry.get("source", "recording")
    if source not in VALID_SOURCES:
        errors.append(
            f"$.source must be one of {sorted(VALID_SOURCES)} — got {source!r}"
        )

    status = entry.get("status", "active")
    if status not in VALID_STATUSES:
        errors.append(
            f"$.status must be one of {sorted(VALID_STATUSES)} — got {status!r}"
        )

    confidence = entry.get("confidence", 1.0)
    try:
        c = float(confidence)
        if not (0.0 <= c <= 1.0):
            errors.append("$.confidence must be a number in [0.0, 1.0]")
    except (TypeError, ValueError):
        errors.append("$.confidence must be a number in [0.0, 1.0]")

    descriptor = entry.get("descriptor")
    if descriptor is not None and not isinstance(descriptor, dict):
        errors.append("$.descriptor must be a dict or null")

    fallback = entry.get("fallback_descriptors")
    if fallback is not None and not isinstance(fallback, list):
        errors.append("$.fallback_descriptors must be a list or null")

    return errors

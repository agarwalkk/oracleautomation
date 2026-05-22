"""
qcs_replay.failure_bundle -- Structured failure bundle for deterministic replay diagnostics.

Captures replay failure context (step, exception, replay log, repo entry, environment)
without calling AI or any LLM.  Written to <artifact_dir>/failure_bundle.json when a
replay action raises.  Optional artifact capture hooks (screenshot, Java snapshot) are
invoked when the relevant backend is available.

No repository entries are modified.  No healing is performed.  No secrets should ever
appear in the bundle; a ``redact_fn`` hook is provided for defence-in-depth.
"""
from __future__ import annotations

import datetime
import json
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class FailureBundle:
    """Structured capture of replay failure context for debugging and review-gated healing.

    Fields
    ------
    run_id          Unique run identifier — defaults to the artifact directory name.
    test_name       Test function name as reported by pytest.
    timestamp       ISO-8601 UTC timestamp of bundle capture.
    failed_step     dict with action / form_ref / element_ref / surface.
    exception       dict with type / message.
    replay_log      List of ReplayAction dicts captured up to the point of failure.
    repo_entry      Repository descriptor for the element that failed, if it was resolved.
    manifest_path   Path to the source manifest, if available.
    environment     Python version, platform, and package version.
    artifacts       Paths to supplementary artifact files (screenshot, java_snapshot).
    """

    run_id: str
    test_name: str
    timestamp: str
    failed_step: dict[str, Any]
    exception: dict[str, str]
    replay_log: list[dict[str, Any]]
    repo_entry: dict[str, Any] | None = None
    manifest_path: str | None = None
    environment: dict[str, str] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "test_name": self.test_name,
            "timestamp": self.timestamp,
            "failed_step": self.failed_step,
            "exception": self.exception,
            "replay_log": self.replay_log,
            "repo_entry": self.repo_entry,
            "manifest_path": self.manifest_path,
            "environment": self.environment,
            "artifacts": self.artifacts,
        }


class BundleWriter:
    """Captures and writes a FailureBundle to disk on replay failure.

    Attach to ``OracleReplay`` via the ``artifact_dir`` constructor parameter.
    The writer is AI-free: it only serialises existing replay state.

    Parameters
    ----------
    artifact_dir:
        Directory for this test run's artefacts.  ``failure_bundle.json`` is
        written here on the first ``write()`` call.  Created on demand.
    run_id:
        Human-readable run identifier.  Defaults to ``artifact_dir.name``.
    test_name:
        Test function name — typically ``request.node.name`` in a pytest fixture.
    manifest_path:
        Optional path to the source manifest included for traceability.
    redact_fn:
        Optional ``(key: str, value: Any) -> Any`` callable applied to every
        leaf value in the bundle dict before writing.  Return the original value
        to skip redaction.  Secrets must never reach the replay log or test names,
        but the hook exists for defence-in-depth.
    """

    def __init__(
        self,
        artifact_dir: Path,
        *,
        run_id: str = "",
        test_name: str = "",
        manifest_path: str | None = None,
        redact_fn: Callable[[str, Any], Any] | None = None,
    ) -> None:
        self.artifact_dir = Path(artifact_dir)
        self.run_id = run_id or self.artifact_dir.name
        self.test_name = test_name
        self.manifest_path = manifest_path
        self.redact_fn = redact_fn

    def write(
        self,
        exc: BaseException,
        *,
        action: str,
        form_ref: str,
        element_ref: str | None,
        surface: str,
        logger: Any,           # ReplayLogger — typed as Any to avoid circular import
        page: Any = None,
        java_backend: Any = None,
        resolved: Any = None,  # ResolvedTarget | None
    ) -> Path:
        """Capture replay failure context and write ``failure_bundle.json``.

        Parameters
        ----------
        exc:            The exception that caused the replay step to fail.
        action:         Replay action name (e.g. ``"click"``, ``"set_text"``).
        form_ref:       Repository form identifier for the failing step.
        element_ref:    Repository element identifier for the failing step.
        surface:        Surface string resolved at the time of failure, or ``"unknown"``.
        logger:         The ``ReplayLogger`` instance from the active OracleReplay.
        page:           Optional Playwright page — used for screenshot capture.
        java_backend:   Optional Java backend — used for Forms hierarchy snapshot.
        resolved:       Optional ``ResolvedTarget`` — included in ``repo_entry`` if present.

        Returns
        -------
        Path
            Path to the written ``failure_bundle.json`` file.
        """
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

        failed_step: dict[str, Any] = {
            "action": action,
            "form_ref": form_ref,
            "element_ref": element_ref,
            "surface": surface,
        }

        exception_info: dict[str, str] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }

        replay_log: list[dict[str, Any]] = [
            {
                "seq": a.seq,
                "action": a.action,
                "target_ref": a.target_ref,
                "surface": a.surface,
                "status": a.status,
                "error": a.error,
                "elapsed_s": a.elapsed_s,
            }
            for a in (logger.actions if logger is not None else [])
        ]

        repo_entry: dict[str, Any] | None = None
        if resolved is not None:
            repo_entry = {
                "form_ref": resolved.form_id,
                "element_ref": resolved.ref,
                "surface": resolved.surface,
                "descriptor": resolved.descriptor,
            }

        environment: dict[str, str] = {
            "python_version": sys.version,
            "platform": platform.platform(),
        }
        try:
            from importlib.metadata import version as _ver  # noqa: PLC0415
            environment["qcs_oracle_automation"] = _ver("qcs-oracle-automation")
        except Exception:  # noqa: BLE001
            pass

        bundle = FailureBundle(
            run_id=self.run_id,
            test_name=self.test_name,
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            failed_step=failed_step,
            exception=exception_info,
            replay_log=replay_log,
            repo_entry=repo_entry,
            manifest_path=self.manifest_path,
            environment=environment,
            artifacts={},
        )

        data = bundle.to_dict()

        # Apply redaction hook before writing any artifacts.
        if self.redact_fn is not None:
            data = _apply_redaction(data, self.redact_fn)

        # Optional: screenshot via Playwright page.
        if page is not None:
            screenshot_path = self.artifact_dir / "failure_screenshot.png"
            try:
                page.screenshot(path=str(screenshot_path))
                data["artifacts"]["screenshot"] = str(screenshot_path)
            except Exception:  # noqa: BLE001
                pass

        # Optional: Java Forms hierarchy snapshot.
        if java_backend is not None and hasattr(java_backend, "snapshot"):
            java_snapshot_path = self.artifact_dir / "failure_java_snapshot.txt"
            try:
                snapshot_text = java_backend.snapshot()
                java_snapshot_path.write_text(str(snapshot_text), encoding="utf-8")
                data["artifacts"]["java_snapshot"] = str(java_snapshot_path)
            except Exception:  # noqa: BLE001
                pass

        bundle_path = self.artifact_dir / "failure_bundle.json"
        bundle_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        return bundle_path


def _apply_redaction(
    data: dict[str, Any],
    redact_fn: Callable[[str, Any], Any],
) -> dict[str, Any]:
    """Recursively apply ``redact_fn(key, value)`` to every leaf value in data."""
    result: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, dict):
            result[k] = _apply_redaction(v, redact_fn)
        elif isinstance(v, list):
            result[k] = [
                _apply_redaction(item, redact_fn) if isinstance(item, dict) else redact_fn(k, item)
                for item in v
            ]
        else:
            result[k] = redact_fn(k, v)
    return result

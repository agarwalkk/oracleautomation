"""Human-readable replay DSL for generated pytest scripts.

Generated tests should read like business replay steps while this module keeps
the deterministic Playwright and Java-agent plumbing in one place.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from qcs_replay.dsl import (
    BrowserReplayBackend,
    FormReplay,
    JavaFormsReplayBackend,
    ReplayAssertionError,
    ReplayBackend,
    ReplayError,
    ReplayLogger,
    ReplayRefNotFoundError,
    ReplayRoutingError,
    RepositoryResolver,
    ResolvedTarget,
)
from qcs_replay.java_agent import attach_java_agent, wait_for_active_form
from qcs_replay.web import open_java_form_sync
from qcs_repo import store as repo_store


class OracleReplay:
    """Top-level replay facade used by generated pytest scripts."""

    def __init__(
        self,
        page: Any,
        healer: Any | None = None,
        object_repository: dict[str, Any] | None = None,
        *,
        resolver: RepositoryResolver | None = None,
        browser_backend: ReplayBackend | None = None,
        java_backend: ReplayBackend | None = None,
        artifact_dir: Path | None = None,
        run_id: str = "",
        test_name: str = "",
        redact_fn: Callable[..., Any] | None = None,
    ) -> None:
        self.page = page
        self.healer = healer
        self.object_repository = object_repository or {}
        self.java_driver: Any | None = None
        self.current_java_form_id = ""
        # DSL layer
        self._resolver: RepositoryResolver = resolver or RepositoryResolver()
        self._browser_backend: ReplayBackend | None = browser_backend
        self._java_backend: ReplayBackend | None = java_backend
        self._logger: ReplayLogger = ReplayLogger()
        # Failure bundle writer — None when no artifact_dir is supplied.
        if artifact_dir is not None:
            from qcs_replay.failure_bundle import BundleWriter  # noqa: PLC0415
            self._bundle_writer: Any = BundleWriter(
                artifact_dir,
                run_id=run_id,
                test_name=test_name,
                redact_fn=redact_fn,
            )
        else:
            self._bundle_writer = None

    # ── High-level surface-agnostic DSL ──────────────────────────────────────

    def step(self, description: str) -> None:
        """Log a human-readable narrative step marker (no action performed)."""
        self._logger.start("step", description, "")
        self._logger.ok(self._logger._seq)

    def form(self, form_ref: str) -> FormReplay:
        """Return a FormReplay handle for actions on a specific form or page."""
        failure_hook = None
        if self._bundle_writer is not None:
            _bw = self._bundle_writer
            _page = self.page
            _java_backend = self._java_backend
            _logger = self._logger
            _form_ref = form_ref

            def failure_hook(exc: BaseException, *, action: str, element_ref: str | None, resolved: Any) -> None:  # noqa: E501
                _bw.write(
                    exc,
                    action=action,
                    form_ref=_form_ref,
                    element_ref=element_ref,
                    surface=resolved.surface if resolved is not None else "unknown",
                    logger=_logger,
                    page=_page,
                    java_backend=_java_backend,
                    resolved=resolved,
                )

        return FormReplay(
            form_ref=form_ref,
            resolver=self._resolver,
            browser_backend=self._browser_backend,
            java_backend=self._java_backend,
            logger=self._logger,
            failure_hook=failure_hook,
        )

    def press_key(self, key: str) -> None:
        """Send a keyboard shortcut to the currently active surface backend.

        Routes to the Java Forms backend when one is configured (most Oracle EBS
        navigation shortcuts target the Forms JVM).  Falls back to the browser
        backend when no Java backend is available.  Raises ReplayRoutingError
        when neither backend is configured.
        """
        backend: ReplayBackend | None = self._java_backend or self._browser_backend
        if backend is None:
            raise ReplayRoutingError(
                f"press_key({key!r}): no backend is configured on this OracleReplay instance"
            )
        seq = self._logger.start("press_key", key, "")
        t0 = time.monotonic()
        try:
            backend.press_key(key)
            self._logger.ok(seq, time.monotonic() - t0)
        except Exception as exc:
            self._logger.fail(seq, str(exc))
            raise

    # ── EBS / Java Forms helpers ──────────────────────────────────────────────

    def login(
        self,
        url: str | None = None,
        *,
        user_env: str = "EBS_USER",
        password_env: str = "EBS_PASSWORD",
    ) -> None:
        """Log into Oracle EBS and fail fast when authentication is rejected."""
        login_url = url or os.environ.get("EBS_URL")
        if not login_url:
            raise RuntimeError("EBS login URL was not provided and EBS_URL is not set")
        self.page.goto(login_url)
        self.page.wait_for_load_state("networkidle")
        self.page.get_by_role("textbox", name="User Name").fill(os.environ[user_env])
        self.page.get_by_role("textbox", name="Password").fill(os.environ[password_env])
        login_button = self.page.get_by_role("button", name="Log In")
        login_button.click()
        self.page.wait_for_load_state("networkidle")
        try:
            login_button.wait_for(state="hidden", timeout=15_000)
        except Exception as exc:
            raise RuntimeError(
                "Oracle EBS login did not complete successfully; the login page remained "
                "visible after submit. Check EBS_USER/EBS_PASSWORD and any login error "
                "shown by the application."
            ) from exc

    def open_form(
        self,
        *,
        url: str,
        name: str | None = None,
        form_id: str | None = None,
        expected_name: str | None = None,
        objects: dict[str, Any] | None = None,
    ) -> None:
        """Navigate to and attach to an Oracle Forms window.

        Opens the form URL in the browser, attaches the Java agent, waits for
        the active Forms window, and stores ``current_java_form_id`` for later
        resolution.  Called by generated conftest fixtures; the return value is
        intentionally ``None`` — use ``oracle_replay.form(form_ref)`` for
        subsequent interactions.
        """
        object_repository = objects or self.object_repository
        form_metadata = object_repository.get("__form__") or {}
        resolved_form_id = form_id or form_metadata.get("form_id")

        resolved_expected = expected_name or form_metadata.get("expected_name") or name
        open_java_form_sync(self.page, url)
        self.java_driver = attach_java_agent()
        active_name = wait_for_active_form(self.java_driver, expected_name=resolved_expected)
        if not resolved_form_id:
            resolved_form_id = _resolve_form_id_by_title(active_name)
        if not resolved_form_id:
            raise RuntimeError(f"Could not resolve repository form for active Oracle form {active_name!r}")
        self.current_java_form_id = resolved_form_id

    def _java_driver(self) -> Any:
        if self.java_driver is None:
            self.java_driver = attach_java_agent()
        return self.java_driver


def _resolve_form_id_by_title(title: str) -> str | None:
    wanted = _normalize_title(title)
    for form_id in repo_store.list_form_ids():
        form = repo_store.load_form(form_id) or {}
        candidate = str(form.get("title") or form.get("name") or "")
        if candidate and _normalize_title(candidate) == wanted:
            return form_id
    return None


def _normalize_title(title: str) -> str:
    return " ".join(str(title or "").casefold().split())



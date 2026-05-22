"""
qcs_replay.dsl — Surface-aware backends, repository resolver, and structured
logging for the OracleReplay high-level DSL.

Provides the plumbing that routes high-level replay calls (click, set_text,
assert_visible, …) to the correct backend based on the surface resolved from
the object repository.  No AI or LLM is invoked from this module.

Public API
----------
ReplayError               base replay exception
ReplayRefNotFoundError    ref not found in repository
ReplayRoutingError        no backend for the resolved surface
ReplayAssertionError      deterministic assertion failure

ReplayAction              dataclass — one structured log entry
ReplayLogger              sequential action logger

ResolvedTarget            dataclass — resolved ref with surface + descriptor
RepositoryResolver        thin adapter over qcs_repo.store

ReplayBackend             abstract backend interface
BrowserReplayBackend      Playwright-backed browser/OAF backend
JavaFormsReplayBackend    Java-agent-backed Oracle Forms backend
"""
from __future__ import annotations

import abc
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

_log = logging.getLogger(__name__)


# ─── Exceptions ───────────────────────────────────────────────────────────────


class ReplayError(RuntimeError):
    """Base class for all deterministic replay failures."""


class ReplayRefNotFoundError(ReplayError):
    """Raised when a target reference cannot be resolved in the repository."""


class ReplayRoutingError(ReplayError):
    """Raised when no registered backend handles the resolved surface."""


class ReplayAssertionError(ReplayError):
    """Raised when a deterministic replay assertion fails."""


# ─── Structured log entry ─────────────────────────────────────────────────────


@dataclass
class ReplayAction:
    """One recorded replay action — written by ReplayLogger for diagnostics."""

    seq: int
    action: str
    target_ref: str
    surface: str
    status: str = "started"    # "started" | "ok" | "failed"
    error: str | None = None
    elapsed_s: float | None = None


class ReplayLogger:
    """Sequential structured logger for replay actions.

    Each call pair ``start()`` / ``ok()`` or ``start()`` / ``fail()`` records
    one action with sequence number, action name, ref, surface, and status.
    Entries are kept in ``actions`` for test assertions.
    """

    def __init__(self) -> None:
        self._seq = 0
        self.actions: list[ReplayAction] = []

    def start(self, action: str, target_ref: str, surface: str) -> int:
        self._seq += 1
        entry = ReplayAction(seq=self._seq, action=action, target_ref=target_ref, surface=surface)
        self.actions.append(entry)
        _log.debug(
            "[Replay#%d] %s ref=%r surface=%s status=started",
            self._seq, action, target_ref, surface,
        )
        return self._seq

    def ok(self, seq: int, elapsed_s: float | None = None) -> None:
        entry = self._entry(seq)
        if entry is None:
            return
        entry.status = "ok"
        entry.elapsed_s = elapsed_s
        _log.debug(
            "[Replay#%d] %s ref=%r surface=%s status=ok elapsed=%.3fs",
            seq, entry.action, entry.target_ref, entry.surface, elapsed_s or 0.0,
        )

    def fail(self, seq: int, error: str) -> None:
        entry = self._entry(seq)
        if entry is None:
            return
        entry.status = "failed"
        entry.error = error
        _log.warning(
            "[Replay#%d] %s ref=%r surface=%s status=failed error=%s",
            seq, entry.action, entry.target_ref, entry.surface, error,
        )

    def _entry(self, seq: int) -> ReplayAction | None:
        for e in reversed(self.actions):
            if e.seq == seq:
                return e
        return None


# ─── Repository resolver ──────────────────────────────────────────────────────


@dataclass
class ResolvedTarget:
    """A target reference resolved to a canonical surface, form, and descriptor."""

    ref: str
    surface: str       # "browser" | "java_forms" | "unknown"
    form_id: str
    descriptor: dict


def _surface_from_form_id(form_id: str) -> str:
    if form_id.startswith("html_"):
        return "browser"
    if form_id.startswith("java_"):
        return "java_forms"
    return "unknown"


class RepositoryResolver:
    """Adapter over the ``repo_entries`` table for target reference resolution.

    Production usage resolves refs through the local SQLite catalog.
    For tests, call ``register(form_ref, element_ref, target)`` to inject
    known entries without touching the disk repository.

    ``resolve(form_ref, element_ref)`` always returns a ``ResolvedTarget`` or
    raises ``ReplayRefNotFoundError`` — it never returns ``None``.
    """

    def __init__(self, repo_dir: Any = None) -> None:
        if repo_dir is None:
            import config as _cfg  # noqa: PLC0415
            repo_dir = _cfg.REPO_DIR
        self._repo_dir = repo_dir
        self._overrides: dict[tuple[str, str], ResolvedTarget] = {}

    def register(self, form_ref: str, element_ref: str, target: ResolvedTarget) -> None:
        """Register a test override — no disk lookup performed for this pair."""
        self._overrides[(form_ref, element_ref)] = target

    def resolve(self, form_ref: str, element_ref: str) -> ResolvedTarget:
        """Resolve (form_ref, element_ref) to a ResolvedTarget.

        Resolution order:
        1. In-memory overrides (test injection via ``register``).
        2. ``repo_entries`` table — primary key (form_ref, element_ref).

        Raises
        ------
        ReplayRefNotFoundError
            When the (form_ref, element_ref) pair is absent from the repository
            or the matching entry has ``status='deprecated'``.
        """
        key = (form_ref, element_ref)
        if key in self._overrides:
            return self._overrides[key]
        from qcs_repo import store as repo_store  # noqa: PLC0415
        entry = repo_store.load_entry(form_ref, element_ref, self._repo_dir)
        if entry is None:
            raise ReplayRefNotFoundError(
                f"ref not found in repository: form_ref={form_ref!r} element_ref={element_ref!r}"
            )
        if entry.is_deprecated:
            raise ReplayRefNotFoundError(
                f"ref is deprecated: form_ref={form_ref!r} element_ref={element_ref!r} "
                "— update the repository entry to active or candidate"
            )
        surface = entry.surface or _surface_from_form_id(form_ref)
        return ResolvedTarget(
            ref=entry.element_ref,
            surface=surface,
            form_id=form_ref,
            descriptor=entry.descriptor or {},
        )


# ─── Backend abstract interface ───────────────────────────────────────────────


class ReplayBackend(abc.ABC):
    """Abstract interface every surface-specific replay backend must implement."""

    @abc.abstractmethod
    def click(self, ref: str, descriptor: dict) -> None: ...

    @abc.abstractmethod
    def double_click(self, ref: str, descriptor: dict) -> None: ...

    @abc.abstractmethod
    def set_text(self, ref: str, descriptor: dict, value: str) -> None: ...

    @abc.abstractmethod
    def select_value(self, ref: str, descriptor: dict, value: str) -> None: ...

    @abc.abstractmethod
    def press_key(self, key: str) -> None: ...

    @abc.abstractmethod
    def wait_for(self, ref: str, descriptor: dict, timeout_ms: int = 10_000) -> None: ...

    @abc.abstractmethod
    def assert_visible(self, ref: str, descriptor: dict) -> None: ...

    @abc.abstractmethod
    def get_text(self, ref: str, descriptor: dict) -> str: ...

    @abc.abstractmethod
    def get_value(self, ref: str, descriptor: dict) -> str: ...


# ─── Browser / Playwright backend ─────────────────────────────────────────────


class BrowserReplayBackend(ReplayBackend):
    """Playwright-backed backend for browser and OAF pages.

    Locator strategies (highest to lowest priority):
      role → label → text → placeholder → test_id → id → css → xpath
    Coordinates are used only when ``allow_coordinates=True`` is set in the
    descriptor — they are never a silent fallback.

    All actions use Playwright's built-in auto-waiting.  Fixed sleeps are never
    added.  Failure messages always include element_ref, surface, and the list
    of locator strategies that were attempted.

    Parameters
    ----------
    page:
        Sync Playwright Page instance.
    timeout_ms:
        Action timeout passed to every Playwright call.  Defaults to
        ``config.LOCATOR_TIMEOUT_S * 1000``.
    """

    def __init__(self, page: Any, timeout_ms: int | None = None) -> None:
        self._page = page
        import config as _cfg  # noqa: PLC0415
        self._timeout: int = (
            timeout_ms if timeout_ms is not None
            else int(_cfg.LOCATOR_TIMEOUT_S * 1000)
        )

    def _locator(self, ref: str, descriptor: dict) -> Any:
        """Resolve *descriptor* to a Playwright locator.

        Raises ``ReplayAssertionError`` (a ``ReplayError``) when no strategy
        matches, so the caller's ``except ReplayError: raise`` propagates it
        unchanged and ``FormReplay._run()`` adds form_ref context.
        """
        from qcs_replay.locator import (  # noqa: PLC0415
            LocatorDescriptor,
            LocatorResolutionError,
            PlaywrightResolver,
        )
        ld = LocatorDescriptor(descriptor or {})
        try:
            return PlaywrightResolver(self._page, timeout_ms=self._timeout).resolve(ld)
        except LocatorResolutionError as exc:
            strats = ", ".join(exc.attempted_strategies) if exc.attempted_strategies else "none"
            raise ReplayAssertionError(
                f"locator resolution failed: element_ref={ref!r} surface=browser "
                f"attempted=[{strats}]"
            ) from exc

    def click(self, ref: str, descriptor: dict) -> None:
        try:
            self._locator(ref, descriptor).click(timeout=self._timeout)
        except ReplayError:
            raise
        except Exception as exc:
            raise ReplayAssertionError(
                f"browser.click failed: element_ref={ref!r}: {exc}"
            ) from exc

    def double_click(self, ref: str, descriptor: dict) -> None:
        try:
            self._locator(ref, descriptor).dblclick(timeout=self._timeout)
        except ReplayError:
            raise
        except Exception as exc:
            raise ReplayAssertionError(
                f"browser.double_click failed: element_ref={ref!r}: {exc}"
            ) from exc

    def set_text(self, ref: str, descriptor: dict, value: str) -> None:
        try:
            self._locator(ref, descriptor).fill(value, timeout=self._timeout)
        except ReplayError:
            raise
        except Exception as exc:
            raise ReplayAssertionError(
                f"browser.set_text failed: element_ref={ref!r}: {exc}"
            ) from exc

    def select_value(self, ref: str, descriptor: dict, value: str) -> None:
        try:
            self._locator(ref, descriptor).select_option(value, timeout=self._timeout)
        except ReplayError:
            raise
        except Exception as exc:
            raise ReplayAssertionError(
                f"browser.select_value failed: element_ref={ref!r}: {exc}"
            ) from exc

    def press_key(self, key: str) -> None:
        self._page.keyboard.press(key)

    def wait_for(self, ref: str, descriptor: dict, timeout_ms: int = 10_000) -> None:
        """Wait using Playwright auto-wait (state='visible') — no fixed sleeps."""
        try:
            self._locator(ref, descriptor).wait_for(state="visible", timeout=timeout_ms)
        except ReplayError:
            raise
        except Exception as exc:
            raise ReplayAssertionError(
                f"browser.wait_for timed out: element_ref={ref!r}: {exc}"
            ) from exc

    def assert_visible(self, ref: str, descriptor: dict) -> None:
        """Assert element is visible using Playwright auto-wait (not a sync poll)."""
        try:
            self._locator(ref, descriptor).wait_for(state="visible", timeout=self._timeout)
        except ReplayError:
            raise
        except Exception as exc:
            raise ReplayAssertionError(
                f"assert_visible failed: element_ref={ref!r} surface=browser: {exc}"
            ) from exc

    def get_text(self, ref: str, descriptor: dict) -> str:
        try:
            return str(self._locator(ref, descriptor).inner_text() or "")
        except ReplayError:
            raise
        except Exception as exc:
            raise ReplayAssertionError(
                f"browser.get_text failed: element_ref={ref!r}: {exc}"
            ) from exc

    def get_value(self, ref: str, descriptor: dict) -> str:
        try:
            return str(self._locator(ref, descriptor).input_value() or "")
        except ReplayError:
            raise
        except Exception as exc:
            raise ReplayAssertionError(
                f"browser.get_value failed: element_ref={ref!r}: {exc}"
            ) from exc


# ─── Java Forms backend ───────────────────────────────────────────────────────


def _descriptor_id(descriptor: dict) -> str:
    """Return a short identity string suitable for embedding in error messages.

    Preference order: Java path → xpath/path → role/name → elementid → truncated repr.
    """
    if not descriptor:
        return "(empty)"
    java = descriptor.get("java") or {}
    path = java.get("path") or descriptor.get("path") or descriptor.get("xpath") or ""
    if path:
        return str(path)[:120]
    name = descriptor.get("name") or descriptor.get("friendly_name") or ""
    role = descriptor.get("role") or ""
    eid  = descriptor.get("elementid") or ""
    if name:
        return f"{role}/{name}" if role else str(name)[:80]
    if eid:
        return str(eid)
    return str(descriptor)[:80]


class JavaFormsReplayBackend(ReplayBackend):
    """Java-agent-backed backend for Oracle Forms windows.

    Accepts either a live ``JavaAgentDriver`` instance or a zero-argument
    callable that lazily attaches to the running Forms JVM on first use.

    Readiness
    ---------
    * ``wait_for()`` verifies the Java agent is responsive (``health()``), then
      polls ``analyze_forms_readiness`` + element existence until both succeed
      or the timeout elapses.
    * All mutating actions (``click``, ``set_text``, …) retry on transient
      ``CommandError`` up to *retry_count* times.
    * ``AttachError`` / ``ProcessNotFoundError`` are non-retryable and raise
      ``ReplayAssertionError`` immediately.

    Coordinates
    -----------
    Descriptors containing only coordinates (``x``, ``y``) are blocked unless
    ``allow_coordinates=True`` is set in the repository entry.  Repository
    descriptors should identify elements by ``path`` / ``name`` / ``elementid``.

    Failure messages
    ----------------
    Every ``ReplayAssertionError`` carries: action, ``element_ref``,
    ``surface=java_forms``, descriptor identity, and (where applicable) the
    number of attempts made.

    snapshot()
    ----------
    Returns a human-readable text hierarchy of the current Forms DOM.  Called
    by ``BundleWriter`` on failure when ``java_backend`` is available.
    """

    def __init__(
        self,
        driver_or_factory: Any,
        *,
        retry_count: int = 2,
        retry_delay_ms: int = 500,
        element_factory: Callable[..., Any] | None = None,
    ) -> None:
        if callable(driver_or_factory) and not hasattr(driver_or_factory, "click"):
            self._factory: Callable[[], Any] = driver_or_factory
            self._driver: Any = None
        else:
            self._factory = lambda: driver_or_factory
            self._driver = driver_or_factory
        self._retry_count = max(0, retry_count)
        self._retry_delay_s = retry_delay_ms / 1000
        # Optional element factory for testing — replaces JavaAgentElement construction.
        self._element_factory = element_factory

    # ── Driver and element helpers ─────────────────────────────────────────────

    def _get_driver(self) -> Any:
        if self._driver is None:
            self._driver = self._factory()
        return self._driver

    def _element(self, descriptor: dict) -> Any:
        if self._element_factory is not None:
            return self._element_factory(self._get_driver(), descriptor)
        from qcs_replay.java_agent import JavaAgentElement  # noqa: PLC0415
        return JavaAgentElement(self._get_driver(), descriptor)

    # ── Coordinate guard ───────────────────────────────────────────────────────

    @staticmethod
    def _no_coordinates_or_raise(ref: str, descriptor: dict, action: str) -> None:
        """Block coordinate-only element access unless explicitly permitted."""
        java = descriptor.get("java") or {}
        has_path = bool(
            java.get("path")
            or descriptor.get("path")
            or descriptor.get("xpath")
        )
        has_name = bool(descriptor.get("name") or descriptor.get("elementid"))
        if has_path or has_name:
            return  # Proper identifying data present.
        has_coords = descriptor.get("x") is not None and descriptor.get("y") is not None
        if has_coords and not descriptor.get("allow_coordinates"):
            raise ReplayAssertionError(
                f"java_forms.{action}: element_ref={ref!r} descriptor contains only "
                f"coordinates (x={descriptor.get('x')}, y={descriptor.get('y')}) "
                f"and allow_coordinates is not set. "
                f"Add path/name/elementid to the repository entry, or set "
                f"allow_coordinates=True to permit coordinate-based actions."
            )

    # ── Retry wrapper ──────────────────────────────────────────────────────────

    def _run_with_retry(
        self,
        action_fn: Callable[[], Any],
        *,
        ref: str,
        action: str,
        descriptor: dict,
    ) -> Any:
        """Execute *action_fn* with retry on transient ``CommandError``.

        * ``AttachError`` / ``ProcessNotFoundError`` — non-retryable; raises immediately.
        * ``CommandError`` — retried up to ``retry_count`` times.
        * ``ReplayError`` — re-raised unchanged (already structured).
        * Anything else — wrapped as ``ReplayAssertionError`` with context.
        """
        from qcs_java_agent.exceptions import AttachError, CommandError, ProcessNotFoundError  # noqa: PLC0415
        desc_id = _descriptor_id(descriptor)
        for attempt in range(self._retry_count + 1):
            try:
                return action_fn()
            except ReplayError:
                raise
            except (AttachError, ProcessNotFoundError) as exc:
                raise ReplayAssertionError(
                    f"java_forms.{action}: Java process not available "
                    f"element_ref={ref!r} surface=java_forms "
                    f"descriptor={desc_id!r}: {exc}"
                ) from exc
            except CommandError as exc:
                if attempt < self._retry_count:
                    time.sleep(self._retry_delay_s)
                    continue
                raise ReplayAssertionError(
                    f"java_forms.{action}: command failed after {attempt + 1} attempt(s) "
                    f"element_ref={ref!r} surface=java_forms "
                    f"descriptor={desc_id!r}: {exc}"
                ) from exc
            except Exception as exc:
                raise ReplayAssertionError(
                    f"java_forms.{action} failed: element_ref={ref!r} surface=java_forms "
                    f"descriptor={desc_id!r}: {exc}"
                ) from exc

    # ── Hierarchy snapshot (called by BundleWriter on failure) ─────────────────

    def snapshot(self) -> str:
        """Return a text hierarchy snapshot of the current Forms DOM.

        Returns an error string rather than raising so that ``BundleWriter``
        does not fail trying to capture diagnostic context.
        """
        from qcs_java_agent import java_elements_to_ai_snapshot, java_nodes_to_repo_elements  # noqa: PLC0415
        try:
            scan = self._get_driver().scan()
            elements = java_nodes_to_repo_elements(scan)
            return java_elements_to_ai_snapshot(elements)
        except Exception as exc:  # noqa: BLE001
            return f"[java_forms snapshot unavailable: {exc}]"

    # ── ReplayBackend implementation ───────────────────────────────────────────

    def click(self, ref: str, descriptor: dict) -> None:
        self._no_coordinates_or_raise(ref, descriptor, "click")
        self._run_with_retry(
            lambda: self._element(descriptor).click(simulate=True),
            ref=ref, action="click", descriptor=descriptor,
        )

    def double_click(self, ref: str, descriptor: dict) -> None:
        self._no_coordinates_or_raise(ref, descriptor, "double_click")
        # Oracle Forms has no dedicated double-click; issue two consecutive clicks.
        def _two_clicks() -> None:
            elem = self._element(descriptor)
            elem.click(simulate=True)
            elem.click(simulate=True)
        self._run_with_retry(_two_clicks, ref=ref, action="double_click", descriptor=descriptor)

    def set_text(self, ref: str, descriptor: dict, value: str) -> None:
        self._no_coordinates_or_raise(ref, descriptor, "set_text")
        self._run_with_retry(
            lambda: self._element(descriptor).send_text(str(value), simulate=True),
            ref=ref, action="set_text", descriptor=descriptor,
        )

    def select_value(self, ref: str, descriptor: dict, value: str) -> None:
        # Oracle Forms LOV/ComboBox: selection is performed by typing the value.
        self._no_coordinates_or_raise(ref, descriptor, "select_value")
        self._run_with_retry(
            lambda: self._element(descriptor).send_text(str(value), simulate=True),
            ref=ref, action="select_value", descriptor=descriptor,
        )

    def press_key(self, key: str) -> None:
        from qcs_java_agent.exceptions import AttachError, CommandError, ProcessNotFoundError  # noqa: PLC0415
        try:
            self._get_driver().press_key(key)
        except (AttachError, ProcessNotFoundError) as exc:
            raise ReplayAssertionError(
                f"java_forms.press_key: Java process not available "
                f"key={key!r} surface=java_forms: {exc}"
            ) from exc
        except CommandError as exc:
            raise ReplayAssertionError(
                f"java_forms.press_key: command failed key={key!r} surface=java_forms: {exc}"
            ) from exc
        except ReplayError:
            raise
        except Exception as exc:
            raise ReplayAssertionError(
                f"java_forms.press_key failed: key={key!r} surface=java_forms: {exc}"
            ) from exc

    def wait_for(self, ref: str, descriptor: dict, timeout_ms: int = 10_000) -> None:
        """Wait until the element is present in a responsive Forms session.

        Readiness sequence:

        1. Verify the driver is alive (``health()``).
        2. Poll: scan the DOM, capture readiness state for diagnostics, then
           check element existence regardless of readiness.  The element may
           be present even during brief transitional Forms states.

        Raises ``ReplayAssertionError`` on timeout or if the driver is dead.
        """
        from qcs_java_agent import analyze_forms_readiness  # noqa: PLC0415
        from qcs_java_agent.exceptions import AttachError, CommandError, ProcessNotFoundError  # noqa: PLC0415
        desc_id = _descriptor_id(descriptor)

        # Step 1: driver alive.
        try:
            self._get_driver().health()
        except (AttachError, ProcessNotFoundError, CommandError, RuntimeError) as exc:
            raise ReplayAssertionError(
                f"java_forms.wait_for: Java agent not available "
                f"element_ref={ref!r} surface=java_forms descriptor={desc_id!r}: {exc}"
            ) from exc

        # Step 2: poll until element found.
        deadline = time.monotonic() + timeout_ms / 1000
        last_readiness = "unknown"
        while time.monotonic() < deadline:
            try:
                scan = self._get_driver().scan()
                last_readiness = analyze_forms_readiness(scan).reason
            except (CommandError, RuntimeError):
                pass
            # Check element regardless of readiness — element may be present
            # during transitional states.
            try:
                if self._element(descriptor).get_element_information().get("_found"):
                    return
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.25)

        raise ReplayAssertionError(
            f"java_forms.wait_for timed out: element_ref={ref!r} surface=java_forms "
            f"descriptor={desc_id!r} after {timeout_ms}ms "
            f"last_readiness={last_readiness!r}"
        )

    def assert_visible(self, ref: str, descriptor: dict) -> None:
        desc_id = _descriptor_id(descriptor)
        info = self._element(descriptor).get_element_information()
        if not info.get("_found"):
            raise ReplayAssertionError(
                f"assert_visible failed: element_ref={ref!r} surface=java_forms "
                f"descriptor={desc_id!r} — element not found in current Forms DOM"
            )
        if info.get("showing") is False:
            raise ReplayAssertionError(
                f"assert_visible failed: element_ref={ref!r} surface=java_forms "
                f"descriptor={desc_id!r} — element found but not showing"
            )

    def get_text(self, ref: str, descriptor: dict) -> str:
        info = self._element(descriptor).get_element_information()
        return str(info.get("text") or "")

    def get_value(self, ref: str, descriptor: dict) -> str:
        info = self._element(descriptor).get_element_information()
        return str(info.get("text") or info.get("value") or "")


# ─── FormReplay ──────────────────────────────────────────────────────────────────────


@dataclass
class FormReplay:
    """Business-facing handle for one Oracle form or page (primary DSL API).

    Created by ``OracleReplay.form(form_ref)`` and passed around in generated
    tests.  All action methods route through the resolver and the appropriate
    backend; no AI is involved.
    """

    form_ref: str
    resolver: RepositoryResolver
    browser_backend: ReplayBackend | None
    java_backend: ReplayBackend | None
    logger: ReplayLogger
    # Optional hook called on any action failure.  Signature:
    #   hook(exc, *, action, element_ref, resolved) -> None
    # Injected by OracleReplay when a BundleWriter is configured.
    failure_hook: Callable[..., None] | None = None

    # ── internal helpers ───────────────────────────────────────────────────────

    def _backend_for(self, surface: str) -> ReplayBackend | None:
        if surface == "browser":
            return self.browser_backend
        if surface == "java_forms":
            return self.java_backend
        return None

    def _resolve_required(self, element_ref: str) -> tuple[ResolvedTarget, ReplayBackend]:
        resolved = self.resolver.resolve(self.form_ref, element_ref)  # raises if not found
        backend = self._backend_for(resolved.surface)
        if backend is None:
            raise ReplayRoutingError(
                f"no backend for surface={resolved.surface!r} "
                f"form_ref={self.form_ref!r} element_ref={element_ref!r}"
            )
        return resolved, backend

    def _run(self, action: str, element_ref: str, *args: Any, **kwargs: Any) -> None:
        resolved: ResolvedTarget | None = None
        try:
            resolved, backend = self._resolve_required(element_ref)
        except Exception as exc:
            if self.failure_hook is not None:
                self.failure_hook(exc, action=action, element_ref=element_ref, resolved=None)
            raise
        ref_str = f"{self.form_ref}.{element_ref}"
        seq = self.logger.start(action, ref_str, resolved.surface)
        t0 = time.monotonic()
        try:
            getattr(backend, action)(element_ref, resolved.descriptor, *args, **kwargs)
            self.logger.ok(seq, elapsed_s=time.monotonic() - t0)
        except ReplayError as exc:
            # Already a structured replay error — log and re-raise without wrapping.
            self.logger.fail(seq, str(exc))
            if self.failure_hook is not None:
                self.failure_hook(exc, action=action, element_ref=element_ref, resolved=resolved)
            raise
        except Exception as exc:
            # Unexpected backend exception — wrap with full step context.
            self.logger.fail(seq, str(exc))
            if self.failure_hook is not None:
                self.failure_hook(exc, action=action, element_ref=element_ref, resolved=resolved)
            raise ReplayError(
                f"action={action!r} form_ref={self.form_ref!r} "
                f"element_ref={element_ref!r} surface={resolved.surface!r}: {exc}"
            ) from exc

    # ── public API ─────────────────────────────────────────────────────────────

    def step(self, description: str) -> None:
        """Log a human-readable step description (no backend call)."""
        _log.info("[Replay step] form=%r: %s", self.form_ref, description)

    def click(self, element_ref: str) -> None:
        self._run("click", element_ref)

    def double_click(self, element_ref: str) -> None:
        self._run("double_click", element_ref)

    def set_text(self, element_ref: str, value: str) -> None:
        self._run("set_text", element_ref, value)

    def select_value(self, element_ref: str, value: str) -> None:
        self._run("select_value", element_ref, value)

    def press_key(self, key: str) -> None:
        ref_str = f"{self.form_ref}.(key)"
        backend = self.java_backend or self.browser_backend
        if backend is None:
            raise ReplayRoutingError("press_key: no backend available")
        surface = "java_forms" if self.java_backend is not None else "browser"
        seq = self.logger.start("press_key", ref_str, surface)
        t0 = time.monotonic()
        try:
            backend.press_key(key)
            self.logger.ok(seq, elapsed_s=time.monotonic() - t0)
        except Exception as exc:
            self.logger.fail(seq, str(exc))
            raise

    def wait_for(self, element_ref: str) -> None:
        self._run("wait_for", element_ref)

    def assert_visible(self, element_ref: str) -> None:
        self._run("assert_visible", element_ref)

    def assert_value(self, element_ref: str, expected: str) -> None:
        resolved, backend = self._resolve_required(element_ref)
        actual = backend.get_value(element_ref, resolved.descriptor)
        if actual != expected:
            raise ReplayAssertionError(
                f"assert_value: form={self.form_ref!r} element={element_ref!r} "
                f"expected={expected!r} actual={actual!r}"
            )

    def assert_text(self, element_ref: str, expected: str) -> None:
        resolved, backend = self._resolve_required(element_ref)
        actual = backend.get_text(element_ref, resolved.descriptor)
        if actual != expected:
            raise ReplayAssertionError(
                f"assert_text: form={self.form_ref!r} element={element_ref!r} "
                f"expected={expected!r} actual={actual!r}"
            )

    def get_text(self, element_ref: str) -> str:
        """Return the visible text of *element_ref* without asserting."""
        resolved, backend = self._resolve_required(element_ref)
        return backend.get_text(element_ref, resolved.descriptor)

    def get_value(self, element_ref: str) -> str:
        """Return the current input value of *element_ref* without asserting."""
        resolved, backend = self._resolve_required(element_ref)
        return backend.get_value(element_ref, resolved.descriptor)


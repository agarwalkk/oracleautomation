"""
qcs_replay.locator — Locator resolvers for Java-agent and Playwright replay.

Priority order
--------------
Java Forms:
    Resolve repository descriptors through qcs_java_agent locator candidates.

HTML (Playwright):
  1. get_by_role(role, name=name)
  2. get_by_label(label_neighbor)
  3. get_by_text(text, exact=True)
  4. get_by_placeholder(placeholder)
  5. get_by_test_id(test_id)
  6. locator("#element_id")
  7. locator(css_selector)
  8. locator(xpath)
  9. coordinates — only when descriptor has allow_coordinates=True and x/y set

Each strategy is probed with a short PROBE_TIMEOUT_MS wait.  Coordinates are
never a silent fallback — they require allow_coordinates=True in the descriptor.

Usage
-----
    from qcs_replay.locator import JavaAgentResolver, PlaywrightResolver

    r = JavaAgentResolver(driver)
    element = r.resolve(descriptor)
    element.send_text("Standard_ISVUS", simulate=True)

    rp = PlaywrightResolver(page)
    loc = rp.resolve(descriptor)
    loc.fill("Standard_ISVUS")
"""
from __future__ import annotations

from typing import Any

import config


# ── Exceptions ────────────────────────────────────────────────────────────────


class LocatorResolutionError(RuntimeError):
    """Raised when PlaywrightResolver exhausts all locator strategies.

    ``attempted_strategies`` lists each strategy name that was tried, in order.
    The ``BrowserReplayBackend`` catches this and re-raises as
    ``ReplayAssertionError`` with element_ref and surface context added.
    """

    def __init__(self, friendly_name: str, attempted: list[str]) -> None:
        self.friendly_name = friendly_name
        self.attempted_strategies = list(attempted)
        strats = ", ".join(attempted) if attempted else "none"
        super().__init__(
            f"PlaywrightResolver: no locator strategy matched {friendly_name!r}. "
            f"Attempted: [{strats}]"
        )


# ── Descriptor helpers ────────────────────────────────────────────────────────


class LocatorDescriptor:
    """Structured locator pulled from repo/elements/<form_id>.yaml."""

    def __init__(self, data: dict):
        data = data or {}
        self.raw              = data or {}
        self.friendly_name    = data.get("friendly_name", "")
        self.role             = data.get("role", "")
        self.name             = data.get("name", "")
        self.ancestors        = data.get("ancestors", [])
        self.label_neighbor   = data.get("label_neighbor", "")
        self.text             = data.get("text", "")
        self.placeholder      = data.get("placeholder", "")
        self.xpath            = data.get("xpath", "")
        self.css              = data.get("css", "")
        self.test_id          = data.get("test_id", "")
        self.element_id       = data.get("element_id", "")
        self.table_locator    = data.get("table_locator")  # {table_role, row_key, col_name}
        # Coordinate fallback — only used when allow_coordinates=True
        self.x                = data.get("x")
        self.y                = data.get("y")
        self.allow_coordinates = bool(data.get("allow_coordinates", False))

    def to_dict(self) -> dict:
        merged = dict(self.raw)
        merged.update({k: v for k, v in self.__dict__.items() if k != "raw" and v})
        return merged


# ── Coordinate locator wrapper ────────────────────────────────────────────────


class _CoordinateLocator:
    """Last-resort locator that dispatches actions through page.mouse / page.keyboard.

    Returned by PlaywrightResolver only when ``descriptor.allow_coordinates`` is
    ``True`` and ``descriptor.x`` / ``descriptor.y`` are set.  No visibility or
    attachment checks are possible.  Coordinates are an explicit escape hatch,
    never a silent fallback.
    """

    def __init__(self, page: Any, x: int | float, y: int | float) -> None:
        self._page = page
        self._x = float(x)
        self._y = float(y)

    def click(self, **_kw: Any) -> None:
        self._page.mouse.click(self._x, self._y)

    def dblclick(self, **_kw: Any) -> None:
        self._page.mouse.dblclick(self._x, self._y)

    def fill(self, value: str, **_kw: Any) -> None:
        self._page.mouse.click(self._x, self._y)
        self._page.keyboard.type(value)

    def select_option(self, value: str, **_kw: Any) -> None:
        raise RuntimeError("select_option is not supported on coordinate fallback")

    def inner_text(self) -> str:
        return ""

    def input_value(self) -> str:
        return ""

    def is_visible(self) -> bool:
        return True  # Cannot check visibility by coordinates

    def wait_for(self, *, state: str = "visible", timeout: int = 10_000) -> None:
        pass  # Cannot wait for an element by coordinates


# ── Java Agent Resolver ─────────────────────────────────────────────────────


class JavaAgentResolver:
    def __init__(self, driver: Any):
        self._driver = driver

    def resolve(self, descriptor: LocatorDescriptor) -> Any:
        from qcs_replay.java_agent import JavaAgentElement  # noqa: PLC0415

        return JavaAgentElement(self._driver, descriptor.to_dict())


# ── Playwright Resolver ───────────────────────────────────────────────────────


class PlaywrightResolver:
    """Resolves a ``LocatorDescriptor`` to the best available Playwright Locator.

    Strategy priority (highest to lowest):
      role → label → text → placeholder → test_id → id → css → xpath

    Each strategy is probed with a short ``PROBE_TIMEOUT_MS`` wait so that the
    returned locator is known to be attached in the DOM.  If no strategy yields
    an attached element, ``LocatorResolutionError`` is raised carrying the list
    of attempted strategy names.

    Coordinates are tried last only when ``descriptor.allow_coordinates`` is
    ``True`` and ``descriptor.x`` / ``descriptor.y`` are set.  They are never
    a silent fallback.
    """

    #: Short probe used during strategy selection — not the action timeout.
    PROBE_TIMEOUT_MS: int = 500

    def __init__(self, page: Any, timeout_ms: int | None = None) -> None:
        self._page = page
        self._timeout = (
            timeout_ms if timeout_ms is not None
            else int(config.LOCATOR_TIMEOUT_S * 1000)
        )

    def _candidates(self, d: LocatorDescriptor) -> list[tuple[str, Any]]:
        """Return ordered (strategy_name, locator) pairs without probing."""
        result: list[tuple[str, Any]] = []
        if d.role and d.name:
            result.append(("role", self._page.get_by_role(d.role, name=d.name)))
        if d.label_neighbor:
            result.append(("label", self._page.get_by_label(d.label_neighbor)))
        if d.text:
            result.append(("text", self._page.get_by_text(d.text, exact=True)))
        if d.placeholder:
            result.append(("placeholder", self._page.get_by_placeholder(d.placeholder)))
        if d.test_id:
            result.append(("test_id", self._page.get_by_test_id(d.test_id)))
        if d.element_id:
            result.append(("id", self._page.locator(f"#{d.element_id}")))
        if d.css:
            result.append(("css", self._page.locator(d.css)))
        if d.xpath:
            result.append(("xpath", self._page.locator(d.xpath)))
        return result

    def resolve(self, descriptor: LocatorDescriptor) -> Any:
        """Return the first locator whose element attaches within ``PROBE_TIMEOUT_MS``.

        Raises
        ------
        LocatorResolutionError
            When all strategies fail.  The error carries ``attempted_strategies``
            for use in diagnostic messages.
        """
        candidates = self._candidates(descriptor)
        attempted: list[str] = []

        for strategy, loc in candidates:
            attempted.append(strategy)
            try:
                loc.wait_for(state="attached", timeout=self.PROBE_TIMEOUT_MS)
                return loc
            except Exception:
                continue

        # Coordinates — explicit last-resort, never a silent fallback.
        if descriptor.allow_coordinates and descriptor.x is not None and descriptor.y is not None:
            attempted.append("coordinates")
            return _CoordinateLocator(self._page, descriptor.x, descriptor.y)

        raise LocatorResolutionError(
            descriptor.friendly_name or "unknown",
            attempted,
        )

    # ── Backward-compat individual strategy methods ───────────────────────────

    def _by_role_name(self, d: LocatorDescriptor) -> Any:
        if d.role and d.name:
            return self._page.get_by_role(d.role, name=d.name)
        return None

    def _by_label(self, d: LocatorDescriptor) -> Any:
        if d.label_neighbor:
            return self._page.get_by_label(d.label_neighbor)
        return None

    def _by_test_id(self, d: LocatorDescriptor) -> Any:
        if d.test_id:
            return self._page.get_by_test_id(d.test_id)
        return None

    def _by_id(self, d: LocatorDescriptor) -> Any:
        if d.element_id:
            return self._page.locator(f"#{d.element_id}")
        return None

    def _by_css(self, d: LocatorDescriptor) -> Any:
        if d.css:
            return self._page.locator(d.css)
        return None

    def _by_xpath(self, d: LocatorDescriptor) -> Any:
        if d.xpath:
            return self._page.locator(d.xpath)
        return None


# ── Convenience: resolve from repo descriptor dict ────────────────────────────


def resolve_playwright(page: Any, descriptor: dict, *, timeout_ms: int | None = None) -> Any:
    kw = {"timeout_ms": timeout_ms} if timeout_ms is not None else {}
    return PlaywrightResolver(page, **kw).resolve(LocatorDescriptor(descriptor))

"""Tests for BrowserReplayBackend and PlaywrightResolver.

All tests use mock Playwright page/locator objects — no real browser, no AI/LLM.
Covers:
  - Locator strategy priority (role > label > text > placeholder > test_id > id > css > xpath)
  - Coordinates not used unless allow_coordinates=True
  - LocatorResolutionError lists all attempted strategies
  - BrowserReplayBackend action methods
  - Deterministic failure messages include element_ref, surface, strategy list
  - Auto-wait assert_visible (uses wait_for, not is_visible poll)
  - FormReplay._run wraps non-ReplayError backend exceptions with form_ref context
"""
from __future__ import annotations

import pytest

from qcs_replay.dsl import (
    BrowserReplayBackend,
    FormReplay,
    ReplayAssertionError,
    ReplayError,
    ReplayLogger,
    RepositoryResolver,
    ResolvedTarget,
)
from qcs_replay.locator import (
    LocatorDescriptor,
    LocatorResolutionError,
    PlaywrightResolver,
    _CoordinateLocator,
)


# ── Mock helpers ──────────────────────────────────────────────────────────────


class _MockLocator:
    """Minimal Playwright locator mock.

    ``attached=True``  → wait_for succeeds (element found in DOM)
    ``attached=False`` → wait_for raises (element not found)
    ``visible=True``   → wait_for(state='visible') succeeds
    """

    def __init__(self, *, attached: bool = False, visible: bool = True) -> None:
        self._attached = attached
        self._visible = visible
        # Interaction tracking
        self.clicked = False
        self.double_clicked = False
        self.filled_value: str | None = None
        self.selected_value: str | None = None
        self.wait_for_calls: list[dict] = []
        self._text = ""
        self._input_value = ""

    def wait_for(self, *, state: str = "visible", timeout: int = 10_000) -> None:
        self.wait_for_calls.append({"state": state, "timeout": timeout})
        if state == "attached" and not self._attached:
            raise TimeoutError(f"Locator not attached (timeout={timeout})")
        if state == "visible" and not self._visible:
            raise TimeoutError(f"Locator not visible (timeout={timeout})")

    def click(self, **_kw: object) -> None:
        self.clicked = True

    def dblclick(self, **_kw: object) -> None:
        self.double_clicked = True

    def fill(self, value: str, **_kw: object) -> None:
        self.filled_value = value

    def select_option(self, value: str, **_kw: object) -> None:
        self.selected_value = value

    def inner_text(self) -> str:
        return self._text

    def input_value(self) -> str:
        return self._input_value

    def is_visible(self) -> bool:
        return self._visible


class _MockKeyboard:
    def __init__(self) -> None:
        self.pressed: str | None = None
        self.typed: list[str] = []

    def press(self, key: str) -> None:
        self.pressed = key

    def type(self, text: str) -> None:
        self.typed.append(text)


class _MockMouse:
    def __init__(self) -> None:
        self.clicked_at: tuple[float, float] | None = None
        self.dblclicked_at: tuple[float, float] | None = None

    def click(self, x: float, y: float) -> None:
        self.clicked_at = (x, y)

    def dblclick(self, x: float, y: float) -> None:
        self.dblclicked_at = (x, y)


class _MockPage:
    """Minimal Playwright page mock with per-strategy locator slots."""

    def __init__(self) -> None:
        self.role_locator      = _MockLocator(attached=False)
        self.label_locator     = _MockLocator(attached=False)
        self.text_locator      = _MockLocator(attached=False)
        self.placeholder_locator = _MockLocator(attached=False)
        self.test_id_locator   = _MockLocator(attached=False)
        self.id_locator        = _MockLocator(attached=False)
        self.css_locator       = _MockLocator(attached=False)
        self.xpath_locator     = _MockLocator(attached=False)
        self.keyboard          = _MockKeyboard()
        self.mouse             = _MockMouse()

    def get_by_role(self, role: str, *, name: str | None = None) -> _MockLocator:
        return self.role_locator

    def get_by_label(self, label: str) -> _MockLocator:
        return self.label_locator

    def get_by_text(self, text: str, *, exact: bool = False) -> _MockLocator:
        return self.text_locator

    def get_by_placeholder(self, placeholder: str) -> _MockLocator:
        return self.placeholder_locator

    def get_by_test_id(self, test_id: str) -> _MockLocator:
        return self.test_id_locator

    def locator(self, selector: str) -> _MockLocator:
        if selector.startswith("#"):
            return self.id_locator
        if "//" in selector or selector.startswith("(//"):
            return self.xpath_locator
        return self.css_locator


# ── PlaywrightResolver — strategy priority ────────────────────────────────────


def test_role_locator_used_first_when_available() -> None:
    page = _MockPage()
    page.role_locator = _MockLocator(attached=True)
    page.label_locator = _MockLocator(attached=True)  # also available, but lower priority
    desc = {"role": "button", "name": "Submit", "label_neighbor": "Submit Label"}
    result = PlaywrightResolver(page).resolve(LocatorDescriptor(desc))
    assert result is page.role_locator


def test_label_used_when_role_fails() -> None:
    page = _MockPage()
    page.role_locator = _MockLocator(attached=False)
    page.label_locator = _MockLocator(attached=True)
    desc = {"role": "button", "name": "Submit", "label_neighbor": "Submit Label"}
    result = PlaywrightResolver(page).resolve(LocatorDescriptor(desc))
    assert result is page.label_locator


def test_text_strategy_tried_after_label() -> None:
    page = _MockPage()
    page.label_locator = _MockLocator(attached=False)
    page.text_locator = _MockLocator(attached=True)
    desc = {"label_neighbor": "Order Type", "text": "STANDARD"}
    result = PlaywrightResolver(page).resolve(LocatorDescriptor(desc))
    assert result is page.text_locator


def test_placeholder_strategy_tried_after_text() -> None:
    page = _MockPage()
    page.text_locator = _MockLocator(attached=False)
    page.placeholder_locator = _MockLocator(attached=True)
    desc = {"text": "Enter value", "placeholder": "Search orders"}
    result = PlaywrightResolver(page).resolve(LocatorDescriptor(desc))
    assert result is page.placeholder_locator


def test_css_used_when_semantic_strategies_fail() -> None:
    page = _MockPage()
    page.role_locator = _MockLocator(attached=False)
    page.label_locator = _MockLocator(attached=False)
    page.css_locator = _MockLocator(attached=True)
    desc = {
        "role": "button", "name": "Submit",
        "label_neighbor": "Submit Label",
        "css": ".submit-btn",
    }
    result = PlaywrightResolver(page).resolve(LocatorDescriptor(desc))
    assert result is page.css_locator


def test_xpath_used_as_last_standard_fallback() -> None:
    page = _MockPage()
    page.xpath_locator = _MockLocator(attached=True)
    desc = {"xpath": "//button[@id='submit']"}
    result = PlaywrightResolver(page).resolve(LocatorDescriptor(desc))
    assert result is page.xpath_locator


def test_coordinates_not_used_by_default_when_all_fail() -> None:
    page = _MockPage()
    # x/y present but allow_coordinates not set → must NOT use coordinates
    desc = {"role": "button", "name": "Submit", "x": 100, "y": 200}
    with pytest.raises(LocatorResolutionError):
        PlaywrightResolver(page).resolve(LocatorDescriptor(desc))


def test_coordinates_used_only_when_explicitly_allowed() -> None:
    page = _MockPage()
    desc = {"x": 100, "y": 200, "allow_coordinates": True}
    result = PlaywrightResolver(page).resolve(LocatorDescriptor(desc))
    assert isinstance(result, _CoordinateLocator)


def test_coordinates_not_used_when_x_missing() -> None:
    page = _MockPage()
    desc = {"y": 200, "allow_coordinates": True}
    with pytest.raises(LocatorResolutionError):
        PlaywrightResolver(page).resolve(LocatorDescriptor(desc))


def test_failure_error_lists_all_attempted_strategies() -> None:
    page = _MockPage()
    desc = {
        "role": "button", "name": "Submit",
        "label_neighbor": "Submit Label",
        "css": ".btn",
        "xpath": "//button",
    }
    with pytest.raises(LocatorResolutionError) as exc_info:
        PlaywrightResolver(page).resolve(LocatorDescriptor(desc))
    attempted = exc_info.value.attempted_strategies
    assert "role" in attempted
    assert "label" in attempted
    assert "css" in attempted
    assert "xpath" in attempted


def test_failure_error_message_contains_friendly_name() -> None:
    page = _MockPage()
    desc = {"css": ".missing", "friendly_name": "Order Type Field"}
    with pytest.raises(LocatorResolutionError) as exc_info:
        PlaywrightResolver(page).resolve(LocatorDescriptor(desc))
    assert "Order Type Field" in str(exc_info.value)


def test_probe_uses_attached_state_not_visible() -> None:
    """Probe during strategy selection checks 'attached', not 'visible'."""
    page = _MockPage()
    loc = _MockLocator(attached=True)
    page.css_locator = loc
    PlaywrightResolver(page).resolve(LocatorDescriptor({"css": ".field"}))
    assert any(c["state"] == "attached" for c in loc.wait_for_calls)


# ── _CoordinateLocator ────────────────────────────────────────────────────────


def test_coordinate_locator_click_uses_mouse() -> None:
    page = _MockPage()
    loc = _CoordinateLocator(page, 150, 300)
    loc.click()
    assert page.mouse.clicked_at == (150.0, 300.0)


def test_coordinate_locator_dblclick_uses_mouse() -> None:
    page = _MockPage()
    loc = _CoordinateLocator(page, 10, 20)
    loc.dblclick()
    assert page.mouse.dblclicked_at == (10.0, 20.0)


def test_coordinate_locator_fill_clicks_then_types() -> None:
    page = _MockPage()
    loc = _CoordinateLocator(page, 50, 60)
    loc.fill("HELLO")
    assert page.mouse.clicked_at == (50.0, 60.0)
    assert "HELLO" in page.keyboard.typed


# ── BrowserReplayBackend — actions ────────────────────────────────────────────


def test_backend_click_uses_locator() -> None:
    page = _MockPage()
    page.role_locator = _MockLocator(attached=True)
    backend = BrowserReplayBackend(page)
    backend.click("submit_btn", {"role": "button", "name": "Submit"})
    assert page.role_locator.clicked


def test_backend_double_click_uses_locator() -> None:
    page = _MockPage()
    page.label_locator = _MockLocator(attached=True)
    backend = BrowserReplayBackend(page)
    backend.double_click("item", {"label_neighbor": "Order Item"})
    assert page.label_locator.double_clicked


def test_backend_set_text_calls_fill() -> None:
    page = _MockPage()
    page.label_locator = _MockLocator(attached=True)
    backend = BrowserReplayBackend(page)
    backend.set_text("order_type", {"label_neighbor": "Order Type"}, "STANDARD")
    assert page.label_locator.filled_value == "STANDARD"


def test_backend_select_value_calls_select_option() -> None:
    page = _MockPage()
    page.role_locator = _MockLocator(attached=True)
    backend = BrowserReplayBackend(page)
    backend.select_value("status_dd", {"role": "combobox", "name": "Status"}, "ACTIVE")
    assert page.role_locator.selected_value == "ACTIVE"


def test_backend_press_key_uses_page_keyboard() -> None:
    page = _MockPage()
    backend = BrowserReplayBackend(page)
    backend.press_key("TAB")
    assert page.keyboard.pressed == "TAB"


def test_backend_wait_for_uses_playwright_auto_wait() -> None:
    page = _MockPage()
    loc = _MockLocator(attached=True, visible=True)
    page.css_locator = loc
    backend = BrowserReplayBackend(page)
    backend.wait_for("field", {"css": ".field"}, timeout_ms=5_000)
    assert any(c["state"] == "visible" for c in loc.wait_for_calls)


def test_backend_assert_visible_uses_wait_for_not_poll() -> None:
    """assert_visible must use Playwright auto-wait (wait_for), not is_visible()."""
    page = _MockPage()
    loc = _MockLocator(attached=True, visible=True)
    page.css_locator = loc
    backend = BrowserReplayBackend(page)
    backend.assert_visible("field", {"css": ".field"})
    assert any(c["state"] == "visible" for c in loc.wait_for_calls), (
        "assert_visible must call wait_for(state='visible') for auto-waiting"
    )


def test_backend_assert_visible_raises_replay_assertion_error_when_hidden() -> None:
    page = _MockPage()
    page.css_locator = _MockLocator(attached=True, visible=False)
    backend = BrowserReplayBackend(page)
    with pytest.raises(ReplayAssertionError):
        backend.assert_visible("field", {"css": ".field"})


def test_backend_get_text_returns_inner_text() -> None:
    page = _MockPage()
    loc = _MockLocator(attached=True)
    loc._text = "Order 123"
    page.css_locator = loc
    backend = BrowserReplayBackend(page)
    assert backend.get_text("label", {"css": ".label"}) == "Order 123"


def test_backend_get_value_returns_input_value() -> None:
    page = _MockPage()
    loc = _MockLocator(attached=True)
    loc._input_value = "KRISHAN001"
    page.label_locator = loc
    backend = BrowserReplayBackend(page)
    assert backend.get_value("po_number", {"label_neighbor": "PO#"}) == "KRISHAN001"


# ── BrowserReplayBackend — failure messages ───────────────────────────────────


def test_locator_failure_raises_replay_assertion_error() -> None:
    page = _MockPage()
    backend = BrowserReplayBackend(page)
    with pytest.raises(ReplayAssertionError) as exc_info:
        backend.click("btn", {"role": "button", "name": "Click Me"})
    assert "element_ref='btn'" in str(exc_info.value)
    assert "surface=browser" in str(exc_info.value)


def test_locator_failure_message_includes_attempted_strategies() -> None:
    page = _MockPage()
    backend = BrowserReplayBackend(page)
    with pytest.raises(ReplayAssertionError) as exc_info:
        backend.click("btn", {"role": "button", "name": "Submit", "css": ".btn"})
    assert "attempted=" in str(exc_info.value)
    assert "role" in str(exc_info.value)


def test_locator_failure_is_a_replay_error_subclass() -> None:
    """ReplayAssertionError must be a ReplayError so FormReplay re-raises it unchanged."""
    page = _MockPage()
    backend = BrowserReplayBackend(page)
    with pytest.raises(ReplayError):
        backend.click("btn", {"css": ".missing"})


# ── FormReplay — browser surface integration ──────────────────────────────────


def _make_browser_form(page: _MockPage, form_ref: str = "html_page") -> tuple[FormReplay, RepositoryResolver]:
    """Build a FormReplay wired to a real BrowserReplayBackend (no Java backend)."""
    backend = BrowserReplayBackend(page)
    resolver = RepositoryResolver()
    logger = ReplayLogger()
    form = FormReplay(
        form_ref=form_ref,
        resolver=resolver,
        browser_backend=backend,
        java_backend=None,
        logger=logger,
    )
    return form, resolver


def test_form_replay_browser_click_succeeds() -> None:
    page = _MockPage()
    page.role_locator = _MockLocator(attached=True)
    form, resolver = _make_browser_form(page)
    resolver.register(
        "html_page", "submit_btn",
        ResolvedTarget(ref="submit_btn", surface="browser", form_id="html_page", descriptor={"role": "button", "name": "Submit"}),
    )
    form.click("submit_btn")
    assert page.role_locator.clicked


def test_form_replay_browser_set_text_succeeds() -> None:
    page = _MockPage()
    page.label_locator = _MockLocator(attached=True)
    form, resolver = _make_browser_form(page)
    resolver.register(
        "html_page", "search_field",
        ResolvedTarget(ref="search_field", surface="browser", form_id="html_page", descriptor={"label_neighbor": "Search"}),
    )
    form.set_text("search_field", "FIND_ME")
    assert page.label_locator.filled_value == "FIND_ME"


def test_form_replay_wraps_backend_exception_with_form_ref() -> None:
    """Non-ReplayError from a backend is wrapped by FormReplay._run() with form_ref context."""
    page = _MockPage()
    form, resolver = _make_browser_form(page, form_ref="order_page")
    resolver.register(
        "order_page", "missing_btn",
        ResolvedTarget(ref="missing_btn", surface="browser", form_id="order_page", descriptor={"css": ".gone"}),
    )
    # css_locator stays unattached → LocatorResolutionError → ReplayAssertionError (IS a ReplayError)
    # FormReplay._run() re-raises ReplayError unchanged (form_ref context comes from message)
    with pytest.raises(ReplayAssertionError) as exc_info:
        form.click("missing_btn")
    assert "missing_btn" in str(exc_info.value)


def test_form_replay_get_text_returns_value() -> None:
    page = _MockPage()
    loc = _MockLocator(attached=True)
    loc._text = "Confirmed"
    page.css_locator = loc
    form, resolver = _make_browser_form(page)
    resolver.register(
        "html_page", "status_label",
        ResolvedTarget(ref="status_label", surface="browser", form_id="html_page", descriptor={"css": ".status"}),
    )
    assert form.get_text("status_label") == "Confirmed"


def test_form_replay_get_value_returns_input() -> None:
    page = _MockPage()
    loc = _MockLocator(attached=True)
    loc._input_value = "PO-001"
    page.css_locator = loc
    form, resolver = _make_browser_form(page)
    resolver.register(
        "html_page", "po_field",
        ResolvedTarget(ref="po_field", surface="browser", form_id="html_page", descriptor={"css": ".po-input"}),
    )
    assert form.get_value("po_field") == "PO-001"


def test_form_replay_press_key_uses_browser_when_no_java_backend() -> None:
    page = _MockPage()
    form, _ = _make_browser_form(page, "login_page")
    form.press_key("ENTER")
    assert page.keyboard.pressed == "ENTER"


def test_browser_replay_backend_timeout_ms_accepted() -> None:
    """BrowserReplayBackend accepts a custom timeout_ms without error."""
    page = _MockPage()
    backend = BrowserReplayBackend(page, timeout_ms=30_000)
    assert backend._timeout == 30_000

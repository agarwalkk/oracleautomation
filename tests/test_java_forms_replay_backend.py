"""Tests for JavaFormsReplayBackend.

All tests use mock driver and element objects — no real JVM, no Playwright, no AI/LLM.

Covers:
  - click / double_click / set_text / select_value / press_key route to Java element
  - get_text / get_value read element information
  - assert_visible passes when found, raises when not found or not showing
  - wait_for calls health() readiness check, polls element, times out with context
  - transient CommandError is retried; non-retryable errors raise immediately
  - coordinates blocked unless allow_coordinates=True in descriptor
  - path/name/elementid descriptors bypass coordinate guard
  - snapshot() returns DOM text or safe error string on failure
  - failure messages include element_ref, surface=java_forms, descriptor identity
  - FormReplay integration: set_text, assert_value, get_text via Java backend
"""
from __future__ import annotations

import pytest

from qcs_replay.dsl import (
    FormReplay,
    JavaFormsReplayBackend,
    ReplayAssertionError,
    ReplayLogger,
    RepositoryResolver,
    ResolvedTarget,
)


# ── Mock helpers ──────────────────────────────────────────────────────────────


class _MockElement:
    """Mock JavaAgentElement."""

    def __init__(
        self,
        *,
        found: bool = True,
        text: str = "",
        value: str = "",
        showing: bool = True,
        click_raises: Exception | None = None,
        send_text_raises: Exception | None = None,
    ) -> None:
        self._info: dict = {
            "_found": found,
            "text": text,
            "value": value,
            "showing": showing,
        }
        self._click_raises = click_raises
        self._send_text_raises = send_text_raises
        self.click_count: int = 0
        self.double_click_count: int = 0
        self.sent_texts: list[str] = []
        self.checked_state: bool | None = None
        self.tree_expanded: bool | None = None
        self.tab_index: int | None = None
        self.tab_title: str | None = None

    def click(self, simulate: bool = True) -> dict:
        if self._click_raises:
            raise self._click_raises
        self.click_count += 1
        return {}

    def send_text(self, text: str, simulate: bool = True) -> dict:
        if self._send_text_raises:
            raise self._send_text_raises
        self.sent_texts.append(text)
        return {}

    def double_click(self) -> dict:
        self.double_click_count += 1
        return {}

    def select_option(self, value: str) -> dict:
        self.sent_texts.append(value)
        return {}

    def set_check(self, checked: bool) -> dict:
        self.checked_state = checked
        return {}

    def expand_tree(self, tree_row: int | None = None) -> dict:
        self.tree_expanded = True
        return {}

    def collapse_tree(self, tree_row: int | None = None) -> dict:
        self.tree_expanded = False
        return {}

    def activate_tab(self, tab_index: int | None = None, tab_title: str | None = None) -> dict:
        self.tab_index = tab_index
        self.tab_title = tab_title
        return {}

    def get_element_information(self) -> dict:
        return dict(self._info)


class _MockDriver:
    """Mock JavaAgentDriver."""

    def __init__(
        self,
        *,
        health_raises: Exception | None = None,
        press_key_raises: Exception | None = None,
        scan_result: dict | None = None,
    ) -> None:
        self._health_raises = health_raises
        self._press_key_raises = press_key_raises
        self._scan_result: dict = scan_result or {
            "windows": [
                {
                    "title": "Test Form",
                    "displayName": "Test Form",
                    "semanticType": "Window",
                    "children": [],
                    "showing": True,
                    "enabled": True,
                    "focused": True,
                }
            ]
        }
        self.press_key_calls: list[str] = []
        self.health_call_count: int = 0

    def health(self) -> dict:
        self.health_call_count += 1
        if self._health_raises:
            raise self._health_raises
        return {"status": "ok"}

    def scan(self, *args, **kwargs) -> dict:
        return self._scan_result

    def press_key(self, key: str, descriptor: dict | None = None) -> dict:
        if self._press_key_raises:
            raise self._press_key_raises
        self.press_key_calls.append(key)
        return {"result": "ok"}


def _backend(driver: _MockDriver, elem: _MockElement) -> JavaFormsReplayBackend:
    """Convenience: backend wired to a fixed mock element and driver."""
    return JavaFormsReplayBackend(
        driver,
        retry_count=0,
        element_factory=lambda _d, _desc: elem,
    )


def _backend_with_retries(
    driver: _MockDriver,
    elem: _MockElement,
    retry_count: int = 2,
    retry_delay_ms: int = 0,
) -> JavaFormsReplayBackend:
    return JavaFormsReplayBackend(
        driver,
        retry_count=retry_count,
        retry_delay_ms=retry_delay_ms,
        element_factory=lambda _d, _desc: elem,
    )


# ── Action routing ────────────────────────────────────────────────────────────


def test_click_routes_to_java_element() -> None:
    elem = _MockElement()
    backend = _backend(_MockDriver(), elem)
    backend.click("order_type", {"name": "Order Type", "role": "Field"})
    assert elem.click_count == 1


def test_set_text_routes_to_java_element() -> None:
    elem = _MockElement()
    backend = _backend(_MockDriver(), elem)
    backend.set_text("order_type", {"name": "Order Type"}, "STANDARD")
    assert "STANDARD" in elem.sent_texts


def test_select_value_routes_via_send_text() -> None:
    """Oracle Forms LOV: select_value calls send_text (types the value)."""
    elem = _MockElement()
    backend = _backend(_MockDriver(), elem)
    backend.select_value("status_fld", {"name": "Status"}, "ACTIVE")
    assert "ACTIVE" in elem.sent_texts


def test_double_click_routes_to_java_element() -> None:
    elem = _MockElement()
    backend = _backend(_MockDriver(), elem)
    backend.double_click("list_item", {"name": "Item 1"})
    assert elem.double_click_count == 1


def test_set_check_routes_to_java_element() -> None:
    elem = _MockElement()
    backend = _backend(_MockDriver(), elem)
    backend.set_check("checkbox_item", {"name": "Checkbox 1"}, True)
    assert elem.checked_state is True


def test_expand_tree_routes_to_java_element() -> None:
    elem = _MockElement()
    backend = _backend(_MockDriver(), elem)
    backend.expand_tree("tree_item", {"name": "Tree 1"})
    assert elem.tree_expanded is True


def test_collapse_tree_routes_to_java_element() -> None:
    elem = _MockElement()
    backend = _backend(_MockDriver(), elem)
    backend.collapse_tree("tree_item", {"name": "Tree 1"})
    assert elem.tree_expanded is False


def test_activate_tab_routes_to_java_element() -> None:
    elem = _MockElement()
    backend = _backend(_MockDriver(), elem)
    backend.activate_tab("tab_item", {"name": "Tab 1"}, tab_index=2, tab_title="Tab 2")
    assert elem.tab_index == 2
    assert elem.tab_title == "Tab 2"


def test_press_key_routes_to_driver() -> None:
    driver = _MockDriver()
    backend = JavaFormsReplayBackend(driver, retry_count=0)
    backend.press_key("TAB")
    assert "TAB" in driver.press_key_calls


def test_press_key_f4() -> None:
    driver = _MockDriver()
    backend = JavaFormsReplayBackend(driver, retry_count=0)
    backend.press_key("F4")
    assert "F4" in driver.press_key_calls


# ── get_text / get_value ──────────────────────────────────────────────────────


def test_get_text_returns_element_text() -> None:
    elem = _MockElement(text="CREDIT_ICO_TDS")
    backend = _backend(_MockDriver(), elem)
    assert backend.get_text("order_type", {"name": "Order Type"}) == "CREDIT_ICO_TDS"


def test_get_value_returns_text_when_value_absent() -> None:
    elem = _MockElement(text="KRISHAN001")
    backend = _backend(_MockDriver(), elem)
    assert backend.get_value("po_number", {"name": "PO Number"}) == "KRISHAN001"


def test_get_value_returns_value_field_as_fallback() -> None:
    """value field used when text is empty."""
    elem = _MockElement(text="", value="FALLBACK")
    backend = _backend(_MockDriver(), elem)
    assert backend.get_value("po_number", {"name": "PO Number"}) == "FALLBACK"


# ── assert_visible ────────────────────────────────────────────────────────────


def test_assert_visible_passes_when_element_found() -> None:
    elem = _MockElement(found=True, showing=True)
    backend = _backend(_MockDriver(), elem)
    backend.assert_visible("field", {"name": "Order Type"})  # no raise


def test_assert_visible_raises_when_not_found() -> None:
    elem = _MockElement(found=False)
    backend = _backend(_MockDriver(), elem)
    with pytest.raises(ReplayAssertionError) as exc_info:
        backend.assert_visible("order_type", {"name": "Order Type"})
    err = str(exc_info.value)
    assert "order_type" in err
    assert "java_forms" in err
    assert "not found" in err


def test_assert_visible_raises_when_not_showing() -> None:
    elem = _MockElement(found=True, showing=False)
    backend = _backend(_MockDriver(), elem)
    with pytest.raises(ReplayAssertionError) as exc_info:
        backend.assert_visible("field", {"name": "Field", "elementid": "e42"})
    assert "not showing" in str(exc_info.value)


# ── Retry policy ──────────────────────────────────────────────────────────────


def test_transient_command_error_is_retried_and_succeeds() -> None:
    from qcs_java_agent.exceptions import CommandError

    attempt_box: list[int] = [0]
    elem = _MockElement()

    def _flakey_click(simulate: bool = True) -> dict:
        attempt_box[0] += 1
        if attempt_box[0] < 2:
            raise CommandError("transient busy")
        elem.click_count += 1
        return {}

    elem.click = _flakey_click  # type: ignore[method-assign]
    backend = _backend_with_retries(_MockDriver(), elem, retry_count=2, retry_delay_ms=0)
    backend.click("btn", {"name": "OK"})
    assert attempt_box[0] == 2  # failed once, succeeded on second attempt


def test_all_retries_exhausted_raises_replay_assertion() -> None:
    from qcs_java_agent.exceptions import CommandError

    elem = _MockElement(click_raises=CommandError("persistent error"))
    backend = _backend_with_retries(_MockDriver(), elem, retry_count=2, retry_delay_ms=0)
    with pytest.raises(ReplayAssertionError) as exc_info:
        backend.click("btn", {"name": "OK"})
    err = str(exc_info.value)
    assert "3 attempt(s)" in err  # retry_count=2 → 3 total attempts
    assert "element_ref='btn'" in err
    assert "java_forms" in err


def test_process_not_found_is_not_retried() -> None:
    """ProcessNotFoundError must not be retried — it is non-transient."""
    from qcs_java_agent.exceptions import ProcessNotFoundError

    attempt_box: list[int] = [0]
    elem = _MockElement()

    def _dead_click(simulate: bool = True) -> None:
        attempt_box[0] += 1
        raise ProcessNotFoundError("javaws gone")

    elem.click = _dead_click  # type: ignore[method-assign]
    backend = _backend_with_retries(_MockDriver(), elem, retry_count=3, retry_delay_ms=0)
    with pytest.raises(ReplayAssertionError) as exc_info:
        backend.click("btn", {"name": "OK"})
    assert attempt_box[0] == 1, "Non-retryable error must not be retried"
    assert "not available" in str(exc_info.value)


def test_attach_error_is_not_retried() -> None:
    from qcs_java_agent.exceptions import AttachError

    attempt_box: list[int] = [0]
    elem = _MockElement()

    def _attach_fail(simulate: bool = True) -> None:
        attempt_box[0] += 1
        raise AttachError("attach failed")

    elem.click = _attach_fail  # type: ignore[method-assign]
    backend = _backend_with_retries(_MockDriver(), elem, retry_count=2, retry_delay_ms=0)
    with pytest.raises(ReplayAssertionError):
        backend.click("btn", {"name": "OK"})
    assert attempt_box[0] == 1


def test_retry_count_zero_means_no_retry() -> None:
    from qcs_java_agent.exceptions import CommandError

    attempt_box: list[int] = [0]
    elem = _MockElement()

    def _fail(simulate: bool = True) -> None:
        attempt_box[0] += 1
        raise CommandError("fail")

    elem.click = _fail  # type: ignore[method-assign]
    backend = _backend_with_retries(_MockDriver(), elem, retry_count=0)
    with pytest.raises(ReplayAssertionError) as exc_info:
        backend.click("btn", {"name": "OK"})
    assert attempt_box[0] == 1
    assert "1 attempt(s)" in str(exc_info.value)


# ── Coordinate guard ──────────────────────────────────────────────────────────


def test_coordinates_blocked_without_allow_flag() -> None:
    driver = _MockDriver()
    backend = JavaFormsReplayBackend(driver, retry_count=0)
    with pytest.raises(ReplayAssertionError) as exc_info:
        backend.click("btn", {"x": 100, "y": 200})
    err = str(exc_info.value)
    assert "allow_coordinates" in err
    assert "btn" in err


def test_coordinates_allowed_when_flag_set() -> None:
    elem = _MockElement()
    backend = JavaFormsReplayBackend(
        _MockDriver(),
        retry_count=0,
        element_factory=lambda _d, _desc: elem,
    )
    backend.click("btn", {"x": 100, "y": 200, "allow_coordinates": True})
    assert elem.click_count == 1


def test_coordinates_not_blocked_when_path_present() -> None:
    """path + coordinates → path takes precedence, no coordinate guard triggered."""
    elem = _MockElement()
    backend = JavaFormsReplayBackend(
        _MockDriver(),
        retry_count=0,
        element_factory=lambda _d, _desc: elem,
    )
    backend.click("btn", {"path": "Root/Window/Panel/Button", "x": 100, "y": 200})
    assert elem.click_count == 1


def test_coordinates_not_blocked_when_name_present() -> None:
    elem = _MockElement()
    backend = JavaFormsReplayBackend(
        _MockDriver(),
        retry_count=0,
        element_factory=lambda _d, _desc: elem,
    )
    backend.set_text("fld", {"name": "PO Number", "x": 50, "y": 60}, "VAL")
    assert "VAL" in elem.sent_texts


def test_coordinates_not_blocked_when_elementid_present() -> None:
    elem = _MockElement()
    backend = JavaFormsReplayBackend(
        _MockDriver(),
        retry_count=0,
        element_factory=lambda _d, _desc: elem,
    )
    backend.click("fld", {"elementid": "e42", "x": 50, "y": 60})
    assert elem.click_count == 1


# ── snapshot() ────────────────────────────────────────────────────────────────


def test_snapshot_returns_string() -> None:
    driver = _MockDriver()
    backend = JavaFormsReplayBackend(driver, retry_count=0)
    result = backend.snapshot()
    assert isinstance(result, str)


def test_snapshot_safe_on_driver_failure() -> None:
    class _BrokenDriver:
        def health(self) -> dict:
            return {}

        def scan(self, *args, **kwargs) -> dict:
            raise RuntimeError("agent died")

        def press_key(self, k: str, d: dict | None = None) -> dict:
            return {}

    backend = JavaFormsReplayBackend(_BrokenDriver(), retry_count=0)
    result = backend.snapshot()
    assert "snapshot unavailable" in result.lower()


# ── wait_for ─────────────────────────────────────────────────────────────────


def test_wait_for_returns_when_element_found() -> None:
    elem = _MockElement(found=True)
    driver = _MockDriver()
    backend = JavaFormsReplayBackend(
        driver,
        retry_count=0,
        element_factory=lambda _d, _desc: elem,
    )
    backend.wait_for("field", {"name": "Field"}, timeout_ms=5_000)
    # No exception → element was found


def test_wait_for_calls_driver_health_check() -> None:
    """wait_for must verify the driver is alive before polling."""
    from qcs_java_agent.exceptions import ProcessNotFoundError

    driver = _MockDriver(health_raises=ProcessNotFoundError("dead"))
    backend = JavaFormsReplayBackend(driver, retry_count=0)
    with pytest.raises(ReplayAssertionError) as exc_info:
        backend.wait_for("field", {"name": "Field"}, timeout_ms=500)
    err = str(exc_info.value)
    assert "not available" in err
    assert "java_forms" in err


def test_wait_for_times_out_with_clear_message() -> None:
    elem = _MockElement(found=False)
    driver = _MockDriver()
    backend = JavaFormsReplayBackend(
        driver,
        retry_count=0,
        element_factory=lambda _d, _desc: elem,
    )
    with pytest.raises(ReplayAssertionError) as exc_info:
        backend.wait_for("order_type", {"name": "Order Type"}, timeout_ms=100)
    err = str(exc_info.value)
    assert "order_type" in err
    assert "java_forms" in err
    assert "wait_for" in err
    assert "last_readiness" in err


def test_wait_for_health_check_called_once() -> None:
    """health() is called exactly once before the poll loop."""
    elem = _MockElement(found=True)
    driver = _MockDriver()
    backend = JavaFormsReplayBackend(
        driver,
        retry_count=0,
        element_factory=lambda _d, _desc: elem,
    )
    backend.wait_for("field", {"name": "Field"}, timeout_ms=5_000)
    assert driver.health_call_count == 1


# ── Failure messages ──────────────────────────────────────────────────────────


def test_failure_message_includes_element_ref() -> None:
    from qcs_java_agent.exceptions import CommandError

    elem = _MockElement(click_raises=CommandError("error"))
    backend = _backend(_MockDriver(), elem)
    with pytest.raises(ReplayAssertionError) as exc_info:
        backend.click("po_number", {"name": "PO Number"})
    err = str(exc_info.value)
    assert "po_number" in err


def test_failure_message_includes_surface() -> None:
    from qcs_java_agent.exceptions import CommandError

    elem = _MockElement(send_text_raises=CommandError("error"))
    backend = _backend(_MockDriver(), elem)
    with pytest.raises(ReplayAssertionError) as exc_info:
        backend.set_text("order_field", {"name": "Order Field"}, "VALUE")
    assert "java_forms" in str(exc_info.value)


def test_failure_message_includes_descriptor_identity() -> None:
    from qcs_java_agent.exceptions import CommandError

    elem = _MockElement(click_raises=CommandError("error"))
    backend = _backend(_MockDriver(), elem)
    with pytest.raises(ReplayAssertionError) as exc_info:
        backend.click("btn", {"path": "Root/Window/Panel/SubmitButton"})
    assert "Root/Window/Panel/SubmitButton" in str(exc_info.value)


def test_press_key_failure_message_includes_key() -> None:
    from qcs_java_agent.exceptions import CommandError

    driver = _MockDriver(press_key_raises=CommandError("key error"))
    backend = JavaFormsReplayBackend(driver, retry_count=0)
    with pytest.raises(ReplayAssertionError) as exc_info:
        backend.press_key("F4")
    err = str(exc_info.value)
    assert "F4" in err
    assert "java_forms" in err


# ── FormReplay integration ────────────────────────────────────────────────────


def _make_java_form(
    driver: _MockDriver,
    elem: _MockElement,
    form_ref: str = "purchase_order",
) -> FormReplay:
    backend = JavaFormsReplayBackend(
        driver,
        retry_count=0,
        element_factory=lambda _d, _desc: elem,
    )
    resolver = RepositoryResolver()
    resolver.register(
        form_ref, "po_number",
        ResolvedTarget(
            ref="po_number",
            surface="java_forms",
            form_id=form_ref,
            descriptor={"name": "PO Number"},
        ),
    )
    return FormReplay(
        form_ref=form_ref,
        resolver=resolver,
        browser_backend=None,
        java_backend=backend,
        logger=ReplayLogger(),
    )


def test_form_replay_set_text_routes_to_java_element() -> None:
    elem = _MockElement()
    driver = _MockDriver()
    form = _make_java_form(driver, elem)
    form.set_text("po_number", "KRISHAN001")
    assert "KRISHAN001" in elem.sent_texts


def test_form_replay_click_routes_to_java_element() -> None:
    elem = _MockElement()
    driver = _MockDriver()
    form = _make_java_form(driver, elem)
    # Register a click target
    form.resolver.register(
        "purchase_order", "ok_btn",
        ResolvedTarget(
            ref="ok_btn",
            surface="java_forms",
            form_id="purchase_order",
            descriptor={"name": "OK"},
        ),
    )
    form.click("ok_btn")
    assert elem.click_count == 1


def test_form_replay_assert_value_passes_when_correct() -> None:
    elem = _MockElement(text="KRISHAN001")
    driver = _MockDriver()
    form = _make_java_form(driver, elem)
    form.assert_value("po_number", "KRISHAN001")  # no raise


def test_form_replay_assert_value_fails_when_wrong() -> None:
    elem = _MockElement(text="WRONG")
    driver = _MockDriver()
    form = _make_java_form(driver, elem)
    with pytest.raises(ReplayAssertionError) as exc_info:
        form.assert_value("po_number", "KRISHAN001")
    assert "expected='KRISHAN001'" in str(exc_info.value)
    assert "actual='WRONG'" in str(exc_info.value)


def test_form_replay_get_text_returns_element_value() -> None:
    elem = _MockElement(text="Order #42")
    driver = _MockDriver()
    form = _make_java_form(driver, elem)
    assert form.get_text("po_number") == "Order #42"


def test_form_replay_get_value_returns_element_value() -> None:
    elem = _MockElement(text="KRISHAN001")
    driver = _MockDriver()
    form = _make_java_form(driver, elem)
    assert form.get_value("po_number") == "KRISHAN001"


def test_form_replay_press_key_uses_java_backend() -> None:
    elem = _MockElement()
    driver = _MockDriver()
    backend = JavaFormsReplayBackend(
        driver,
        retry_count=0,
        element_factory=lambda _d, _desc: elem,
    )
    resolver = RepositoryResolver()
    form = FormReplay(
        form_ref="my_form",
        resolver=resolver,
        browser_backend=None,
        java_backend=backend,
        logger=ReplayLogger(),
    )
    form.press_key("TAB")
    assert "TAB" in driver.press_key_calls

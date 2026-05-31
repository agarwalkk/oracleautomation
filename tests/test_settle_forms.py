"""Tests for qcs_java_agent.settle.settle_forms.

Uses a FakeDriver whose .scan() returns a scripted sequence of dicts so that
no real JVM is required.  All timing is real (using small timeouts / fast poll
intervals so the suite stays well under 1 s total).
"""
from __future__ import annotations

import pytest

from qcs_java_agent.settle import SettleResult, _cursor_is_busy, settle_forms


# ── Shared helpers ────────────────────────────────────────────────────────────

def _scan(window_cursor: str = "Default Cursor", leaf_cursor: str = "Default Cursor") -> dict:
    """Build a minimal two-level scan dict (window + one leaf child)."""
    return {
        "windows": [
            {
                "id": 1,
                "path": "JFrame[0]",
                "cursorName": window_cursor,
                "children": [
                    {
                        "id": 2,
                        "path": "JFrame[0]/VTextField[0]",
                        "cursorName": leaf_cursor,
                        "children": [],
                    }
                ],
            }
        ]
    }


def _idle_scan() -> dict:
    return _scan("Default Cursor", "Default Cursor")


def _text_cursor_scan() -> dict:
    """A field focused — leaf has Text Cursor, window has Default Cursor."""
    return _scan("Default Cursor", "Text Cursor")


def _busy_scan(leaf_cursor: str = "Default Cursor") -> dict:
    """Window node carries Wait Cursor; leaf may carry anything."""
    return _scan("Wait Cursor", leaf_cursor)


class FakeDriver:
    """Returns scripted scan dicts; repeats the last item once the script is exhausted.

    Repeating the last item allows timeout-based tests to work correctly: the
    driver keeps returning a busy scan (or raising) forever so the timeout fires
    naturally rather than the script running out and switching to scan_error.
    """

    def __init__(self, scans: list[dict | Exception]) -> None:
        self._scans = list(scans)
        self._idx = 0

    def scan(self) -> dict:
        if self._idx < len(self._scans):
            item = self._scans[self._idx]
            self._idx += 1
        else:
            # Repeat last item indefinitely so timeout fires without exhausting.
            item = self._scans[-1] if self._scans else RuntimeError("empty script")
        if isinstance(item, Exception):
            raise item
        return item


# Helper: build an idle scan that also satisfies analyze_forms_readiness.ready.
# analyze_forms_readiness needs at least one actionable element + a non-empty title.
def _ready_idle_scan() -> dict:
    """Idle scan that passes analyze_forms_readiness (has title + actionable element)."""
    return {
        "windows": [
            {
                "id": 1,
                "path": "JFrame[0]",
                "cursorName": "Default Cursor",
                "title": "Oracle Forms",
                "accessibleName": "Oracle Forms",
                "displayName": "Oracle Forms",
                "name": "Oracle Forms",
                "semanticType": "Window",
                "showing": True,
                "enabled": True,
                "visible": True,
                "focused": True,
                "children": [
                    {
                        "id": 2,
                        "path": "JFrame[0]/VTextField[0]",
                        "cursorName": "Default Cursor",
                        "semanticType": "Field",
                        "accessibleRole": "text",
                        "displayName": "Order Number",
                        "name": "Order Number",
                        "showing": True,
                        "enabled": True,
                        "visible": True,
                        "focused": False,
                        "children": [],
                    }
                ],
            }
        ]
    }


def _ready_busy_scan() -> dict:
    """Busy scan (window Wait Cursor) that still has an actionable element."""
    scan = _ready_idle_scan()
    scan["windows"][0]["cursorName"] = "Wait Cursor"
    return scan


# ── _cursor_is_busy unit tests ────────────────────────────────────────────────


class TestCursorIsBusy:
    def test_default_cursor_not_busy(self) -> None:
        assert _cursor_is_busy(_idle_scan()) is False

    def test_text_cursor_not_busy(self) -> None:
        assert _cursor_is_busy(_text_cursor_scan()) is False

    def test_wait_cursor_on_window_is_busy(self) -> None:
        """Core requirement: Wait Cursor on ancestor-only node detected."""
        scan = _busy_scan(leaf_cursor="Default Cursor")
        assert _cursor_is_busy(scan) is True

    def test_wait_cursor_case_insensitive_upper(self) -> None:
        assert _cursor_is_busy(_scan("WAIT CURSOR", "Default Cursor")) is True

    def test_wait_cursor_case_insensitive_title(self) -> None:
        assert _cursor_is_busy(_scan("Wait Cursor", "Default Cursor")) is True

    def test_wait_cursor_bare_word(self) -> None:
        assert _cursor_is_busy(_scan("wait", "Default Cursor")) is True

    def test_wait_cursor_underscore_form(self) -> None:
        assert _cursor_is_busy(_scan("wait_cursor", "Default Cursor")) is True

    def test_busy_on_ancestor_only_not_on_leaf(self) -> None:
        """Regression guard: leaf=Default Cursor must not suppress the busy flag."""
        scan = _busy_scan(leaf_cursor="Default Cursor")
        assert _cursor_is_busy(scan) is True

    def test_empty_scan_not_busy(self) -> None:
        assert _cursor_is_busy({"windows": []}) is False

    def test_missing_cursor_name_not_busy(self) -> None:
        scan = {"windows": [{"id": 1, "path": "J", "children": []}]}
        assert _cursor_is_busy(scan) is False


# ── settle_forms integration tests ───────────────────────────────────────────


class TestSettleForms:
    def test_immediately_idle(self) -> None:
        """Two idle scans -> settles in stable_polls=2 polls."""
        scans = [_ready_idle_scan(), _ready_idle_scan()]
        driver = FakeDriver(scans)
        result = settle_forms(
            driver, timeout_s=5.0, poll_interval_s=0.0, stable_polls=2
        )
        assert result.settled is True
        assert result.reason == "idle_stable"
        assert result.polls == 2

    def test_busy_then_idle(self) -> None:
        """First N scans busy (window Wait Cursor only), then stable_polls idle."""
        scans = (
            [_ready_busy_scan()] * 3
            + [_ready_idle_scan(), _ready_idle_scan()]
        )
        driver = FakeDriver(scans)
        result = settle_forms(
            driver, timeout_s=5.0, poll_interval_s=0.0, stable_polls=2
        )
        assert result.settled is True
        assert result.reason == "idle_stable"
        # Waited at least 3 busy polls + 2 idle = 5 polls
        assert result.polls == 5

    def test_stable_polls_enforced_reset_on_busy(self) -> None:
        """idle-busy-idle-idle must not settle at poll 2; requires 2 consecutive."""
        scans = [
            _ready_idle_scan(),   # consecutive=1
            _ready_busy_scan(),   # reset
            _ready_idle_scan(),   # consecutive=1
            _ready_idle_scan(),   # consecutive=2 -> settled
        ]
        driver = FakeDriver(scans)
        result = settle_forms(
            driver, timeout_s=5.0, poll_interval_s=0.0, stable_polls=2
        )
        assert result.settled is True
        assert result.polls == 4

    def test_timeout_returns_not_settled(self) -> None:
        """Driver always busy -> timeout before stable_polls reached."""
        # Use a tiny timeout so the test stays fast.
        driver = FakeDriver([_ready_busy_scan()] * 100)
        result = settle_forms(
            driver, timeout_s=0.2, poll_interval_s=0.0, stable_polls=2
        )
        assert result.settled is False
        assert result.reason == "timeout"
        assert result.waited_s <= 0.5  # generous upper bound

    def test_scan_error_never_propagates(self) -> None:
        """scan() raising every call -> settled=False, reason='scan_error', no exception."""
        driver = FakeDriver([RuntimeError("JVM gone")] * 20)
        result = settle_forms(
            driver, timeout_s=0.2, poll_interval_s=0.0, stable_polls=2
        )
        assert result.settled is False
        assert result.reason == "scan_error"

    def test_scan_error_reason_overridden_by_later_timeout_cause(self) -> None:
        """If the last poll was NOT a scan error, reason is 'timeout' not 'scan_error'."""
        scans: list = [RuntimeError("err")] * 3 + [_ready_busy_scan()] * 20
        driver = FakeDriver(scans)
        result = settle_forms(
            driver, timeout_s=0.2, poll_interval_s=0.0, stable_polls=2
        )
        assert result.settled is False
        # Last poll was a busy scan (not a scan error) -> reason is 'timeout'
        assert result.reason == "timeout"

    def test_polls_counted_correctly(self) -> None:
        """polls field reflects the total number of driver.scan() calls made."""
        scans = [_ready_idle_scan()] * 3
        driver = FakeDriver(scans)
        result = settle_forms(
            driver, timeout_s=5.0, poll_interval_s=0.0, stable_polls=3
        )
        assert result.settled is True
        assert result.polls == 3

    def test_waited_s_non_negative(self) -> None:
        driver = FakeDriver([_ready_idle_scan(), _ready_idle_scan()])
        result = settle_forms(
            driver, timeout_s=5.0, poll_interval_s=0.0, stable_polls=2
        )
        assert result.waited_s >= 0.0

    def test_single_stable_poll(self) -> None:
        """stable_polls=1 settles on the first idle scan."""
        driver = FakeDriver([_ready_busy_scan(), _ready_idle_scan()])
        result = settle_forms(
            driver, timeout_s=5.0, poll_interval_s=0.0, stable_polls=1
        )
        assert result.settled is True
        assert result.polls == 2

    def test_wait_cursor_on_ancestor_only_detected(self) -> None:
        """Confirm _cursor_is_busy=True when ONLY the window has Wait Cursor."""
        # Leaf is Default Cursor — the old naive "check last node" approach would miss this.
        scan = _busy_scan(leaf_cursor="Default Cursor")
        assert _cursor_is_busy(scan) is True, (
            "Must detect Wait Cursor on ancestor even when leaf has Default Cursor"
        )

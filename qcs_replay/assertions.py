"""
qcs_replay.assertions — Retry-based assertions for Java-agent and Playwright replay.

All helpers raise AssertionError on failure so pytest captures them naturally.
"""
from __future__ import annotations

import time
from typing import Any

import config


def _retry(fn, timeout_ms: int = config.POST_ACTION_VERIFY_MS, poll_ms: int = 200):
    """Call fn() repeatedly until it returns truthy or timeout elapses."""
    deadline = time.monotonic() + timeout_ms / 1000
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            result = fn()
            if result:
                return result
        except Exception as exc:
            last_exc = exc
        time.sleep(poll_ms / 1000)
    if last_exc:
        raise last_exc
    return None


# ── Java Forms assertions ────────────────────────────────────────────────────

def assert_java_text(element: Any, expected: str, *, timeout_ms: int = config.POST_ACTION_VERIFY_MS):
    """Assert that a Java-agent element's text/name equals expected."""
    def _check():
        info = element.get_element_information()
        actual = info.get("text") or info.get("name") or ""
        return actual.strip() == expected.strip()

    if not _retry(_check, timeout_ms):
        info = element.get_element_information()
        actual = info.get("text") or info.get("name") or ""
        raise AssertionError(f"Java element text: expected {expected!r}, got {actual!r}")


def assert_java_state(element: Any, state: str, *, timeout_ms: int = config.POST_ACTION_VERIFY_MS):
    """Assert that a Java-agent element has the given captured state."""
    def _check():
        info = element.get_element_information()
        if state == "closed":
            return info.get("_found") is False
        return state in info.get("states", [])

    if not _retry(_check, timeout_ms):
        info = element.get_element_information()
        raise AssertionError(
            f"Java element state: expected state {state!r}, got {info.get('states')}"
        )


# ── Playwright assertions (thin wrappers for consistency) ────────────────────

def assert_pw_text(locator: Any, expected: str, *, timeout_ms: int = config.POST_ACTION_VERIFY_MS):
    """Assert that a Playwright locator's inner text matches expected."""
    locator.wait_for(state="visible", timeout=timeout_ms)
    actual = locator.inner_text().strip()
    if actual != expected.strip():
        raise AssertionError(f"Playwright text: expected {expected!r}, got {actual!r}")


def assert_pw_visible(locator: Any, *, timeout_ms: int = config.POST_ACTION_VERIFY_MS):
    locator.wait_for(state="visible", timeout=timeout_ms)


def assert_pw_value(locator: Any, expected: str, *, timeout_ms: int = config.POST_ACTION_VERIFY_MS):
    """Assert that an input's value matches expected."""
    def _check():
        actual = locator.input_value()
        return actual.strip() == expected.strip()

    if not _retry(_check, timeout_ms):
        actual = locator.input_value()
        raise AssertionError(f"Playwright value: expected {expected!r}, got {actual!r}")

"""Tests for oracle_ai_agent.tools.execute_resolved_action.

Verifies that execute_resolved_action:
  1. Calls locator_params(element) to build the Java-agent locator params.
  2. Dispatches the correct command dict through driver._run for each action type.
  3. Returns the raw result dict from driver._run.
  4. Returns {} immediately for assert/done (no driver call).
  5. Raises ValueError for unrecognised actions.

driver._run is mocked so no real JVM is required.
"""
from __future__ import annotations

import base64
from unittest.mock import MagicMock

import pytest

from oracle_ai_agent.tools import SnapshotAction, execute_resolved_action
from qcs_java_agent.snapshot import locator_params


# ── Shared helpers ────────────────────────────────────────────────────────────

def _make_element(
    node_id: int = 10,
    name: str = "PO Number",
    path: str = "w0/n10",
    x: int = 110,
    y: int = 200,
    width: int = 120,
    height: int = 24,
) -> dict:
    """Minimal repo element descriptor with enough fields for locator_params."""
    return {
        "elementid": f"e{node_id}",
        "name": name,
        "path": path,
        "xpath": path,
        "bounds": {"x": x, "y": y, "width": width, "height": height},
        "java": {"path": path},
    }


def _mock_driver(return_value: dict | None = None) -> MagicMock:
    driver = MagicMock()
    driver._run.return_value = return_value or {"status": "ok"}
    return driver


# ── click ─────────────────────────────────────────────────────────────────────

class TestClick:

    def test_dispatches_click_command_with_locator_params(self):
        element = _make_element()
        driver = _mock_driver({"status": "ok", "clicked": True})
        action = SnapshotAction(action="click", element_id="e10")

        result = execute_resolved_action(driver, element, action)

        expected_params = locator_params(element)
        driver._run.assert_called_once_with({"command": "click", **expected_params})

    def test_returns_driver_run_result(self):
        element = _make_element()
        driver = _mock_driver({"status": "ok", "clicked": True})
        action = SnapshotAction(action="click", element_id="e10")

        result = execute_resolved_action(driver, element, action)

        assert result == {"status": "ok", "clicked": True}

    def test_locator_path_is_in_command(self):
        element = _make_element(path="w0/dialog/n42")
        driver = _mock_driver()
        action = SnapshotAction(action="click", element_id="e42")

        execute_resolved_action(driver, element, action)

        cmd = driver._run.call_args[0][0]
        assert cmd["command"] == "click"
        assert cmd.get("locatorPath") == "w0/dialog/n42"

    def test_locator_name_or_text_in_command(self):
        element = _make_element(name="Find")
        driver = _mock_driver()
        action = SnapshotAction(action="click", element_id="e10")

        execute_resolved_action(driver, element, action)

        cmd = driver._run.call_args[0][0]
        # name feeds locatorText (final fallback in locator_params)
        assert cmd.get("locatorText") == "Find" or cmd.get("locatorName") == "Find"


# ── set_text ──────────────────────────────────────────────────────────────────

class TestSetText:

    def test_dispatches_settext_command_with_locator_params(self):
        element = _make_element()
        driver = _mock_driver()
        action = SnapshotAction(action="set_text", element_id="e10", value="PO-001")

        execute_resolved_action(driver, element, action)

        expected_params = locator_params(element)
        expected_encoded = base64.b64encode("PO-001".encode("utf-8")).decode("ascii")
        driver._run.assert_called_once_with({
            "command": "settext",
            "text64": expected_encoded,
            **expected_params,
        })

    def test_value_is_base64_encoded(self):
        element = _make_element()
        driver = _mock_driver()
        value = "hello world ☺"
        action = SnapshotAction(action="set_text", element_id="e10", value=value)

        execute_resolved_action(driver, element, action)

        cmd = driver._run.call_args[0][0]
        decoded = base64.b64decode(cmd["text64"]).decode("utf-8")
        assert decoded == value

    def test_empty_value_becomes_empty_base64(self):
        element = _make_element()
        driver = _mock_driver()
        action = SnapshotAction(action="set_text", element_id="e10", value="")

        execute_resolved_action(driver, element, action)

        cmd = driver._run.call_args[0][0]
        assert cmd["command"] == "settext"
        assert base64.b64decode(cmd["text64"]).decode("utf-8") == ""

    def test_returns_driver_run_result(self):
        element = _make_element()
        driver = _mock_driver({"status": "ok", "text_set": True})
        action = SnapshotAction(action="set_text", element_id="e10", value="ABC")

        result = execute_resolved_action(driver, element, action)

        assert result == {"status": "ok", "text_set": True}


# ── press_key ─────────────────────────────────────────────────────────────────

class TestPressKey:

    def test_dispatches_presskey_command_with_key(self):
        element = _make_element()
        driver = _mock_driver()
        action = SnapshotAction(action="press_key", key="TAB")

        execute_resolved_action(driver, element, action)

        cmd = driver._run.call_args[0][0]
        assert cmd["command"] == "presskey"
        assert cmd["key"] == "TAB"

    def test_locator_params_included_for_element_context(self):
        element = _make_element(path="w0/n10")
        driver = _mock_driver()
        action = SnapshotAction(action="press_key", key="F10")

        execute_resolved_action(driver, element, action)

        expected_params = locator_params(element)
        cmd = driver._run.call_args[0][0]
        for k, v in expected_params.items():
            assert cmd.get(k) == v, f"locator param {k!r} missing or wrong in press_key command"

    def test_empty_element_produces_key_only_command(self):
        driver = _mock_driver()
        action = SnapshotAction(action="press_key", key="ESC")

        execute_resolved_action(driver, {}, action)

        cmd = driver._run.call_args[0][0]
        assert cmd == {"command": "presskey", "key": "ESC"}

    def test_returns_driver_run_result(self):
        driver = _mock_driver({"status": "ok"})
        action = SnapshotAction(action="press_key", key="F11")

        result = execute_resolved_action(driver, {}, action)

        assert result == {"status": "ok"}


# ── assert / done (no-op markers) ─────────────────────────────────────────────

class TestNoOpMarkers:

    @pytest.mark.parametrize("act,kwargs", [
        ("assert", {"element_id": "e10", "assertion_kind": "text", "value": "PO-001"}),
        ("done",   {"value": "Step complete"}),
    ])
    def test_returns_empty_dict_without_calling_driver(self, act, kwargs):
        driver = _mock_driver()
        action = SnapshotAction(action=act, **kwargs)

        result = execute_resolved_action(driver, _make_element(), action)

        assert result == {}
        driver._run.assert_not_called()


# ── error cases ───────────────────────────────────────────────────────────────

class TestErrors:

    def test_unsupported_action_raises_value_error(self):
        driver = _mock_driver()
        action = SnapshotAction(action="click", element_id="e10")
        action.action = "jump"  # force an unsupported action bypassing validation

        with pytest.raises(ValueError, match="unsupported action 'jump'"):
            execute_resolved_action(driver, _make_element(), action)

    def test_driver_run_exception_propagates(self):
        from qcs_java_agent.exceptions import CommandError

        driver = _mock_driver()
        driver._run.side_effect = CommandError("element not found")
        action = SnapshotAction(action="click", element_id="e10")

        with pytest.raises(CommandError, match="element not found"):
            execute_resolved_action(driver, _make_element(), action)


# ── locator_params contract (same path as replay) ────────────────────────────

class TestLocatorParamsIntegration:
    """Ensures execute_resolved_action builds the SAME locator params as the
    replay engine (JavaAgentElement) would for the same element."""

    def test_click_uses_identical_params_to_direct_locator_params_call(self):
        element = _make_element(node_id=22, name="Save", path="w0/toolbar/n22")
        driver = _mock_driver()
        action = SnapshotAction(action="click", element_id="e22")

        execute_resolved_action(driver, element, action)

        cmd = driver._run.call_args[0][0]
        expected = {"command": "click", **locator_params(element)}
        assert cmd == expected

    def test_set_text_uses_identical_params_to_direct_locator_params_call(self):
        element = _make_element(node_id=5, name="Quantity", path="w0/grid/n5")
        driver = _mock_driver()
        action = SnapshotAction(action="set_text", element_id="e5", value="10")

        execute_resolved_action(driver, element, action)

        cmd = driver._run.call_args[0][0]
        expected_encoded = base64.b64encode(b"10").decode("ascii")
        expected = {"command": "settext", "text64": expected_encoded, **locator_params(element)}
        assert cmd == expected


class TestTreeAction:

    def test_dispatches_treeaction_command_with_op_and_locator_params(self):
        element = _make_element()
        driver = _mock_driver()
        action = SnapshotAction(action="tree_action", element_id="e10", value="expand")

        execute_resolved_action(driver, element, action)

        expected_params = locator_params(element)
        driver._run.assert_called_once_with({
            "command": "treeaction",
            "op": "expand",
            **expected_params,
        })

    def test_returns_driver_run_result(self):
        element = _make_element()
        driver = _mock_driver({"status": "ok", "expanded": True})
        action = SnapshotAction(action="tree_action", element_id="e10", value="expand")

        result = execute_resolved_action(driver, element, action)

        assert result == {"status": "ok", "expanded": True}


class TestSetCheckbox:

    @pytest.mark.parametrize("val,expected_checked", [
        ("true", "true"),
        ("1", "true"),
        ("false", "false"),
        ("0", "false"),
    ])
    def test_dispatches_setcheckbox_command(self, val, expected_checked):
        element = _make_element()
        driver = _mock_driver()
        action = SnapshotAction(action="set_checkbox", element_id="e10", value=val)

        execute_resolved_action(driver, element, action)

        expected_params = locator_params(element)
        driver._run.assert_called_once_with({
            "command": "setcheckbox",
            "checked": expected_checked,
            **expected_params,
        })

    def test_returns_driver_run_result(self):
        element = _make_element()
        driver = _mock_driver({"status": "ok", "state_changed": True})
        action = SnapshotAction(action="set_checkbox", element_id="e10", value="true")

        result = execute_resolved_action(driver, element, action)

        assert result == {"status": "ok", "state_changed": True}


class TestPressButton:

    def test_dispatches_pressbutton_command(self):
        element = _make_element()
        driver = _mock_driver()
        action = SnapshotAction(action="press_button", element_id="e10")

        execute_resolved_action(driver, element, action)

        expected_params = locator_params(element)
        driver._run.assert_called_once_with({
            "command": "pressbutton",
            **expected_params,
        })

    def test_returns_driver_run_result(self):
        element = _make_element()
        driver = _mock_driver({"status": "ok", "pressed": True})
        action = SnapshotAction(action="press_button", element_id="e10")

        result = execute_resolved_action(driver, element, action)

        assert result == {"status": "ok", "pressed": True}


class TestSetPoplist:

    def test_dispatches_setpoplist_command(self):
        element = _make_element()
        driver = _mock_driver()
        action = SnapshotAction(action="set_poplist", element_id="e10", value="Options")

        execute_resolved_action(driver, element, action)

        expected_params = locator_params(element)
        driver._run.assert_called_once_with({
            "command": "setpoplist",
            "value": "Options",
            **expected_params,
        })

    def test_returns_driver_run_result(self):
        element = _make_element()
        driver = _mock_driver({"status": "ok", "selected": True})
        action = SnapshotAction(action="set_poplist", element_id="e10", value="Options")

        result = execute_resolved_action(driver, element, action)

        assert result == {"status": "ok", "selected": True}

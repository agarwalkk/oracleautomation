"""Tests for oracle_ai_agent.tools.parse_snapshot_action.

No real JVM, no Azure OpenAI, no Playwright — pure unit tests.

Covers:
  - All five valid actions with their required fields
  - done with and without a note
  - SnapshotActionError on unknown action
  - SnapshotActionError when element_id is not in the snapshot
  - SnapshotActionError when element_id is missing for actions that need it
  - SnapshotActionError when value is missing for set_text
  - SnapshotActionError when key is missing for press_key
  - SnapshotActionError when assertion_kind is missing/invalid for assert
  - SnapshotActionError when action field is absent entirely
"""
from __future__ import annotations

import pytest

from oracle_ai_agent.tools import (
    SnapshotAction,
    SnapshotActionError,
    parse_snapshot_action,
)

# Snapshot returned by build_action_context for an imaginary purchase-order form
_VALID_IDS: set[str] = {"e11", "e12", "e13", "e14", "e15"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse(raw: dict, valid_ids: set[str] | None = None) -> SnapshotAction:
    return parse_snapshot_action(raw, valid_ids if valid_ids is not None else _VALID_IDS)


# ── Valid actions ─────────────────────────────────────────────────────────────

class TestValidActions:

    def test_set_text_returns_dataclass(self):
        result = _parse({"action": "set_text", "element_id": "e11", "value": "PO-001"})
        assert isinstance(result, SnapshotAction)
        assert result.action == "set_text"
        assert result.element_id == "e11"
        assert result.value == "PO-001"
        assert result.key is None
        assert result.assertion_kind is None

    def test_click_returns_dataclass(self):
        result = _parse({"action": "click", "element_id": "e12"})
        assert result.action == "click"
        assert result.element_id == "e12"
        assert result.value is None

    def test_press_key_returns_dataclass(self):
        result = _parse({"action": "press_key", "key": "TAB"})
        assert result.action == "press_key"
        assert result.key == "TAB"
        assert result.element_id is None

    def test_press_key_composite_shortcut(self):
        result = _parse({"action": "press_key", "key": "ctrl+F11"})
        assert result.key == "ctrl+F11"

    def test_assert_text(self):
        result = _parse({
            "action": "assert",
            "element_id": "e13",
            "assertion_kind": "text",
            "value": "Standard",
        })
        assert result.action == "assert"
        assert result.assertion_kind == "text"
        assert result.value == "Standard"

    def test_assert_all_kinds_accepted(self):
        for kind in ("text", "value", "visible", "enabled"):
            result = _parse({
                "action": "assert",
                "element_id": "e14",
                "assertion_kind": kind,
            })
            assert result.assertion_kind == kind

    def test_done_no_fields(self):
        result = _parse({"action": "done"})
        assert result.action == "done"
        assert result.element_id is None
        assert result.value is None

    def test_done_with_note(self):
        result = _parse({"action": "done", "value": "Step already satisfied"})
        assert result.value == "Step already satisfied"

    def test_done_with_fail_note(self):
        result = _parse({"action": "done", "value": "FAIL: element not in snapshot"})
        assert result.value.startswith("FAIL:")

    def test_extra_fields_are_ignored(self):
        """Unknown extra keys in raw_args must not raise."""
        result = _parse({
            "action": "click",
            "element_id": "e15",
            "surprise_field": "ignored",
        })
        assert result.action == "click"

    def test_set_text_value_zero_string(self):
        """A value of '0' is non-empty and must be accepted."""
        result = _parse({"action": "set_text", "element_id": "e11", "value": "0"})
        assert result.value == "0"


# ── Invalid element_id ────────────────────────────────────────────────────────

class TestInvalidElementId:

    def test_click_with_unknown_element_id(self):
        with pytest.raises(SnapshotActionError, match="element_id"):
            _parse({"action": "click", "element_id": "e99"})

    def test_set_text_with_unknown_element_id(self):
        with pytest.raises(SnapshotActionError, match="element_id"):
            _parse({"action": "set_text", "element_id": "e99", "value": "x"})

    def test_assert_with_unknown_element_id(self):
        with pytest.raises(SnapshotActionError, match="element_id"):
            _parse({
                "action": "assert",
                "element_id": "e99",
                "assertion_kind": "text",
            })

    def test_element_id_empty_string_treated_as_missing(self):
        with pytest.raises(SnapshotActionError, match="element_id"):
            _parse({"action": "click", "element_id": "  "})

    def test_click_missing_element_id_key(self):
        with pytest.raises(SnapshotActionError, match="element_id"):
            _parse({"action": "click"})

    def test_press_key_does_not_require_element_id(self):
        """press_key must succeed even when element_id is completely absent."""
        result = _parse({"action": "press_key", "key": "F10"})
        assert result.element_id is None

    def test_done_does_not_require_element_id(self):
        result = _parse({"action": "done"})
        assert result.element_id is None


# ── Missing / invalid required fields ────────────────────────────────────────

class TestMissingRequiredFields:

    def test_missing_action_field(self):
        with pytest.raises(SnapshotActionError, match="action"):
            _parse({})

    def test_unknown_action(self):
        with pytest.raises(SnapshotActionError, match="unknown action"):
            _parse({"action": "fly_to_moon"})

    def test_set_text_missing_value(self):
        with pytest.raises(SnapshotActionError, match="value"):
            _parse({"action": "set_text", "element_id": "e11"})

    def test_set_text_empty_value(self):
        with pytest.raises(SnapshotActionError, match="value"):
            _parse({"action": "set_text", "element_id": "e11", "value": ""})

    def test_press_key_missing_key(self):
        with pytest.raises(SnapshotActionError, match="key"):
            _parse({"action": "press_key"})

    def test_press_key_empty_key(self):
        with pytest.raises(SnapshotActionError, match="key"):
            _parse({"action": "press_key", "key": ""})

    def test_assert_missing_assertion_kind(self):
        with pytest.raises(SnapshotActionError, match="assertion_kind"):
            _parse({"action": "assert", "element_id": "e11"})

    def test_assert_invalid_assertion_kind(self):
        with pytest.raises(SnapshotActionError, match="assertion_kind"):
            _parse({
                "action": "assert",
                "element_id": "e11",
                "assertion_kind": "colour",
            })

    def test_action_none_raises(self):
        with pytest.raises(SnapshotActionError, match="action"):
            _parse({"action": None})


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_valid_element_ids_empty_set_blocks_all_element_actions(self):
        """If the snapshot is empty, any element_id is unknown."""
        with pytest.raises(SnapshotActionError, match="element_id"):
            parse_snapshot_action(
                {"action": "click", "element_id": "e11"},
                valid_element_ids=set(),
            )

    def test_assert_value_is_optional(self):
        """assert with assertion_kind='visible' needs no value field."""
        result = _parse({
            "action": "assert",
            "element_id": "e11",
            "assertion_kind": "visible",
        })
        assert result.value is None

    def test_none_values_in_raw_args_coerced(self):
        """None values for optional fields should be stored as None, not crash."""
        result = _parse({
            "action": "click",
            "element_id": "e11",
            "value": None,
            "key": None,
            "assertion_kind": None,
        })
        assert result.value is None
        assert result.key is None
        assert result.assertion_kind is None

    def test_tree_action_valid_ops(self):
        for op in ("select", "expand", "collapse", "activate"):
            result = _parse({"action": "tree_action", "element_id": "e11", "value": op})
            assert result.action == "tree_action"
            assert result.element_id == "e11"
            assert result.value == op

    def test_tree_action_missing_value(self):
        with pytest.raises(SnapshotActionError, match="value"):
            _parse({"action": "tree_action", "element_id": "e11"})

    def test_tree_action_invalid_value(self):
        with pytest.raises(SnapshotActionError, match="unknown tree operation"):
            _parse({"action": "tree_action", "element_id": "e11", "value": "dance"})

    def test_set_checkbox_valid_values(self):
        for val in ("true", "false", "1", "0"):
            result = _parse({"action": "set_checkbox", "element_id": "e11", "value": val})
            assert result.action == "set_checkbox"
            assert result.element_id == "e11"
            assert result.value == val

    def test_set_checkbox_missing_value(self):
        with pytest.raises(SnapshotActionError, match="value"):
            _parse({"action": "set_checkbox", "element_id": "e11"})

    def test_set_checkbox_invalid_value(self):
        with pytest.raises(SnapshotActionError, match="unknown checked state"):
            _parse({"action": "set_checkbox", "element_id": "e11", "value": "maybe"})

    def test_press_button_valid(self):
        result = _parse({"action": "press_button", "element_id": "e11"})
        assert result.action == "press_button"
        assert result.element_id == "e11"

    def test_set_poplist_valid(self):
        result = _parse({"action": "set_poplist", "element_id": "e11", "value": "Options"})
        assert result.action == "set_poplist"
        assert result.element_id == "e11"
        assert result.value == "Options"

    def test_set_poplist_missing_value(self):
        with pytest.raises(SnapshotActionError, match="value"):
            _parse({"action": "set_poplist", "element_id": "e11"})

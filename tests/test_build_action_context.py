"""Tests for qcs_java_agent.snapshot.build_action_context.

No real JVM, no Playwright, no AI — uses a small synthetic scan dict.

Invariant under test:
    Every "[eN]" token that appears in the snapshot text must have a
    corresponding key "eN" in the id_map, and nothing that does NOT
    appear in the text should be in the map.
"""
from __future__ import annotations

import re

import pytest

from qcs_java_agent.snapshot import build_action_context


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_node(
    node_id: int,
    semantic_type: str,
    display_name: str,
    *,
    enabled: bool = True,
    visible: bool = True,
    showing: bool = True,
    parent_path: str = "",
    children: list | None = None,
) -> dict:
    path = f"w0/node{node_id}"
    return {
        "id": node_id,
        "semanticType": semantic_type,
        "displayName": display_name,
        "accessibleName": display_name,
        "path": path,
        "parentPath": parent_path or "w0",
        "enabled": enabled,
        "visible": visible,
        "showing": showing,
        "focusable": enabled,
        "focused": False,
        "children": children or [],
        "screenBounds": {"screenX": 10 * node_id, "screenY": 20, "width": 100, "height": 24},
    }


def _element_ids_in_text(text: str) -> set[str]:
    """Extract every 'eN' token from lines like '[e12] Name | Role ...'."""
    return set(re.findall(r"\[([^\]]+)\]", text))


# ── Fixtures / shared scan ────────────────────────────────────────────────────

def _make_scan() -> dict:
    """
    Topology
    --------
    Window (e1)          – non-actionable, no role match
      Field  "PO Number"  (e2)  enabled   → IN snapshot + map
      Button "Find"       (e3)  enabled   → IN snapshot + map
      Panel  "Content"    (e4)  enabled   → NOT in snapshot (role "Panel" excluded)
      Field  "Vendor"     (e5)  disabled  → NOT in snapshot (no "enabled" state)
      Toolbar "Toolbar1"  (e6)  disabled  → IN snapshot (Toolbar exempt from enabled check)
      ComboBox "Status"   (e7)  enabled   → IN snapshot + map
    """
    window = _make_node(1, "Window", "Purchase Orders", parent_path="")
    window["path"] = "w0"
    window["parentPath"] = ""
    window["children"] = [
        _make_node(2, "Field",   "PO Number", enabled=True,  parent_path="w0"),
        _make_node(3, "Button",  "Find",       enabled=True,  parent_path="w0"),
        _make_node(4, "Panel",   "Content",    enabled=True,  parent_path="w0"),
        _make_node(5, "Field",   "Vendor",     enabled=False, parent_path="w0"),
        _make_node(6, "Toolbar", "Toolbar1",   enabled=False, parent_path="w0"),
        _make_node(7, "ComboBox","Status",     enabled=True,  parent_path="w0"),
    ]
    return {"windows": [window]}


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestBuildActionContext:

    @pytest.fixture
    def ctx(self):
        scan = _make_scan()
        return build_action_context(scan)

    def test_returns_two_element_tuple(self, ctx):
        text, id_map = ctx
        assert isinstance(text, str)
        assert isinstance(id_map, dict)

    def test_snapshot_text_is_nonempty(self, ctx):
        text, _ = ctx
        assert text and text != "(no actionable elements found)"

    def test_every_token_in_text_has_map_entry(self, ctx):
        """Core invariant: no [eN] in text without a map key."""
        text, id_map = ctx
        tokens = _element_ids_in_text(text)
        missing = tokens - id_map.keys()
        assert not missing, (
            f"Snapshot text references element_id(s) not in id_map: {missing}\n"
            f"Snapshot:\n{text}"
        )

    def test_every_map_key_appears_in_text(self, ctx):
        """Inverse: no key in map that doesn't appear in the snapshot text."""
        text, id_map = ctx
        tokens = _element_ids_in_text(text)
        extra = id_map.keys() - tokens
        assert not extra, (
            f"id_map contains element_id(s) not referenced in snapshot text: {extra}\n"
            f"Snapshot:\n{text}"
        )

    def test_actionable_elements_present(self, ctx):
        """Field (enabled), Button, ComboBox must appear."""
        text, id_map = ctx
        assert "e2" in id_map, "enabled Field should be in map"
        assert "e3" in id_map, "enabled Button should be in map"
        assert "e7" in id_map, "enabled ComboBox should be in map"

    def test_non_actionable_role_excluded(self, ctx):
        """Panel is not in _ACTION_SNAPSHOT_ROLES — must be absent."""
        text, id_map = ctx
        assert "e4" not in id_map, "Panel should not be in map"
        assert "e4" not in _element_ids_in_text(text), "Panel should not appear in text"

    def test_disabled_field_excluded(self, ctx):
        """A Field without 'enabled' in states must be absent."""
        text, id_map = ctx
        assert "e5" not in id_map, "Disabled Field should not be in map"
        assert "e5" not in _element_ids_in_text(text), "Disabled Field should not appear in text"

    def test_toolbar_exempt_from_enabled_check(self, ctx):
        """Toolbar is allowed even when not enabled."""
        text, id_map = ctx
        assert "e6" in id_map, "Toolbar should be in map regardless of enabled state"

    def test_window_excluded(self, ctx):
        """Window-role node must not appear."""
        text, id_map = ctx
        assert "e1" not in id_map, "Window should not be in map"
        assert "e1" not in _element_ids_in_text(text), "Window should not appear in text"

    def test_map_values_are_full_repo_elements(self, ctx):
        """Each map value must be the full repo element with expected keys."""
        _, id_map = ctx
        required_keys = {"elementid", "role", "name", "surface", "java"}
        for eid, element in id_map.items():
            missing = required_keys - element.keys()
            assert not missing, f"id_map[{eid!r}] missing keys: {missing}"
            assert element["elementid"] == eid, (
                f"id_map key {eid!r} does not match element['elementid'] {element['elementid']!r}"
            )

    def test_empty_scan_returns_no_action_elements(self):
        text, id_map = build_action_context({"windows": []})
        assert id_map == {}
        assert text == "(no actionable elements found)"

    def test_snapshot_text_contains_role_and_name(self, ctx):
        """Spot-check that the text has the expected '[eN] Name | Role' format."""
        text, _ = ctx
        assert "Find | Button" in text
        assert "PO Number | Field" in text

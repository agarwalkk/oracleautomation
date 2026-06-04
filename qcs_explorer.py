"""QCS DOM Explorer — interactive tree + screenshot overlay viewer.

Usage:
    python qcs_explorer.py                          # uses ./scan_dump.json + ./screenshot.png
    python qcs_explorer.py <snapshot_dir>            # uses <dir>/java_scan_dump.json + <dir>/screenshot.png
    python qcs_explorer.py --scan s.json --img i.png # explicit paths
    python qcs_explorer.py --port 8765               # custom port
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from qcs_java_agent.snapshot import (
    active_window_scan,
    active_form_title,
    _oracle_forms_active_frame,
    flatten_nodes,
    java_nodes_to_repo_elements,
    _actionable_elements,
    _detect_tabs,
    _group_by_rows,
    _detect_table,
    _detect_record_indicator_y,
    _extract_tools_menu,
    _looks_like_technical_name,
    _strip_tab_prefix,
    _format_field,
    _format_button,
    _LOV_SUFFIX,
    _ALT_KEY_SUFFIX,
    ACTIONABLE_ROLES,
    _ORACLE_FORMS_DIALOG_CLASSES,
    _ORACLE_FORMS_POPUP_CLASSES,
)


# ── Condensed tree builder (mirrors build_action_context logic) ───────────

def _el_bounds(el: dict, origin_x: int, origin_y: int) -> dict | None:
    """Screenshot-relative bounds from a repo element."""
    x = el.get("x", -1)
    y = el.get("y", -1)
    w = el.get("width", 0)
    h = el.get("height", 0)
    if w <= 0 or h <= 0:
        return None
    return {"x": x - origin_x, "y": y - origin_y, "w": w, "h": h}


def _el_to_node(el: dict, display: str, origin_x: int, origin_y: int, *, kind: str = "field") -> dict:
    """Convert a repo element to a condensed tree node."""
    java = el.get("java") or {}
    return {
        "id": (java.get("id") if java.get("id") is not None
               else el.get("elementid", "").lstrip("e") or None),
        "eid": el.get("elementid", ""),
        "display": display,
        "role": str(el.get("role") or ""),
        "kind": kind,
        "bounds": _el_bounds(el, origin_x, origin_y),
        "path": java.get("path") or el.get("path") or "",
        "locators": java.get("locators") or [],
        "text": (el.get("text") or "").strip(),
        "states": el.get("states") or [],
        "cls": str(java.get("simpleClassName") or ""),
        "children": [],
    }


def build_condensed_tree(scan: dict, origin_x: int, origin_y: int) -> list[dict]:
    """Build a user-friendly tree using the same logic as build_action_context."""
    scoped = active_window_scan(scan)
    all_elements = java_nodes_to_repo_elements(scoped)
    actionable = _actionable_elements(all_elements)
    scoped_nodes = flatten_nodes(scoped)
    tab_info = _detect_tabs(scoped_nodes)
    form_title = active_form_title(scoped)

    root: dict = {
        "id": None, "eid": "", "display": f"Form: {form_title}",
        "role": "Form", "kind": "form", "bounds": None,
        "path": "", "locators": [], "text": "", "states": [], "cls": "",
        "children": [],
    }

    if tab_info:
        _build_tabbed_tree(
            root, scan, scoped, scoped_nodes, all_elements, actionable,
            tab_info, origin_x, origin_y,
        )
    else:
        root_cls = ""
        if scoped_nodes:
            root_cls = str(scoped_nodes[0].get("simpleClassName") or "")
        if root_cls in _ORACLE_FORMS_DIALOG_CLASSES:
            _build_dialog_tree(root, scoped_nodes, actionable, origin_x, origin_y)
        elif root_cls in _ORACLE_FORMS_POPUP_CLASSES:
            _build_popup_tree(root, scoped_nodes, actionable, origin_x, origin_y)
        else:
            for el in actionable:
                display = _format_field(el)
                root["children"].append(_el_to_node(el, display, origin_x, origin_y))

    return [root]


def _build_tabbed_tree(
    root: dict, scan: dict, scoped: dict, scoped_nodes: list[dict],
    all_elements: list[dict], actionable: list[dict],
    tab_info: dict, origin_x: int, origin_y: int,
) -> None:
    selected_tab = tab_info["selected"]
    tab_titles = tab_info["titles"]
    tab_states = tab_info["tab_states"]
    tab_content_ids = tab_info["selected_content_ids"]
    form_level_ids = tab_info["form_level_ids"]
    tab_bar_y = tab_info["tab_bar_y"]
    tab_bar_element_id = tab_info.get("tab_bar_element_id")
    outer_tab_bars = tab_info.get("outer_tab_bars") or []

    # Partition elements (same logic as build_action_context)
    tab_elements: list[dict] = []
    button_elements: list[dict] = []
    tree_elements: list[dict] = []
    form_field_elements: list[dict] = []

    human_field_names: set[str] = set()
    for el in all_elements:
        name = el.get("name") or ""
        if not _looks_like_technical_name(name) and name:
            stripped = name
            if selected_tab:
                pfx = f"{selected_tab} tab page "
                if stripped.startswith(pfx):
                    stripped = stripped[len(pfx):]
            human_field_names.add(stripped)

    for el in actionable:
        eid = el.get("elementid", "")
        role = str(el.get("role") or "")
        name = el.get("name") or ""
        if role in ("Toolbar", "Menu", "MenuItem", "Tab"):
            continue
        if _looks_like_technical_name(name):
            text = (el.get("text") or "").strip()
            w = el.get("width", 0)
            if not text or text in human_field_names or w <= 15:
                continue
        if eid in form_level_ids:
            if role == "Button":
                button_elements.append(el)
            elif role == "Tree":
                tree_elements.append(el)
            else:
                form_field_elements.append(el)
        elif eid in tab_content_ids:
            tab_elements.append(el)

    # Re-add display-only Panels with data values
    included_ids = {el.get("elementid") for el in tab_elements}
    for el in all_elements:
        eid = el.get("elementid", "")
        if eid in included_ids:
            continue
        role = str(el.get("role") or "")
        if role != "Panel":
            continue
        states = el.get("states") or []
        if "showing" not in states:
            continue
        name = el.get("name") or ""
        text = (el.get("text") or "").strip()
        if not name or not text or _looks_like_technical_name(name):
            continue
        if text == name:
            continue
        w = el.get("width", 0)
        if w <= 0:
            continue
        if eid in tab_content_ids:
            tab_elements.append(el)

    # Split buttons
    toolbar_buttons: list[dict] = []
    footer_buttons: list[dict] = []
    for el in button_elements:
        if el.get("y", 0) < tab_bar_y:
            toolbar_buttons.append(el)
        else:
            footer_buttons.append(el)

    # Deduplicate
    if tab_elements:
        tab_btn_positions = set()
        for el in tab_elements:
            if str(el.get("role") or "") == "Button":
                tab_btn_positions.add((el.get("x", 0), el.get("y", 0)))
        if tab_btn_positions:
            _TOL = 5
            def _overlaps(el: dict) -> bool:
                ex, ey = el.get("x", 0), el.get("y", 0)
                return any(abs(ex - tx) <= _TOL and abs(ey - ty) <= _TOL
                           for tx, ty in tab_btn_positions)
            toolbar_buttons = [b for b in toolbar_buttons if not _overlaps(b)]
            footer_buttons = [b for b in footer_buttons if not _overlaps(b)]

    tab_page_prefix = f"{selected_tab} tab page " if selected_tab else None

    # -- Build tree structure --

    # Toolbar buttons
    if toolbar_buttons:
        tb_group = _make_group("Toolbar Buttons", "toolbar")
        for el in toolbar_buttons:
            display = _format_button(el, tab_page_prefix)
            tb_group["children"].append(_el_to_node(el, display, origin_x, origin_y, kind="button"))
        root["children"].append(tb_group)

    # Sidebar trees (left of tabs)
    tree_before_tabs = False
    if tree_elements and tab_bar_element_id:
        tree_min_x = min(el.get("x", 9999) for el in tree_elements)
        tab_bar_x = 9999
        for n in scoped_nodes:
            if f"e{n.get('id', '')}" == tab_bar_element_id:
                tab_bar_x = (n.get("screenBounds") or {}).get("x", 9999)
                break
        if tree_min_x < tab_bar_x:
            tree_before_tabs = True

    if tree_before_tabs:
        for el in tree_elements:
            display = f"[{el.get('elementid','')}] Tree"
            root["children"].append(_el_to_node(el, display, origin_x, origin_y, kind="tree"))

    # Outer tab bars
    for otb in outer_tab_bars:
        labels = []
        for i, t in enumerate(otb["titles"]):
            st = otb["tab_states"][i] if i < len(otb["tab_states"]) else {}
            if not st.get("visible", True):
                continue
            if t == otb.get("selected"):
                labels.append(f"[*{t}*]")
            elif not st.get("enabled", True):
                labels.append(f"{t} (disabled)")
            else:
                labels.append(t)
        otb_node = _make_group(f"Tabs: {' | '.join(labels)}", "outer-tabs")
        otb_eid = otb.get("element_id") or ""
        otb_node["eid"] = otb_eid
        root["children"].append(otb_node)

    # Inner tab bar
    tab_labels = []
    for i, t in enumerate(tab_titles):
        st = tab_states[i] if i < len(tab_states) else {}
        if not st.get("visible", True):
            continue
        if t == selected_tab:
            tab_labels.append(f"[*{t}*]")
        elif not st.get("enabled", True):
            tab_labels.append(f"{t} (disabled)")
        else:
            tab_labels.append(t)

    tabs_node = _make_group(f"Tabs: {' | '.join(tab_labels)}", "tabs")
    tabs_node["eid"] = tab_bar_element_id or ""
    root["children"].append(tabs_node)

    # Tab content: detect table vs standalone rows
    rows = _group_by_rows(tab_elements)
    table_rows, pre_rows, post_rows = _detect_table(rows, tab_page_prefix)
    selected_y = _detect_record_indicator_y(scoped_nodes, tab_content_ids)

    # Pre-table rows
    for row in pre_rows:
        row_node = _make_row_node(row, tab_page_prefix, origin_x, origin_y)
        tabs_node["children"].append(row_node)

    # Table
    if table_rows:
        table_node = _make_group(f"Table ({len(table_rows)} rows)", "table")
        for row_idx, row in enumerate(table_rows):
            is_selected = False
            if selected_y is not None and row:
                row_y = row[0].get("y", 0)
                is_selected = abs(row_y - selected_y) <= 10
            marker = f"Row {row_idx + 1}" + (" ●" if is_selected else "")
            row_group = _make_group(marker, "table-row")
            for el in row:
                raw_name = el.get("name") or ""
                if _looks_like_technical_name(raw_name):
                    continue
                display = _format_field(el, tab_page_prefix)
                row_group["children"].append(
                    _el_to_node(el, display, origin_x, origin_y)
                )
            _compute_group_bounds(row_group)
            table_node["children"].append(row_group)
        tabs_node["children"].append(table_node)

    # Post-table rows
    for row in post_rows:
        row_node = _make_row_node(row, tab_page_prefix, origin_x, origin_y)
        tabs_node["children"].append(row_node)

    # Form-level fields
    if form_field_elements:
        form_group = _make_group("Form Fields", "form-fields")
        form_rows = _group_by_rows(form_field_elements)
        for row in form_rows:
            row_node = _make_row_node(row, tab_page_prefix, origin_x, origin_y)
            form_group["children"].append(row_node)
        root["children"].append(form_group)

    # Footer buttons
    if footer_buttons:
        btn_group = _make_group("Buttons", "buttons")
        for el in footer_buttons:
            display = _format_button(el, tab_page_prefix)
            btn_group["children"].append(_el_to_node(el, display, origin_x, origin_y, kind="button"))
        root["children"].append(btn_group)

    # Trees (right-side or below tabs)
    if not tree_before_tabs:
        for el in (tree_elements or []):
            display = f"[{el.get('elementid','')}] Tree"
            root["children"].append(_el_to_node(el, display, origin_x, origin_y, kind="tree"))

    # Tools menu
    tools_menu_items = _extract_tools_menu(flatten_nodes(scan))
    if tools_menu_items:
        menu_group = _make_group("Tools Menu", "menu")
        for label, is_checkbox, checked in tools_menu_items:
            if is_checkbox:
                mark = "[x]" if checked else "[ ]"
                display = f"{mark} {label}"
            else:
                display = label
            menu_group["children"].append({
                "id": None, "eid": "", "display": display,
                "role": "MenuItem", "kind": "menu-item", "bounds": None,
                "path": "", "locators": [], "text": "", "states": [], "cls": "",
                "children": [],
            })
        root["children"].append(menu_group)


def _build_dialog_tree(
    root: dict, nodes: list[dict], actionable: list[dict],
    origin_x: int, origin_y: int,
) -> None:
    root["kind"] = "dialog"
    for n in nodes:
        cls = str(n.get("simpleClassName") or "")
        if cls == "MultiLineLabel":
            msg = str(n.get("accessibleName") or "").strip()
            if msg:
                root["children"].append({
                    "id": None, "eid": "", "display": f"Message: {msg}",
                    "role": "Label", "kind": "message", "bounds": None,
                    "path": "", "locators": [], "text": msg, "states": [], "cls": cls,
                    "children": [],
                })
                break
    _REAL_BTNS = {"PushButton", "FormButton"}
    for el in actionable:
        if str(el.get("role") or "") == "Button":
            java_cls = str((el.get("java") or {}).get("simpleClassName") or "")
            if java_cls in _REAL_BTNS:
                display = _format_button(el)
                root["children"].append(_el_to_node(el, display, origin_x, origin_y, kind="button"))


def _build_popup_tree(
    root: dict, nodes: list[dict], actionable: list[dict],
    origin_x: int, origin_y: int,
) -> None:
    root["kind"] = "popup"
    for el in actionable:
        role = str(el.get("role") or "")
        if role in ("Field", "ComboBox"):
            display = _format_field(el)
            root["children"].append(_el_to_node(el, display, origin_x, origin_y))
    _REAL_BTNS = {"PushButton", "FormButton"}
    for el in actionable:
        if str(el.get("role") or "") == "Button":
            java_cls = str((el.get("java") or {}).get("simpleClassName") or "")
            if java_cls in _REAL_BTNS:
                display = _format_button(el)
                root["children"].append(_el_to_node(el, display, origin_x, origin_y, kind="button"))


def _make_group(label: str, kind: str) -> dict:
    return {
        "id": None, "eid": "", "display": label,
        "role": "", "kind": kind, "bounds": None,
        "path": "", "locators": [], "text": "", "states": [], "cls": "",
        "children": [],
    }


def _make_row_node(row: list[dict], prefix: str | None, ox: int, oy: int) -> dict:
    """Create a row group node containing field nodes."""
    if len(row) == 1:
        el = row[0]
        role = str(el.get("role") or "")
        if role == "Button":
            return _el_to_node(el, _format_button(el, prefix), ox, oy, kind="button")
        return _el_to_node(el, _format_field(el, prefix), ox, oy)
    # Multi-field row: group them
    labels = []
    for el in row[:3]:
        name = el.get("name") or ""
        name = _strip_tab_prefix(name, prefix)
        name = _LOV_SUFFIX.sub("", name)
        if name and not _looks_like_technical_name(name):
            labels.append(name)
    summary = ", ".join(labels)
    if len(row) > 3:
        summary += f" … (+{len(row) - 3})"
    group = _make_group(summary or f"Row ({len(row)} fields)", "row")
    for el in row:
        role = str(el.get("role") or "")
        if role == "Button":
            display = _format_button(el, prefix)
            group["children"].append(_el_to_node(el, display, ox, oy, kind="button"))
        else:
            display = _format_field(el, prefix)
            group["children"].append(_el_to_node(el, display, ox, oy))
    _compute_group_bounds(group)
    return group


def _compute_group_bounds(group: dict) -> None:
    """Set group bounds to the bounding box of all children."""
    xs, ys, x2s, y2s = [], [], [], []
    for ch in group["children"]:
        b = ch.get("bounds")
        if b:
            xs.append(b["x"]); ys.append(b["y"])
            x2s.append(b["x"] + b["w"]); y2s.append(b["y"] + b["h"])
    if xs:
        group["bounds"] = {
            "x": min(xs), "y": min(ys),
            "w": max(x2s) - min(xs), "h": max(y2s) - min(ys),
        }


# ── Raw DOM tree builder (for "Raw DOM" tab) ─────────────────────────────

def _node_label(node: dict) -> str:
    name = node.get("accessibleName") or node.get("displayName") or node.get("name") or ""
    if name and name != "null":
        return str(name)
    text = node.get("text") or node.get("value") or ""
    if text and text != "null":
        return str(text)
    return ""


def build_raw_tree(scan: dict, origin_x: int, origin_y: int, scope_root: dict | None = None) -> list[dict]:
    """Full Java DOM tree."""
    def convert(node: dict) -> dict:
        sb = node.get("screenBounds") or {}
        bounds = None
        if sb.get("width") and sb.get("height"):
            bounds = {
                "x": (sb.get("x", 0) - origin_x),
                "y": (sb.get("y", 0) - origin_y),
                "w": sb.get("width", 0),
                "h": sb.get("height", 0),
            }
        children = [convert(c) for c in (node.get("children") or [])]
        role = str(node.get("semanticType") or node.get("accessibleRole") or "")
        cls = node.get("simpleClassName") or ""
        label = _node_label(node)
        return {
            "id": node.get("id"), "eid": f"e{node.get('id', '')}",
            "display": f"{cls}: {label}" if label else cls,
            "role": role, "kind": "dom",
            "bounds": bounds, "path": node.get("path") or "",
            "locators": node.get("locators") or [],
            "text": str(node.get("text") or ""), "states": [],
            "cls": cls, "children": children,
        }
    if scope_root:
        return [convert(scope_root)]
    return [convert(w) for w in (scan.get("windows") or [])]


# ── HTTP handler ──────────────────────────────────────────────────────────

class ExplorerHandler(BaseHTTPRequestHandler):
    condensed_json: str = "[]"
    raw_json: str = "[]"
    snapshot_text: str = ""
    screenshot_path: Path | None = None
    html_content: str = ""

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._serve(200, "text/html", self.html_content.encode())
        elif self.path == "/api/tree":
            self._serve(200, "application/json", self.condensed_json.encode())
        elif self.path == "/api/raw":
            self._serve(200, "application/json", self.raw_json.encode())
        elif self.path == "/api/snapshot":
            self._serve(200, "text/plain", self.snapshot_text.encode())
        elif self.path == "/screenshot.png" and self.screenshot_path:
            data = self.screenshot_path.read_bytes()
            self._serve(200, "image/png", data)
        else:
            self._serve(404, "text/plain", b"Not Found")

    def _serve(self, code: int, content_type: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


# ── HTML ──────────────────────────────────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>QCS DOM Explorer</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', system-ui, sans-serif; background: #1e1e1e; color: #d4d4d4; height: 100vh; overflow: hidden; }
.layout { display: flex; height: 100vh; }

/* Left panel */
.tree-panel {
  width: 480px; min-width: 280px; max-width: 55vw;
  display: flex; flex-direction: column;
  border-right: 2px solid #333; background: #252526;
}
.tree-header {
  padding: 8px 12px; font-size: 12px; font-weight: 600;
  background: #2d2d30; border-bottom: 1px solid #333;
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
}
.view-tabs { display: flex; gap: 2px; }
.view-tab {
  padding: 3px 10px; background: #3c3c3c; color: #888;
  border: 1px solid #555; border-radius: 3px 3px 0 0; cursor: pointer;
  font-size: 11px; font-weight: 600;
}
.view-tab.active { background: #252526; color: #d4d4d4; border-bottom-color: #252526; }
.view-tab:hover { color: #d4d4d4; }
.tree-header input {
  flex: 1; min-width: 100px; padding: 3px 8px; background: #3c3c3c; color: #d4d4d4;
  border: 1px solid #555; border-radius: 3px; font-size: 11px; outline: none;
}
.tree-header input:focus { border-color: #007acc; }
.tree-scroll { flex: 1; overflow: auto; padding: 4px 0; font-size: 12px; }

/* Tree rows */
.tree-row {
  display: flex; align-items: center; gap: 3px;
  padding: 2px 6px 2px 0; cursor: pointer; white-space: nowrap;
  border: 1px solid transparent; border-radius: 2px; user-select: none;
}
.tree-row:hover { background: #2a2d2e; }
.tree-row.highlight { background: #264f78; border-color: #007acc; }
.tree-row.selected { background: #094771; }
.tree-row.search-match { background: #613214; }
.toggle { width: 14px; text-align: center; font-size: 10px; color: #888; flex-shrink: 0; }
.toggle.has-children { cursor: pointer; }
.toggle.has-children:hover { color: #fff; }

/* Kind-based styling */
.node-display { font-size: 11px; }
.kind-form .node-display { color: #dcdcaa; font-weight: 700; font-size: 12px; }
.kind-tabs .node-display, .kind-outer-tabs .node-display { color: #c586c0; font-weight: 600; }
.kind-toolbar .node-display, .kind-buttons .node-display { color: #569cd6; font-weight: 600; }
.kind-menu .node-display { color: #569cd6; font-weight: 600; }
.kind-table .node-display { color: #4ec9b0; font-weight: 600; }
.kind-table-row .node-display { color: #9cdcfe; }
.kind-row .node-display { color: #d4d4d4; }
.kind-form-fields .node-display { color: #569cd6; font-weight: 600; }
.kind-field .node-display { color: #ce9178; }
.kind-button .node-display { color: #4fc1ff; }
.kind-menu-item .node-display { color: #d4d4d4; }
.kind-message .node-display { color: #d7ba7d; }
.kind-dom .node-display { color: #9cdcfe; }
.node-eid { color: #6a9955; margin-left: 4px; font-size: 10px; }

/* Right panel */
.right-panel { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.screenshot-area {
  flex: 1; overflow: auto; position: relative; background: #1a1a1a;
  display: flex; align-items: flex-start; justify-content: flex-start;
}
.screenshot-container { position: relative; display: inline-block; transform-origin: top left; }
.screenshot-container img { display: block; }
.overlay-box {
  position: absolute; border: 2px solid rgba(0, 122, 204, 0.8);
  background: rgba(0, 122, 204, 0.15); pointer-events: none; z-index: 10;
}
.overlay-box.hover-hit {
  border-color: rgba(255, 165, 0, 0.9);
  background: rgba(255, 165, 0, 0.2); z-index: 20;
}
.hover-tooltip {
  position: absolute; background: rgba(30, 30, 30, 0.95);
  color: #d4d4d4; font-size: 12px; padding: 6px 10px;
  border: 1px solid #007acc; border-radius: 4px;
  pointer-events: none; z-index: 100; white-space: nowrap;
  max-width: 500px; overflow: hidden; text-overflow: ellipsis; display: none;
}

/* Detail panel */
.detail-panel {
  height: 200px; min-height: 80px;
  border-top: 2px solid #333; background: #252526;
  overflow: auto; padding: 10px 14px; font-size: 12px;
}
.detail-panel h3 { color: #007acc; font-size: 13px; margin-bottom: 8px; }
.detail-row { margin-bottom: 3px; }
.detail-key { color: #9cdcfe; display: inline-block; width: 110px; }
.detail-val { color: #ce9178; word-break: break-all; }
.detail-path {
  color: #6a9955; font-family: 'Cascadia Code', 'Consolas', monospace;
  font-size: 11px; padding: 6px 8px; background: #1e1e1e;
  border-radius: 3px; margin-top: 6px; overflow-x: auto; white-space: pre;
  user-select: all; cursor: text;
}

/* Snapshot text panel */
.snapshot-panel {
  display: none; flex: 1; overflow: auto; padding: 10px 14px;
  font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 11px;
  color: #d4d4d4; white-space: pre; background: #1e1e1e;
}

/* Controls */
.zoom-controls {
  position: absolute; top: 8px; right: 20px; display: flex; gap: 4px; z-index: 50;
}
.zoom-btn {
  background: #333; color: #d4d4d4; border: 1px solid #555;
  padding: 3px 10px; cursor: pointer; border-radius: 3px; font-size: 12px;
}
.zoom-btn:hover { background: #444; }
.resize-handle { width: 5px; cursor: col-resize; background: transparent; flex-shrink: 0; }
.resize-handle:hover { background: #007acc; }
</style>
</head>
<body>
<div class="layout">
  <div class="tree-panel" id="treePanel">
    <div class="tree-header">
      <div class="view-tabs">
        <div class="view-tab active" data-view="condensed" onclick="switchView('condensed')">Snapshot</div>
        <div class="view-tab" data-view="raw" onclick="switchView('raw')">Raw DOM</div>
        <div class="view-tab" data-view="text" onclick="switchView('text')">Text</div>
      </div>
      <input type="text" id="searchBox" placeholder="Filter…" />
    </div>
    <div class="tree-scroll" id="treeScroll"></div>
    <div class="snapshot-panel" id="snapshotPanel"></div>
  </div>
  <div class="resize-handle" id="resizeHandle"></div>
  <div class="right-panel">
    <div class="screenshot-area" id="screenshotArea">
      <div class="zoom-controls">
        <button class="zoom-btn" onclick="zoom(-0.1)">−</button>
        <button class="zoom-btn" id="zoomLabel" onclick="zoom(0)">100%</button>
        <button class="zoom-btn" onclick="zoom(0.1)">+</button>
        <button class="zoom-btn" onclick="fitZoom()">Fit</button>
      </div>
      <div class="screenshot-container" id="screenshotContainer">
        <img id="screenshotImg" src="/screenshot.png" draggable="false" />
        <div class="hover-tooltip" id="hoverTooltip"></div>
      </div>
    </div>
    <div class="detail-panel" id="detailPanel">
      <h3>Element Details</h3>
      <div style="color:#888">Click a tree node to see details</div>
    </div>
  </div>
</div>

<script>
let TREES = {};
let currentView = 'condensed';
let flatNodes = [];
let currentScale = 1.0;
let selectedNode = null;
let highlightedNode = null;

async function init() {
  const [cResp, rResp, sResp] = await Promise.all([
    fetch('/api/tree'), fetch('/api/raw'), fetch('/api/snapshot')
  ]);
  TREES.condensed = await cResp.json();
  TREES.raw = await rResp.json();
  const snapshotText = await sResp.text();
  document.getElementById('snapshotPanel').textContent = snapshotText;
  rebuildFlat();
  renderTree();
  document.getElementById('screenshotImg').onload = fitZoom;
}

function rebuildFlat() {
  flatNodes = [];
  flattenAll(TREES[currentView] || [], 0);
}

function flattenAll(nodes, depth) {
  for (const n of nodes) {
    n._depth = depth;
    n._expanded = (currentView === 'condensed') ? depth < 3 : depth < 2;
    n._el = null;
    flatNodes.push(n);
    if (n.children) flattenAll(n.children, depth + 1);
  }
}

function switchView(view) {
  currentView = view;
  document.querySelectorAll('.view-tab').forEach(t => t.classList.toggle('active', t.dataset.view === view));
  const treeScroll = document.getElementById('treeScroll');
  const snapshotPanel = document.getElementById('snapshotPanel');
  if (view === 'text') {
    treeScroll.style.display = 'none';
    snapshotPanel.style.display = 'block';
  } else {
    treeScroll.style.display = 'block';
    snapshotPanel.style.display = 'none';
    rebuildFlat();
    renderTree(document.getElementById('searchBox').value.trim());
  }
}

function renderTree(filter) {
  filter = filter || '';
  const container = document.getElementById('treeScroll');
  container.innerHTML = '';
  const fl = filter.toLowerCase();

  function matches(node) {
    if (!fl) return true;
    const t = (node.display + ' ' + node.eid + ' ' + node.role + ' ' + node.kind).toLowerCase();
    if (t.indexOf(fl) >= 0) return true;
    return (node.children || []).some(c => matches(c));
  }

  function render(node, indent) {
    if (!matches(node)) return;
    const hasKids = node.children && node.children.length > 0;
    const isMatch = fl && (node.display + ' ' + node.eid + ' ' + node.role + ' ' + node.kind).toLowerCase().indexOf(fl) >= 0;
    if (fl) node._expanded = true;

    const row = document.createElement('div');
    row.className = 'tree-row kind-' + (node.kind || 'field') + (isMatch ? ' search-match' : '');
    row.style.paddingLeft = (indent * 14 + 4) + 'px';
    node._el = row;

    const toggle = document.createElement('span');
    toggle.className = 'toggle' + (hasKids ? ' has-children' : '');
    toggle.textContent = hasKids ? (node._expanded ? '\u25BC' : '\u25B6') : ' ';
    if (hasKids) {
      toggle.addEventListener('click', function(e) {
        e.stopPropagation();
        node._expanded = !node._expanded;
        renderTree(filter);
      });
    }
    row.appendChild(toggle);

    const disp = document.createElement('span');
    disp.className = 'node-display';
    disp.textContent = truncate(node.display, 70);
    row.appendChild(disp);

    if (node.eid) {
      const eidSpan = document.createElement('span');
      eidSpan.className = 'node-eid';
      eidSpan.textContent = node.eid;
      row.appendChild(eidSpan);
    }

    row.addEventListener('mouseenter', function() { highlightOnScreenshot(node); });
    row.addEventListener('mouseleave', function() { clearHighlight(); });
    row.addEventListener('click', function() { selectNode(node); });
    container.appendChild(row);

    if (hasKids && node._expanded) {
      for (var i = 0; i < node.children.length; i++) render(node.children[i], indent + 1);
    }
  }

  var tree = TREES[currentView] || [];
  for (var i = 0; i < tree.length; i++) render(tree[i], 0);
}

function truncate(s, n) { return s.length > n ? s.substring(0, n) + '\u2026' : s; }

// Highlight
var activeOverlay = null;
function highlightOnScreenshot(node) {
  clearHighlight();
  highlightedNode = node;
  if (!node.bounds) return;
  var b = node.bounds;
  var box = document.createElement('div');
  box.className = 'overlay-box';
  box.style.left = b.x + 'px'; box.style.top = b.y + 'px';
  box.style.width = b.w + 'px'; box.style.height = b.h + 'px';
  document.getElementById('screenshotContainer').appendChild(box);
  activeOverlay = box;
  if (node._el) node._el.classList.add('highlight');
}
function clearHighlight() {
  if (activeOverlay) { activeOverlay.remove(); activeOverlay = null; }
  if (highlightedNode && highlightedNode._el) highlightedNode._el.classList.remove('highlight');
  highlightedNode = null;
}

// Screenshot hover
document.addEventListener('DOMContentLoaded', function() {
  var container = document.getElementById('screenshotContainer');
  var tooltip = document.getElementById('hoverTooltip');
  var hoverOverlay = null;

  container.addEventListener('mousemove', function(e) {
    var rect = container.getBoundingClientRect();
    var x = (e.clientX - rect.left) / currentScale;
    var y = (e.clientY - rect.top) / currentScale;
    var best = null, bestArea = Infinity;
    for (var i = 0; i < flatNodes.length; i++) {
      var n = flatNodes[i];
      if (!n.bounds) continue;
      var b = n.bounds;
      if (x >= b.x && x < b.x + b.w && y >= b.y && y < b.y + b.h) {
        var area = b.w * b.h;
        if (area < bestArea) { bestArea = area; best = n; }
      }
    }
    if (hoverOverlay) { hoverOverlay.remove(); hoverOverlay = null; }
    if (best && best.bounds) {
      var b = best.bounds;
      var box = document.createElement('div');
      box.className = 'overlay-box hover-hit';
      box.style.left = b.x + 'px'; box.style.top = b.y + 'px';
      box.style.width = b.w + 'px'; box.style.height = b.h + 'px';
      container.appendChild(box);
      hoverOverlay = box;
      tooltip.style.display = 'block';
      tooltip.textContent = (best.eid ? '['+best.eid+'] ' : '') + best.display;
      var tx = e.clientX - rect.left + 12, ty = e.clientY - rect.top - 28;
      if (tx + 350 > rect.width) tx = e.clientX - rect.left - 200;
      if (ty < 0) ty = e.clientY - rect.top + 18;
      tooltip.style.left = (tx / currentScale) + 'px';
      tooltip.style.top = (ty / currentScale) + 'px';
      if (best._el) {
        document.querySelectorAll('.tree-row.highlight').forEach(function(r) { r.classList.remove('highlight'); });
        best._el.classList.add('highlight');
        best._el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      }
    } else {
      tooltip.style.display = 'none';
      document.querySelectorAll('.tree-row.highlight').forEach(function(r) { r.classList.remove('highlight'); });
    }
  });

  container.addEventListener('mouseleave', function() {
    if (hoverOverlay) { hoverOverlay.remove(); hoverOverlay = null; }
    tooltip.style.display = 'none';
    document.querySelectorAll('.tree-row.highlight').forEach(function(r) { r.classList.remove('highlight'); });
  });

  container.addEventListener('click', function(e) {
    var rect = container.getBoundingClientRect();
    var x = (e.clientX - rect.left) / currentScale;
    var y = (e.clientY - rect.top) / currentScale;
    var best = null, bestArea = Infinity;
    for (var i = 0; i < flatNodes.length; i++) {
      var n = flatNodes[i];
      if (!n.bounds) continue;
      var b = n.bounds;
      if (x >= b.x && x < b.x + b.w && y >= b.y && y < b.y + b.h) {
        var area = b.w * b.h;
        if (area < bestArea) { bestArea = area; best = n; }
      }
    }
    if (best) selectNode(best);
  });
});

// Selection
function selectNode(node) {
  document.querySelectorAll('.tree-row.selected').forEach(function(r) { r.classList.remove('selected'); });
  selectedNode = node;
  if (node._el) {
    node._el.classList.add('selected');
    node._el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
  highlightOnScreenshot(node);
  var panel = document.getElementById('detailPanel');
  var html = '<h3>Element Details</h3>';
  html += dRow('Element ID', node.eid || '\u2014');
  html += dRow('Display', node.display);
  html += dRow('Kind', node.kind);
  html += dRow('Role', node.role || '\u2014');
  html += dRow('Class', node.cls || '\u2014');
  if (node.text) html += dRow('Value', node.text);
  if (node.states && node.states.length) html += dRow('States', node.states.join(', '));
  if (node.bounds) html += dRow('Bounds', 'x=' + node.bounds.x + ' y=' + node.bounds.y + ' w=' + node.bounds.w + ' h=' + node.bounds.h);
  if (node.locators && node.locators.length) {
    html += '<div class="detail-row" style="margin-top:5px"><span class="detail-key">Locators:</span></div>';
    for (var i = 0; i < node.locators.length; i++) {
      var l = node.locators[i];
      html += '<div class="detail-row" style="padding-left:16px"><span class="detail-key">' + (l.strategy||'?') + '</span><span class="detail-val">' + esc(l.value||'') + '</span></div>';
    }
  }
  if (node.path) {
    html += '<div style="margin-top:6px"><span class="detail-key">Java Path:</span></div>';
    html += '<div class="detail-path">' + esc(node.path) + '</div>';
  }
  panel.innerHTML = html;
}
function dRow(k, v) { return '<div class="detail-row"><span class="detail-key">' + k + '</span><span class="detail-val">' + esc(String(v)) + '</span></div>'; }
function esc(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

// Zoom
function zoom(d) {
  if (d === 0) currentScale = 1.0;
  else currentScale = Math.max(0.2, Math.min(3.0, currentScale + d));
  applyZoom();
}
function fitZoom() {
  var a = document.getElementById('screenshotArea');
  var img = document.getElementById('screenshotImg');
  if (!img.naturalWidth) return;
  currentScale = Math.min((a.clientWidth-20)/img.naturalWidth, (a.clientHeight-20)/img.naturalHeight, 1.5);
  applyZoom();
}
function applyZoom() {
  document.getElementById('screenshotContainer').style.transform = 'scale(' + currentScale + ')';
  document.getElementById('zoomLabel').textContent = Math.round(currentScale*100) + '%';
}

// Search
document.addEventListener('DOMContentLoaded', function() {
  var box = document.getElementById('searchBox');
  var t = null;
  box.addEventListener('input', function() { clearTimeout(t); t = setTimeout(function() { renderTree(box.value.trim()); }, 150); });
});

// Panel resize
document.addEventListener('DOMContentLoaded', function() {
  var h = document.getElementById('resizeHandle');
  var p = document.getElementById('treePanel');
  var d = false;
  h.addEventListener('mousedown', function(e) { d = true; e.preventDefault(); });
  document.addEventListener('mousemove', function(e) { if (d) p.style.width = Math.max(200, Math.min(window.innerWidth*0.6, e.clientX)) + 'px'; });
  document.addEventListener('mouseup', function() { d = false; });
});

init();
</script>
</body>
</html>
"""


# ── Main ──────────────────────────────────────────────────────────────────────

def resolve_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    if args.scan and args.img:
        return Path(args.scan), Path(args.img)
    if args.snapshot_dir:
        d = Path(args.snapshot_dir)
        scan = d / "java_scan_dump.json"
        if not scan.exists():
            scan = d / "scan_dump.json"
        img = d / "screenshot.png"
        return scan, img
    scan = Path("scan_dump.json")
    img = Path("screenshot.png")
    return scan, img


def main():
    from qcs_java_agent.snapshot import build_action_context

    parser = argparse.ArgumentParser(description="QCS DOM Explorer")
    parser.add_argument("snapshot_dir", nargs="?", help="Directory with scan + screenshot")
    parser.add_argument("--scan", help="Path to scan JSON file")
    parser.add_argument("--img", help="Path to screenshot PNG")
    parser.add_argument("--port", type=int, default=8769)
    args = parser.parse_args()

    scan_path, img_path = resolve_paths(args)
    if not scan_path.exists():
        print(f"Error: scan file not found: {scan_path}", file=sys.stderr)
        sys.exit(1)
    if not img_path.exists():
        print(f"Warning: screenshot not found: {img_path}", file=sys.stderr)

    with open(scan_path) as f:
        scan = json.load(f)

    # Determine screenshot origin
    ef = _oracle_forms_active_frame(scan)
    if ef:
        ef_sb = ef.get("screenBounds") or {}
        origin_x = ef_sb.get("x", 0)
        origin_y = ef_sb.get("y", 0)
    else:
        origin_x, origin_y = 0, 0

    # Build both trees
    condensed = build_condensed_tree(scan, origin_x, origin_y)
    raw = build_raw_tree(scan, origin_x, origin_y, scope_root=ef)

    # Build snapshot text
    snapshot_text, _ = build_action_context(scan)

    # Configure handler
    ExplorerHandler.condensed_json = json.dumps(condensed)
    ExplorerHandler.raw_json = json.dumps(raw)
    ExplorerHandler.snapshot_text = snapshot_text
    ExplorerHandler.screenshot_path = img_path if img_path.exists() else None
    ExplorerHandler.html_content = HTML_TEMPLATE

    server = HTTPServer(("127.0.0.1", args.port), ExplorerHandler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"QCS DOM Explorer running at {url}")
    print(f"  Scan:       {scan_path}")
    print(f"  Screenshot: {img_path}")
    print(f"  Press Ctrl+C to stop")

    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()

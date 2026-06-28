"""Convert QCS Java agent DOM snapshots into repository and AI-friendly shapes.

As of agent **schema 2.0**, the Java agent resolves UI structure and identity
itself and stamps it onto every node:

    structure:  containerRole, ownerTab, recordIndex, columnKey, current,
                isMirror, treePath, expanded
    identity:   semanticId, primaryLocator (verified-unique), canonicalLabel

This module is therefore a **thin renderer + dispatcher**. It scopes to the
active window, groups nodes by the agent-provided structure, and formats the AI
snapshot / Studio tree. The legacy geometry + name-prefix heuristics
(`_detect_tabs`, `_detect_table`, blue-pixel record indicator, frozen-column
merge, the old `render_snapshot_text`/`_build_action_tree`) are gone.

Kept intact for callers: scoping (`active_window_scan`, `active_form_title`,
`full_view_scan`, `flatten_nodes`), element mapping (`java_nodes_to_repo_elements`),
overlay (`build_full_overlay_elements`), dispatch (`locator_params`), coordinate
hit-testing (`actioned_element_at`), and multi-scan capture merge (`merge_scans`).
"""
from __future__ import annotations

import re
from typing import Any

import config


ACTIONABLE_ROLES = {
    "Field", "TextArea", "Button", "List", "LOV", "ComboBox", "Checkbox",
    "RadioButton", "Menu", "MenuItem", "Tab", "Grid", "Table", "Tree", "TreeItem",
}

_ORACLE_FORMS_INNER_FRAME = "ExtendedFrame"
_ORACLE_FORMS_DIALOG_CLASSES = frozenset({
    "ChoiceBox", "AlertBox", "MessageBox", "LWDialog",
})
_ORACLE_FORMS_POPUP_CLASSES = frozenset({"FWindow"})
_ORACLE_FORMS_OVERLAY_CLASSES = _ORACLE_FORMS_DIALOG_CLASSES | _ORACLE_FORMS_POPUP_CLASSES


# ───────────────────────────────────────────────────────────────────────────
# Active-window scoping
# ───────────────────────────────────────────────────────────────────────────

def _active_window_root(scan: dict) -> dict | None:
    windows = scan.get("windows") or []
    if not windows:
        return None
    for w in windows:
        if w.get("focused"):
            return w

    def _has_focused(node: dict) -> bool:
        if node.get("focused"):
            return True
        return any(_has_focused(c) for c in (node.get("children") or []))

    for w in windows:
        if _has_focused(w):
            return w
    return windows[-1]


def _oracle_forms_active_frame(scan: dict) -> dict | None:
    nodes: list[dict] = []

    def _walk(node: dict) -> None:
        nodes.append(node)
        for child in node.get("children") or []:
            _walk(child)

    for window in scan.get("windows") or []:
        _walk(window)

    for n in nodes:
        cls = str(n.get("simpleClassName") or "")
        if cls in _ORACLE_FORMS_OVERLAY_CLASSES and n.get("showing"):
            return n

    ef_nodes = [
        n for n in nodes
        if str(n.get("simpleClassName") or "") == _ORACLE_FORMS_INNER_FRAME
        and n.get("showing")
    ]
    if not ef_nodes:
        return None

    focusable = [n for n in ef_nodes if n.get("focusable")]
    if focusable:
        for n in focusable:
            b = n.get("bounds") or {}
            if b.get("x", 0) != 0 or b.get("y", 0) != 0:
                return n
        return focusable[0]
    return max(ef_nodes, key=lambda n: n.get("index") or 0)


def active_window_scan(scan: dict) -> dict:
    ef = _oracle_forms_active_frame(scan)
    if ef is not None:
        return {"windows": [ef]}
    root = _active_window_root(scan)
    if root is None:
        return scan
    return {"windows": [root]}


def full_view_scan(scan: dict) -> list[dict]:
    nodes = flatten_nodes(scan)
    if not nodes:
        return []
    exclude_ids: set[int] = set()
    active_ef_id: int | None = None
    for n in nodes:
        if str(n.get("simpleClassName") or "") != "ExtendedFrame":
            continue
        nid = n.get("id")
        if n.get("focusable") and n.get("showing"):
            active_ef_id = nid
        else:
            exclude_ids.add(nid)
            _collect_descendant_ids(n, exclude_ids)
    if not active_ef_id:
        return flatten_nodes(active_window_scan(scan))
    result: list[dict] = []
    for n in nodes:
        if n.get("id") not in exclude_ids:
            n_copy = n.copy()
            n_copy.pop("children", None)
            result.append(n_copy)
    return result


def _collect_descendant_ids(node: dict, ids: set[int]) -> None:
    ids.add(node.get("id"))
    for child in node.get("children") or []:
        _collect_descendant_ids(child, ids)


def flatten_nodes(scan: dict) -> list[dict]:
    result: list[dict] = []

    def walk(node: dict) -> None:
        result.append(node)
        for child in node.get("children") or []:
            walk(child)

    for window in scan.get("windows") or []:
        walk(window)
    return result


def active_form_title(scan: dict) -> str:
    nodes = flatten_nodes(scan)
    if nodes:
        root = nodes[0]
        if str(root.get("simpleClassName") or "") in _ORACLE_FORMS_OVERLAY_CLASSES:
            for n in nodes:
                if str(n.get("simpleClassName") or "") == "TitleBar":
                    for child in n.get("children") or []:
                        for gc in (child.get("children") or []):
                            name = str(gc.get("accessibleName") or "").strip()
                            if name and name != "null":
                                return name

    ef_nodes = [n for n in nodes if str(n.get("simpleClassName") or "") == "ExtendedFrame"]
    focused = [node for node in nodes if node.get("focused")]
    if ef_nodes and focused:
        ef = ef_nodes[0]
        for key in ("title", "displayName", "accessibleName", "name", "text"):
            value = str(ef.get(key) or "").strip()
            if value and value != "null":
                return value

    candidates = focused or [n for n in nodes if n.get("semanticType") in {"Dialog", "Window"}]
    for node in candidates:
        for key in ("title", "displayName", "accessibleName", "name", "text"):
            value = str(node.get(key) or "").strip()
            if value and value != "null":
                return value

    if ef_nodes:
        ef = ef_nodes[0]
        for key in ("title", "displayName", "accessibleName", "name", "text"):
            value = str(ef.get(key) or "").strip()
            if value and value != "null":
                return value
    return "Oracle Forms"


# ───────────────────────────────────────────────────────────────────────────
# DOM node → repo element mapping (now passes through schema-2.0 identity)
# ───────────────────────────────────────────────────────────────────────────

def java_nodes_to_repo_elements(scan: dict) -> list[dict]:
    nodes = flatten_nodes(scan)
    path_to_elementid = {str(node.get("path") or ""): f"e{node.get('id')}" for node in nodes}
    elements: list[dict] = []
    for node in nodes:
        element_id = f"e{node.get('id')}"
        screen = node.get("screenBounds") or {}
        bounds = node.get("bounds") or {}
        x = _screen_x(screen)
        y = _screen_y(screen)
        width = _int(screen.get("width"), bounds.get("width", 0))
        height = _int(screen.get("height"), bounds.get("height", 0))
        if x < 0 or y < 0:
            x = _int(bounds.get("x"), 0)
            y = _int(bounds.get("y"), 0)

        role = str(node.get("semanticType") or node.get("accessibleRole") or "")
        display = _first_text(
            node.get("canonicalLabel"),
            node.get("displayName"),
            node.get("accessibleName"),
            node.get("name"),
            node.get("text"),
            node.get("title"),
            node.get("simpleClassName"),
        )
        states = _states(node)
        parent_id = path_to_elementid.get(str(node.get("parentPath") or ""))
        path = str(node.get("path") or "")
        elements.append({
            "elementid": element_id,
            "friendly_name": _friendly_name(display or element_id),
            "surface": "java",
            "role": role,
            "name": display,
            "description": str(node.get("accessibleDescription") or ""),
            "xpath": path,
            "path": path,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "bounds": {"x": x, "y": y, "width": width, "height": height},
            "states": states,
            "text": str(node.get("text") or node.get("value") or ""),
            "filteredparentid": parent_id,
            "ancestors": _ancestor_names(path, nodes),
            # Stable identity persisted to the repo (NOT eN).
            "semantic_id": node.get("semanticId"),
            "java": {
                "id": node.get("id"),
                "path": path,
                "parentPath": node.get("parentPath"),
                "type": node.get("type"),
                "className": node.get("className"),
                "simpleClassName": node.get("simpleClassName"),
                "packageName": node.get("packageName"),
                "semanticType": node.get("semanticType"),
                "accessibleName": node.get("accessibleName"),
                "accessibleRole": node.get("accessibleRole"),
                "displayName": node.get("displayName"),
                "canonicalLabel": node.get("canonicalLabel"),
                "confidence": node.get("confidence"),
                "cursorType": node.get("cursorType"),
                "cursorName": node.get("cursorName"),
                "value": node.get("value"),
                "valueOptions": node.get("valueOptions") or [],
                "locators": node.get("locators") or [],
                "reflection": node.get("reflection") or {},
                "attributes": node.get("attributes") or {},
                # schema-2.0 structure + identity
                "semanticId": node.get("semanticId"),
                "primaryLocator": node.get("primaryLocator"),
                "containerRole": node.get("containerRole"),
                "ownerTab": node.get("ownerTab"),
                "recordIndex": node.get("recordIndex", -1),
                "columnKey": node.get("columnKey"),
                "treePath": node.get("treePath"),
                "isMirror": node.get("isMirror", False),
            },
        })
    return elements


_ROLE_ACTIONS: dict[str, list[str]] = {
    "Button": ["click"],
    "Field": ["type", "clear"],
    "ComboBox": ["select"],
    "Checkbox": ["toggle"],
    "RadioButton": ["select"],
    "List": ["select"],
    "Tree": ["select", "expand"],
    "TreeItem": ["select"],
    "Table": ["select"],
    "Menu": ["open"],
    "MenuItem": ["click"],
    "Tab": ["activate"],
}


def build_full_overlay_elements(elements: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen_ids: set[str] = set()
    seen_identity: set[tuple] = set()
    for el in elements:
        ref = str(el.get("elementid") or "")
        if not ref or ref in seen_ids:
            continue
        bounds = el.get("bounds") or {}
        w = int(bounds.get("width", el.get("width", 0)) or 0)
        h = int(bounds.get("height", el.get("height", 0)) or 0)
        if w <= 0 or h <= 0:
            continue
        x = int(bounds.get("x", el.get("x", 0)) or 0)
        y = int(bounds.get("y", el.get("y", 0)) or 0)
        name = str(el.get("name") or el.get("friendly_name") or "")
        identity = (name, x, y, w, h)
        if name and identity in seen_identity:
            continue
        java = el.get("java") or {}
        states = el.get("states") or []
        out.append({
            "element_ref": ref,
            "name": name,
            "role": str(el.get("role") or ""),
            "type": str(java.get("simpleClassName") or el.get("role") or ""),
            "actions": _element_actions(el),
            "value": str(java.get("value") or el.get("text") or ""),
            "enabled": "enabled" in states,
            "bounds": {"x": x, "y": y, "width": w, "height": h},
            "descriptor": str(java.get("descriptor") or ""),
            "locator_params": locator_params(el),
        })
        seen_ids.add(ref)
        if name:
            seen_identity.add(identity)
    return out


def _element_actions(el: dict) -> list[str]:
    role = str(el.get("role") or "")
    java = el.get("java") or {}
    states: list[str] = el.get("states") or []
    if java.get("treePath") or role == "TreeItem":
        return ["click", "select"]
    if bool(java.get("isMirror")):
        return ["inspect"]
    base = _ROLE_ACTIONS.get(role, [])
    if not base:
        return ["inspect"]
    if role == "Field" and "editable" not in states:
        return ["inspect"]
    return base


def build_full_scan(raw_dom: dict) -> dict[str, object]:
    scoped = active_window_scan(raw_dom)
    elements = java_nodes_to_repo_elements(scoped)
    payload = build_action_payload(scoped)
    overlay = build_full_overlay_elements(elements)
    title = active_form_title(scoped)
    return {
        "scoped_dom": scoped,
        "snapshot_text": str(payload.get("text") or ""),
        "tree": list(payload.get("tree") or []),
        "full_elements": overlay,
        "title": title,
    }


def actioned_element_at(scan: dict, screen_x: int, screen_y: int) -> dict | None:
    nodes = flatten_nodes(scan)
    candidates: list[tuple[tuple[int, int, int, int], dict]] = []
    for node in nodes:
        role = str(node.get("semanticType") or node.get("accessibleRole") or "")
        if role not in ACTIONABLE_ROLES:
            continue
        if not node.get("showing") or not node.get("enabled"):
            continue
        screen = node.get("screenBounds") or {}
        x = _screen_x(screen)
        y = _screen_y(screen)
        width = _int(screen.get("width"), 0)
        height = _int(screen.get("height"), 0)
        if width <= 0 or height <= 0 or x < 0 or y < 0:
            continue
        if not (x <= screen_x < x + width and y <= screen_y < y + height):
            continue
        area = width * height
        depth = _int(node.get("depth"), 0)
        focus_bonus = -1 if node.get("focused") else 0
        name = _first_text(
            node.get("canonicalLabel"), node.get("displayName"),
            node.get("accessibleName"), node.get("name"), node.get("text"),
        )
        technical_name_penalty = 1 if _looks_like_technical_name(name) else 0
        candidates.append(((technical_name_penalty, area, focus_bonus, -depth), node))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    target_path = str(candidates[0][1].get("path") or "")
    for element in java_nodes_to_repo_elements(scan):
        if str(element.get("path") or element.get("xpath") or "") == target_path:
            return element
    return None


# ───────────────────────────────────────────────────────────────────────────
# THIN RENDERER — consumes the agent's resolved structure/identity directly
# ───────────────────────────────────────────────────────────────────────────

_ACTION_ROLES = frozenset({
    "Field", "TextArea", "Button", "List", "LOV", "ComboBox",
    "Checkbox", "RadioButton", "Menu", "MenuItem", "Tab", "Tree", "TreeItem",
})

# Locator strategy → ComponentResolver command-param key.
_LOCATOR_PARAM_KEYS = {
    "semanticId": "locatorSemanticId",
    "treePath": "locatorTreePath",
    "canonicalLabel": "locatorCanonicalLabel",
    "accessibleName": "locatorAccessibleName",
    "name": "locatorName",
    "text": "locatorText",
    "path": "locatorPath",
}


def _eid(n: dict) -> str:
    return f"e{n.get('id')}"


def _label(n: dict) -> str:
    return str(n.get("canonicalLabel") or n.get("displayName")
               or n.get("accessibleName") or n.get("name") or "").strip()


def _is_actionable(n: dict) -> bool:
    role = str(n.get("semanticType") or "")
    if role not in _ACTION_ROLES:
        return False
    if n.get("isMirror"):
        return False
    if "enabled" not in _states(n) and role not in ("Toolbar", "TreeItem", "Tab"):
        return False
    return True


def _build_locator_params(node: dict) -> dict[str, str]:
    """Translate a node's verified identity into ComponentResolver params.

    The primary (verified-unique) locator goes first; scope + ordinal are
    included so a non-globally-unique label still resolves deterministically.
    Screen bounds are added as a Robot-click fallback for rows/tabs with no
    model-level selector.
    """
    params: dict[str, str] = {}
    sem = node.get("semanticId")
    if sem:
        params["locatorSemanticId"] = str(sem)

    pl = node.get("primaryLocator") or {}
    strat = str(pl.get("strategy") or "")
    val = str(pl.get("value") or "")
    if strat and val:
        key = _LOCATOR_PARAM_KEYS.get(strat)
        if key:
            params[key] = val
        if pl.get("scope"):
            params["locatorScope"] = str(pl["scope"])
        if int(pl.get("ordinal", -1)) >= 0:
            params["locatorOrdinal"] = str(pl["ordinal"])

    if node.get("treePath"):
        params.setdefault("locatorTreePath", str(node["treePath"]))
    if int(node.get("recordIndex", -1)) >= 0:
        params.setdefault("locatorRecordIndex", str(node["recordIndex"]))

    sb = node.get("screenBounds") or node.get("bounds") or {}
    x = sb.get("screenX", sb.get("x"))
    y = sb.get("screenY", sb.get("y"))
    w = sb.get("width")
    h = sb.get("height")
    if None not in (x, y, w, h) and (w or 0) > 0 and (h or 0) > 0:
        params.setdefault("locatorBounds", f"{x},{y},{w},{h}")
    return params


def build_action_tree(scan: dict, all_tabs: bool = False) -> tuple[list[dict[str, Any]], dict[str, dict]]:
    """Return ``(tree, id_map)`` for a scan.

    The tree groups actionable nodes purely by the agent-resolved structure
    (ownerTab, containerRole, recordIndex, columnKey, treePath). ``id_map``
    values are repo elements augmented with deterministic ``locator_params``.

    ``all_tabs`` is accepted for signature compatibility but ignored: an
    enriched scan already contains every tab's content tagged with ``ownerTab``.
    """
    scoped = active_window_scan(scan)
    nodes = flatten_nodes(scoped)
    repo_by_id = {e["elementid"]: e for e in java_nodes_to_repo_elements(scoped)}

    tree = _build_semantic_tree(nodes)

    id_map: dict[str, dict] = {}
    for n in nodes:
        if not _is_actionable(n):
            continue
        eid = _eid(n)
        el = dict(repo_by_id.get(eid) or {"elementid": eid})
        el["locator_params"] = _build_locator_params(n)
        el["semantic_id"] = n.get("semanticId")
        id_map[eid] = el
    return tree, id_map


def build_action_context(scan: dict, all_tabs: bool = False) -> tuple[str, dict[str, dict]]:
    tree, id_map = build_action_tree(scan, all_tabs=all_tabs)
    if not id_map:
        return "(no actionable elements found)", id_map
    title = active_form_title(active_window_scan(scan))
    return _render_text(tree, title), id_map


def build_action_payload(scan: dict, all_tabs: bool = False) -> dict[str, Any]:
    tree, id_map = build_action_tree(scan, all_tabs=all_tabs)
    if not id_map:
        return {"text": "(no actionable elements found)", "tree": tree, "id_map": id_map}
    title = active_form_title(active_window_scan(scan))
    return {"text": _render_text(tree, title), "tree": tree, "id_map": id_map}


# ── tree assembly (grouping by agent-resolved fields) ──────────────────────

def _build_semantic_tree(nodes: list[dict]) -> list[dict]:
    tab_folders = [n for n in nodes if n.get("containerRole") == "TabFolder"]
    tab_titles: list[str] = []
    selected_tab = ""
    if tab_folders:
        attrs = tab_folders[0].get("attributes") or {}
        tab_titles = [t.strip() for t in str(attrs.get("tabTitles", "")).split("|") if t.strip()]
        selected_tab = str(attrs.get("tabSelectedTitle", "")).strip()

    actionable = [n for n in nodes if _is_actionable(n)]

    by_tab: dict[str, list[dict]] = {}
    form_level: list[dict] = []
    tree_nodes: list[dict] = []
    for n in actionable:
        if n.get("containerRole") == "TreeItem":
            continue  # rendered under its owning Tree node
        if n.get("semanticType") == "Tree":
            tree_nodes.append(n)
            continue
        ot = n.get("ownerTab")
        if ot:
            by_tab.setdefault(ot, []).append(n)
        else:
            form_level.append(n)

    children: list[dict] = []
    for t in (tab_titles or list(by_tab.keys())):
        is_sel = (t == selected_tab) if selected_tab else False
        children.append({
            "element_ref": _tab_ref(nodes, t),
            "label": t,
            "role": "Tab",
            "states": "enabled, selected" if is_sel else "enabled",
            "children": _render_tab_content(by_tab.get(t, [])),
        })

    children.extend(_render_tab_content(form_level))
    for tr in tree_nodes:
        children.append(_tree_node(tr))
    return children


def _tab_ref(nodes: list[dict], title: str) -> str:
    for n in nodes:
        if n.get("semanticType") == "Tab" and _label(n) == title:
            return _eid(n)
    return ""


def _render_tab_content(tab_nodes: list[dict]) -> list[dict]:
    grid_cells = [n for n in tab_nodes if n.get("containerRole") == "GridCell"]
    loose = [n for n in tab_nodes if n.get("containerRole") != "GridCell"]
    out: list[dict] = list(_field_rows(loose))
    if grid_cells:
        out.append(_grid_node(grid_cells))
    return out


def _grid_node(cells: list[dict]) -> dict:
    """Assemble a Table node purely from recordIndex + columnKey (no geometry)."""
    columns: list[str] = []
    seen: set[str] = set()
    for c in sorted(cells, key=lambda n: int(n.get("recordIndex", 0))):
        col = c.get("columnKey") or _label(c)
        if col not in seen:
            seen.add(col)
            columns.append(col)

    rows: dict[int, dict[str, dict]] = {}
    for c in cells:
        rows.setdefault(int(c.get("recordIndex", 0)), {})[c.get("columnKey") or _label(c)] = c

    table_rows: list[dict] = []
    for ridx in sorted(rows):
        rowmap = rows[ridx]
        is_cur = any(cell.get("current") for cell in rowmap.values())
        marker = f"**{ridx + 1}**" if is_cur else str(ridx + 1)
        out_cells = []
        for col in columns:
            cell = rowmap.get(col)
            if cell:
                out_cells.append({
                    "element_ref": _eid(cell),
                    "current_value": str(cell.get("text") or ""),
                    "role": cell.get("semanticType"),
                })
            else:
                out_cells.append({"element_ref": "", "current_value": "", "role": ""})
        table_rows.append({"marker": marker, "cells": out_cells})

    return {"role": "Table", "label": "", "table_columns": columns,
            "table_rows": table_rows, "children": []}


def _field_rows(nodes: list[dict]) -> list[dict]:
    out: list[dict] = []
    for n in nodes:
        role = n.get("semanticType")
        node = {
            "element_ref": _eid(n),
            "label": _label(n),
            "role": role,
            "states": ", ".join(_states(n)),
            "children": [],
        }
        if role == "ComboBox":
            vo = n.get("valueOptions") or []
            if vo:
                node["value_options"] = vo
            node["read_only"] = not n.get("editable")
        elif role == "Checkbox":
            node["checked"] = bool(n.get("selected"))
        elif role == "LOV":
            node["has_lov"] = True
        cur = str(n.get("text") or "").strip()
        if cur and cur != node["label"]:
            node["current_value"] = cur
        out.append(node)
    return out


def _tree_node(tree: dict) -> dict:
    items = []
    for c in tree.get("children") or []:
        if c.get("containerRole") != "TreeItem":
            continue
        items.append({
            "element_ref": _eid(c),
            "label": _label(c),
            "selected": bool(c.get("selected")),
            "depth": c.get("depth", 0),
        })
    return {"element_ref": _eid(tree), "label": _label(tree),
            "role": "Tree", "tree_items": items, "children": []}


# ── tree → AI snapshot text (same format the recorder prompt expects) ───────

def _render_text(tree: list[dict], form_title: str,
                 max_chars: int = config.MAX_SNAPSHOT_CHARS) -> str:
    lines = [f"Form: {form_title}"]

    def field_text(n: dict) -> str:
        ref = str(n.get("element_ref") or "")
        tag = f"[{ref}] " if ref.startswith("e") else ""
        label = n.get("label", "")
        role = n.get("role", "")
        if role == "Button":
            return f"{tag}{label} (Button, {n.get('states') or 'enabled'})"
        if role == "Checkbox":
            return f"{tag}{label} (Checkbox, {'checked' if n.get('checked') else 'unchecked'})"
        if role == "ComboBox":
            extra = " (read-only)" if n.get("read_only") else ""
            vo = n.get("value_options")
            if vo:
                extra += f" values: {vo}"
            return f"{tag}{label} (ComboBox){extra}"
        suffix = " (LOV)" if n.get("has_lov") else ""
        cv = n.get("current_value")
        if cv:
            suffix += f" = {cv}"
        return f"{tag}{label}{suffix}"

    def walk(nodes: list[dict], indent: str) -> None:
        for n in nodes:
            role = n.get("role")
            ref = str(n.get("element_ref") or "")
            tag = f"[{ref}] " if ref.startswith("e") else ""
            if role == "Tab":
                lines.append(f"{indent}{tag}{n.get('label')} (Tab, {n.get('states')})")
                walk(n.get("children") or [], indent + "  ")
            elif role == "Table":
                cols = n.get("table_columns") or []
                lines.append(f"{indent}| # | {' | '.join(cols)} |")
                lines.append(f"{indent}|{'|'.join('---' for _ in range(len(cols) + 1))}|")
                for r in n.get("table_rows") or []:
                    cells = [f"[{c['element_ref']}] {c.get('current_value', '')}"
                             if c.get("element_ref") else ""
                             for c in r.get("cells") or []]
                    lines.append(f"{indent}| {r.get('marker', '')} | {' | '.join(cells)} |")
            elif role == "Tree":
                lines.append(f"{indent}{tag}{n.get('label')}")
                for it in n.get("tree_items") or []:
                    mark = " *" if it.get("selected") else ""
                    lines.append(f"{indent}    [{it['element_ref']}] {it['label']}{mark}")
            elif role in ("Field", "ReadOnly", "LOV", "ComboBox", "Checkbox", "Button"):
                lines.append(f"{indent}{field_text(n)}")
            else:
                lines.append(f"{indent}{tag}{n.get('label', '')}")
                walk(n.get("children") or [], indent + "  ")

    walk(tree, "  ")
    result = "\n".join(lines) or "(no actionable elements found)"
    if len(result) > max_chars:
        result = result[:max_chars] + "\n...(truncated)"
    return result


# ───────────────────────────────────────────────────────────────────────────
# Dispatch — locator params for the Java agent (back-compat + schema-2.0)
# ───────────────────────────────────────────────────────────────────────────

def locator_params(element: dict) -> dict[str, str]:
    """Build Java-agent locator params from a repo element descriptor.

    Prefers the schema-2.0 verified-unique identity (semanticId / primaryLocator
    / treePath / recordIndex) when present, then falls back to the legacy
    descriptor strategies so older repo rows still resolve.
    """
    java = element.get("java") or {}
    params: dict[str, str] = {}

    # schema-2.0 verified identity first.
    if java.get("semanticId") or java.get("primaryLocator") or java.get("treePath"):
        params.update(_build_locator_params(java))
        if params:
            # Still append legacy bounds/text as harmless extra fallbacks below.
            pass

    for candidate in element.get("locator_candidates") or []:
        strategy = candidate.get("strategy")
        value = candidate.get("value")
        if not value:
            continue
        if strategy == "java_path":
            params.setdefault("locatorPath", str(value))
        elif strategy == "component_name":
            params.setdefault("locatorName", str(value))
        elif strategy == "accessible_name":
            params.setdefault("locatorAccessibleName", str(value))
        elif strategy == "text":
            params.setdefault("locatorText", str(value))
        elif strategy == "class_name":
            params.setdefault("locatorClassName", str(value))
        elif strategy == "bounds":
            params.setdefault("locatorBounds", str(value))

    path = java.get("path") or element.get("path") or element.get("xpath")
    if path:
        params.setdefault("locatorPath", str(path))
    for loc in java.get("locators") or []:
        strategy = loc.get("strategy")
        value = loc.get("value")
        if not value:
            continue
        if strategy == "name":
            params.setdefault("locatorName", str(value))
        elif strategy == "accessibleName":
            params.setdefault("locatorAccessibleName", str(value))
        elif strategy == "text":
            params.setdefault("locatorText", str(value))
    if element.get("name"):
        params.setdefault("locatorText", str(element["name"]))
    bounds = element.get("bounds") or {}
    if bounds:
        params.setdefault(
            "locatorBounds",
            f"{bounds.get('x', 0)},{bounds.get('y', 0)},{bounds.get('width', 0)},{bounds.get('height', 0)}",
        )
    return params


def java_component_result_to_repo_element(result: dict) -> dict:
    component = result.get("component") or {}
    bounds = component.get("bounds") or {}
    screen_x = _int(component.get("screenX"), 0)
    screen_y = _int(component.get("screenY"), 0)
    width = _int(bounds.get("width"), 0)
    height = _int(bounds.get("height"), 0)
    simple_name = str(component.get("simpleName") or "")
    accessible_name = str(component.get("accessibleName") or "")
    text = str(component.get("text") or "")
    name = _first_text(accessible_name, text, component.get("name"), simple_name)
    path = str(result.get("path") or "")
    role = _semantic_role(simple_name, accessible_name, text)
    states = ["visible", "showing"]
    if screen_x >= 0 and screen_y >= 0:
        states.append("enabled")
    element = {
        "elementid": "",
        "friendly_name": _friendly_name(name or simple_name or "element"),
        "surface": "java",
        "role": role,
        "name": name,
        "description": "",
        "xpath": path,
        "path": path,
        "x": screen_x,
        "y": screen_y,
        "width": width,
        "height": height,
        "bounds": {"x": screen_x, "y": screen_y, "width": width, "height": height},
        "states": states,
        "text": text,
        "filteredparentid": None,
        "ancestors": [],
        "java": {
            "path": path,
            "className": component.get("className"),
            "simpleClassName": simple_name,
            "accessibleName": accessible_name,
            "displayName": name,
            "locators": [
                {"strategy": "accessibleName", "value": accessible_name} if accessible_name else {},
                {"strategy": "text", "value": text} if text else {},
            ],
        },
    }
    element["java"]["locators"] = [loc for loc in element["java"]["locators"] if loc]
    return element


def _semantic_role(simple_name: str, accessible_name: str, text: str) -> str:
    lowered = simple_name.lower()
    if "button" in lowered:
        return "Button"
    if "textfield" in lowered or "text" in lowered:
        return "Field"
    if "list" in lowered:
        return "List"
    if "menu" in lowered:
        return "Menu"
    if accessible_name or text:
        return "Field"
    return simple_name or "Component"


# ───────────────────────────────────────────────────────────────────────────
# Small utilities
# ───────────────────────────────────────────────────────────────────────────

def _states(node: dict) -> list[str]:
    states: list[str] = []
    for key in ("visible", "showing", "enabled", "focusable", "focused", "editable", "selected"):
        if node.get(key):
            states.append(key)
    return states


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text and text != "null":
            return text
    return ""


def _friendly_name(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    return slug[:60] or "element"


def _looks_like_technical_name(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return True
    return re.fullmatch(r"[A-Za-z_$]+\d+", value) is not None


def _ancestor_names(path: str, nodes: list[dict]) -> list[str]:
    by_path = {str(node.get("path") or ""): node for node in nodes}
    names: list[str] = []
    current = by_path.get(path)
    depth = 0
    while current and current.get("parentPath") and depth < 10:
        parent = by_path.get(str(current.get("parentPath") or ""))
        if not parent:
            break
        name = _first_text(parent.get("canonicalLabel"), parent.get("displayName"),
                           parent.get("accessibleName"), parent.get("name"), parent.get("title"))
        if name:
            names.insert(0, name)
        current = parent
        depth += 1
    return names


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default or 0)


def _screen_x(screen_bounds: dict) -> int:
    return _int(screen_bounds.get("screenX", screen_bounds.get("x")), -1)


def _screen_y(screen_bounds: dict) -> int:
    return _int(screen_bounds.get("screenY", screen_bounds.get("y")), -1)


# ───────────────────────────────────────────────────────────────────────────
# Multi-scan capture merge (used by qcs_studio multi-tab capture). Unchanged.
# Kept because capture-merge is orthogonal to the renderer; with the schema-2.0
# agent a single scan is usually complete, but lazy-rendered pages may still be
# captured across scans and merged here before rendering.
# ───────────────────────────────────────────────────────────────────────────

_ANY_TAB_PAGE_PREFIX = re.compile(r"^[A-Za-z].*? tab page ")


def _has_tab_prefix(node: dict) -> bool:
    for key in ("name", "accessibleName", "displayName"):
        val = str(node.get(key) or "")
        if " tab page " in val:
            return True
    attrs = node.get("attributes") or {}
    if attrs.get("tabSelectedTitle"):
        return True
    return False


def _find_descendant_tab_prefix(node: dict) -> str | None:
    an = node.get("accessibleName") or ""
    if " tab page " in an:
        idx = an.find(" tab page ")
        return an[:idx + 10]
    for child in node.get("children") or []:
        pfx = _find_descendant_tab_prefix(child)
        if pfx:
            return pfx
    return None


def _strip_all_tab_prefixes(name: str) -> str:
    while True:
        match = _ANY_TAB_PAGE_PREFIX.match(name)
        if not match:
            break
        name = name[match.end():]
    return name


def _node_identity(node: dict) -> tuple | None:
    name = str(node.get("accessibleName") or node.get("name") or "")
    cls = str(node.get("simpleClassName") or "")
    if cls == "FScrollBox" and not name:
        prefix = _find_descendant_tab_prefix(node)
        if prefix:
            return (prefix, cls)
    if not name:
        return None
    return (_strip_all_tab_prefixes(name), cls)


def _merge_dom_nodes(n1: dict, n2: dict) -> None:
    n1_has_prefix = _has_tab_prefix(n1)
    n2_has_prefix = _has_tab_prefix(n2)
    if (n2_has_prefix and not n1_has_prefix) or (not n1_has_prefix and not n1.get("name") and n2.get("name")):
        for k, v in n2.items():
            if k != "children" and k != "id":
                n1[k] = v

    c1 = n1.get("children") or []
    c2 = n2.get("children") or []
    if not c1 and c2:
        n1["children"] = c2
        return
    if c1 and c2:
        c1_by_path = {c.get("path"): c for c in c1 if c.get("path")}
        c1_by_identity: dict[tuple, dict] = {}
        for c in c1:
            ident = _node_identity(c)
            if ident:
                c1_by_identity.setdefault(ident, c)
        for child2 in c2:
            path2 = child2.get("path")
            cls2 = str(child2.get("simpleClassName") or "")
            if cls2 == "FScrollBox":
                ident2 = _node_identity(child2)
                merged = False
                if ident2:
                    if ident2 in c1_by_identity:
                        _merge_dom_nodes(c1_by_identity[ident2], child2)
                        merged = True
                else:
                    if path2 in c1_by_path:
                        target = c1_by_path[path2]
                        if not _node_identity(target):
                            _merge_dom_nodes(target, child2)
                            merged = True
                if not merged:
                    c1.append(child2)
                    if ident2:
                        c1_by_identity.setdefault(ident2, child2)
            else:
                if path2 in c1_by_path:
                    _merge_dom_nodes(c1_by_path[path2], child2)
                else:
                    ident2 = _node_identity(child2)
                    if ident2 and ident2 in c1_by_identity:
                        _merge_dom_nodes(c1_by_identity[ident2], child2)
                    else:
                        c1.append(child2)
                        if ident2:
                            c1_by_identity.setdefault(ident2, child2)
        n1["children"] = c1


def _max_node_id(node: dict) -> int:
    max_id = node.get("id") or 0
    for child in node.get("children") or []:
        max_id = max(max_id, _max_node_id(child))
    for win in node.get("windows") or []:
        max_id = max(max_id, _max_node_id(win))
    return max_id


def _offset_node_ids(node: dict, offset: int) -> None:
    nid = node.get("id")
    if nid is not None:
        node["id"] = nid + offset
    for child in node.get("children") or []:
        _offset_node_ids(child, offset)
    for win in node.get("windows") or []:
        _offset_node_ids(win, offset)


def merge_scans(scan1: dict, scan2: dict) -> dict:
    """Recursively merge the DOM trees of scan2 into scan1."""
    import copy
    res = copy.deepcopy(scan1)
    scan2_copy = copy.deepcopy(scan2)
    max_id1 = _max_node_id(res)
    _offset_node_ids(scan2_copy, max_id1 + 1)
    w1 = res.get("windows") or []
    w2 = scan2_copy.get("windows") or []
    w1_by_path = {w.get("path"): w for w in w1 if w.get("path")}
    for win2 in w2:
        path2 = win2.get("path")
        if path2 in w1_by_path:
            _merge_dom_nodes(w1_by_path[path2], win2)
        else:
            w1.append(win2)
    res["windows"] = w1
    return res

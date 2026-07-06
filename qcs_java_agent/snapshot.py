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
            if isinstance(nid, int):
                active_ef_id = nid
        else:
            if isinstance(nid, int):
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
    node_id = node.get("id")
    if isinstance(node_id, int):
        ids.add(node_id)
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
                "attributes": node.get("attributes") or {},
                # schema-2.0 structure + identity
                "semanticId": node.get("semanticId"),
                "primaryLocator": node.get("primaryLocator"),
                "handlerId": node.get("handlerId"),
                "hasLov": node.get("hasLov", False),
                "required": node.get("required", False),
                "locked": node.get("locked", False),
                "formsType": node.get("formsType"),
                "formsTabName": node.get("formsTabName"),
                "formsItemName": node.get("formsItemName"),
                "formsActions": node.get("formsActions"),
                "dirty": node.get("dirty", False),
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
    "List": ["select", "double_click"],
    "Tree": ["select", "expand", "collapse"],
    "TreeItem": ["select", "expand", "collapse", "double_click"],
    "Table": ["select", "double_click"],
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
        role = str(el.get("role") or "")
        # WHY: Oracle ListView row nodes (TreeItem) frequently report 0x0 bounds
        # even though they are actionable and visible in the curated tree.
        # Keep them in full_elements so Studio can show the info tooltip/icon.
        if w <= 0 or h <= 0:
            if role != "TreeItem":
                continue
            # Minimal non-zero box to keep UI lookup paths alive. Visual overlay
            # boxes for tree rows are driven by _tree_node() fallback bounds.
            w = max(1, w)
            h = max(1, h)
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
            "role": role,
            "type": str(java.get("simpleClassName") or el.get("role") or ""),
            "actions": _element_actions(el),
            "forms_action": str(java.get("formsActions") or ""),
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
        # Tree nodes are selectable, openable, and (when they hold children)
        # expandable. expand/collapse are safe to advertise on any tree node —
        # the agent no-ops them on a leaf.
        return ["click", "select", "expand", "collapse", "double_click"]
    if bool(java.get("isMirror")) or bool(java.get("locked")):
        return ["inspect"]  # read-only echo / runtime-locked
    # LOV field — ground truth from isLOVButtonDisplayed(): supports opening the
    # List-of-Values in addition to typing.
    if role == "LOV" or bool(java.get("hasLov")):
        return ["type", "open_lov", "clear"]
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
    "Field", "TextArea", "LOV", "ComboBox", "Checkbox", "RadioButton",
    "Button", "List", "Tab", "Tree", "TreeItem",
})

# Oracle Forms button labels carry "alt X" / "mnemonic X" accelerator suffixes.
_ALT_RE = re.compile(r"\s+(?:alt|mnemonic)\s+\S+$", re.IGNORECASE)
# Defensive: strip a trailing "List of Values" the agent may not have stripped.
_LOV_RE = re.compile(r"\s*List of Values$")

# Locator strategy → ComponentResolver command-param key.
_LOCATOR_PARAM_KEYS = {
    "handlerId": "locatorHandlerId",
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
    raw = str(n.get("canonicalLabel") or n.get("displayName")
              or n.get("accessibleName") or n.get("name") or "").strip()
    # WHY: Oracle Forms list widgets often expose technical names like
    # "ListView19" for the tree container while the semantic title lives in
    # the form scope/title (e.g., "form:Order Types"). Prefer that human title
    # so snapshots stay readable and regressions don't lock in class-like labels.
    if str(n.get("semanticType") or "") == "Tree" and _looks_like_technical_name(raw):
        scope = str((n.get("primaryLocator") or {}).get("scope") or "")
        form_scope = scope[5:] if scope.startswith("form:") else ""
        title = str(n.get("formTitle") or n.get("windowTitle") or "").strip()
        human = form_scope or title
        if human:
            raw = human
    # WHY: Oracle ListView rows often serialize multiple visible columns into a
    # single label joined by em-dash delimiters. Snapshot readability is better
    # when tree/list rows show only the first (primary) column value.
    if str(n.get("semanticType") or "") == "TreeItem" and "—" in raw:
        raw = raw.split("—", 1)[0].strip()
    return _ALT_RE.sub("", _LOV_RE.sub("", raw))


def _is_actionable(n: dict) -> bool:
    """Gate for both the snapshot tree and the id_map (AI-targetable set).

    Relies on the agent's resolved structure: isMirror (read-only echoes) and
    containerRole == 'OrphanTabContent' (data fields inside the tab region that
    belong to no matched tab) are dropped. Unlabeled chrome (technical names,
    or a label equal to the widget's class name like 'ToolBarButton') is hidden.
    """
    role = str(n.get("semanticType") or "")
    if role not in _ACTION_ROLES:
        return False
    if n.get("containerRole") in ("OrphanTabContent", "OccludedCanvas"):
        return False
    # The multi-tab DFS capture flags every non-focused tab's content as a mirror
    # echo. For a 2+ level tab path we WANT that content — it is the only copy of
    # a hidden top-tab's fields (e.g. the whole "Order Information" branch when
    # "Line Items" is the live block). All other gates below still apply, so the
    # focused tab's genuine mirror duplicates (technical/disabled) stay dropped.
    # Single-level forms keep dropping mirrors exactly as before.
    _otp = n.get("ownerTabPath")
    _nested = isinstance(_otp, (list, tuple)) and len(_otp) >= 2
    if n.get("isMirror") and not _nested:
        return False
    lbl = _label(n)
    if not lbl:
        return False
    if role not in ("Tree", "TreeItem"):
        if _looks_like_technical_name(lbl) or lbl == str(n.get("simpleClassName") or ""):
            return False
    if "enabled" not in _states(n) and role not in ("TreeItem", "Tab"):
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
    # Forms item handler id first — strongest within-session locator (the agent
    # tries it before everything else). Falls back to the rest for cross-session
    # recordings, so emitting it is always safe.
    hid = node.get("handlerId")
    if hid:
        params["locatorHandlerId"] = str(hid)
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

    return params


def _assign_owner_tabs(scan: dict) -> None:
    """Compute each element's owning tab from the DOM hierarchy (Python-owned).

    Oracle Forms labels only a few fields with a "<Tab> tab page " prefix, so we
    propagate from the CONTAINER: a node belongs to the tab of the *innermost*
    FScrollBox whose subtree contains exactly one distinct tab prefix
    ("dedicated"). A data field inside the tabbed area (any FScrollBox carrying
    >=1 prefix) but owned by no dedicated box is background content
    (containerRole='OrphanTabContent' -> dropped by the renderer). Buttons and
    trees are never orphaned, so footer buttons (Clear/Find/Open Folder) that
    sit in a prefix-less box fall through to form level.

    Prefers the agent-extracted handler tab name (``formsTabName``) when an item
    carries it; otherwise mutates nodes in place using the prefix heuristic,
    overriding any agent-set ownerTab/OrphanTabContent. Leaves
    Grid/GridCell/TabFolder/TreeItem container roles untouched.
    """
    nodes: list[dict] = []
    parent: dict[int, dict | None] = {}

    def walk(n: dict, p: dict | None = None) -> None:
        nodes.append(n)
        parent[id(n)] = p
        for c in n.get("children") or []:
            walk(c, n)

    for w in scan.get("windows") or []:
        walk(w)

    def subtree_prefixes(n: dict, acc: set[str]) -> None:
        an = str(n.get("accessibleName") or "")
        i = an.find(" tab page ")
        if i > 0:
            acc.add(an[:i])
        for c in n.get("children") or []:
            subtree_prefixes(c, acc)

    dedicated: dict[int, str] = {}   # id(FScrollBox) -> tab
    region: set[int] = set()         # id(FScrollBox) with >=1 prefix
    for n in nodes:
        if str(n.get("simpleClassName") or "") != "FScrollBox":
            continue
        acc: set[str] = set()
        subtree_prefixes(n, acc)
        if acc:
            region.add(id(n))
        if len(acc) == 1:
            dedicated[id(n)] = next(iter(acc))

    # WHY: Some Oracle Forms pages expose selected-tab controls in a sibling
    # scrollbox with no "<Tab> tab page" prefixes. The old logic marked those
    # controls as OrphanTabContent, which hid real fields (e.g., Supplier,
    # Currency) and left the selected tab empty. We now use the nearest tab
    # strip's selected title as a fallback owner for such unlabeled data items.
    def _node_screen_box(n: dict) -> tuple[int, int, int, int]:
        sb = n.get("screenBounds") or {}
        b = n.get("bounds") or {}
        x = _screen_x(sb)
        y = _screen_y(sb)
        w = _int(sb.get("width"), 0)
        h = _int(sb.get("height"), 0)
        if x < 0:
            x = _int(b.get("x"), -1)
        if y < 0:
            y = _int(b.get("y"), -1)
        if w <= 0:
            w = _int(b.get("width"), 0)
        if h <= 0:
            h = _int(b.get("height"), 0)
        return x, y, w, h

    tab_folders: list[tuple[int, int, int, int, str]] = []  # x, y, w, h, selected
    for n in nodes:
        if n.get("containerRole") != "TabFolder":
            continue
        attrs = n.get("attributes") or {}
        titles = [t.strip() for t in str(attrs.get("tabTitles", "")).split("|") if t.strip()]
        selected = str(attrs.get("tabSelectedTitle", "")).strip()
        if len(titles) <= 1 or not selected:
            continue
        x, y, w, h = _node_screen_box(n)
        if w > 0:
            tab_folders.append((x, y, w, h, selected))

    def _selected_tab_fallback(n: dict) -> str | None:
        nx, ny, nw, nh = _node_screen_box(n)
        if nw <= 0:
            nw = 1
        mid_x = nx + (nw // 2)
        best: tuple[int, str] | None = None
        for tx, ty, tw, _th, sel in tab_folders:
            if tw <= 0 or not sel:
                continue
            if not (tx <= mid_x <= (tx + tw)):
                continue
            # Prefer the closest tab strip in Y among X-overlapping candidates.
            dist = abs(ny - ty)
            if best is None or dist < best[0]:
                best = (dist, sel)
        return best[1] if best else None

    def _in_tab_content_band(n: dict) -> bool:
        nx, ny, nw, _nh = _node_screen_box(n)
        if nw <= 0:
            nw = 1
        mid_x = nx + (nw // 2)
        for tx, ty, tw, _th, _sel in tab_folders:
            if tw <= 0:
                continue
            if not (tx <= mid_x <= (tx + tw)):
                continue
            # WHY: only nodes at/under the tab strip should use selected-tab
            # fallback ownership. Fields above the strip are form-level and
            # must not be pulled into whichever tab is currently selected.
            if ny >= ty:
                return True
        return False

    _DATA = ("Field", "LOV", "ComboBox", "Checkbox", "RadioButton")
    for n in nodes:
        # Authoritative tab from the Forms handler (agent-extracted via
        # getParentTabName): when the item itself knows its tab, trust it over
        # the layout heuristic and never orphan it. The prefix heuristic below
        # stays the fallback for nodes with no handler (containers, chrome).
        forms_tab = n.get("formsTabName")
        if forms_tab:
            n["ownerTab"] = forms_tab
            if n.get("containerRole") == "OrphanTabContent":
                n["containerRole"] = None
            continue

        cur = parent.get(id(n))
        owner: str | None = None
        in_region = False
        while cur is not None:
            if owner is None and id(cur) in dedicated:
                owner = dedicated[id(cur)]
            if id(cur) in region:
                in_region = True
            cur = parent.get(id(cur))

        cr = n.get("containerRole")
        if owner:
            n["ownerTab"] = owner
            if cr == "OrphanTabContent":
                n["containerRole"] = None
        else:
            n["ownerTab"] = None
            if in_region and cr != "GridCell" and n.get("semanticType") in _DATA and _in_tab_content_band(n):
                sel = _selected_tab_fallback(n)
                if sel:
                    n["ownerTab"] = sel
                    if cr == "OrphanTabContent":
                        n["containerRole"] = None
                else:
                    n["containerRole"] = "OrphanTabContent"
            elif cr == "OrphanTabContent":
                n["containerRole"] = None


def _propagate_owner_tab_paths(scan: dict) -> None:
    """Fan each capture-seeded ``ownerTabPath`` out across its whole tab-page region.

    The capture layer can only mark the ONE field per tab whose accessibleName
    carries the ``"<leaf> tab page "`` anchor Oracle Forms stamps (≈1 node per
    tab — not every field carries it). This spreads that seed to every node in
    the same tab-page region: the FScrollBox whose subtree contains only that
    leaf's ``" tab page "`` marker. Two same-named sub-tabs (``Order Information
    -> Main`` vs ``Line Items -> Main``) live in separate regions, so they keep
    separate full paths — which is exactly what makes the nested render correct.

    Runs after ``_assign_owner_tabs`` and before the tree build; nodes outside
    any seeded region keep their flat single-level ownership, unchanged.
    """
    parent: dict[int, dict | None] = {}
    nodes: list[dict] = []

    def walk(n: dict, p: dict | None) -> None:
        nodes.append(n)
        parent[id(n)] = p
        for c in n.get("children") or []:
            walk(c, n)

    for w in scan.get("windows") or []:
        walk(w, None)

    anchors = [n for n in nodes
               if isinstance(n.get("ownerTabPath"), list) and n.get("ownerTabPath")]
    if not anchors:
        return

    def leaf_prefixes(node: dict) -> set[str]:
        acc: set[str] = set()
        stack = [node]
        while stack:
            x = stack.pop()
            an = str(x.get("accessibleName") or "")
            i = an.find(" tab page ")
            if i > 0:
                acc.add(an[:i])
            stack.extend(x.get("children") or [])
        return acc

    # An FScrollBox that wraps exactly one tab page (its subtree carries a single
    # "<leaf> tab page" marker) is that leaf's content region.
    single_prefix: dict[int, str] = {}
    for n in nodes:
        if str(n.get("simpleClassName") or "") == "FScrollBox":
            pf = leaf_prefixes(n)
            if len(pf) == 1:
                single_prefix[id(n)] = next(iter(pf))

    def stamp_subtree(root: dict, path: list) -> None:
        stack = [root]
        while stack:
            x = stack.pop()
            x["ownerTabPath"] = list(path)
            stack.extend(x.get("children") or [])

    for a in anchors:
        path = list(a["ownerTabPath"])
        leaf = str(path[-1])
        # Region = outermost ancestor FScrollBox scoped to this leaf alone.
        region = None
        cur: dict | None = a
        while cur is not None:
            if single_prefix.get(id(cur)) == leaf:
                region = cur
            cur = parent.get(id(cur))
        if region is not None:
            stamp_subtree(region, path)


def build_action_tree(scan: dict, all_tabs: bool = False) -> tuple[list[dict[str, Any]], dict[str, dict]]:
    """Return ``(tree, id_map)`` for a scan.

    The tree groups actionable nodes purely by the agent-resolved structure
    (ownerTab, containerRole, recordIndex, columnKey, treePath). ``id_map``
    values are repo elements augmented with deterministic ``locator_params``.

    ``all_tabs`` is accepted for signature compatibility but ignored: an
    enriched scan already contains every tab's content tagged with ``ownerTab``.
    """
    scoped = active_window_scan(scan)
    # Grouping (which tab owns each element) is INTERPRETATION, not extraction,
    # so it lives here — tuned without recompiling the agent. This overrides any
    # ownerTab/orphan the agent stamped, using only the raw hierarchy + labels
    # the agent provides (Java stays an unfiltered extractor).
    _assign_owner_tabs(scoped)
    _propagate_owner_tab_paths(scoped)
    nodes = flatten_nodes(scoped)
    _promote_grid_controls(nodes)
    repo_by_id = {e["elementid"]: e for e in java_nodes_to_repo_elements(scoped)}

    tree = _build_semantic_tree(nodes)

    # Tools menu lives on the menu bar, OUTSIDE the scoped frame — read from
    # the full scan and append as a group.
    for grp in _menu_groups(flatten_nodes(scan)):
        tree.append(grp)

    id_map: dict[str, dict] = {}
    for n in nodes:
        if not _is_actionable(n):
            continue
        eid = _eid(n)
        # WHY: id_map entries carry mixed scalar and structured values
        # (for example locator_params dict), so keep this local map value type
        # broad instead of inferring a narrow string-only dictionary.
        el: dict[str, Any] = dict(repo_by_id.get(eid) or {"elementid": eid})
        el["locator_params"] = _build_locator_params(n)
        semantic_id = n.get("semanticId")
        if semantic_id is not None:
            el["semantic_id"] = semantic_id
        tree_path = n.get("treePath")
        if tree_path:
            el["tree_path"] = tree_path
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

def _tab_titles(folder: dict) -> list[str]:
    attrs = folder.get("attributes") or {}
    return [t.strip() for t in str(attrs.get("tabTitles", "")).split("|") if t.strip()]


def _owner_path(n: dict) -> tuple[str, ...]:
    """Full tab path (outermost → innermost) a node belongs to.

    Prefers ``ownerTabPath`` (a list stamped by the capture layer, which is the
    only place the parent→child tab relationship is known for a multi-level tab
    layout). Falls back to the single flat ``ownerTab`` (length-1 path) for
    single-level forms and any scan captured before the path was stamped, so
    those render exactly as before.
    """
    p = n.get("ownerTabPath")
    if isinstance(p, (list, tuple)) and p:
        return tuple(str(x).strip() for x in p if str(x).strip())
    ot = n.get("ownerTab")
    ot = str(ot).strip() if ot else ""
    return (ot,) if ot else ()


def _tab_bars_info(nodes: list[dict]) -> list[dict]:
    """One entry per distinct multi-title tab bar: titles, selected, enabled.

    Oracle Forms exposes two nodes per bar (the TabBar carries the selected
    title; the FormsTabPanel container does not); we keep the one that knows the
    selection. Used only to recover display order + selected/enabled state — the
    hierarchy itself comes from each node's ``ownerTabPath``.
    """
    dedup: dict[tuple, dict] = {}
    for n in nodes:
        if n.get("containerRole") != "TabFolder":
            continue
        titles = _tab_titles(n)
        if len(titles) <= 1:
            continue
        attrs = n.get("attributes") or {}
        selected = str(attrs.get("tabSelectedTitle", "")).strip()
        enabled: dict[str, bool] = {}
        visible: dict[str, bool] = {}
        states_raw = str(attrs.get("tabStates", "")).strip()
        if states_raw:
            for idx, part in enumerate(states_raw.split("|")):
                bits = [b.strip() for b in part.strip().split(",")]
                if idx < len(titles) and bits and bits[0] in ("0", "1"):
                    enabled[titles[idx]] = (bits[0] == "1")
                if idx < len(titles) and len(bits) > 1 and bits[1] in ("0", "1"):
                    visible[titles[idx]] = (bits[1] == "1")
        key = tuple(titles)
        prev = dedup.get(key)
        if prev is None or (selected and not prev["selected"]):
            dedup[key] = {"titles": titles, "selected": selected,
                          "enabled": enabled, "visible": visible}
    return list(dedup.values())


def _order_children(titles: set[str], bars: list[dict]) -> tuple[list[str], dict | None]:
    """Order a level's sibling tab titles and find the tab bar that owns them.

    The bar is matched by title-set overlap (the sub-tab groups of two parents
    differ as sets — e.g. 7 vs 9 sub-tabs — so the right bar wins even when a
    leaf name like ``Main`` is shared). The bar supplies display order + the
    selected/enabled state; the set of children comes from the paths.
    """
    best: tuple[tuple[int, int], dict] | None = None
    for b in bars:
        bt = set(b["titles"])
        overlap = len(bt & titles)
        if not overlap:
            continue
        score = (overlap, -len(bt ^ titles))
        if best is None or score > best[0]:
            best = (score, b)
    if best is None:
        return sorted(titles), None
    bar = best[1]
    ordered = [t for t in bar["titles"] if t in titles]
    ordered += [t for t in sorted(titles) if t not in set(bar["titles"])]
    return ordered, bar


def _build_nested_tab_tree(nodes: list[dict], actionable: list[dict]) -> list[dict]:
    """Render a multi-level tab layout as nested Tab nodes.

    Structure comes entirely from each node's ``ownerTabPath`` (the relationship
    the capture layer extracted); this function is the interpretation/rendering
    logic. Dedup is keyed by (semanticId, full path) so two fields that share a
    leaf semanticId under different parents (``Order Information -> Main`` vs
    ``Line Items -> Main``) both survive instead of one clobbering the other.
    """
    bars = _tab_bars_info(nodes)

    seen: set = set()
    by_path: dict[tuple, list[dict]] = {}
    form_level: list[dict] = []
    tree_nodes: list[dict] = []
    for n in actionable:
        role = str(n.get("semanticType") or "")
        if role == "Tab" or n.get("containerRole") == "TreeItem":
            continue
        if role == "Tree":
            tree_nodes.append(n)
            continue
        path = _owner_path(n)
        sem = n.get("semanticId")
        key = (sem, path) if sem else (_label(n), path, role, n.get("id"))
        if key in seen:
            continue
        seen.add(key)
        if path:
            by_path.setdefault(path, []).append(n)
        else:
            form_level.append(n)

    # Trie of tab paths: prefix -> set of next-level titles.
    children_titles: dict[tuple, set[str]] = {}
    for path in by_path:
        for i in range(len(path)):
            children_titles.setdefault(path[:i], set()).add(path[i])

    def render_level(prefix: tuple) -> list[dict]:
        titles = children_titles.get(prefix)
        if not titles:
            return []
        ordered, bar = _order_children(titles, bars)
        # Structure fidelity: when the matched tab bar is a superset of the tabs
        # we found content for, surface EVERY visible tab of that bar in bar
        # order — so a sub-tab whose content was dropped/merged away upstream
        # still appears (empty) instead of vanishing. The subset guard means a
        # mismatched bar can never inject unrelated tabs.
        if bar and titles <= set(bar["titles"]):
            vis = bar.get("visible", {})
            ordered = [t for t in bar["titles"] if vis.get(t, True)]
            for t in (children_titles.get(prefix) or set()):
                if t not in ordered:
                    ordered.append(t)
        selected = (bar or {}).get("selected", "")
        enabled_map = (bar or {}).get("enabled", {})
        out: list[dict] = []
        for t in ordered:
            p = prefix + (t,)
            # A tab's own content comes first, then any nested sub-tabs.
            content = _render_tab_content(by_path.get(p, []))
            subtabs = render_level(p)
            is_sel = (t == selected) if selected else False
            if not enabled_map.get(t, True):
                state = "disabled"
            else:
                state = "enabled, selected" if is_sel else "enabled"
            out.append({
                "element_ref": _tab_ref_for_path(nodes, p),
                "label": t,
                "role": "Tab",
                "states": state,
                "children": content + subtabs,
            })
        return out

    children: list[dict] = render_level(())
    children.extend(_render_tab_content(form_level))
    for tr in tree_nodes:
        children.append(_tree_node(tr))
    return children


def _build_semantic_tree(nodes: list[dict]) -> list[dict]:
    # Multi-level tab layout: when the capture layer stamped a 2+ deep
    # ownerTabPath on any actionable node, render nested tabs. Otherwise fall
    # through to the single-level path below (unchanged).
    actionable_all = [n for n in nodes if _is_actionable(n)]
    if any(len(_owner_path(n)) >= 2 for n in actionable_all):
        return _build_nested_tab_tree(nodes, actionable_all)
    # Use the innermost multi-title TabFolder for the tab list (a single-title
    # folder is a sub-region, not the form's tab bar).
    folders = [n for n in nodes if n.get("containerRole") == "TabFolder" and len(_tab_titles(n)) > 1]
    folders.sort(key=lambda n: (n.get("screenBounds") or {}).get("y", 0))
    tab_titles: list[str] = []
    selected_tab = ""
    tab_enabled: dict[str, bool] = {}
    if folders:
        tab_titles = _tab_titles(folders[-1])
        attrs = folders[-1].get("attributes") or {}
        selected_tab = str(attrs.get("tabSelectedTitle", "")).strip()
        # tabStates = "1,1 | 0,1 | ..." (enabled,visible per tab) — mark disabled.
        # Absent/empty → all tabs default enabled (do not fabricate "disabled").
        states_raw = str(attrs.get("tabStates", "")).strip()
        if states_raw:
            for idx, part in enumerate(states_raw.split("|")):
                bits = [b.strip() for b in part.strip().split(",")]
                if idx < len(tab_titles) and bits and bits[0] in ("0", "1"):
                    tab_enabled[tab_titles[idx]] = (bits[0] == "1")

    actionable = [n for n in nodes if _is_actionable(n)]

    # Dedup by semanticId (collapses the duplicates produced by merge_scans).
    seen: set = set()
    by_tab: dict[str, list[dict]] = {}
    form_level: list[dict] = []
    tree_nodes: list[dict] = []
    for n in actionable:
        role = str(n.get("semanticType") or "")
        if role == "Tab" or n.get("containerRole") == "TreeItem":
            continue  # tabs come from the tab list; tree rows from their tree
        if role == "Tree":
            tree_nodes.append(n)
            continue
        # Dedup key: prefer semanticId (globally unique) so merge_scans
        # duplicates are collapsed. For nodes without a semanticId, include
        # the Java node id so that per-row items (e.g. "On Hold" checkboxes
        # that appear once per table row but carry no semanticId) are kept
        # intact instead of being collapsed to a single instance.
        sem = n.get("semanticId")
        key = sem if sem else (_label(n), n.get("ownerTab"), role, n.get("id"))
        if key in seen:
            continue
        seen.add(key)
        ot = n.get("ownerTab")
        if ot:
            by_tab.setdefault(ot, []).append(n)
        else:
            form_level.append(n)

    children: list[dict] = []
    for t in (tab_titles or list(by_tab.keys())):
        is_sel = (t == selected_tab) if selected_tab else False
        en = tab_enabled.get(t, True)
        if not en:
            state = "disabled"
        else:
            state = "enabled, selected" if is_sel else "enabled"
        children.append({
            "element_ref": _tab_ref(nodes, t),
            "label": t,
            "role": "Tab",
            "states": state,
            "children": _render_tab_content(by_tab.get(t, [])),
        })

    children.extend(_render_tab_content(form_level))
    for tr in tree_nodes:
        children.append(_tree_node(tr))
    return children


# Top-level menus to surface in the snapshot, by name (without the "ALT x"
# suffix). To add more — e.g. New/Save/Print via File, Find via View — just add
# the menu name here; the items come from each menu's accessibleMenuItems.
_SURFACED_MENUS = ("Tools",)


def _menu_groups(all_nodes: list[dict]) -> list[dict]:
    """Build a Group per surfaced top-level menu from its accessibleMenuItems."""
    found: dict[str, dict] = {}
    for n in all_nodes:
        if str(n.get("simpleClassName") or "") != "LWMenu":
            continue
        nm = str(n.get("accessibleName") or n.get("name") or "")
        # Menu-bar entries look like "Tools ALT T"; submenus carry "mnemonic x".
        m = re.match(r"^(.*?)\s+ALT\s+\S+$", nm)
        title = (m.group(1).strip() if m else nm.strip())
        if title not in _SURFACED_MENUS or title in found:
            continue
        items: list[dict] = []
        raw = str((n.get("attributes") or {}).get("accessibleMenuItems") or "")
        for entry in raw.split(" || "):
            parts = entry.split("\t")
            if len(parts) < 3:
                continue
            label = re.sub(r"\s+mnemonic\s+\S+$", "", parts[0].strip()).strip()
            if not label:
                continue
            item = {"label": label, "role": "Item", "children": []}
            if parts[1] == "check_box":
                item["checked"] = (parts[2] == "1")
            items.append(item)
        if items:
            found[title] = {"label": f"{title} Menu", "role": "Group", "children": items}
    # Preserve _SURFACED_MENUS order.
    return [found[t] for t in _SURFACED_MENUS if t in found]


def _tab_ref(nodes: list[dict], title: str) -> str:
    for n in nodes:
        if n.get("semanticType") == "Tab" and _label(n) == title:
            return _eid(n)
    return ""


def _tab_ref_for_path(nodes: list[dict], path: tuple) -> str:
    """Element ref of a tab header, parent-qualified when possible.

    When the tab header nodes carry ``ownerTabPath`` (stamped by the capture
    layer), two same-named sub-tabs under different parents resolve to their own
    header. Falls back to a leaf-title match for un-stamped scans.
    """
    title = path[-1]
    for n in nodes:
        if n.get("semanticType") != "Tab" or _label(n) != title:
            continue
        p = n.get("ownerTabPath")
        if isinstance(p, (list, tuple)) and tuple(str(x).strip() for x in p) == path:
            return _eid(n)
    return _tab_ref(nodes, title)


def _render_tab_content(tab_nodes: list[dict]) -> list[dict]:
    grid_cells = [n for n in tab_nodes if n.get("containerRole") == "GridCell"]
    # A real grid needs >=2 distinct columns; a lone repeating column is not a
    # table (guards against a stray single-column GridCell annotation).
    distinct_cols = {(c.get("formsItemName") or c.get("columnKey") or _label(c)) for c in grid_cells}
    if len(distinct_cols) < 2:
        return list(_field_rows(tab_nodes))
    loose = [n for n in tab_nodes if n.get("containerRole") != "GridCell"]
    out: list[dict] = list(_field_rows(loose))
    out.append(_grid_node(grid_cells))
    return out


def _grid_node(cells: list[dict]) -> dict:
    """Assemble a Table node from recordIndex + column identity (no geometry).

    Columns are grouped by the authoritative Forms item name (``formsItemName``)
    when present, so two columns that share a header stay distinct; the friendly
    label (``columnKey``) is kept for display.
    """
    def gkey(c: dict) -> str:
        return str(c.get("formsItemName") or c.get("columnKey") or _label(c))

    def glabel(c: dict) -> str:
        return str(c.get("columnKey") or _label(c))

    columns: list[str] = []          # ordered group keys
    col_label: dict[str, str] = {}   # group key -> display header
    seen: set[str] = set()
    for c in sorted(cells, key=lambda n: int(n.get("recordIndex", 0))):
        k = gkey(c)
        if k not in seen:
            seen.add(k)
            columns.append(k)
            col_label[k] = glabel(c)

    rows: dict[int, dict[str, dict]] = {}
    for c in cells:
        rows.setdefault(int(c.get("recordIndex", 0)), {})[gkey(c)] = c

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
                    "current_value": str(cell.get("text") or cell.get("value") or ""),
                    "role": cell.get("semanticType"),
                })
            else:
                out_cells.append({"element_ref": "", "current_value": "", "role": ""})
        table_rows.append({"marker": marker, "cells": out_cells})

    first_cell = cells[0] if cells else {}
    ref = f"{_eid(first_cell)}_table" if first_cell else ""
    return {"element_ref": ref, "role": "Table", "label": "Table",
            "table_columns": [col_label[k] for k in columns],
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
        elif role == "RadioButton":
            node["selected"] = bool(n.get("selected"))
        elif role == "LOV":
            node["has_lov"] = True
        # Forms item capabilities (ground truth from the agent). has_lov also
        # set defensively from the flag in case role wasn't reclassified.
        if n.get("hasLov"):
            node["has_lov"] = True
        if n.get("required"):
            node["required"] = True
        if n.get("locked"):
            node["locked"] = True
        if n.get("dirty"):
            node["dirty"] = True
        cur = str(n.get("text") or "").strip()
        if cur and cur != node["label"]:
            node["current_value"] = cur
        out.append(node)
    return out


def _tree_node(tree: dict) -> dict:
    items = []
    tree_sb = tree.get("screenBounds") or tree.get("bounds") or {}
    tree_x = _screen_x(tree_sb)
    tree_y = _screen_y(tree_sb)
    tree_w = _int(tree_sb.get("width"), 0)
    tree_h = _int(tree_sb.get("height"), 0)
    if tree_x < 0 or tree_y < 0:
        tb = tree.get("bounds") or {}
        tree_x = _int(tb.get("x"), 0)
        tree_y = _int(tb.get("y"), 0)
        if tree_w <= 0:
            tree_w = _int(tb.get("width"), 0)
        if tree_h <= 0:
            tree_h = _int(tb.get("height"), 0)
    tree_children = [c for c in (tree.get("children") or []) if c.get("containerRole") == "TreeItem"]
    total_rows = len(tree_children)
    attrs = tree.get("attributes") or {}
    has_columns = bool(str(attrs.get("listColumns") or "").strip())
    # WHY: ListView bounds include header and large empty tail area; splitting
    # full height by row count produces oversized bands. Model a compact body:
    # reserve header height and clamp row height to realistic line heights.
    header_h = 22 if has_columns else 0
    body_y = tree_y + header_h
    body_h = max(0, tree_h - header_h)
    if body_h > 0 and total_rows > 0:
        estimated = body_h // max(total_rows + 4, total_rows)
        row_h = max(18, min(24, estimated))
    else:
        row_h = 0
    children_list = []
    for idx, c in enumerate(tree_children):
        if c.get("containerRole") != "TreeItem":
            continue
        items.append({
            "element_ref": _eid(c),
            "label": _label(c),
            "selected": bool(c.get("selected")),
            "depth": c.get("depth", 0),
        })
        sb = c.get("screenBounds") or c.get("bounds") or {}
        cx = _screen_x(sb)
        cy = _screen_y(sb)
        cw = _int(sb.get("width"), 0)
        ch = _int(sb.get("height"), 0)
        # WHY: rowBoundsPx is component-relative in Java diagnostics while
        # overlay rendering needs screen-space coordinates. Prefer screenBounds
        # from the node itself and only fall back when geometry is missing.
        if cx < 0 or cy < 0:
            bnd = c.get("bounds") or {}
            cx = _int(bnd.get("x"), 0)
            cy = _int(bnd.get("y"), 0)
        # WHY: Some Forms ListView rows report no row-level bounds. Derive a
        # deterministic fallback band from the parent tree rectangle so Studio
        # can draw per-row overlays and hover targets.
        if (cw <= 0 or ch <= 0) and tree_w > 0 and body_h > 0 and row_h > 0:
            cx = tree_x
            cy = body_y + (idx * row_h)
            cw = tree_w
            remaining = max(1, (body_y + body_h) - cy)
            ch = min(row_h, remaining)
        children_list.append({
            "element_ref": _eid(c),
            "label": _label(c),
            "role": "TreeItem",
            "included": True,
            "bounds": {"x": cx, "y": cy, "width": cw, "height": ch},
            "children": [],
        })
    return {"element_ref": _eid(tree), "label": _label(tree),
            "role": "Tree", "tree_items": items, "children": children_list}


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
            st = "enabled" if "enabled" in str(n.get("states") or "") else "disabled"
            return f"{tag}{label} (Button, {st})"
            
        if role == "Checkbox":
            return f"{tag}{label} (Checkbox, {'checked' if n.get('checked') else 'unchecked'})"
        if role == "ComboBox":
            extra = " (read-only)" if n.get("read_only") else ""
            vo = n.get("value_options")
            if vo:
                extra += f" values: {vo}"
            if n.get("required"):
                extra += " (required)"
            return f"{tag}{label} (ComboBox){extra}"
        suffix = " (LOV)" if n.get("has_lov") else ""
        if n.get("required"):
            suffix += " (required)"
        if n.get("locked"):
            suffix += " (locked)"
        if n.get("dirty"):
            suffix += " (modified)"
        cv = n.get("current_value")
        if cv:
            suffix += f" = {cv}"
        return f"{tag}{label}{suffix}"

    def walk(nodes: list[dict], indent: str) -> None:
        i = 0
        while i < len(nodes):
            n = nodes[i]
            role = n.get("role")
            ref = str(n.get("element_ref") or "")
            tag = f"[{ref}] " if ref.startswith("e") else ""
            # Join a run of consecutive Buttons on one line (matches old output).
            if role == "Button":
                parts = []
                while i < len(nodes) and nodes[i].get("role") == "Button":
                    parts.append(field_text(nodes[i]))
                    i += 1
                lines.append(f"{indent}{' , '.join(parts)}")
                continue
            if role == "RadioButton":
                lines.append(f"{indent}Select one:")
                while i < len(nodes) and nodes[i].get("role") == "RadioButton":
                    r = nodes[i]
                    rref = str(r.get("element_ref") or "")
                    rtag = f"[{rref}] " if rref.startswith("e") else ""
                    dot = "(x)" if r.get("selected") else "( )"
                    lines.append(f"{indent}  {rtag}{dot} {r.get('label', '')}")
                    i += 1
                continue
            if role == "Group":
                lines.append(f"{indent}{n.get('label')}:")
                for it in n.get("children") or []:
                    if it.get("checked") is not None:
                        mark = "[x] " if it.get("checked") else "[ ] "
                    else:
                        mark = ""
                    lines.append(f"{indent}  {mark}{it.get('label', '')}")
                i += 1
                continue
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
            elif role in ("Field", "ReadOnly", "LOV", "ComboBox", "Checkbox"):
                lines.append(f"{indent}{field_text(n)}")
            else:
                lines.append(f"{indent}{tag}{n.get('label', '')}")
                walk(n.get("children") or [], indent + "  ")
            i += 1

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

    # schema-2.0 verified identity first (handlerId / semanticId / primaryLocator).
    if (java.get("handlerId") or java.get("semanticId")
            or java.get("primaryLocator") or java.get("treePath")):
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

def _iter_nodes(dom: dict):
    stack = list(dom.get("windows") or [])
    while stack:
        n = stack.pop()
        yield n
        stack.extend(n.get("children") or [])


def attribute_unique_tab_fields(merged_dom: dict, tab_doms: dict[str, dict]) -> int:
    """Stamp ownerTab on merged fields that appear in exactly ONE tab's scan.

    Oracle Forms doesn't always put the " tab page <Title>" marker in a field's
    accessibleName, so the agent leaves such fields ownerTab=None. But a field
    that was only present while one tab was active belongs to that tab. Tagging
    it makes the tree place it under the right Tab node and the overlay pick that
    tab's screenshot. Keyed on canonicalLabel (stable across scans); a label seen
    on 2+ tabs is treated as shared and left untouched (conservative — never
    mis-attributes a shared field).

    Returns the number of fields newly attributed.
    """
    def _eligible(n: dict) -> bool:
        # Only loose tab fields. Skip grid cells (they belong to a grid, which
        # carries the tab; their same-label repeats are rows, not tabs) and any
        # field already scoped to a grid/other window.
        if n.get("semanticType") != "Field":
            return False
        if int(n.get("recordIndex", -1)) >= 0:
            return False
        sid = str(n.get("semanticId") or "")
        return not sid.startswith("grid:")

    label_tabs: dict[str, set] = {}
    for tab_path, dom in tab_doms.items():
        if tab_path == "default" or not dom:
            continue
        for n in _iter_nodes(dom):
            if _eligible(n) and n.get("canonicalLabel"):
                label_tabs.setdefault(n["canonicalLabel"], set()).add(tab_path)

    if not label_tabs:
        return 0

    fixed = 0
    for n in _iter_nodes(merged_dom):
        if not _eligible(n) or n.get("ownerTab"):
            continue
        tabs = label_tabs.get(n.get("canonicalLabel"))
        if tabs and len(tabs) == 1:
            # innermost tab title (matches the tree's Tab-node / tabPath segment)
            n["ownerTab"] = next(iter(tabs)).split(" -> ")[-1].strip()
            fixed += 1
    return fixed

def _promote_grid_controls(nodes: list[dict]) -> None:
    """Fold grid-body controls (e.g. an ``On Hold`` checkbox column) into the
    table instead of leaving them as loose fields above it.

    Schema-2.0 Java stamps ``containerRole=GridCell`` + ``recordIndex`` +
    ``columnKey`` on the *text* cells of a multi-record block, but leaves the
    non-text widgets that share the same grid canvas (checkboxes, LOV buttons)
    untagged — so the thin renderer emits them separately, above the table.

    Java has already extracted the relationship we need to fix this in Python:
    the widget is a descendant of the *same grid canvas* as the tagged cells
    (its ``path`` is under their shared ``parentPath``) and its screen bounds
    line up with a record row and column. This function uses only those
    Java-provided facts — no pixel heuristics in the table builder itself — to
    stamp the missing ``recordIndex``/``columnKey`` so the existing renderer
    places the control in its own row/column.
    """
    # Group the already-resolved grid cells by their grid canvas (the parent
    # path every cell of one block shares). Each group is one table.
    grids: dict[str, list[dict]] = {}
    for n in nodes:
        if n.get("containerRole") == "GridCell":
            grids.setdefault(str(n.get("parentPath") or ""), []).append(n)
    if not grids:
        return

    for canvas_path, cells in grids.items():
        if not canvas_path:
            continue
        owner_tabs = {c.get("ownerTab") for c in cells if c.get("ownerTab")}

        # Row bands: screen-y -> recordIndex, learned from the tagged cells.
        bands: dict[int, int] = {}
        for c in cells:
            ridx = c.get("recordIndex")
            y = _screen_y(c.get("screenBounds") or {})
            if ridx is not None and y >= 0:
                bands.setdefault(y, int(ridx))
        if not bands:
            continue
        ys = sorted(bands)
        pitch = min((b - a for a, b in zip(ys, ys[1:])), default=24) or 24
        tol = max(2, pitch // 2)

        # Horizontal span of the block, to reject widgets outside the columns.
        xs = [_screen_x(c.get("screenBounds") or {}) for c in cells]
        xs = [x for x in xs if x >= 0]
        rights = [
            _screen_x(c.get("screenBounds") or {})
            + _int((c.get("screenBounds") or {}).get("width"), 0)
            for c in cells
        ]
        x_min = min(xs) if xs else 0
        x_max = max(rights) if rights else 0

        prefix = canvas_path + "/"
        for n in nodes:
            if n.get("containerRole") == "GridCell":
                continue
            role = str(n.get("semanticType") or "")
            # Text fields inside a grid are already GridCells; tabs/trees are
            # structural. Only recover the interactive column widgets.
            if role not in _ACTION_ROLES or role in ("Field", "Tab", "Tree", "TreeItem"):
                continue
            if not str(n.get("path") or "").startswith(prefix):
                continue
            if owner_tabs and n.get("ownerTab") not in owner_tabs:
                continue
            label = _label(n)
            if not label or _looks_like_technical_name(label):
                continue
            sb = n.get("screenBounds") or {}
            x = _screen_x(sb)
            y = _screen_y(sb)
            if x < 0 or y < 0:
                continue
            if x_max and not (x_min - tol <= x <= x_max + tol):
                continue
            best_ridx = None
            best_dist = None
            for band_y, ridx in bands.items():
                dist = abs(y - band_y)
                if best_dist is None or dist < best_dist:
                    best_dist, best_ridx = dist, ridx
            if best_ridx is None or best_dist is None or best_dist > tol:
                continue
            n["containerRole"] = "GridCell"
            n["recordIndex"] = best_ridx
            n["columnKey"] = label


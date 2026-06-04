"""Convert QCS Java agent DOM snapshots into repository and AI-friendly shapes."""
from __future__ import annotations

import re
from typing import Any

import config


ACTIONABLE_ROLES = {
    "Field",
    "TextArea",
    "Button",
    "List",
    "LOV",
    "ComboBox",
    "Checkbox",
    "RadioButton",
    "Menu",
    "MenuItem",
    "Tab",
    "Grid",
    "Table",
    "Tree",
}


_ORACLE_FORMS_INNER_FRAME = "ExtendedFrame"

# Oracle Forms modal dialog class names that sit alongside ExtendedFrame
# inside FormDesktopContainer.
_ORACLE_FORMS_DIALOG_CLASSES = frozenset({
    "ChoiceBox", "AlertBox", "MessageBox", "LWDialog",
})

# Oracle Forms popup window class names (LOV popups, etc.)
_ORACLE_FORMS_POPUP_CLASSES = frozenset({
    "FWindow",
})

# All non-ExtendedFrame overlay classes (dialogs + popups)
_ORACLE_FORMS_OVERLAY_CLASSES = _ORACLE_FORMS_DIALOG_CLASSES | _ORACLE_FORMS_POPUP_CLASSES


def _active_window_root(scan: dict) -> dict | None:
    """Return the top-level window node that currently holds focus.

    Priority order:
    1. Window directly marked ``focused``.
    2. Window that contains any focused descendant.
    3. Last window in the list (Oracle Forms renders the frontmost form last).
    """
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
    """Return the active Oracle Forms MDI child frame node, if present.

    Oracle Forms hosts multiple form modules inside a single JVM under a
    ``FormDesktopContainer``.  Each module is an ``oracle.forms.ui.ExtendedFrame``
    (``simpleClassName == "ExtendedFrame"``).  Oracle Forms marks the active
    MDI child with ``focusable=True`` and sets ``focusable=False`` on all
    background frames.

    Modal dialogs (``ChoiceBox``, ``AlertBox``, etc.) are siblings of
    ExtendedFrame inside ``FormDesktopContainer``.  When one is showing
    it takes top priority.

    Selection priority:
    0. A showing Oracle Forms dialog (``ChoiceBox``, ``AlertBox``, …).
    1. ``focusable=True`` ExtendedFrame whose ``bounds`` have a non-zero origin
       (floated / raised dialog on top of the desktop).
    2. Any ``focusable=True`` ExtendedFrame.
    3. Fall back: the showing ExtendedFrame with the highest sibling ``index``
       (Oracle Forms puts the most recently activated form at the top of the
       Z-order list).
    """
    # We need a full flatten to find ExtendedFrame nodes anywhere in the tree
    nodes: list[dict] = []

    def _walk(node: dict) -> None:
        nodes.append(node)
        for child in node.get("children") or []:
            _walk(child)

    for window in scan.get("windows") or []:
        _walk(window)

    # Priority 0: Oracle Forms overlay (modal dialog or LOV popup) showing on top
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
        # Prefer a floating frame (non-zero origin = raised on top of desktop)
        for n in focusable:
            b = n.get("bounds") or {}
            if b.get("x", 0) != 0 or b.get("y", 0) != 0:
                return n
        return focusable[0]

    # No focusable frame — highest sibling index = most recently activated
    return max(ef_nodes, key=lambda n: n.get("index") or 0)


def active_window_scan(scan: dict) -> dict:
    """Return a scan dict scoped to the active/focused window only.

    For Oracle Forms sessions the active MDI child frame
    (``oracle.forms.ui.ExtendedFrame``) is detected via the ``focusable``
    flag and used as the scope root.  For other Java applications the
    focused top-level window is used instead.

    All other callers (replay, healing, coord mapping) should continue to
    use the full scan so they can locate elements regardless of which module
    they belong to.
    """
    # Oracle Forms MDI: scope to the active ExtendedFrame module
    ef = _oracle_forms_active_frame(scan)
    if ef is not None:
        return {"windows": [ef]}

    # Standard: scope to the focused top-level window
    root = _active_window_root(scan)
    if root is None:
        return scan
    return {"windows": [root]}


def full_view_scan(scan: dict) -> list[dict]:
    """Return the list of nodes for the full-view overlay — active frame +
    shared toolbar/menu bar, excluding non-active ExtendedFrame subtrees.

    WHY: The full view in Studio should show ALL elements from the active
    form window, plus the menu bar and toolbar (which sit outside the
    ExtendedFrame in the DOM), but NOT elements from other inactive form
    windows that happen to be open in the same JVM session.
    """
    nodes = flatten_nodes(scan)
    if not nodes:
        return []

    # Collect IDs to exclude: all descendants of non-active ExtendedFrames
    exclude_ids: set[int] = set()
    active_ef_id: int | None = None

    for n in nodes:
        sc = str(n.get("simpleClassName") or "")
        if sc != "ExtendedFrame":
            continue
        nid = n.get("id")
        if n.get("focusable") and n.get("showing"):
            active_ef_id = nid
        else:
            # Collect this EF and all its descendants for exclusion
            exclude_ids.add(nid)
            _collect_descendant_ids(n, exclude_ids)

    if not active_ef_id:
        # No active ExtendedFrame found — fallback to active_window_scan
        return flatten_nodes(active_window_scan(scan))

    # Build result: include all nodes EXCEPT those in inactive EFs.
    # This keeps the top-level JFrame, menu bar, toolbar, and the active
    # form window, while excluding background form modules.
    # IMPORTANT: We strip `children` from every returned node because
    # flatten_nodes() (used downstream in java_nodes_to_repo_elements)
    # recursively walks children arrays.  If we didn't strip children,
    # excluded subtrees would be re-discovered through parent→child links.
    # The flat list only contains nodes that survived the exclusion filter
    # and the caller only needs the flat list — not the original tree
    # structure.
    result: list[dict] = []
    for n in nodes:
        nid = n.get("id")
        if nid not in exclude_ids:
            result.append(n)
    for n in result:
        n.pop("children", None)
    return result


def _collect_descendant_ids(node: dict, ids: set[int]) -> None:
    """Collect the id of *node* and all its descendants into *ids*."""
    ids.add(node.get("id"))
    for child in node.get("children") or []:
        _collect_descendant_ids(child, ids)


def flatten_nodes(scan: dict) -> list[dict]:
    """Return all Java agent DOM nodes in depth-first order."""
    result: list[dict] = []

    def walk(node: dict) -> None:
        result.append(node)
        for child in node.get("children") or []:
            walk(child)

    for window in scan.get("windows") or []:
        walk(window)
    return result


def active_form_title(scan: dict) -> str:
    """Best-effort title/display name for the active visible Forms window/dialog."""
    nodes = flatten_nodes(scan)

    # For Oracle Forms overlays (dialogs & popups), look for a TitleBar label
    if nodes:
        root = nodes[0]
        root_cls = str(root.get("simpleClassName") or "")
        if root_cls in _ORACLE_FORMS_OVERLAY_CLASSES:
            for n in nodes:
                if str(n.get("simpleClassName") or "") == "TitleBar":
                    # The TitleBar contains an LWLabel child with the dialog title
                    for child in n.get("children") or []:
                        for gc in (child.get("children") or []):
                            name = str(gc.get("accessibleName") or "").strip()
                            if name and name != "null":
                                return name

    # WHY: When the scoped scan is an ExtendedFrame, the form title should
    # come from the ExtendedFrame itself, not from a focused VTextField whose
    # displayName is a field label (e.g. "Order Number").  Find the
    # ExtendedFrame node first for Oracle Forms MDI sessions.
    ef_nodes = [n for n in nodes if str(n.get("simpleClassName") or "") == "ExtendedFrame"]
    focused = [node for node in nodes if node.get("focused")]

    # Prefer ExtendedFrame title over focused field labels
    if ef_nodes and focused:
        ef = ef_nodes[0]
        for key in ("title", "displayName", "accessibleName", "name", "text"):
            value = str(ef.get(key) or "").strip()
            if value and value != "null":
                return value

    # General case: focused node, or Window/Dialog semanticType
    candidates = focused or [node for node in nodes if node.get("semanticType") in {"Dialog", "Window"}]
    for node in candidates:
        for key in ("title", "displayName", "accessibleName", "name", "text"):
            value = str(node.get(key) or "").strip()
            if value and value != "null":
                return value

    # Fallback: ExtendedFrame title (when no focused field exists)
    if ef_nodes:
        ef = ef_nodes[0]
        for key in ("title", "displayName", "accessibleName", "name", "text"):
            value = str(ef.get(key) or "").strip()
            if value and value != "null":
                return value

    return "Oracle Forms"


def java_nodes_to_repo_elements(scan: dict) -> list[dict]:
    """Map Java agent DOM nodes to qcs_repo element records.

    The repo shape intentionally keeps legacy top-level keys like role/name/xpath/bounds
    so existing qcs_repo storage can persist these records, while Java-agent-specific
    details live under the `java` sub-dict.
    """
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
                "confidence": node.get("confidence"),
                "cursorType": node.get("cursorType"),
                "cursorName": node.get("cursorName"),
                "value": node.get("value"),
                "valueOptions": node.get("valueOptions") or [],
                "locators": node.get("locators") or [],
                "reflection": node.get("reflection") or {},
                "attributes": node.get("attributes") or {},
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
    "Table": ["select"],
    "Menu": ["open"],
    "MenuItem": ["click"],
    "Tab": ["activate"],
}


def build_full_overlay_elements(elements: list[dict]) -> list[dict]:
    """Build the full hoverable element overlay from all scanned elements.

    WHY: Every element from the full scan must be hoverable and inspectable
    (id, name, type, possible actions, Java locator) and draggable into the
    curated tree. Bounds are raw (unadjusted) Java-window client coordinates
    that align directly with the fullscreen screenshot. Zero-size elements
    are skipped. We intentionally do NOT require a "showing"/"visible" state
    here: Oracle Forms omits those flags on many real, on-screen widgets.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for el in elements:
        ref = str(el.get("elementid") or "")
        if not ref or ref in seen:
            continue
        bounds = el.get("bounds") or {}
        w = int(bounds.get("width", el.get("width", 0)) or 0)
        h = int(bounds.get("height", el.get("height", 0)) or 0)
        if w <= 0 or h <= 0:
            continue
        x = int(bounds.get("x", el.get("x", 0)) or 0)
        y = int(bounds.get("y", el.get("y", 0)) or 0)
        java = el.get("java") or {}
        states = el.get("states") or []
        out.append({
            "element_ref": ref,
            "name": str(el.get("name") or el.get("friendly_name") or ""),
            "role": str(el.get("role") or ""),
            "type": str(java.get("simpleClassName") or el.get("role") or ""),
            "actions": _element_actions(el),
            "value": str(java.get("value") or el.get("text") or ""),
            "enabled": "enabled" in states,
            "bounds": {"x": x, "y": y, "width": w, "height": h},
            "descriptor": str(java.get("descriptor") or ""),
            "locator_params": java.get("locator_params") or {},
        })
        seen.add(ref)
    return out


def _element_actions(el: dict) -> list[str]:
    """Determine possible action verbs for a scanned element.

    WHY: The full-element hover overlay shows users what they can do with
    any element on screen. Actions are inferred from role + Oracle-specific
    signals (LOV name suffix, editability) so the tooltip is helpful
    without invoking AI.
    """
    role = str(el.get("role") or "")
    name = str(el.get("name") or "").lower()
    states: list[str] = el.get("states") or []
    if "list of values" in name or "(lov)" in name:
        return ["click", "open list"]
    base = _ROLE_ACTIONS.get(role, [])
    if not base:
        return ["inspect"]  # read-only display / unknown: inspectable only
    if role == "Field" and "editable" not in states:
        return ["inspect"]
    return base


def build_full_scan(raw_dom: dict) -> dict[str, object]:
    """Single entry point: produce everything from one raw Java agent DOM.

    Returns a dict with:
        scoped_dom       — active-window-scoped DOM (dict)
        snapshot_text    — AI-friendly action snapshot (str)
        tree             — Studio UI tree (list[dict])
        full_elements    — hoverable overlay for every scanned element (list[dict])
        title            — best-effort form title (str)

    All coordinates are raw (unadjusted) and align with a fullscreen
    screenshot.  Callers (Studio, dump_scan, etc.) should use this as the
    one canonical source for scan data.
    """
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
    """Return the best action-worthy repo element containing screen coords."""
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
            node.get("displayName"),
            node.get("accessibleName"),
            node.get("name"),
            node.get("text"),
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


def java_elements_to_ai_snapshot(
    elements: list[dict],
    max_chars: int = config.MAX_SNAPSHOT_CHARS,
) -> str:
    lines: list[str] = []
    for element in elements:
        element_id = element.get("elementid", "?")
        role = element.get("role", "")
        name = element.get("name") or element.get("text") or ""
        states = ",".join(element.get("states") or [])
        parent = element.get("filteredparentid") or ""
        line = f"[{element_id}] {name} | {role}"
        if states:
            line += f" | states:{states}"
        if parent:
            line += f" | parent:{parent}"
        java = element.get("java") or {}
        path = java.get("path") or element.get("path")
        if path:
            line += f" | path:{path}"
        lines.append(line)
    result = "\n".join(lines)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n...(truncated)"
    return result


_ACTION_SNAPSHOT_ROLES: frozenset[str] = frozenset({
    "Field", "Button", "List", "ComboBox", "Checkbox", "RadioButton",
    "Menu", "MenuItem", "Tab", "Table", "Tree", "Toolbar",
})


def _actionable_elements(elements: list[dict]) -> list[dict]:
    """Return only elements that pass the action-snapshot filter.

    An element qualifies when:
    - its ``role`` is one of ``_ACTION_SNAPSHOT_ROLES``, AND
    - it carries an ``"enabled"`` state, OR its role is ``"Toolbar"``
      (Toolbar items render without an explicit enabled flag).
    - for Checkboxes: only keep LWCheckbox (the real widget), not the
      CheckBox container which doesn't carry the selected state.
    """
    result: list[dict] = []
    for element in elements:
        role = str(element.get("role") or "")
        if role not in _ACTION_SNAPSHOT_ROLES:
            continue
        states = element.get("states") or []
        if "enabled" not in states and role != "Toolbar":
            continue
        # Skip CheckBox containers — LWCheckbox is the real widget
        java_cls = str((element.get("java") or {}).get("simpleClassName") or "")
        if java_cls == "CheckBox":
            continue
        result.append(element)
    return result


def java_elements_to_action_snapshot(
    elements: list[dict],
    max_chars: int = config.MAX_SNAPSHOT_CHARS,
) -> str:
    """Return only action-worthy Java elements for the recorder AI.

    Oracle Forms scans include many layout containers whose refs are technically
    clickable but not meaningful replay targets. Keeping them out of the model
    context prevents recordings from binding script steps to volatile panels.

    Uses ``_actionable_elements`` for filtering so that
    ``build_action_context`` can guarantee an exact element_id match.
    """
    lines: list[str] = []
    for element in _actionable_elements(elements):
        element_id = element.get("elementid", "?")
        name = element.get("name") or element.get("text") or ""
        role = str(element.get("role") or "")
        states = element.get("states") or []
        semantic_ref = element.get("semantic_ref") or element.get("friendly_name") or ""
        state_text = ",".join(states)
        line = f"[{element_id}] {name} | {role}"
        if semantic_ref and semantic_ref != element_id:
            line += f" | ref:{semantic_ref}"
        if state_text:
            line += f" | states:{state_text}"
        lines.append(line)
    result = "\n".join(lines) or "(no actionable elements found)"
    if len(result) > max_chars:
        result = result[:max_chars] + "\n...(truncated)"
    return result


def build_action_context(scan: dict) -> tuple[list[dict[str, Any]], dict[str, dict]]:
    """Return ``(action_tree, {element_id: repo_element})`` for a scan.

    The tree is the PRIMARY output. It is a hierarchical list of nodes
    representing the form structure:

    ::

        Form: <window title>
          [e207] Open Folder... (Button, enabled)
          [e213] Quote/Order Information (Tab, enabled, selected)
            [e36] Order Number , [e39] Order Type (LOV) , ...
          Buttons:
            [e226] Clear (Button, enabled) , ...

    The AI snapshot text is derived from the tree via
    :func:`render_snapshot_text`.

    Only actionable elements from the selected tab are included.
    Background form modules are excluded via :func:`active_window_scan`.
    """
    scoped = active_window_scan(scan)
    all_elements = java_nodes_to_repo_elements(scoped)
    actionable = _actionable_elements(all_elements)

    # --- Detect tab structure from the scoped DOM tree ---
    scoped_nodes = flatten_nodes(scoped)
    full_nodes = flatten_nodes(scan)  # full scan — needed for TabBar children
    tab_info = _detect_tabs(scoped_nodes)
    form_title = active_form_title(scoped)

    if tab_info:
        selected_tab = tab_info["selected"]
        tab_titles = tab_info["titles"]
        tab_states = tab_info["tab_states"]
        tab_content_ids = tab_info["selected_content_ids"]
        form_level_ids = tab_info["form_level_ids"]
        tab_bar_y = tab_info["tab_bar_y"]
        tab_bar_element_id = tab_info.get("tab_bar_element_id")

        # Partition actionable elements into: selected-tab, buttons, other
        tab_elements: list[dict] = []
        button_elements: list[dict] = []
        tree_elements: list[dict] = []
        form_field_elements: list[dict] = []  # form-level fields with data

        # Build set of human-readable field names to identify VTextField labels.
        # Tech-named elements whose text matches a field name are redundant
        # captions; those with data values (e.g. customer names) are kept.
        # Include ALL elements (not just actionable) since labels can reference
        # fields with non-actionable semantic types like Panel.
        human_field_names: set[str] = set()
        for el in all_elements:
            name = el.get("name") or ""
            if not _looks_like_technical_name(name) and name:
                # Strip tab-page prefix for matching
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
                continue  # skip toolbar/menu/tab chrome
            # Skip VTextField labels: tech-named elements whose text is a
            # known field caption.  Keep display-only fields with data values.
            # Also skip zero-width/invisible and narrow indicator cells (≤15px).
            if _looks_like_technical_name(name):
                text = (el.get("text") or "").strip()
                w = el.get("width", 0)
                if not text or text in human_field_names or w <= 15:
                    continue  # label, empty, invisible, or indicator — skip
            # Skip read-only display mirrors (the "(read-only)" field labels).
            # WHY: Oracle Forms pairs each LOV/input field with a non-editable
            # element that merely echoes the resolved value. Per qcs_studio UX
            # decision these clutter the curated tree + AI snapshot, so we drop
            # them here; they stay discoverable via the full-element hover
            # overlay. Interactive controls are never treated as read-only.
            #
            # IMPORTANT: Never filter tab/grid content columns with _is_readonly_display —
            # the table builder needs all columns including read-only ones. For
            # form-level elements, only filter technical-name mirror fields
            # (VTextField items), but keep standalone display fields like
            # "Summary tab page null".
            if eid not in tab_content_ids and _is_readonly_display(el):
                # For form-level elements: only skip technical-name mirrors.
                # Standalone display fields with real names pass through.
                if _looks_like_technical_name(el.get("name", "")):
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

        # Include display-only Panel elements that carry real data values.
        # Oracle Forms marks some read-only fields as Panel (not Field),
        # so _actionable_elements excludes them.  Re-add qualifying ones.
        #
        # NOTE: Per the qcs_studio "exclude read-only display elements" rule,
        # _is_readonly_display() now suppresses these Panel mirrors as well, so
        # this block is effectively inert for non-editable panels. It is kept
        # only to re-add any (rare) editable Panel-typed inputs with data.
        included_ids = {el.get("elementid") for el in tab_elements}
        for el in all_elements:
            eid = el.get("elementid", "")
            if eid in included_ids:
                continue
            role = str(el.get("role") or "")
            if role != "Panel":
                continue
            if _is_readonly_display(el):
                continue  # read-only display mirror — excluded (hover-only)
            states = el.get("states") or []
            if "showing" not in states:
                continue
            name = el.get("name") or ""
            text = (el.get("text") or "").strip()
            if not name or not text or _looks_like_technical_name(name):
                continue
            # Must have a value different from its label name
            if text == name:
                continue
            w = el.get("width", 0)
            if w <= 0:
                continue
            if eid in tab_content_ids:
                tab_elements.append(el)

        # Split buttons: toolbar (above tabs) vs footer (below tabs)
        toolbar_buttons: list[dict] = []
        footer_buttons: list[dict] = []
        for el in button_elements:
            if el.get("y", 0) < tab_bar_y:
                toolbar_buttons.append(el)
            else:
                footer_buttons.append(el)

        # Deduplicate form-level buttons that overlap with tab-content buttons
        # Oracle Forms often has overlapping FScrollBox canvases with duplicate buttons
        if tab_elements:
            tab_btn_positions = set()
            for el in tab_elements:
                if str(el.get("role") or "") == "Button":
                    tab_btn_positions.add((el.get("x", 0), el.get("y", 0)))
            if tab_btn_positions:
                _OVERLAP_TOL = 5
                def _overlaps(el: dict) -> bool:
                    ex, ey = el.get("x", 0), el.get("y", 0)
                    return any(
                        abs(ex - tx) <= _OVERLAP_TOL and abs(ey - ty) <= _OVERLAP_TOL
                        for tx, ty in tab_btn_positions
                    )
                toolbar_buttons = [b for b in toolbar_buttons if not _overlaps(b)]
                footer_buttons = [b for b in footer_buttons if not _overlaps(b)]

        tools_menu_items = _extract_tools_menu(flatten_nodes(scan))

        # ── Per-tab element partitioning ───────────────────────────────
        # Partition actionable tab elements so each tab gets its own list.
        # This lets _build_action_tree render each tab as an individual
        # section rather than collapsing everything into one block.
        per_tab_elements: dict[str, list[dict]] = {}
        all_prefixes = {t: f"{t} tab page " for t in tab_titles}
        for el in tab_elements:
            name = el.get("name") or ""
            matched = False
            for t, prefix in all_prefixes.items():
                if name.startswith(prefix) or (
                    # Also match unstripped names (pre-tab-prefix removal)
                    str(el.get("java", {}).get("accessibleName") or "").startswith(prefix)
                ):
                    per_tab_elements.setdefault(t, []).append(el)
                    matched = True
                    break
            if not matched:
                # Fallback: put unmatched into selected tab
                per_tab_elements.setdefault(selected_tab, []).append(el)

        # ── TabBar DOM nodes (for mapping tab titles → element IDs) ───
        tab_bar_nodes: list[dict] = []
        for n in full_nodes:
            if str(n.get("simpleClassName") or "") == "TabBar":
                attrs = n.get("attributes") or {}
                if attrs.get("tabTitles"):
                    tab_bar_nodes.append(n)

        tree = _build_action_tree(
            form_title, tab_titles, tab_states, selected_tab,
            tab_elements, toolbar_buttons, footer_buttons,
            tree_elements=tree_elements,
            form_field_elements=form_field_elements,
            tab_bar_element_id=tab_bar_element_id,
            scoped_nodes=scoped_nodes,
            tab_content_ids=tab_content_ids,
            outer_tab_bars=tab_info.get("outer_tab_bars"),
            tools_menu_items=tools_menu_items,
            per_tab_elements=per_tab_elements,
            tab_bar_nodes=tab_bar_nodes,
        )
    else:
        # Check if this is an Oracle Forms overlay (dialog or popup)
        root_cls = ""
        if scoped_nodes:
            root_cls = str(scoped_nodes[0].get("simpleClassName") or "")
        if root_cls in _ORACLE_FORMS_DIALOG_CLASSES:
            # Build simple tree: Form → Message + Buttons
            tree = _build_dialog_tree(form_title, scoped_nodes, actionable)
        elif root_cls in _ORACLE_FORMS_POPUP_CLASSES:
            tree = _build_popup_tree(form_title, scoped_nodes, actionable)
        else:
            # No tab structure — flat tree
            tree = _build_flat_tree(all_elements, form_title)

    id_map: dict[str, dict] = {
        el["elementid"]: el
        for el in actionable
        if el.get("elementid")
    }
    return tree, id_map


def build_action_payload(
    scan: dict,
) -> dict[str, Any]:
    """Return AI text + UI tree from one canonical snapshot source.

    WHY: The tree is the PRIMARY output from ``build_action_context``.
    Text is DERIVED from the tree via ``render_snapshot_text``, ensuring
    the AI snapshot and the Studio UI always agree. Previously text was
    built independently and then parsed back into a tree, which could drift.

    Since screenshots are no longer cropped, element coordinates are raw
    (unadjusted) and align directly with the fullscreen screenshot.
    """
    tree, id_map = build_action_context(scan)
    # Extract form title from the scoped scan, not from the tree root.
    # The tree is a flat list of top-level children (no Form wrapper),
    # so active_form_title() is the canonical source for the title.
    form_title = active_form_title(active_window_scan(scan))
    snapshot_text = render_snapshot_text(tree, form_title)
    return {
        "text": snapshot_text,
        "tree": tree,
        "id_map": id_map,
    }


# ---------------------------------------------------------------------------
# Tab detection helpers
# ---------------------------------------------------------------------------

def _detect_tabs(nodes: list[dict]) -> dict | None:
    """Find FormsTabPanel/TabBar info and map selected tab to content node IDs.

    Returns ``None`` if no tab bar is found.
    """
    # Find ALL TabBar nodes with tab metadata
    all_tab_bars: list[dict] = []
    for n in nodes:
        if str(n.get("simpleClassName") or "") == "TabBar":
            attrs = n.get("attributes") or {}
            if attrs.get("tabTitles"):
                all_tab_bars.append(n)
    if not all_tab_bars:
        return None

    # Sort by y ascending — innermost (largest y) drives content matching
    all_tab_bars.sort(
        key=lambda n: (n.get("screenBounds") or {}).get("y", 0)
    )
    tab_bar = all_tab_bars[-1]  # innermost (largest y)

    attrs = tab_bar.get("attributes") or {}
    raw_titles = str(attrs.get("tabTitles", ""))
    titles = [t.strip() for t in raw_titles.split("|") if t.strip()]
    selected = str(attrs.get("tabSelectedTitle", "")).strip()
    if not titles or not selected:
        return None
    if selected not in titles:
        return None

    # Parse tabStates — each tab has "enabled,visible" pair separated by " | "
    raw_states = str(attrs.get("tabStates", ""))
    tab_states: list[dict] = []
    for part in raw_states.split("|"):
        bits = [b.strip() for b in part.strip().split(",")]
        enabled = bits[0] == "1" if len(bits) > 0 else True
        visible = bits[1] == "1" if len(bits) > 1 else True
        tab_states.append({"enabled": enabled, "visible": visible})

    tab_bar_sb = tab_bar.get("screenBounds") or tab_bar.get("bounds") or {}
    tab_bar_y = _int(tab_bar_sb.get("y"), 0)

    # Build outer_tab_bars list (all tab bars except the innermost)
    outer_tab_bars: list[dict] = []
    for tb in all_tab_bars[:-1]:
        tb_attrs = tb.get("attributes") or {}
        tb_titles_raw = str(tb_attrs.get("tabTitles", ""))
        tb_titles = [t.strip() for t in tb_titles_raw.split("|") if t.strip()]
        tb_selected = str(tb_attrs.get("tabSelectedTitle", "")).strip()
        tb_raw_states = str(tb_attrs.get("tabStates", ""))
        tb_states: list[dict] = []
        for part in tb_raw_states.split("|"):
            bits = [b.strip() for b in part.strip().split(",")]
            en = bits[0] == "1" if len(bits) > 0 else True
            vis = bits[1] == "1" if len(bits) > 1 else True
            tb_states.append({"enabled": en, "visible": vis})
        tb_id = tb.get("id")
        outer_tab_bars.append({
            "titles": tb_titles,
            "selected": tb_selected,
            "tab_states": tb_states,
            "element_id": f"e{tb_id}" if tb_id is not None else None,
        })

    # Find the parent FormsTabPanel for the innermost TabBar
    tab_bar_path = str(tab_bar.get("path") or "")
    forms_tab_panel = None
    for n in nodes:
        if str(n.get("simpleClassName") or "") == "FormsTabPanel":
            n_path = str(n.get("path") or "")
            if tab_bar_path.startswith(n_path + "/"):
                forms_tab_panel = n
                break

    if not forms_tab_panel:
        return None

    # Walk up to find the DrawnPanel parent (contains FormsTabPanel + FScrollBoxes)
    ftp_parent_path = str(forms_tab_panel.get("parentPath") or "")
    parent_dp = None
    for n in nodes:
        if str(n.get("path") or "") == ftp_parent_path:
            parent_dp = n
            break

    if not parent_dp:
        return None

    # Classify sibling FScrollBoxes by prefix matching.
    # Oracle Forms names fields "<Tab Name> tab page <Field Name>", so we
    # check each FScrollBox against every tab title's prefix.
    # Boxes matching the selected tab → selected content.
    # Boxes matching another tab → non-selected content (skip).
    # Boxes matching no tab → form-level.
    all_prefixes = {t: f"{t} tab page " for t in titles}
    sibling_boxes: list[dict] = [
        child for child in parent_dp.get("children") or []
        if str(child.get("simpleClassName") or "") == "FScrollBox"
    ]

    selected_content_ids: set[str] = set()
    form_level_ids: set[str] = set()
    tab_prefix = all_prefixes[selected]

    unmatched_boxes: list[dict] = []
    matched_tab_names: set[str] = set()
    for box in sibling_boxes:
        # Check if this box belongs to any tab
        matched_tab = None
        for t, prefix in all_prefixes.items():
            if _subtree_has_prefix(box, prefix):
                matched_tab = t
                break
        if matched_tab == selected:
            _collect_ids(box, selected_content_ids)
            if matched_tab is not None:
                matched_tab_names.add(matched_tab)
        elif matched_tab is not None:
            matched_tab_names.add(matched_tab)
            pass  # belongs to another tab — skip
        else:
            unmatched_boxes.append(box)

    # When prefix matching identified fewer tabs than exist, the unmatched
    # boxes are likely content for the un-prefixed tab pages (Oracle Forms
    # omits the prefix when field names are unique across tabs).  Track this
    # so the frozen-column merge can be more conservative.
    tabs_missing_prefix = len(titles) - len(matched_tab_names)

    # Oracle Forms sometimes omits the "tab page" prefix from data fields
    # (e.g. when the cursor is not on the first record).  When prefix
    # matching produces very few content IDs and a much larger unmatched
    # FScrollBox exists, adopt the largest unmatched box as tab content.
    if unmatched_boxes:
        largest_unmatched = max(unmatched_boxes, key=lambda b: _subtree_size(b))
        largest_size = _subtree_size(largest_unmatched)
        if largest_size > len(selected_content_ids) * 3 and largest_size > 50:
            _collect_ids(largest_unmatched, selected_content_ids)
            unmatched_boxes = [b for b in unmatched_boxes if b is not largest_unmatched]

    # Frozen-column merge: Oracle Forms multi-record grids often have a
    # left-side FScrollBox with frozen columns (e.g. "Line", "Ordered Item")
    # that sits alongside the main scrollable tab-content FScrollBox.  These
    # frozen columns share the same y-range as the grid rows but have no
    # tab-page prefix, so they end up unmatched.  Detect this by computing
    # the tab-content y-range and merging overlapping elements from
    # unmatched sibling FScrollBoxes into the content set.  Elements below
    # the grid (summary fields like "Line Total") stay as form-level.
    #
    # Guard: in multi-tab forms (e.g. Find forms), ALL tab pages share the
    # same y-range because they're stacked in the same panel area.  Large
    # unmatched boxes are likely other tab pages, not frozen columns.  Only
    # merge a box if it is small relative to the selected content (typical
    # frozen-column size is ~10-20 elements vs 100+ for the main grid).
    # When some tabs lack prefix naming, be extra conservative — any
    # non-trivial unmatched box is likely a missing tab page.
    #
    # Key heuristic: frozen columns are narrow vertical strips (< 50% of
    # the main content width).  Other tab pages span the full content width.
    if selected_content_ids and unmatched_boxes:
        content_ys = _collect_field_ys(sibling_boxes, selected_content_ids)
        # Compute x-extent of selected content for width comparison
        sel_x_extent: tuple[int, int] | None = None
        for box in sibling_boxes:
            box_ids: set[str] = set()
            _collect_ids(box, box_ids)
            if box_ids & selected_content_ids:
                sel_x_extent = _collect_box_x_extent(box)
                break
        sel_width = (sel_x_extent[1] - sel_x_extent[0]) if sel_x_extent else 0
        if content_ys:
            y_min, y_max = min(content_ys), max(content_ys)
            still_unmatched: list[dict] = []
            for box in unmatched_boxes:
                box_ys = _collect_all_field_ys(box)
                overlapping = [y for y in box_ys if y_min <= y <= y_max]
                if overlapping:
                    # Check if this is a narrow frozen-column strip or a
                    # full-width tab page.
                    box_x = _collect_box_x_extent(box)
                    box_width = (box_x[1] - box_x[0]) if box_x else 0
                    is_narrow = sel_width > 0 and box_width < sel_width * 0.5
                    if is_narrow:
                        # Narrow strip → frozen columns, merge
                        _collect_ids_in_y_range(box, y_min, y_max, selected_content_ids, form_level_ids)
                    else:
                        pass  # full-width → likely another tab page, drop
                else:
                    still_unmatched.append(box)
            unmatched_boxes = still_unmatched

    for box in unmatched_boxes:
        _collect_ids(box, form_level_ids)

    # Collect IDs from sibling FormsTabPanels (e.g. a sidebar tree panel).
    # These are always-visible panels that sit alongside the main tab panel.
    # Skip FormsTabPanels that contain outer tab bars — they are navigation
    # chrome, not content.
    outer_ftp_ids = set()
    for tb in all_tab_bars[:-1]:
        tb_path = str(tb.get("path") or "")
        for child in parent_dp.get("children") or []:
            if str(child.get("simpleClassName") or "") != "FormsTabPanel":
                continue
            child_path = str(child.get("path") or "")
            if tb_path.startswith(child_path + "/"):
                outer_ftp_ids.add(child.get("id"))
    for child in parent_dp.get("children") or []:
        if str(child.get("simpleClassName") or "") != "FormsTabPanel":
            continue
        if child is forms_tab_panel:
            continue  # skip the main tab panel we already processed
        if child.get("id") in outer_ftp_ids:
            continue  # skip outer tab panels — rendered as tab bars
        _collect_ids(child, form_level_ids)

    tab_bar_id = tab_bar.get("id")

    return {
        "titles": titles,
        "selected": selected,
        "tab_states": tab_states,
        "selected_content_ids": selected_content_ids,
        "form_level_ids": form_level_ids,
        "tab_bar_y": tab_bar_y,
        "tab_bar_element_id": f"e{tab_bar_id}" if tab_bar_id is not None else None,
        "outer_tab_bars": outer_tab_bars,
    }


def _subtree_has_prefix(node: dict, prefix: str) -> bool:
    """Check if any node in subtree has an accessibleName starting with *prefix*."""
    an = node.get("accessibleName") or ""
    if an.startswith(prefix):
        return True
    for child in node.get("children") or []:
        if _subtree_has_prefix(child, prefix):
            return True
    return False


def _subtree_size(node: dict) -> int:
    """Count total descendants in a subtree (excluding the root)."""
    count = 0
    for child in node.get("children") or []:
        count += 1 + _subtree_size(child)
    return count


def _collect_ids(node: dict, ids: set[str]) -> None:
    """Recursively collect ``eN`` element IDs from a subtree."""
    nid = node.get("id")
    if nid is not None:
        ids.add(f"e{nid}")
    for child in node.get("children") or []:
        _collect_ids(child, ids)


def _collect_field_ys(boxes: list[dict], id_set: set[str]) -> list[int]:
    """Collect y-coordinates of VTextFields whose IDs are in *id_set*."""
    ys: list[int] = []
    for box in boxes:
        _gather_field_ys(box, id_set, ys)
    return ys


def _collect_box_x_extent(box: dict) -> tuple[int, int] | None:
    """Return (x_min, x_max) screen-coordinate extent of all fields in *box*."""
    xs: list[int] = []
    def _gather(node: dict) -> None:
        sb = node.get("screenBounds") or {}
        x = sb.get("x")
        w = sb.get("width", 0)
        if x is not None and w > 0:
            xs.append(x)
            xs.append(x + w)
        for child in node.get("children") or []:
            _gather(child)
    _gather(box)
    if not xs:
        return None
    return min(xs), max(xs)


def _gather_field_ys(node: dict, id_set: set[str], ys: list[int]) -> None:
    nid = node.get("id")
    if nid is not None and f"e{nid}" in id_set:
        if str(node.get("simpleClassName") or "") == "VTextField":
            sb = node.get("screenBounds") or {}
            y = sb.get("y")
            if y is not None:
                ys.append(y)
    for child in node.get("children") or []:
        _gather_field_ys(child, id_set, ys)


def _collect_all_field_ys(node: dict) -> list[int]:
    """Collect y-coordinates of all VTextFields in a subtree."""
    ys: list[int] = []
    if str(node.get("simpleClassName") or "") == "VTextField":
        sb = node.get("screenBounds") or {}
        y = sb.get("y")
        if y is not None:
            ys.append(y)
    for child in node.get("children") or []:
        ys.extend(_collect_all_field_ys(child))
    return ys


def _collect_ids_in_y_range(
    node: dict,
    y_min: int, y_max: int,
    content_ids: set[str],
    form_ids: set[str],
) -> None:
    """Split a subtree's element IDs based on y-coordinate range.

    Elements with y in ``[y_min, y_max]`` go to *content_ids*;
    others go to *form_ids*.
    """
    nid = node.get("id")
    if nid is not None:
        sb = node.get("screenBounds") or {}
        y = sb.get("y", 0)
        eid = f"e{nid}"
        if y_min <= y <= y_max:
            content_ids.add(eid)
        else:
            form_ids.add(eid)
    for child in node.get("children") or []:
        _collect_ids_in_y_range(child, y_min, y_max, content_ids, form_ids)


_BLUE_BG_PATTERN = re.compile(r"r=0,g=0,b=(?:25[0-5]|2[0-4]\d)")


def _detect_record_indicator_y(
    nodes: list[dict], content_ids: set[str] | None = None
) -> int | None:
    """Find the y-coordinate of the Oracle Forms record indicator (blue bar).

    Oracle Forms multi-record blocks have a narrow VTextField per row in a
    sibling FScrollBox.  The one with a blue background marks the selected
    record.  When ``content_ids`` is given, only indicators whose element ID
    falls within the tab-content FScrollBox are considered (avoids form-level
    overlay duplicates).
    """
    for node in nodes:
        cls = node.get("simpleClassName") or ""
        if cls != "VTextField":
            continue
        name = node.get("accessibleName") or ""
        if name:
            continue  # indicator has no name
        sb = node.get("screenBounds") or {}
        w = sb.get("width", 0)
        if w > 15:
            continue  # indicator is narrow (~9px)
        if content_ids is not None:
            nid = f"e{node.get('id', '')}"
            if nid not in content_ids:
                continue
        attrs = node.get("attributes") or {}
        bg = attrs.get("getBackground") or ""
        if _BLUE_BG_PATTERN.search(bg):
            return sb.get("y", 0)
    return None


def _row_field_names(
    row: list[dict], tab_page_prefix: str | None
) -> frozenset[str]:
    """Return normalized, deduplicated field names for a row.

    Technical names and duplicates (e.g. from the active-record prefix) are
    excluded so that the selected row's signature matches non-selected rows.
    """
    names: set[str] = set()
    for el in row:
        n = el.get("name") or ""
        if _looks_like_technical_name(n):
            continue
        n = _strip_tab_prefix(n, tab_page_prefix)
        # WHY: Also strip any "<TabName> tab page " prefix from OTHER tabs
        # (e.g. "Summary tab page Order Number" in an Orders-tab grid) so
        # the active-record row signature matches non-selected rows.
        n = _ANY_TAB_PAGE_PREFIX.sub("", n)
        n = _LOV_SUFFIX.sub("", n)
        names.add(n)
    return frozenset(names)


def _detect_table(
    rows: list[list[dict]], tab_page_prefix: str | None
) -> tuple[list[list[dict]], list[list[dict]], list[list[dict]]]:
    """Split rows into (table_rows, pre_rows, post_rows).

    A table is detected when ≥2 consecutive rows share the same
    normalized field names.  The active record row may have extra
    fields (checkboxes only visible on focus) — these superset rows
    are included in the table run.
    Returns ([], all_rows, []) when no table.
    """
    if len(rows) < 2:
        return [], rows, []

    # Compute signatures
    sigs = [_row_field_names(r, tab_page_prefix) for r in rows]

    # Find the longest run of identical signatures
    best_start = best_end = 0
    i = 0
    while i < len(sigs):
        j = i + 1
        while j < len(sigs) and sigs[j] == sigs[i]:
            j += 1
        run_len = j - i
        if run_len >= 2 and run_len > (best_end - best_start):
            best_start, best_end = i, j
        i = j

    if best_end - best_start < 2:
        return [], rows, []

    # Extend the run to include adjacent superset rows (active record may
    # have extra checkboxes or buttons that are only visible on focus).
    base_sig = sigs[best_start]
    while best_start > 0 and sigs[best_start - 1] >= base_sig:
        best_start -= 1
    while best_end < len(sigs) and sigs[best_end] >= base_sig:
        best_end += 1

    return rows[best_start:best_end], rows[:best_start], rows[best_end:]


# ---------------------------------------------------------------------------
# Tree → snapshot text renderer
# ---------------------------------------------------------------------------

def render_snapshot_text(
    tree_nodes: list[dict[str, Any]],
    form_title: str,
    max_chars: int = config.MAX_SNAPSHOT_CHARS,
) -> str:
    """Render a UI tree into the AI-friendly snapshot text format.

    This is a pure formatting function. The tree is the primary artifact
    (built by ``_build_action_tree``); this function walks it and produces
    the AI snapshot text. The text is DERIVED from the tree rather than
    built independently.

    WHY: The front-end Studio UI will modify and persist the tree. The AI
    snapshot text must always reflect the current state of that tree.
    Previously text was produced directly and a tree was recovered from
    it by parsing — which could drift. Now the tree is the single source
    of truth.
    """
    lines: list[str] = [f"Form: {form_title}"]

    def _format_field_text(child: dict) -> str:
        """Format a single field/button element as inline text (no newlines).
        Matches the legacy _format_field / _format_button output format so the
        AI snapshot is identical to what _format_hierarchical produced.
        """
        child_ref = child.get("element_ref", "")
        child_label = child.get("label", "")
        child_role = child.get("role", "")
        child_states = child.get("states", "")
        child_eid = child_ref if (child_ref and child_ref.startswith("e")) else ""
        child_eid_tag = f"[{child_eid}] " if child_eid else ""

        if child_role == "Button":
            cs = child_states or "enabled"
            return f"{child_eid_tag}{child_label} (Button, {cs})"
        if child_role == "ReadOnly":
            return f"{child_eid_tag}{child_label} (read-only)"
        if child_role == "LOV":
            return f"{child_eid_tag}{child_label} (LOV)"
        if child_role == "ComboBox":
            extra = ""
            if child.get("read_only"):
                extra = " (read-only)"
            vo = child.get("value_options")
            if vo:
                extra += f" values: {vo}"
            return f"{child_eid_tag}{child_label} (ComboBox){extra}"
        if child_role == "Checkbox":
            checked_str = "checked" if child.get("checked") else "unchecked"
            return f"{child_eid_tag}{child_label} (Checkbox, {checked_str})"
        # Field or other
        suffix = ""
        if child.get("has_lov"):
            suffix += " (LOV)"
        cv = child.get("current_value")
        if cv:
            suffix += f" = {cv}"
        if child.get("read_only") and child_role != "ComboBox":
            suffix += " (read-only)"
        return f"{child_eid_tag}{child_label}{suffix}"

    def _format_table_cell(cell: dict) -> str:
        """Format a single table cell with element ID prefix and value."""
        eid = cell.get("element_ref", "")
        eid_tag = f"[{eid}] " if (eid and eid.startswith("e")) else ""
        role = cell.get("role", "")
        if role == "Checkbox":
            val = "[x]" if cell.get("checked") else "[ ]"
            return f"{eid_tag}{val}"
        cv = cell.get("current_value")
        if cv is not None:
            return f"{eid_tag}{cv}"
        return f"{eid_tag}{cell.get('label', '')}"

    def _walk(nodes: list[dict[str, Any]], indent: str) -> None:
        i = 0
        while i < len(nodes):
            node = nodes[i]
            role = node.get("role", "")
            ref = node.get("element_ref", "")
            label = node.get("label", "")
            states = node.get("states", "")
            children = node.get("children") or []

            eid = ref if (ref and ref.startswith("e")) else ""
            eid_tag = f"[{eid}] " if eid else ""

            if role == "Button":
                # Consecutive Buttons: join with " , " on one line
                btn_parts: list[str] = []
                while i < len(nodes) and nodes[i].get("role") == "Button":
                    btn_parts.append(_format_field_text(nodes[i]))
                    i += 1
                lines.append(f"{indent}{' , '.join(btn_parts)}")
                continue

            elif role == "Tab":
                state_str = states or "enabled"
                lines.append(f"{indent}{eid_tag}{label} (Tab, {state_str})")
                if children:
                    _walk(children, indent + "  ")

            elif role == "Group":
                # WHY: Groups like "Tabs: ..." carry a real element ID that
                # should be rendered as [eNN] prefix. Other groups like
                # "Buttons:" and "Tools Menu:" are synthetic headers.
                if label == "Buttons":
                    lines.append(f"{indent}Buttons:")
                elif label == "Tools Menu":
                    lines.append(f"{indent}Tools Menu:")
                else:
                    lines.append(f"{indent}{eid_tag}{label}")
                if children:
                    _walk(children, indent + "  ")

            elif role == "FieldRow":
                parts = [_format_field_text(child) for child in children]
                lines.append(f"{indent}{' , '.join(parts)}")

            elif role == "Table":
                columns = node.get("table_columns") or []
                rows_data = node.get("table_rows") or []
                lines.append(f"{indent}| # | {' | '.join(columns)} |")
                lines.append(f"{indent}|{'|'.join('---' for _ in range(len(columns) + 1))}|")
                for row_data in rows_data:
                    marker = row_data.get("marker", "")
                    cells = row_data.get("cells") or []
                    cell_texts = [_format_table_cell(c) for c in cells]
                    lines.append(f"{indent}| {marker} | {' | '.join(cell_texts)} |")

            elif role == "Item":
                if node.get("checked") is not None:
                    mark = "[x]" if node.get("checked") else "[ ]"
                    lines.append(f"{indent}{mark} {label}")
                else:
                    lines.append(f"{indent}{label}")

            elif role in ("Field", "ReadOnly", "LOV", "ComboBox", "Checkbox"):
                lines.append(f"{indent}{_format_field_text(node)}")

            elif role == "Tree":
                tree_items = node.get("tree_items") or []
                if tree_items:
                    lines.append(f"{indent}{eid_tag}{label}")
                    for ti in tree_items:
                        ti_label = ti.get("label", "")
                        ti_marker = " *" if ti.get("selected") else ""
                        lines.append(f"{indent}    {ti_label}{ti_marker}")
                else:
                    lines.append(f"{indent}{eid_tag}{label} (Tree, empty)")

            else:
                lines.append(f"{indent}{eid_tag}{label}")
                if children:
                    _walk(children, indent + "  ")

            i += 1

    _walk(tree_nodes, "  ")

    result = "\n".join(lines) or "(no actionable elements found)"
    if len(result) > max_chars:
        result = result[:max_chars] + "\n...(truncated)"
    return result


# ---------------------------------------------------------------------------
# Element → tree builder
# ---------------------------------------------------------------------------

def _build_action_tree(
    form_title: str,
    tab_titles: list[str],
    tab_states: list[dict],
    selected_tab: str,
    tab_elements: list[dict],
    toolbar_buttons: list[dict],
    footer_buttons: list[dict],
    tree_elements: list[dict] | None = None,
    form_field_elements: list[dict] | None = None,
    tab_bar_element_id: str | None = None,
    scoped_nodes: list[dict] | None = None,
    tab_content_ids: set[str] | None = None,
    outer_tab_bars: list[dict] | None = None,
    tools_menu_items: list[tuple[str, bool, bool]] | None = None,
    per_tab_elements: dict[str, list[dict]] | None = None,
    tab_bar_nodes: list[dict] | None = None,
) -> list[dict[str, Any]]:
    """Build a hierarchical UI tree from partitioned form elements.

    This is the PRIMARY output of action context building. The tree is the
    single source of truth for both the Studio UI and the AI snapshot text
    (via ``render_snapshot_text``).

    WHY: Previously text was produced first, then parsed back into a tree.
    Now the tree is built directly from partitioned elements and text is
    derived from it — no parsing step, no drift.
    """
    synth_counter = 0

    def _synth_ref() -> str:
        nonlocal synth_counter
        synth_counter += 1
        return f"grp-{synth_counter}"

    def _bounds_for(el: dict) -> dict[str, int]:
        b = el.get("bounds") or {}
        return {
            "x": int(b.get("x", el.get("x", 0)) or 0),
            "y": int(b.get("y", el.get("y", 0)) or 0),
            "width": int(b.get("width", el.get("width", 0)) or 0),
            "height": int(b.get("height", el.get("height", 0)) or 0),
        }

    def _field_role(el: dict) -> str:
        """Determine the display role for a field element in the tree."""
        role = str(el.get("role") or "")
        name = el.get("name") or ""
        states = el.get("states") or []
        is_editable = "editable" in states
        has_lov = bool(_LOV_SUFFIX.search(name))
        java_cls = str((el.get("java") or {}).get("simpleClassName") or "")

        if role == "Button":
            return "Button"
        if role == "Checkbox":
            return "Checkbox"
        if role == "ComboBox":
            return "ComboBox"
        if has_lov:
            return "LOV"
        if not is_editable:
            return "ReadOnly"
        return "Field"

    def _field_node(el: dict, tab_page_prefix: str | None = None) -> dict[str, Any]:
        """Build a tree node for a single field/button element."""
        element_id = el.get("elementid", "")
        name = el.get("name") or el.get("text") or ""
        name = _strip_tab_prefix(name, tab_page_prefix)
        role = str(el.get("role") or "")
        states_list = el.get("states") or []
        is_editable = "editable" in states_list
        has_lov = bool(_LOV_SUFFIX.search(name))
        name_clean = _LOV_SUFFIX.sub("", name)
        java_data = el.get("java") or {}
        text = (el.get("text") or "").strip()

        display_role = _field_role(el)

        node: dict[str, Any] = {
            "element_ref": element_id,
            "label": name_clean,
            "role": display_role,
            "states": "",
            "included": True,
            "bounds": _bounds_for(el),
            "children": [],
        }

        if display_role == "Button":
            state_str = "enabled" if "enabled" in states_list else "disabled"
            node["states"] = state_str
            node["label"] = _ALT_KEY_SUFFIX.sub("", name_clean)
        elif display_role == "Checkbox":
            node["checked"] = "selected" in states_list
        elif display_role == "ComboBox":
            value_options = java_data.get("valueOptions") or []
            if value_options:
                node["value_options"] = value_options
            node["read_only"] = not is_editable
        elif display_role == "LOV":
            node["has_lov"] = True
            node["read_only"] = not is_editable
        elif display_role == "ReadOnly":
            if _looks_like_technical_name(name_clean) and text:
                node["label"] = text
            node["read_only"] = True
        elif display_role == "Field":
            node["read_only"] = not is_editable

        # Current value — always set for table rendering, even if empty.
        # WHY: Table cells need to distinguish between "no value" (which
        # falls back to showing the label) and "empty value" (blank cell).
        # An editable field with empty text is a blank cell, not a label.
        if text != name_clean.strip():
            node["current_value"] = text

        # Value options for non-ComboBox with options
        if display_role not in ("ComboBox",):
            value_options = java_data.get("valueOptions") or []
            if value_options:
                node["value_options"] = value_options

        return node

    tree_children: list[dict[str, Any]] = []

    # Toolbar buttons
    for el in toolbar_buttons:
        node = _field_node(el)
        tree_children.append(node)

    # Determine tree panel placement
    tree_before_tabs = False
    if tree_elements and tab_bar_element_id:
        tree_min_x = min(el.get("x", 9999) for el in tree_elements)
        tab_bar_x = 9999
        for n in (scoped_nodes or []):
            if f"e{n.get('id', '')}" == tab_bar_element_id:
                tab_bar_x = (n.get("screenBounds") or {}).get("x", 9999)
                break
        if tree_min_x < tab_bar_x:
            tree_before_tabs = True

    # Tree panels before tabs
    if tree_before_tabs:
        for el in (tree_elements or []):
            tnode = _field_node(el)
            tnode["role"] = "Tree"
            tnode["tree_items"] = _extract_tree_items(el)
            tree_children.append(tnode)

    # Outer tab bars: build each outer tab as a Tab node.
    # The selected outer tab gets inner tabs + content as children.
    # Unselected outer tabs are leaf Tab nodes.
    # WHY: With the new per-tab design, each tab is independently
    # referenceable via its own element ID, and the AI can see the
    # full tab structure. Previously they were collapsed into a
    # "Tabs: ..." summary line.
    #
    # We also need to build a tab title → element ID map for ALL
    # tab bars (outer + inner) so individual TabBarItem nodes get
    # proper element IDs.
    per_tab = per_tab_elements or {}
    tab_page_prefix = f"{selected_tab} tab page " if selected_tab else None

    # Build a combined map: tab title → element_id from ALL TabBar nodes
    all_tab_title_to_eid: dict[str, str] = {}
    if tab_bar_nodes:
        for n in tab_bar_nodes:
            for child in n.get("children") or []:
                child_sc = str(child.get("simpleClassName") or "")
                child_name = str(child.get("accessibleName") or child.get("displayName") or "").strip()
                child_id = child.get("id")
                if (child_sc == "TabBarItem" or child.get("semanticType") == "Tab") and child_name and child_id is not None:
                    all_tab_title_to_eid[child_name] = f"e{child_id}"

    # ── Build inner tab nodes first (they will be nested under the selected outer tab) ──
    inner_tab_nodes: list[dict[str, Any]] = []
    for i, t in enumerate(tab_titles):
        st = tab_states[i] if i < len(tab_states) else {}
        if not st.get("visible", True):
            continue

        tab_eid = all_tab_title_to_eid.get(t, "")
        is_selected = (t == selected_tab)
        is_disabled = not st.get("enabled", True)
        if is_selected:
            state_str = "disabled, selected" if is_disabled else "enabled, selected"
        else:
            state_str = "disabled" if is_disabled else "enabled"

        tab_node: dict[str, Any] = {
            "element_ref": tab_eid or _synth_ref(),
            "label": t,
            "role": "Tab",
            "states": state_str,
            "included": True,
            "bounds": {"x": 0, "y": 0, "width": 0, "height": 0},
            "children": [],
        }

        if is_selected:
            tab_els = per_tab.get(t, tab_elements)
            rows = _group_by_rows(tab_els)
            table_rows, pre_rows, post_rows = _detect_table(rows, tab_page_prefix)

            for row in pre_rows:
                row_children = [_field_node(el, tab_page_prefix) for el in row]
                if len(row_children) == 1:
                    tab_node["children"].append(row_children[0])
                else:
                    tab_node["children"].append({
                        "element_ref": _synth_ref(),
                        "label": "",
                        "role": "FieldRow",
                        "states": "",
                        "included": True,
                        "bounds": {"x": 0, "y": 0, "width": 0, "height": 0},
                        "children": row_children,
                    })

            if table_rows:
                selected_y = _detect_record_indicator_y(
                    scoped_nodes or [], tab_content_ids
                )
                table_node = _build_table_node(
                    table_rows, tab_page_prefix, selected_y, _synth_ref, _field_node
                )
                tab_node["children"].append(table_node)

            for row in post_rows:
                row_children = [_field_node(el, tab_page_prefix) for el in row]
                if len(row_children) == 1:
                    tab_node["children"].append(row_children[0])
                else:
                    tab_node["children"].append({
                        "element_ref": _synth_ref(),
                        "label": "",
                        "role": "FieldRow",
                        "states": "",
                        "included": True,
                        "bounds": {"x": 0, "y": 0, "width": 0, "height": 0},
                        "children": row_children,
                    })

        inner_tab_nodes.append(tab_node)

    # ── Build outer tab nodes, nesting inner tabs under the selected outer tab ──
    for otb in (outer_tab_bars or []):
        otb_eid = otb.get("element_id") or ""
        otb_selected = otb.get("selected", "")

        for i, t in enumerate(otb["titles"]):
            ost = otb["tab_states"][i] if i < len(otb["tab_states"]) else {}
            if not ost.get("visible", True):
                continue

            tab_eid = all_tab_title_to_eid.get(t, otb_eid)
            is_selected = (t == otb_selected)
            is_disabled = not ost.get("enabled", True)
            if is_selected:
                state_str = "disabled, selected" if is_disabled else "enabled, selected"
            else:
                state_str = "disabled" if is_disabled else "enabled"

            outer_tab_node: dict[str, Any] = {
                "element_ref": tab_eid or _synth_ref(),
                "label": t,
                "role": "Tab",
                "states": state_str,
                "included": True,
                "bounds": {"x": 0, "y": 0, "width": 0, "height": 0},
                "children": [],
            }

            if is_selected:
                # Nest inner tabs under the selected outer tab
                outer_tab_node["children"].extend(inner_tab_nodes)

            tree_children.append(outer_tab_node)

    # If no outer tabs, inner tabs go at top level
    if not outer_tab_bars:
        tree_children.extend(inner_tab_nodes)

    # ── Form-level fields ──────────────────────────────────────────────

    # ── Form-level fields ──────────────────────────────────────────────
    if form_field_elements:
        form_rows = _group_by_rows(form_field_elements)
        for row in form_rows:
            row_children = [_field_node(el, tab_page_prefix) for el in row]
            if len(row_children) == 1:
                tree_children.append(row_children[0])
            else:
                tree_children.append({
                    "element_ref": _synth_ref(),
                    "label": "",
                    "role": "FieldRow",
                    "states": "",
                    "included": True,
                    "bounds": {"x": 0, "y": 0, "width": 0, "height": 0},
                    "children": row_children,
                })

    # ── Footer buttons ─────────────────────────────────────────────────
    if footer_buttons:
        btn_group = {
            "element_ref": _synth_ref(),
            "label": "Buttons",
            "role": "Group",
            "states": "",
            "included": True,
            "bounds": {"x": 0, "y": 0, "width": 0, "height": 0},
            "children": [_field_node(el, tab_page_prefix) for el in footer_buttons],
        }
        tree_children.append(btn_group)

    # ── Tree panels after tabs ─────────────────────────────────────────
    if not tree_before_tabs:
        for el in (tree_elements or []):
            tnode = _field_node(el)
            tnode["role"] = "Tree"
            tnode["tree_items"] = _extract_tree_items(el)
            tree_children.append(tnode)

    # ── Tools menu ─────────────────────────────────────────────────────
    if tools_menu_items:
        menu_children: list[dict[str, Any]] = []
        for label, is_checkbox, checked in tools_menu_items:
            item_node: dict[str, Any] = {
                "element_ref": _synth_ref(),
                "label": label,
                "role": "Item",
                "states": "",
                "included": True,
                "bounds": {"x": 0, "y": 0, "width": 0, "height": 0},
                "children": [],
            }
            if is_checkbox:
                item_node["checked"] = checked
            menu_children.append(item_node)
        menu_group = {
            "element_ref": _synth_ref(),
            "label": "Tools Menu",
            "role": "Group",
            "states": "",
            "included": True,
            "bounds": {"x": 0, "y": 0, "width": 0, "height": 0},
            "children": menu_children,
        }
        tree_children.append(menu_group)

    return tree_children


def _extract_tree_items(el: dict) -> list[dict[str, Any]]:
    """Extract tree items from a Tree element's attributes."""
    java_data = el.get("java") or {}
    attrs = java_data.get("attributes") or {}
    tree_rows_raw = attrs.get("treeRows") or ""
    if not tree_rows_raw:
        return []
    parsed = _parse_tree_rows(tree_rows_raw)
    items: list[dict[str, Any]] = []
    for row in parsed:
        label = row["label"]
        if label.startswith("Level ") and " " in label[6:]:
            label = label.split(" ", 2)[-1]
        items.append({"label": label, "selected": row["selected"]})
    return items


def _build_table_node(
    table_rows: list[list[dict]],
    tab_page_prefix: str | None,
    selected_y: int | None,
    synth_ref_fn,
    field_node_fn,
) -> dict[str, Any]:
    """Build a Table tree node from multi-record rows."""
    # Normalize rows (same logic as _format_table)
    def _normalize_row(row: list[dict]) -> list[dict]:
        seen: set[str] = set()
        out: list[dict] = []
        for el in row:
            raw_name = el.get("name") or ""
            if _looks_like_technical_name(raw_name):
                continue
            norm = _strip_tab_prefix(raw_name, tab_page_prefix)
            # WHY: Also strip any "<TabName> tab page " prefix from OTHER tabs
            # so different outer-tab rows normalize to the same column name.
            norm = _ANY_TAB_PAGE_PREFIX.sub("", norm)
            norm = _LOV_SUFFIX.sub("", norm)
            if norm in seen:
                continue
            seen.add(norm)
            out.append(el)
        return out

    norm_rows = [_normalize_row(r) for r in table_rows]

    # Build columns from shortest normalized row
    base_row = min(norm_rows, key=len)
    columns: list[str] = []
    for el in base_row:
        name = el.get("name") or ""
        name = _strip_tab_prefix(name, tab_page_prefix)
        # WHY: The element name may contain a different tab page prefix
        # (e.g. "Summary tab page Order Number" in an Orders-tab grid).
        # Strip it so column headers are clean and match across rows.
        name = _ANY_TAB_PAGE_PREFIX.sub("", name)
        has_lov = bool(_LOV_SUFFIX.search(name))
        col_name = _LOV_SUFFIX.sub("", name)
        role = str(el.get("role") or "")
        states = el.get("states") or []
        is_editable = "editable" in states
        suffix = ""
        if has_lov:
            suffix = " (LOV)"
        elif role == "Checkbox":
            suffix = " (CB)"
        elif role == "ComboBox":
            suffix = " (CB)"
        elif role == "Button":
            suffix = " (Btn)"
        elif role != "Field":
            suffix = f" ({role})"
        if not is_editable and role not in ("Button",):
            suffix += " RO"
        columns.append(f"{col_name}{suffix}")

    # Build table rows
    table_rows_data: list[dict[str, Any]] = []
    for row_idx, row in enumerate(norm_rows):
        is_selected = False
        if selected_y is not None and row:
            row_y = row[0].get("y", 0)
            is_selected = abs(row_y - selected_y) <= 10
        marker = f"**{row_idx + 1}**" if is_selected else str(row_idx + 1)

        # Build lookup from column name to element
        row_by_col: dict[str, dict] = {}
        for el in row:
            raw = el.get("name") or ""
            col = _strip_tab_prefix(raw, tab_page_prefix)
            # WHY: Match the column-building logic — also strip any
            # cross-tab prefix so row cells match column headers.
            col = _ANY_TAB_PAGE_PREFIX.sub("", col)
            col = _LOV_SUFFIX.sub("", col)
            row_by_col[col] = el

        cells: list[dict[str, Any]] = []
        for col in columns:
            clean_col = _LOV_SUFFIX.sub("", col)
            # Strip role suffix for matching
            for suffix in (" (LOV)", " (CB)", " (Btn)", " RO", " (Checkbox)", " (ComboBox)", " (Button)"):
                clean_col = clean_col.replace(suffix, "")

            el = row_by_col.get(clean_col)
            if el:
                cell = field_node_fn(el, tab_page_prefix)
                cells.append(cell)
            else:
                cells.append({
                    "element_ref": "",
                    "label": "",
                    "role": "TableField",
                    "states": "",
                    "included": True,
                    "bounds": {"x": 0, "y": 0, "width": 0, "height": 0},
                    "children": [],
                })

        table_rows_data.append({"marker": marker, "cells": cells})

    return {
        "element_ref": synth_ref_fn(),
        "label": "",
        "role": "Table",
        "states": "",
        "included": True,
        "bounds": {"x": 0, "y": 0, "width": 0, "height": 0},
        "children": [],
        "table_columns": columns,
        "table_rows": table_rows_data,
    }


def _build_dialog_tree(
    title: str,
    nodes: list[dict],
    actionable: list[dict],
) -> list[dict[str, Any]]:
    """Build a tree for an Oracle Forms modal dialog (ChoiceBox, AlertBox, etc.)."""
    synth = 0

    def _sref() -> str:
        nonlocal synth
        synth += 1
        return f"grp-{synth}"

    def _bounds_for(el: dict) -> dict[str, int]:
        b = el.get("bounds") or {}
        return {
            "x": int(b.get("x", el.get("x", 0)) or 0),
            "y": int(b.get("y", el.get("y", 0)) or 0),
            "width": int(b.get("width", el.get("width", 0)) or 0),
            "height": int(b.get("height", el.get("height", 0)) or 0),
        }

    form_children: list[dict[str, Any]] = []

    # Extract dialog message from MultiLineLabel
    for n in nodes:
        cls = str(n.get("simpleClassName") or "")
        if cls == "MultiLineLabel":
            msg = str(n.get("accessibleName") or "").strip()
            if msg:
                form_children.append({
                    "element_ref": _sref(),
                    "label": f"Message: {msg}",
                    "role": "Field",
                    "states": "",
                    "included": True,
                    "bounds": {"x": 0, "y": 0, "width": 0, "height": 0},
                    "children": [],
                    "read_only": True,
                })
                break

    # Real buttons only
    _REAL_BUTTON_CLASSES = {"PushButton", "FormButton"}
    buttons = [
        el for el in actionable
        if str(el.get("role") or "") == "Button"
        and str((el.get("java") or {}).get("simpleClassName") or "") in _REAL_BUTTON_CLASSES
    ]
    if buttons:
        btn_children: list[dict[str, Any]] = []
        for el in buttons:
            eid = el.get("elementid", "")
            name = el.get("name") or el.get("text") or ""
            name = _ALT_KEY_SUFFIX.sub("", name)
            states = "enabled" if "enabled" in (el.get("states") or []) else "disabled"
            btn_children.append({
                "element_ref": eid,
                "label": name,
                "role": "Button",
                "states": states,
                "included": True,
                "bounds": _bounds_for(el),
                "children": [],
            })
        form_children.append({
            "element_ref": _sref(),
            "label": "Buttons",
            "role": "Group",
            "states": "",
            "included": True,
            "bounds": {"x": 0, "y": 0, "width": 0, "height": 0},
            "children": btn_children,
        })

    return [
        {
            "element_ref": _sref(),
            "label": title,
            "role": "Form",
            "states": "",
            "included": True,
            "bounds": {"x": 0, "y": 0, "width": 0, "height": 0},
            "children": form_children,
        }
    ]


def _build_popup_tree(
    title: str,
    nodes: list[dict],
    actionable: list[dict],
) -> list[dict[str, Any]]:
    """Build a tree for an Oracle Forms popup (LOV, etc.)."""
    synth = 0

    def _sref() -> str:
        nonlocal synth
        synth += 1
        return f"grp-{synth}"

    def _bounds_for(el: dict) -> dict[str, int]:
        b = el.get("bounds") or {}
        return {
            "x": int(b.get("x", el.get("x", 0)) or 0),
            "y": int(b.get("y", el.get("y", 0)) or 0),
            "width": int(b.get("width", el.get("width", 0)) or 0),
            "height": int(b.get("height", el.get("height", 0)) or 0),
        }

    form_children: list[dict[str, Any]] = []

    # Fields
    fields = [
        el for el in actionable
        if str(el.get("role") or "") in ("Field", "ComboBox")
    ]
    for el in fields:
        eid = el.get("elementid", "")
        name = el.get("name") or el.get("text") or ""
        role = str(el.get("role") or "")
        form_children.append({
            "element_ref": eid,
            "label": name,
            "role": role,
            "states": "",
            "included": True,
            "bounds": _bounds_for(el),
            "children": [],
        })

    # List items from ListView
    for n in nodes:
        cls = str(n.get("simpleClassName") or "")
        if cls == "ListView":
            nid = n.get("id")
            eid = f"e{nid}" if nid is not None else "?"
            attrs = n.get("attributes") or {}
            tree_rows_raw = attrs.get("treeRows") or ""
            if tree_rows_raw:
                parsed = _parse_tree_rows(tree_rows_raw)
                table = _tree_rows_to_table(parsed)
                visible = [(cells, sel) for cells, sel in table["rows"]
                           if any(c.strip() for c in cells)]
                items: list[dict[str, Any]] = []
                for row_cells, sel in visible[:20]:
                    items.append({
                        "element_ref": _sref(),
                        "label": " | ".join(row_cells) + (" *" if sel else ""),
                        "role": "Item",
                        "states": "",
                        "included": True,
                        "bounds": {"x": 0, "y": 0, "width": 0, "height": 0},
                        "children": [],
                    })
                list_node: dict[str, Any] = {
                    "element_ref": eid,
                    "label": f"List ({len(visible)} items)",
                    "role": "Group",
                    "states": "",
                    "included": True,
                    "bounds": {"x": 0, "y": 0, "width": 0, "height": 0},
                    "children": items,
                }
                if table["headers"]:
                    list_node["label"] = " | ".join(table["headers"]) + f" ({len(visible)} items)"
                form_children.append(list_node)
            else:
                items_data = n.get("valueOptions") or []
                items: list[dict[str, Any]] = []
                for item in (items_data[:10] or []):
                    items.append({
                        "element_ref": _sref(),
                        "label": str(item),
                        "role": "Item",
                        "states": "",
                        "included": True,
                        "bounds": {"x": 0, "y": 0, "width": 0, "height": 0},
                        "children": [],
                    })
                form_children.append({
                    "element_ref": eid,
                    "label": f"Items: {items_data[:10]}" if items_data else "Items: (list)",
                    "role": "Group",
                    "states": "",
                    "included": True,
                    "bounds": {"x": 0, "y": 0, "width": 0, "height": 0},
                    "children": items,
                })
            break

    # Buttons
    _REAL_BUTTON_CLASSES = {"PushButton", "FormButton"}
    buttons = [
        el for el in actionable
        if str(el.get("role") or "") == "Button"
        and str((el.get("java") or {}).get("simpleClassName") or "") in _REAL_BUTTON_CLASSES
    ]
    if buttons:
        btn_children: list[dict[str, Any]] = []
        for el in buttons:
            eid = el.get("elementid", "")
            name = el.get("name") or el.get("text") or ""
            name = _ALT_KEY_SUFFIX.sub("", name)
            states = "enabled" if "enabled" in (el.get("states") or []) else "disabled"
            btn_children.append({
                "element_ref": eid,
                "label": name,
                "role": "Button",
                "states": states,
                "included": True,
                "bounds": _bounds_for(el),
                "children": [],
            })
        form_children.append({
            "element_ref": _sref(),
            "label": "Buttons",
            "role": "Group",
            "states": "",
            "included": True,
            "bounds": {"x": 0, "y": 0, "width": 0, "height": 0},
            "children": btn_children,
        })

    return [
        {
            "element_ref": _sref(),
            "label": title,
            "role": "Form",
            "states": "",
            "included": True,
            "bounds": {"x": 0, "y": 0, "width": 0, "height": 0},
            "children": form_children,
        }
    ]


def _build_flat_tree(
    elements: list[dict],
    form_title: str,
) -> list[dict[str, Any]]:
    """Build a flat tree (no tab structure) from all elements."""
    def _bounds_for(el: dict) -> dict[str, int]:
        b = el.get("bounds") or {}
        return {
            "x": int(b.get("x", el.get("x", 0)) or 0),
            "y": int(b.get("y", el.get("y", 0)) or 0),
            "width": int(b.get("width", el.get("width", 0)) or 0),
            "height": int(b.get("height", el.get("height", 0)) or 0),
        }

    children: list[dict[str, Any]] = []
    for el in _actionable_elements(elements):
        eid = el.get("elementid", "")
        name = el.get("name") or el.get("text") or ""
        role = str(el.get("role") or "")
        states_list = el.get("states") or []
        states = ",".join(states_list)
        children.append({
            "element_ref": eid,
            "label": f"{name} | {role}" + (f" | states:{states}" if states else ""),
            "role": role,
            "states": states,
            "included": True,
            "bounds": _bounds_for(el),
            "children": [],
        })

    return [
        {
            "element_ref": "grp-0",
            "label": form_title,
            "role": "Form",
            "states": "",
            "included": True,
            "bounds": {"x": 0, "y": 0, "width": 0, "height": 0},
            "children": children,
        }
    ]


def _group_by_rows(
    elements: list[dict], y_tolerance: int = 10,
) -> list[list[dict]]:
    """Group elements into visual rows by y-coordinate, sorted by x within."""
    if not elements:
        return []
    # Sort by y then x
    def _sort_key(el: dict) -> tuple[int, int]:
        return (el.get("y", 0), el.get("x", 0))
    sorted_els = sorted(elements, key=_sort_key)

    rows: list[list[dict]] = []
    current_row: list[dict] = [sorted_els[0]]
    current_y = sorted_els[0].get("y", 0)
    for el in sorted_els[1:]:
        ey = el.get("y", 0)
        if abs(ey - current_y) <= y_tolerance:
            current_row.append(el)
        else:
            # Sort completed row by x before appending
            current_row.sort(key=lambda e: e.get("x", 0))
            rows.append(current_row)
            current_row = [el]
            current_y = ey
    # Sort and append the last row
    current_row.sort(key=lambda e: e.get("x", 0))
    rows.append(current_row)
    return rows


_LOV_SUFFIX = re.compile(r"\s*List of Values$")
_ALT_KEY_SUFFIX = re.compile(r"\s+alt\s+\S+$", re.IGNORECASE)
_MNEMONIC_SUFFIX = re.compile(r"\s+mnemonic\s+\S+$", re.IGNORECASE)
# WHY: Oracle Forms prefixes field names with "<TabName> tab page " for
# elements owned by a specific tab. When the known prefix (e.g. "Orders tab page ")
# doesn't match, fall back to stripping any "X tab page " prefix so that
# active-record rows and non-active rows normalize to the same field names
# for table detection and column building.
_ANY_TAB_PAGE_PREFIX = re.compile(r"^[A-Za-z].*? tab page ")


def _extract_tools_menu(
    nodes: list[dict],
) -> list[tuple[str, bool, bool]]:
    """Extract Tools menu items from the DOM node list.

    Looks for an ``LWMenu`` node whose name starts with ``"Tools"`` and
    reads its ``accessibleMenuItems`` attribute (preferred, has role +
    checked state) or falls back to ``treeRows``.

    Returns a list of ``(label, is_checkbox, checked)`` tuples.
    """
    for n in nodes:
        cn = str(n.get("simpleClassName") or "")
        if cn != "LWMenu":
            continue
        name = str(n.get("accessibleName") or n.get("name") or "")
        if not name.startswith("Tools"):
            continue
        attrs = n.get("attributes") or {}

        # Prefer accessibleMenuItems (has role + checked state)
        acc_items = attrs.get("accessibleMenuItems") or ""
        if acc_items:
            items: list[tuple[str, bool, bool]] = []
            for entry in acc_items.split(" || "):
                parts = entry.split("\t")
                if len(parts) < 3:
                    continue
                label = _MNEMONIC_SUFFIX.sub("", parts[0].strip()).strip()
                if not label:
                    continue
                is_checkbox = parts[1] == "check_box"
                checked = parts[2] == "1"
                items.append((label, is_checkbox, checked))
            return items

        # Fallback to treeRows (no role/checked info)
        tree_rows = attrs.get("treeRows") or ""
        if not tree_rows:
            return []
        items = []
        for row in tree_rows.split(" || "):
            parts = row.split("\t")
            if len(parts) < 4:
                continue
            label = _MNEMONIC_SUFFIX.sub("", parts[3].strip()).strip()
            if not label:
                continue
            items.append((label, False, False))
        return items
    return []


def _parse_tree_rows(raw: str) -> list[dict]:
    """Parse treeRows attribute: ``depth\\tsel\\texp\\tlabel || ...``."""
    rows: list[dict] = []
    for part in raw.split(" || "):
        fields = part.split("\t")
        if len(fields) < 4:
            continue
        rows.append({
            "depth": int(fields[0]),
            "selected": fields[1] == "1",
            "expanded": fields[2] == "1",
            "label": "\t".join(fields[3:]),  # label may contain tabs
        })
    return rows


def _tree_rows_to_table(
    parsed: list[dict],
) -> dict:
    """Split ``Header:Value`` pairs in treeRows labels into table columns."""
    headers: list[str] = []
    data_rows: list[tuple[list[str], bool]] = []
    for row in parsed:
        cells: list[str] = []
        for part in row["label"].split("\t"):
            if ":" in part:
                hdr, val = part.split(":", 1)
                if not headers or len(cells) >= len(headers):
                    headers.append(hdr.strip())
                cells.append(val.strip())
            else:
                cells.append(part.strip())
        data_rows.append((cells, row["selected"]))
    return {"headers": headers, "rows": data_rows}


def _is_readonly_display(el: dict) -> bool:
    """True if ``el`` is a read-only display mirror to exclude from the snapshot.

    WHY: Oracle Forms pairs each LOV/input field with a non-editable element
    that merely echoes the resolved value (rendered as ``(read-only)``). Per the
    qcs_studio "exclude field labels" UX decision, these mirrors clutter the
    curated element tree and the AI action snapshot, so they are dropped here
    and surfaced only via the full-element hover overlay. Interactive controls
    (LOV, ComboBox, Checkbox, RadioButton, List, Tree, Table, Button, menus,
    tabs) are NEVER treated as read-only display even when not editable.
    """
    states = el.get("states") or []
    if "editable" in states:
        return False
    role = str(el.get("role") or "")
    if role in (
        "Button", "ComboBox", "Checkbox", "RadioButton",
        "List", "Tree", "Table", "Menu", "MenuItem", "Tab", "Toolbar",
    ):
        return False
    if _LOV_SUFFIX.search(str(el.get("name") or "")):
        return False
    return True


def _strip_tab_prefix(name: str, tab_page_prefix: str | None) -> str:
    """Remove Oracle Forms '<Tab Name> tab page ' prefix from a name.

    The active/selected record row may double the prefix (e.g.
    ``"Pricing tab page Pricing tab page Qty"``), so we strip
    repeatedly until no prefix remains.
    """
    if tab_page_prefix:
        while name.startswith(tab_page_prefix):
            name = name[len(tab_page_prefix):]
    return name


def _format_field(element: dict, tab_page_prefix: str | None = None) -> str:
    """Format a field element.  Strip 'List of Values' suffix, add (LOV).
    For ComboBox elements, show possible values.  Show current value if set.
    Field role is the default and is omitted for brevity.
    Non-editable fields are annotated with ``(read-only)``."""
    element_id = element.get("elementid", "?")
    name = element.get("name") or element.get("text") or ""
    name = _strip_tab_prefix(name, tab_page_prefix)
    role = str(element.get("role") or "")
    states = element.get("states") or []
    is_editable = "editable" in states

    # Display-only VTextField: suppress technical name, show value directly
    if _looks_like_technical_name(name):
        text = (element.get("text") or "").strip()
        if text:
            return f"[{element_id}] {text} (read-only)"
        return f"[{element_id}]"

    has_lov = bool(_LOV_SUFFIX.search(name))
    name = _LOV_SUFFIX.sub("", name)
    # Show role only for non-Field types (Panel is also suppressed —
    # display-only fields that Oracle marks as Panel behave like Field).
    if role and role not in ("Field", "Panel"):
        parts = [f"[{element_id}] {name} ({role}"]
        # Append checkbox state inside the parens
        if role == "Checkbox":
            checked = "selected" in states
            parts[0] += ", checked)" if checked else ", unchecked)"
        else:
            parts[0] += ")"
    else:
        parts = [f"[{element_id}] {name}"]
    if has_lov:
        parts.append("(LOV)")
    if not is_editable:
        parts.append("(read-only)")
    # Show possible values for ComboBox elements
    java_data = element.get("java") or {}
    value_options = java_data.get("valueOptions") or []
    if value_options:
        parts.append(f"values: {value_options}")
    # Show current field value if present
    text = (element.get("text") or "").strip()
    # Avoid showing the value if it's the same as the field name
    if text and text != name.strip():
        parts.append(f"= {text}")
    return " ".join(parts)


def _format_button(element: dict, tab_page_prefix: str | None = None) -> str:
    """Format a button with enabled/disabled state.  Strip 'alt X' shortcut."""
    element_id = element.get("elementid", "?")
    name = element.get("name") or element.get("text") or ""
    name = _strip_tab_prefix(name, tab_page_prefix)
    name = _ALT_KEY_SUFFIX.sub("", name)
    states = element.get("states") or []
    state = "enabled" if "enabled" in states else "disabled"
    return f"[{element_id}] {name} (Button, {state})"


def locator_params(element: dict) -> dict[str, str]:
    """Build Java-agent locator params from a repo element descriptor."""
    java = element.get("java") or {}
    params: dict[str, str] = {}

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
    """Convert the Java agent ``elementat`` result to a repo element record."""
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
        name = _first_text(parent.get("displayName"), parent.get("accessibleName"), parent.get("name"), parent.get("title"))
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

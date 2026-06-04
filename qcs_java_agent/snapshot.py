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

    focused = [node for node in nodes if node.get("focused")]
    candidates = focused or [node for node in nodes if node.get("semanticType") in {"Dialog", "Window"}]
    for node in candidates:
        for key in ("title", "displayName", "accessibleName", "name", "text"):
            value = str(node.get(key) or "").strip()
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


def build_action_context(scan: dict) -> tuple[str, dict[str, dict]]:
    """Return ``(action_snapshot_text, {element_id: repo_element})`` for a scan.

    The snapshot text is hierarchical:

    ::

        Form: <window title>
        Tabs: [*Selected Tab*] | Other Tab | ...
          [e33] Order Number | Field | ...
          [e34] Quote Number | Field | ...
        Buttons:
          [e205] Clear | Button | ...

    Only actionable elements from the selected tab are included.
    Background form modules are excluded via :func:`active_window_scan`.
    """
    scoped = active_window_scan(scan)
    all_elements = java_nodes_to_repo_elements(scoped)
    actionable = _actionable_elements(all_elements)

    # --- Detect tab structure from the scoped DOM tree ---
    scoped_nodes = flatten_nodes(scoped)
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
            if _is_readonly_display(el):
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

        snapshot_text = _format_hierarchical(
            form_title, tab_titles, tab_states, selected_tab,
            tab_elements, toolbar_buttons, footer_buttons,
            tree_elements=tree_elements,
            form_field_elements=form_field_elements,
            tab_bar_element_id=tab_bar_element_id,
            scoped_nodes=scoped_nodes,
            tab_content_ids=tab_content_ids,
            outer_tab_bars=tab_info.get("outer_tab_bars"),
            tools_menu_items=tools_menu_items,
        )
    else:
        # Check if this is an Oracle Forms overlay (dialog or popup)
        root_cls = ""
        if scoped_nodes:
            root_cls = str(scoped_nodes[0].get("simpleClassName") or "")
        if root_cls in _ORACLE_FORMS_DIALOG_CLASSES:
            snapshot_text = _format_dialog(form_title, scoped_nodes, actionable)
        elif root_cls in _ORACLE_FORMS_POPUP_CLASSES:
            snapshot_text = _format_popup(form_title, scoped_nodes, actionable)
        else:
            # No tab structure — flat output
            snapshot_text = java_elements_to_action_snapshot(all_elements)

    id_map: dict[str, dict] = {
        el["elementid"]: el
        for el in actionable
        if el.get("elementid")
    }
    return snapshot_text, id_map


def build_action_payload(
    scan: dict,
    *,
    origin_x: int = 0,
    origin_y: int = 0,
) -> dict[str, Any]:
    """Return AI text + UI tree from one canonical snapshot source.

    WHY: Studio previously assembled overlay/render data independently from
    the AI snapshot text, which caused drift and incorrect box placement.
    This helper guarantees both outputs are produced from the same filtered
    element set used by ``build_action_context``.
    """
    snapshot_text, id_map = build_action_context(scan)
    # WHY: ``id_map`` carries every actionable element (incl. menu/toolbar/tab
    # chrome), but the human-friendly snapshot text only lists meaningful
    # fields/buttons via ``[eNN]`` tokens. We build the Studio tree by parsing
    # the canonical snapshot text directly so the tree mirrors EXACTLY the
    # hierarchical structure the recorder AI sees in ai_snapshot.txt (sections
    # nested under the form, fields nested under their tab, buttons under
    # "Buttons:", menu items under "Tools Menu:") instead of a flattened DOM
    # dump. Parsing the text (not the id_map graph) keeps the tree and the AI
    # prompt from ever drifting apart.
    tree = _parse_snapshot_tree(
        snapshot_text, id_map, origin_x=origin_x, origin_y=origin_y
    )
    return {
        "text": snapshot_text,
        "tree": tree,
        "id_map": id_map,
    }


def _parse_snapshot_tree(
    snapshot_text: str,
    id_map: dict[str, dict],
    *,
    origin_x: int = 0,
    origin_y: int = 0,
) -> list[dict[str, Any]]:
    """Build a nested UI tree that mirrors the AI snapshot text hierarchy.

    WHY: Studio must display the same hierarchy the recorder AI consumes (see
    ai_snapshot.txt / snapshot_output.txt). The snapshot text is indentation
    based:

    ::

        Form: <title>
          [e207] Open Folder... (Button, enabled)
          [e197] Tabs: [*Quote/Order Information*] | Line Information | ...
            [e33] Order Number , [e84] Order Type (read-only) , ...
          Buttons:
            [e205] Clear (Button, enabled) , [e206] Find (Button, enabled)
          Tools Menu:
            Find Customer

    We reconstruct nesting from leading-space indentation. A line containing
    multiple comma-separated ``[eNN] label`` segments becomes several leaf
    siblings; a single-segment / header line becomes a container that following
    deeper lines nest under. Element bounds (for screenshot overlays) come from
    ``id_map``; header / menu rows get zero-size bounds (no overlay box).
    """

    seg_re = re.compile(r"^\[(e\d+)\]\s*(.*)$")
    roots: list[dict[str, Any]] = []
    # Stack of (indent, children_list) describing the current open containers.
    stack: list[tuple[int, list]] = []
    synth = 0

    def _bounds_for(element_id: str) -> dict[str, int]:
        el = id_map.get(element_id)
        if not el:
            return {"x": 0, "y": 0, "width": 0, "height": 0}
        b = dict(el.get("bounds") or {})
        x = int(b.get("x", el.get("x", 0)) or 0)
        y = int(b.get("y", el.get("y", 0)) or 0)
        w = int(b.get("width", el.get("width", 0)) or 0)
        h = int(b.get("height", el.get("height", 0)) or 0)
        return {
            "x": x - int(origin_x),
            "y": y - int(origin_y),
            "width": w,
            "height": h,
        }

    def _role_for(element_id: str, label: str) -> str:
        el = id_map.get(element_id)
        if el and el.get("role"):
            return str(el.get("role"))
        low = label.lower()
        if "(button" in low:
            return "Button"
        if "(lov)" in low:
            return "LOV"
        if "(combobox" in low:
            return "ComboBox"
        if "(read-only)" in low:
            return "Field"
        return "Field"

    def _make_segment_node(segment: str) -> dict[str, Any]:
        nonlocal synth
        m = seg_re.match(segment)
        if m:
            eid, label = m.group(1), m.group(2).strip()
            return {
                "element_ref": eid,
                "label": label or eid,
                "role": _role_for(eid, label),
                "included": True,
                "bounds": _bounds_for(eid),
                "children": [],
            }
        # Label-only row (e.g. a Tools Menu item) — no element/overlay.
        synth += 1
        return {
            "element_ref": f"grp-{synth}",
            "label": segment.rstrip(":").strip() or f"item-{synth}",
            "role": "Item",
            "included": True,
            "bounds": {"x": 0, "y": 0, "width": 0, "height": 0},
            "children": [],
        }

    for raw in snapshot_text.split("\n"):
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        content = raw.strip()

        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent_children = stack[-1][1] if stack else roots

        if content.startswith("Form:"):
            synth += 1
            node = {
                "element_ref": f"grp-{synth}",
                "label": content[len("Form:"):].strip() or "Form",
                "role": "Form",
                "included": True,
                "bounds": {"x": 0, "y": 0, "width": 0, "height": 0},
                "children": [],
            }
            parent_children.append(node)
            stack.append((indent, node["children"]))
            continue

        if " , " in content:
            # Multiple leaf segments on one visual row — all siblings.
            for seg in content.split(" , "):
                seg = seg.strip()
                if seg:
                    parent_children.append(_make_segment_node(seg))
            continue

        # Single-segment / header line: becomes a container that may hold
        # deeper-indented children (e.g. "Buttons:", the Tabs line, a section).
        node = _make_segment_node(content)
        parent_children.append(node)
        stack.append((indent, node["children"]))

    # Flatten the synthetic "Buttons" group: buttons should appear at the same
    # level as their sibling fields/tabs, not nested under a "Buttons" header.
    # WHY: the AI snapshot text groups footer/toolbar buttons under a "Buttons:"
    # heading for readability, but the curated UI tree should not introduce that
    # extra grouping level (per qcs_studio UX request). Other group headers
    # (e.g. "Tools Menu") are preserved.
    def _flatten_button_groups(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for node in nodes:
            children = _flatten_button_groups(node.get("children") or [])
            node["children"] = children
            is_group = str(node.get("element_ref", "")).startswith("grp-")
            if is_group and str(node.get("label", "")).strip().lower() == "buttons":
                out.extend(children)  # splice buttons up to this level
            else:
                out.append(node)
        return out

    # The form line wraps everything; surface its children as the top-level
    # tree so the container name is not duplicated (it is shown separately as
    # the container title in the UI).
    if len(roots) == 1 and roots[0].get("role") == "Form":
        return _flatten_button_groups(list(roots[0].get("children") or []))
    return _flatten_button_groups(roots)



def _build_ui_tree_from_id_map(
    id_map: dict[str, dict],
    *,
    origin_x: int = 0,
    origin_y: int = 0,
) -> list[dict[str, Any]]:
    """Build a stable tree payload for Studio from action-context elements."""
    nodes: dict[str, dict[str, Any]] = {}
    roots: list[dict[str, Any]] = []

    def _normalize_bounds(element: dict) -> dict[str, int]:
        b = dict(element.get("bounds") or {})
        x = int(b.get("x", element.get("x", 0)) or 0)
        y = int(b.get("y", element.get("y", 0)) or 0)
        w = int(b.get("width", element.get("width", 0)) or 0)
        h = int(b.get("height", element.get("height", 0)) or 0)
        nx = x - int(origin_x)
        ny = y - int(origin_y)
        return {
            "x": nx,
            "y": ny,
            "width": w,
            "height": h,
        }

    for element_id, element in id_map.items():
        name = str(element.get("name") or element.get("friendly_name") or element_id)
        role = str(element.get("role") or "")
        bounds = _normalize_bounds(element)
        node = {
            "element_ref": element_id,
            "label": name,
            "role": role,
            "included": bool(element.get("included", True)),
            "bounds": bounds,
            "children": [],
        }
        nodes[element_id] = node

    for element_id, element in id_map.items():
        node = nodes[element_id]
        parent_ref = str(element.get("filteredparentid") or element.get("parent_ref") or "")
        parent_node = nodes.get(parent_ref)
        if parent_node is None:
            roots.append(node)
        else:
            parent_node["children"].append(node)

    def _sort_nodes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items.sort(
            key=lambda n: (
                int((n.get("bounds") or {}).get("y", 0)),
                int((n.get("bounds") or {}).get("x", 0)),
                str(n.get("label") or ""),
            )
        )
        for item in items:
            item["children"] = _sort_nodes(list(item.get("children") or []))
        return items

    return _sort_nodes(roots)


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


def _format_table(
    table_rows: list[list[dict]],
    tab_page_prefix: str | None,
    selected_y: int | None = None,
    indent: str = "    ",
) -> list[str]:
    """Render multi-record rows as a markdown table.

    Returns indented lines to append to the snapshot.
    The row matching ``selected_y`` (blue record indicator) is bolded.
    """
    if not table_rows:
        return []

    # Normalize rows: strip technical names and prefix-duplicated fields.
    # The active record row may have extra elements (VTextFieldNN,
    # ContinuousButtonN, and a duplicate field from the tab-page prefix).
    def _normalize_row(row: list[dict]) -> list[dict]:
        seen: set[str] = set()
        out: list[dict] = []
        for el in row:
            raw_name = el.get("name") or ""
            if _looks_like_technical_name(raw_name):
                continue
            norm = _strip_tab_prefix(raw_name, tab_page_prefix)
            norm = _LOV_SUFFIX.sub("", norm)
            if norm in seen:
                continue  # skip duplicate from prefix
            seen.add(norm)
            out.append(el)
        return out

    norm_rows = [_normalize_row(r) for r in table_rows]

    # Use the shortest normalized row for headers — this is the base column
    # set.  The active record row may have extra columns (checkboxes, etc.)
    # that don't appear on other rows.
    base_row = min(norm_rows, key=len)
    headers: list[str] = []
    header_names: list[str] = []  # track column names for alignment
    for el in base_row:
        name = el.get("name") or ""
        name = _strip_tab_prefix(name, tab_page_prefix)
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
        headers.append(f"{col_name}{suffix}")
        header_names.append(col_name)

    # Add Row / selection column
    header_line = f"{indent}| # | " + " | ".join(headers) + " |"
    sep_line = f"{indent}|" + "|".join("---" for _ in range(len(headers) + 1)) + "|"

    lines = [header_line, sep_line]

    for row_idx, row in enumerate(norm_rows):
        # Detect selected row via blue record indicator y-coordinate
        is_selected = False
        if selected_y is not None and row:
            row_y = row[0].get("y", 0)
            is_selected = abs(row_y - selected_y) <= 10
        marker = f"**{row_idx + 1}**" if is_selected else str(row_idx + 1)

        # Build a lookup from column name → element for this row
        row_by_col: dict[str, dict] = {}
        for el in row:
            raw = el.get("name") or ""
            col = _LOV_SUFFIX.sub("", _strip_tab_prefix(raw, tab_page_prefix))
            row_by_col[col] = el

        cells: list[str] = []
        for col_name in header_names:
            el = row_by_col.get(col_name)
            if el is None:
                cells.append("")
                continue
            eid = el.get("elementid") or ""
            eid_tag = f"[{eid}] " if eid else ""
            role = str(el.get("role") or "")
            if role == "Checkbox":
                states = el.get("states") or []
                val = "[x]" if "selected" in states else "[ ]"
                cells.append(f"{eid_tag}{val}")
            elif role == "Button":
                name = el.get("name") or ""
                name = _strip_tab_prefix(name, tab_page_prefix)
                name = _ALT_KEY_SUFFIX.sub("", name)
                cells.append(f"{eid_tag}{name}")
            else:
                text = (el.get("text") or "").strip()
                cells.append(f"{eid_tag}{text}")

        row_line = f"{indent}| {marker} | " + " | ".join(cells) + " |"
        lines.append(row_line)

    return lines


def _format_hierarchical(
    form_title: str,
    tab_titles: list[str],
    tab_states: list[dict],
    selected_tab: str,
    tab_elements: list[dict],
    toolbar_buttons: list[dict],
    footer_buttons: list[dict],
    tree_elements: list[dict] | None = None,
    form_field_elements: list[dict] | None = None,
    max_chars: int = config.MAX_SNAPSHOT_CHARS,
    tab_bar_element_id: str | None = None,
    scoped_nodes: list[dict] | None = None,
    tab_content_ids: set[str] | None = None,
    outer_tab_bars: list[dict] | None = None,
    tools_menu_items: list[tuple[str, bool, bool]] | None = None,
) -> str:
    """Format a hierarchical snapshot string."""
    lines: list[str] = []

    # Form header
    lines.append(f"Form: {form_title}")

    # Toolbar buttons (above tabs)
    if toolbar_buttons:
        parts = [_format_button(el) for el in toolbar_buttons]
        lines.append(f"  {' , '.join(parts)}")

    # Determine whether tree panels sit to the left of the tab bar (sidebar).
    # If so, render them before the tabs per x-ascending display order.
    tree_before_tabs = False
    if tree_elements and tab_bar_element_id:
        tree_min_x = min(el.get("x", 9999) for el in tree_elements)
        # Tab bar x comes from the tab labels element
        tab_bar_x = 9999
        for n in (scoped_nodes or []):
            if f"e{n.get('id', '')}" == tab_bar_element_id:
                tab_bar_x = (n.get("screenBounds") or {}).get("x", 9999)
                break
        if tree_min_x < tab_bar_x:
            tree_before_tabs = True

    # Tree panels before tabs (sidebar on the left)
    if tree_before_tabs:
        for el in (tree_elements or []):
            lines.append(f"  {_format_tree(el)}")

    # Outer (higher-level) tab bars — rendered before the inner tab bar.
    # Each outer level adds indentation depth for nested content.
    outer_depth = len(outer_tab_bars or [])
    for otb in (outer_tab_bars or []):
        otb_labels = []
        otb_eid = otb.get("element_id") or ""
        otb_prefix = f"[{otb_eid}] " if otb_eid else ""
        for i, t in enumerate(otb["titles"]):
            ost = otb["tab_states"][i] if i < len(otb["tab_states"]) else {}
            if not ost.get("visible", True):
                continue
            if t == otb.get("selected"):
                otb_labels.append(f"[*{t}*]")
            elif not ost.get("enabled", True):
                otb_labels.append(f"{t} (disabled)")
            else:
                otb_labels.append(t)
        lines.append(f"  {otb_prefix}Tabs: {' | '.join(otb_labels)}")

    # Indentation for content nested under outer tabs
    indent = "  " * (1 + outer_depth)  # base "  " + one per outer level
    field_indent = "  " * (2 + outer_depth)

    # Tab bar — hide invisible tabs, mark disabled ones
    tab_labels = []
    eid_prefix = f"[{tab_bar_element_id}] " if tab_bar_element_id else ""
    for i, t in enumerate(tab_titles):
        st = tab_states[i] if i < len(tab_states) else {}
        if not st.get("visible", True):
            continue  # invisible tab — omit entirely
        if t == selected_tab:
            tab_labels.append(f"[*{t}*]")
        elif not st.get("enabled", True):
            tab_labels.append(f"{t} (disabled)")
        else:
            tab_labels.append(t)
    lines.append(f"{indent}{eid_prefix}Tabs: {' | '.join(tab_labels)}")

    # Build tab-page prefix to strip from element names
    # Oracle Forms prepends "<Tab Name> tab page " to accessible names
    tab_page_prefix = f"{selected_tab} tab page " if selected_tab else None

    # Selected tab fields — group by visual rows (similar y), sort by x
    rows = _group_by_rows(tab_elements)

    # Detect multi-record table: consecutive rows with same field names
    table_rows, pre_rows, post_rows = _detect_table(rows, tab_page_prefix)

    # Render pre-table rows (toolbar area, standalone fields)
    for row in pre_rows:
        parts = [
            _format_button(el, tab_page_prefix)
            if str(el.get("role") or "") == "Button"
            else _format_field(el, tab_page_prefix)
            for el in row
        ]
        lines.append(f"{field_indent}{' , '.join(parts)}")

    # Render table if detected
    if table_rows:
        selected_y = _detect_record_indicator_y(
            scoped_nodes or [], tab_content_ids
        )
        lines.extend(
            _format_table(table_rows, tab_page_prefix, selected_y, field_indent)
        )

    # Render post-table rows (buttons below the table)
    for row in post_rows:
        parts = [
            _format_button(el, tab_page_prefix)
            if str(el.get("role") or "") == "Button"
            else _format_field(el, tab_page_prefix)
            for el in row
        ]
        lines.append(f"{field_indent}{' , '.join(parts)}")

    # Form-level fields below the grid (e.g. Line Total, Description)
    if form_field_elements:
        form_rows = _group_by_rows(form_field_elements)
        for row in form_rows:
            parts = [
                _format_button(el, tab_page_prefix)
                if str(el.get("role") or "") == "Button"
                else _format_field(el, tab_page_prefix)
                for el in row
            ]
            lines.append(f"{field_indent}{' , '.join(parts)}")

    # Footer buttons (below tabs)
    if footer_buttons:
        lines.append(f"{indent}Buttons:")
        parts = [_format_button(el, tab_page_prefix) for el in footer_buttons]
        lines.append(f"{field_indent}{' , '.join(parts)}")

    # Tree panels (e.g. sidebar navigation tree) — skip if already rendered above
    if not tree_before_tabs:
        for el in (tree_elements or []):
            lines.append(f"  {_format_tree(el)}")

    # Tools menu items (from the application menu bar)
    if tools_menu_items:
        lines.append(f"{indent}Tools Menu:")
        for label, is_checkbox, checked in tools_menu_items:
            if is_checkbox:
                mark = "[x]" if checked else "[ ]"
                lines.append(f"{field_indent}{mark} {label}")
            else:
                lines.append(f"{field_indent}{label}")

    result = "\n".join(lines) or "(no actionable elements found)"
    if len(result) > max_chars:
        result = result[:max_chars] + "\n...(truncated)"
    return result


def _format_dialog(
    title: str,
    nodes: list[dict],
    actionable: list[dict],
) -> str:
    """Format an Oracle Forms modal dialog (ChoiceBox, AlertBox, etc.)."""
    lines: list[str] = [f"Dialog: {title}"]

    # Extract the dialog message from MultiLineLabel or LWLabel with long text
    for n in nodes:
        cls = str(n.get("simpleClassName") or "")
        if cls == "MultiLineLabel":
            msg = str(n.get("accessibleName") or "").strip()
            if msg:
                lines.append(f"  Message: {msg}")
                break

    # Show real buttons only (skip Toolbar, Menu, SystemMenu chrome)
    _REAL_BUTTON_CLASSES = {"PushButton", "FormButton"}
    buttons = [
        el for el in actionable
        if str(el.get("role") or "") == "Button"
        and str((el.get("java") or {}).get("simpleClassName") or "") in _REAL_BUTTON_CLASSES
    ]
    if buttons:
        parts = [_format_button(el) for el in buttons]
        lines.append(f"  Buttons: {' , '.join(parts)}")

    return "\n".join(lines)


def _format_popup(
    title: str,
    nodes: list[dict],
    actionable: list[dict],
) -> str:
    """Format an Oracle Forms popup window (LOV, etc.)."""
    lines: list[str] = [f"LOV: {title}"]

    # Show fields (Find field, etc.)
    fields = [
        el for el in actionable
        if str(el.get("role") or "") in ("Field", "ComboBox")
    ]
    for el in fields:
        lines.append(f"  {_format_field(el)}")

    # Show list items from ListView node if present
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
                # Filter out rows where all cells are empty
                visible = [(cells, sel) for cells, sel in table["rows"]
                           if any(c.strip() for c in cells)]
                lines.append(f"  [{eid}] List ({len(visible)} items):")
                if table["headers"]:
                    lines.append(f"    {' | '.join(table['headers'])}")
                for row_cells, sel in visible[:20]:
                    marker = " *" if sel else ""
                    lines.append(f"    {' | '.join(row_cells)}{marker}")
                if len(visible) > 20:
                    lines.append(f"    ...({len(visible)} total)")
            else:
                items = n.get("valueOptions") or []
                if items:
                    lines.append(f"  [{eid}] Items: {items[:10]}")
                    if len(items) > 10:
                        lines.append(f"    ...({len(items)} total)")
                else:
                    lines.append(f"  [{eid}] Items: (list)")
            break

    # Show only real buttons (PushButton), skip scrollbar arrows and containers
    _REAL_BUTTON_CLASSES = {"PushButton", "FormButton"}
    buttons = [
        el for el in actionable
        if str(el.get("role") or "") == "Button"
        and str((el.get("java") or {}).get("simpleClassName") or "") in _REAL_BUTTON_CLASSES
    ]
    if buttons:
        parts = [_format_button(el) for el in buttons]
        lines.append(f"  Buttons: {' , '.join(parts)}")

    return "\n".join(lines)


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


def _format_tree(element: dict) -> str:
    """Format a tree element showing its items with selection markers."""
    element_id = element.get("elementid", "?")
    name = element.get("name") or ""
    java_data = element.get("java") or {}
    attrs = java_data.get("attributes") or {}
    tree_rows_raw = attrs.get("treeRows") or ""
    if not tree_rows_raw:
        return f"[{element_id}] {name} (Tree, empty)"
    parsed = _parse_tree_rows(tree_rows_raw)
    items: list[str] = []
    for row in parsed:
        label = row["label"]
        # Strip "Level N " prefix from DTree labels
        if label.startswith("Level ") and " " in label[6:]:
            label = label.split(" ", 2)[-1]
        marker = " *" if row["selected"] else ""
        items.append(f"{label}{marker}")
    header = f"[{element_id}] {name} (Tree, {len(items)} items):"
    item_lines = "\n".join(f"    {item}" for item in items)
    return f"{header}\n{item_lines}"


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

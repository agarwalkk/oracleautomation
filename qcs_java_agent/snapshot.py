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

    Selection priority:
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
    """
    result: list[dict] = []
    for element in elements:
        role = str(element.get("role") or "")
        if role not in _ACTION_SNAPSHOT_ROLES:
            continue
        states = element.get("states") or []
        if "enabled" not in states and role != "Toolbar":
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

    Both the snapshot text and the map are derived from exactly the same
    ``_actionable_elements`` filter pass, so every ``[eN]`` token that appears
    in the text has a corresponding key ``eN`` in the map — and nothing else does.

    Intended use (Approach B recorder):
    1. Render ``snapshot_text`` into the AI prompt.
    2. AI returns ``element_id`` for the target element.
    3. Look up ``id_map[element_id]`` to obtain the full repo element descriptor
       and pass it straight to ``locator_params`` / ``JavaAgentDriver``.

    Only elements from the active/focused window are included; background
    form modules open in the same JVM are excluded via :func:`active_window_scan`.
    """
    elements = java_nodes_to_repo_elements(active_window_scan(scan))
    actionable = _actionable_elements(elements)
    snapshot_text = java_elements_to_action_snapshot(elements)
    id_map: dict[str, dict] = {
        el["elementid"]: el
        for el in actionable
        if el.get("elementid")
    }
    return snapshot_text, id_map


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

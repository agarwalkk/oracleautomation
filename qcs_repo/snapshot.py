"""
qcs_repo.snapshot — Shared snapshot text-encoder.

Used by:
    - qcs_replay.healing.snapshot (Tier-1 healer needs a compact snapshot)

Keeps the representation in one place so healer output is comparable
to what the recorder saw.
"""
from __future__ import annotations

import json
from typing import Any

import config


# ── Java-agent element list → compact text ──────────────────────────────────

def java_elements_to_text(elements: list[dict], max_chars: int = config.MAX_SNAPSHOT_CHARS) -> str:
    """
    Convert a flat list of Java-agent element dicts to a
    compact, LLM-readable string.

    Format per element:
        [e12] text | role | name | states: enabled,focused | parent: e3
    """
    lines: list[str] = []
    for el in elements:
        eid    = el.get("elementid", "?")
        role   = el.get("role", "")
        name   = el.get("name", "")
        text   = el.get("text", "")
        states = ",".join(el.get("states") or [])
        parent = el.get("filteredparentid", "")
        label  = name or text
        line   = f"[{eid}] {label} | {role}"
        if states:
            line += f" | states:{states}"
        if parent:
            line += f" | parent:{parent}"
        lines.append(line)

    result = "\n".join(lines)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n...(truncated)"
    return result


# ── Playwright accessibility snapshot → compact text ─────────────────────────

def _pw_node_to_lines(node: dict, indent: int = 0, lines: list[str] | None = None) -> list[str]:
    if lines is None:
        lines = []
    role  = node.get("role", "")
    name  = node.get("name", "")
    value = node.get("value", "")
    pad   = "  " * indent
    line  = f"{pad}[{role}] {name}"
    if value:
        line += f" = {value!r}"
    lines.append(line)
    for child in node.get("children", []):
        _pw_node_to_lines(child, indent + 1, lines)
    return lines


def playwright_a11y_to_text(snapshot: Any, max_chars: int = config.MAX_SNAPSHOT_CHARS) -> str:
    """
    Convert a Playwright accessibility snapshot dict (from page.accessibility.snapshot())
    to a compact, LLM-readable string.
    """
    if snapshot is None:
        return "(empty snapshot)"
    if isinstance(snapshot, str):
        # Already text (some PW versions return aria-snapshot markup)
        return snapshot[:max_chars]
    lines = _pw_node_to_lines(snapshot)
    result = "\n".join(lines)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n...(truncated)"
    return result


# ── Java element enrichment (tab grouping + table detection) ─────────────────


def _page_tab_grouping(elements: list[dict]) -> list[dict]:
    """Re-parent elements that fall spatially inside a page tab's bounds.

    Only operates on direct children of the first internal frame (filteredparentid == 'e1').
    """
    modified = [dict(el) for el in elements]

    page_tabs = [
        el for el in modified
        if el.get("role") == "page tab" and el.get("filteredparentid") == "e1"
    ]
    other_elements = [
        el for el in modified
        if el.get("filteredparentid") == "e1"
    ]

    page_tabs.sort(key=lambda pt: (pt.get("y", 0), pt.get("x", 0)), reverse=True)
    other_elements.sort(key=lambda el: (el.get("y", 0), el.get("x", 0)), reverse=True)

    for el in other_elements:
        for pt in page_tabs:
            if (
                el.get("x", 0) >= pt.get("x", 0)
                and el.get("y", 0) >= pt.get("y", 0) + 10
                and el.get("x", 0) <= pt.get("x", 0) + pt.get("width", 0) - 10
                and el.get("y", 0) <= pt.get("y", 0) + pt.get("height", 0) - 10
                and el.get("elementid") != pt.get("elementid")
            ):
                el["filteredparentid"] = pt.get("elementid")
                break

    return modified


def _identify_tables(elements: list[dict]) -> list[dict]:
    """Detect grid-like structures and add virtual table/row elements.

    Adds virtual table/row nodes for grid-like coordinate groupings.
    """

    def _group_by(items: list, keyfunc) -> dict:
        groups: dict = {}
        for item in items:
            key = keyfunc(item)
            groups.setdefault(key, []).append(item)
        return groups

    def _bucket(coord: float, tolerance: int = 3) -> int:
        return int(round(coord / tolerance) * tolerance)

    modified = list(elements)

    def _max_index(prefix: str) -> int:
        hi = 0
        for el in elements:
            eid = el.get("elementid", "")
            if eid.startswith(prefix):
                try:
                    hi = max(hi, int(eid[len(prefix):]))
                except ValueError:
                    pass
        return hi

    table_idx = _max_index("table")
    row_idx   = _max_index("tablerow")

    for parent_id, group in _group_by(elements, lambda el: el.get("filteredparentid")).items():
        if parent_id is None:
            continue

        y_groups = _group_by(group, lambda el: _bucket(el.get("y", 0)))
        candidate_rows = {
            y: els for y, els in y_groups.items()
            if (len(els) >= 2
                and len({el.get("name") for el in els}) > 1
                and sum(1 for el in els if el.get("role") == "text") >= 2)
        }
        if len(candidate_rows) < 2:
            continue

        all_cands: list[dict] = []
        for els in candidate_rows.values():
            all_cands.extend(els)

        xname_groups = _group_by(all_cands, lambda el: (_bucket(el.get("x", 0)), el.get("name")))
        column_groups = [
            (x, name, els)
            for (x, name), els in xname_groups.items()
            if (len(els) >= 2
                and len({_bucket(el.get("y", 0)) for el in els}) >= 5
                and sum(1 for el in els if el.get("role") == "text") >= 2)
        ]
        if not column_groups:
            continue

        table_idx += 1
        table_id = f"t{table_idx}"
        modified.append({
            "elementid": table_id, "name": "", "role": "table",
            "description": "", "xpath": "", "states": [], "text": "",
            "x":      min(el.get("x", 0) for el in all_cands),
            "y":      min(el.get("y", 0) for el in all_cands),
            "width":  (max(el.get("x", 0) + el.get("width", 0) for el in all_cands)
                       - min(el.get("x", 0) for el in all_cands)),
            "height": (max(el.get("y", 0) + el.get("height", 0) for el in all_cands)
                       - min(el.get("y", 0) for el in all_cands)),
            "filteredparentid": parent_id,
        })

        ncols = 0
        for y, row_els in candidate_rows.items():
            if ncols == 0:
                ncols = len(row_els)
            if len(row_els) != ncols:
                continue
            row_idx += 1
            row_id = f"r{row_idx}"
            modified.append({
                "elementid": row_id, "name": "", "role": "row",
                "description": "", "xpath": "", "states": [], "text": "",
                "x":      min(el.get("x", 0) for el in row_els),
                "y":      y,
                "width":  (max(el.get("x", 0) + el.get("width", 0) for el in row_els)
                           - min(el.get("x", 0) for el in row_els)),
                "height": (max(el.get("y", 0) + el.get("height", 0) for el in row_els) - y),
                "filteredparentid": table_id,
            })
            for el in row_els:
                if el.get("filteredparentid") == parent_id:
                    el["filteredparentid"] = row_id

    return modified


def enrich_java_elements(elements: list[dict]) -> list[dict]:
    """Apply page-tab grouping and table detection to a flat element list.

    Call this on the raw extraction result before saving to the DB or
    rendering the AI snapshot.
    """
    enriched = _page_tab_grouping(elements)
    enriched = _identify_tables(enriched)
    return enriched


def _element_to_snapshot_lines(el: dict, indent: str = "- ") -> list[str]:
    """Render one element (and its children) into snapshot lines.

    Render role-specific labels for Java-agent snapshots.
    """
    name   = (el.get("description") or el.get("name") or "").replace("List of Values", "").strip()
    role   = el.get("role", "")
    states = el.get("states") or []
    text   = el.get("text") or ""
    ref    = f"[ref={el['elementid']}]"

    active_state = "active" if "active" in states else "deselected"

    if role == "internal frame":
        line = f"{indent}page {ref}, {active_state}: {name}"
    elif role == "text" and "focusable" in states:
        line = f'{indent}textbox "{name}" {ref}: {text}'
    elif role == "check box":
        state_label = "checked" if "checked" in states else "unchecked"
        line = f'{indent}check box "{name}" {ref}: {state_label}'
    elif role == "page tab":
        if "selected" in states:
            line = f"{indent}tab {ref}, selected: {name}"
        elif "selectable" in states:
            line = f"{indent}tab {ref}, selectable: {name}"
        else:
            line = f"{indent}tab {ref}, disabled: {name}"
    elif role == "push button" and "enabled" not in states:
        line = f"{indent}{role} {ref}, disabled: {name}"
    else:
        line = f"{indent}{role} {ref}: {name}"

    child_indent = "    " + indent
    child_lines = [
        child_line
        for child in el.get("children", [])
        for child_line in _element_to_snapshot_lines(child, child_indent)
    ]
    return [line] + child_lines


def java_elements_to_ai_snapshot(
    elements: list[dict],
    max_chars: int = config.MAX_SNAPSHOT_CHARS,
) -> str:
    """Hierarchical, role-labelled Java-agent snapshot.

    Applies page-tab grouping and table detection, builds a parent→children
    tree, then renders using role-specific labels identical to the old
    ``get_ai_page_snapshot()`` output::

        ### Page state
        ```yaml
        - page [ref=e1], active: EMR Global Sales Order Entry Form ...
            - tab [ref=e2], selected: Main
                - textbox "Sold To" [ref=e5]:
                - textbox "Type" [ref=e6]: Standard_ISVUS
        ```
    """
    # 1. Enrich
    enriched = enrich_java_elements([dict(el) for el in elements])

    # 2. Sort by position for stable tree order
    enriched.sort(key=lambda el: (el.get("y", 0), el.get("x", 0)))

    # 3. Build tree
    lookup: dict[str, dict] = {}
    for el in enriched:
        el.setdefault("children", [])
        lookup[el["elementid"]] = el

    roots: list[dict] = []
    for el in enriched:
        pid = el.get("filteredparentid")
        if pid and pid in lookup:
            lookup[pid]["children"].append(el)
        else:
            roots.append(el)

    # 4. Render
    lines = ["### Page state", "```yaml"]
    for root in roots:
        lines.extend(_element_to_snapshot_lines(root))
    lines.append("```")

    result = "\n".join(lines)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n...(truncated)\n```"
    return result


# ── Scrub secrets before sending to LLM ─────────────────────────────────────

_SECRET_KEYS = {"password", "secret", "token", "credential", "key", "passwd"}


def scrub_secrets(text: str) -> str:
    """
    Best-effort scrub of common credential patterns from snapshot text.
    Replaces values next to known secret field labels with [REDACTED].
    """
    import re
    # Replace patterns like: Password | text = 'abc...'
    for kw in _SECRET_KEYS:
        text = re.sub(
            rf"(?i)({re.escape(kw)}[^|\\n]{{0,30}}=\s*)['\"]?[^'\"\n]{{1,100}}['\"]?",
            r"\1[REDACTED]",
            text,
        )
    return text

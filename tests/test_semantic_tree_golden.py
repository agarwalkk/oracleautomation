"""Golden, per-layout tests for the agent-authority pipeline (schema v2).

These fixtures simulate the ENRICHED scan the Java agent now emits (structure +
identity already resolved). They lock in the contract and prove Python is a thin
renderer: every layout type renders from explicit fields, and every actionable
node yields deterministic locator params — no geometry, no name-prefix guessing.

Run: python -m pytest tests/test_semantic_tree_golden.py -q
"""
from __future__ import annotations

from qcs_java_agent import ui_schema
from qcs_java_agent.snapshot import build_action_payload


def _field(nid, label, *, role="Field", owner_tab=None, editable=True,
           record_index=-1, column_key=None, current=False, mirror=False,
           tree_path=None, container_role=None, selected=False, text=""):
    sem_scope = f"tab:{owner_tab}" if owner_tab else "form:Test"
    return {
        "id": nid, "semanticType": role, "simpleClassName": "VTextField",
        "canonicalLabel": label, "displayName": label, "accessibleName": label,
        "ownerTab": owner_tab, "editable": editable, "enabled": True,
        "showing": True, "visible": True, "selected": selected,
        "recordIndex": record_index, "columnKey": column_key, "current": current,
        "isMirror": mirror, "treePath": tree_path, "containerRole": container_role,
        "text": text,
        "semanticId": f"{sem_scope}::{label}::{max(record_index,0)}",
        "primaryLocator": {
            "strategy": "canonicalLabel", "value": label, "confidence": 1.0,
            "verifiedUnique": record_index < 0, "scope": sem_scope,
            "ordinal": record_index,
        },
        "locatorAmbiguous": record_index >= 0,
        "screenBounds": {"x": 10, "y": 10 + nid, "width": 120, "height": 20,
                         "screenX": 110, "screenY": 110 + nid},
        "children": [],
    }


def _scan(*roots):
    return {"agent": {"schema": "2.0"}, "windows": list(roots)}


def _frame(title, children):
    return {"id": 1, "semanticType": "Window", "simpleClassName": "ExtendedFrame",
            "title": title, "showing": True, "children": children}


# ── 1. single-record form ──────────────────────────────────────────────────

def test_single_record_form_renders_fields_with_locators():
    scan = _scan(_frame("Find Orders", [
        _field(36, "Order Number"),
        _field(39, "Order Type", role="LOV"),
    ]))
    assert ui_schema.is_enriched(scan)
    assert ui_schema.validate_scan(scan) == []
    payload = build_action_payload(scan)
    assert "Order Number" in payload["text"]
    lp = payload["id_map"]["e36"]["locator_params"]
    assert lp["locatorCanonicalLabel"] == "Order Number"
    assert lp["locatorSemanticId"].endswith("Order Number::0")


# ── 2. tabbed form: content grouped by ownerTab (no prefix parsing) ─────────

def test_tabs_group_by_owner_tab():
    tabfolder = {"id": 2, "semanticType": "Tab", "simpleClassName": "TabBar",
                 "containerRole": "TabFolder",
                 "attributes": {"tabTitles": "Quote/Order Information | Line Information",
                                "tabSelectedTitle": "Quote/Order Information"},
                 "children": []}
    scan = _scan(_frame("Find Orders", [
        tabfolder,
        _field(36, "Order Number", owner_tab="Quote/Order Information"),
        _field(1024, "Ordered Item", owner_tab="Line Information", role="LOV"),
    ]))
    payload = build_action_payload(scan)
    text = payload["text"]
    assert "Quote/Order Information (Tab, enabled, selected)" in text
    # Order Number nests under its tab; Ordered Item under the other tab.
    assert "Order Number" in text and "Ordered Item" in text


# ── 3. multi-record grid: table from recordIndex + columnKey ───────────────

def test_grid_table_from_record_index_and_column_key():
    cells = []
    nid = 500
    for r in range(3):
        cells.append(_field(nid, "Order", role="Field", owner_tab="Lines",
                            record_index=r, column_key="Order",
                            container_role="GridCell", current=(r == 0),
                            text=f"47448{r}"))
        nid += 1
        cells.append(_field(nid, "Status", role="Field", owner_tab="Lines",
                            record_index=r, column_key="Status",
                            container_role="GridCell", text="Cancelled"))
        nid += 1
    scan = _scan(_frame("Orders", [
        {"id": 4, "semanticType": "Tab", "simpleClassName": "TabBar",
         "containerRole": "TabFolder",
         "attributes": {"tabTitles": "Lines", "tabSelectedTitle": "Lines"},
         "children": []},
        *cells,
    ]))
    payload = build_action_payload(scan)
    text = payload["text"]
    assert "| # | Order | Status |" in text
    assert "**1**" in text  # current record bolded from `current` flag
    # Each cell is independently addressable with a record-scoped ordinal.
    lp = payload["id_map"]["e500"]["locator_params"]
    assert lp["locatorRecordIndex"] == "0"


# ── 4. TREE: items are real, addressable, and carry treePath locators ──────

def test_tree_items_have_identity_and_locators():
    tree = {"id": 969, "semanticType": "Tree", "simpleClassName": "DTree",
            "accessibleName": "Orders Tree", "canonicalLabel": "Orders Tree",
            "displayName": "Orders Tree", "enabled": True, "showing": True,
            "children": [
                _field(970, "Today's Orders", role="TreeItem",
                       container_role="TreeItem", tree_path="Orders Tree/Today's Orders"),
                _field(971, "Search Results", role="TreeItem", selected=True,
                       container_role="TreeItem", tree_path="Orders Tree/Search Results"),
                _field(972, "Personal Folders", role="TreeItem",
                       container_role="TreeItem", tree_path="Orders Tree/Personal Folders"),
                _field(973, "Public Folders", role="TreeItem",
                       container_role="TreeItem", tree_path="Orders Tree/Public Folders"),
            ]}
    scan = _scan(_frame("Order Organizer", [tree]))
    assert ui_schema.validate_scan(scan) == []
    payload = build_action_payload(scan)

    # The regression that motivated this work: Personal Folders MUST be
    # addressable with a deterministic locator (it was lost before).
    pf = payload["id_map"]["e972"]
    assert pf["tree_path"] == "Orders Tree/Personal Folders"
    assert pf["locator_params"]["locatorTreePath"] == "Orders Tree/Personal Folders"
    assert "Personal Folders" in payload["text"]
    # Selected item is marked.
    assert "Search Results *" in payload["text"]
    # Tree items render ONCE (under their tree), not duplicated as loose fields.
    assert payload["text"].count("Personal Folders") == 1


# ── 5. read-only mirror is suppressed from the action snapshot ──────────────

def test_mirror_field_excluded_from_snapshot():
    scan = _scan(_frame("Find Orders", [
        _field(36, "Order Number", editable=True),
        _field(37, "Order Number", editable=False, mirror=True),
    ]))
    payload = build_action_payload(scan)
    # Only the editable input is an action target; the mirror is hover-only.
    assert "e36" in payload["id_map"]
    assert "e37" not in payload["id_map"]


# ── 6. contract validation catches malformed grid/tree nodes ───────────────

def test_validation_flags_missing_grid_keys():
    bad = _field(1, "X", container_role="GridCell")  # no recordIndex/columnKey
    bad["recordIndex"] = -1
    bad["columnKey"] = None
    errs = ui_schema.validate_node(bad)
    assert any("GridCell missing recordIndex" in e for e in errs)
    assert any("GridCell missing columnKey" in e for e in errs)

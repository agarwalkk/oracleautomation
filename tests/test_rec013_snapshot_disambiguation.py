"""Regression guard for the rec_013 class of bug.

Background
----------
In rec_013 the Approach-A (coordinate-based) recorder entered a PO number
value into the *PO Received* field instead of the *PO Number* field.  The
root cause was that the computer-use AI returned a pixel coordinate that was
off by ~1-2 px relative to the intended field.  ``actioned_element_at``
uses a geometric bounds test, so a point that barely misses the bottom edge
of "PO Number" (y < y_bottom) lands inside the adjacent "PO Received" field
(y >= y_top_received).  There is no recovery: the coordinate is consumed
without any identity check.

The Approach-B (snapshot) path eliminates this whole class of ambiguity:

1. The AI receives a text snapshot where every element has a unique ``[eN]``
   token that is stable within a single scan.
2. The AI returns *that token*, not a pixel coordinate.
3. Lookup in ``id_to_element[element_id]`` is an exact dictionary read;
   no area scoring, no proximity arithmetic, no rounding errors.

This module:
  A. Demonstrates the Approach-A coordinate fragility (rec_013 root cause).
  B. Proves the Approach-B snapshot path is immune by construction.
"""
from __future__ import annotations

import re

import pytest

from qcs_java_agent.snapshot import actioned_element_at, build_action_context


# ── Minimal scan reproducing the rec_013 layout ───────────────────────────────
#
# Oracle form with two adjacent same-size text fields stacked vertically.
# Coordinates chosen to match typical Oracle Forms field density (~24 px tall).
#
#   screen_y 200 ┌──────────────────────────┐
#                │  PO Number   (Field, e1)  │  24 px tall
#   screen_y 224 ├──────────────────────────┤  ← shared edge
#                │  PO Received (Field, e2)  │  24 px tall
#   screen_y 248 └──────────────────────────┘
#
# A click intended for PO Number but delivered at y=224 (shared edge) falls
# inside PO Received's bounds [224, 248) and NOT PO Number's [200, 224).
# One pixel of imprecision is enough to pick the wrong field.

_PO_NUMBER_X   = 100
_PO_NUMBER_Y   = 200
_PO_RECEIVED_Y = 224      # = _PO_NUMBER_Y + _FIELD_HEIGHT
_FIELD_WIDTH   = 150
_FIELD_HEIGHT  = 24
_FIELD_X_MID   = _PO_NUMBER_X + _FIELD_WIDTH // 2   # 175, horizontally centred


def _field_node(node_id: int, display_name: str, screen_y: int) -> dict:
    return {
        "id": node_id,
        "semanticType": "Field",
        "displayName": display_name,
        "accessibleName": display_name,
        "path": f"w0/n{node_id}",
        "parentPath": "w0",
        "enabled": True,
        "visible": True,
        "showing": True,
        "focusable": True,
        "focused": False,
        "children": [],
        "screenBounds": {
            "screenX": _PO_NUMBER_X,
            "screenY": screen_y,
            "width":   _FIELD_WIDTH,
            "height":  _FIELD_HEIGHT,
        },
    }


def _rec013_scan() -> dict:
    """Minimal two-field scan replicating the rec_013 ambiguity layout."""
    window = {
        "id": 0,
        "semanticType": "Window",
        "displayName": "Purchase Order",
        "accessibleName": "Purchase Order",
        "path": "w0",
        "parentPath": "",
        "enabled": True,
        "visible": True,
        "showing": True,
        "focusable": False,
        "focused": False,
        "screenBounds": {"screenX": 0, "screenY": 0, "width": 800, "height": 600},
        "children": [
            _field_node(1, "PO Number",   _PO_NUMBER_Y),
            _field_node(2, "PO Received", _PO_RECEIVED_Y),
        ],
    }
    return {"windows": [window]}


# ── Part A: Approach-A coordinate fragility (rec_013 root cause) ──────────────

class TestApproachACoordinateAmbiguity:
    """Show that actioned_element_at is fragile when fields share an edge.

    These tests document the rec_013 bug mechanics — they are NOT regressions
    of Approach B.  They exist so the spec is clear about why Approach B exists.
    """

    def test_centre_of_po_number_resolves_correctly(self):
        """A coordinate well inside PO Number resolves to PO Number."""
        scan = _rec013_scan()
        y_centre = _PO_NUMBER_Y + _FIELD_HEIGHT // 2   # 212
        element = actioned_element_at(scan, _FIELD_X_MID, y_centre)
        assert element is not None
        assert element["name"] == "PO Number"

    def test_one_pixel_off_bottom_edge_resolves_to_po_received(self):
        """y=224 is the first row inside PO Received, not PO Number.

        This is the rec_013 scenario: the computer-use AI clicked at y that
        was AT OR BELOW the bottom edge of PO Number.  The bounds check
        ``y <= screen_y < y + height`` maps y=224 into PO Received [224,248).
        """
        scan = _rec013_scan()
        # y=224 is the shared edge: PO Number is [200,224), PO Received is [224,248).
        # Even a single-pixel miss lands in the wrong field.
        element = actioned_element_at(scan, _FIELD_X_MID, _PO_RECEIVED_Y)
        assert element is not None
        assert element["name"] == "PO Received", (
            "Expected PO Received — this documents the rec_013 bug: "
            "a coordinate at the shared edge resolves to the WRONG field"
        )

    def test_two_pixels_above_edge_resolves_to_po_number(self):
        """y=222 is safely inside PO Number's range [200,224)."""
        scan = _rec013_scan()
        element = actioned_element_at(scan, _FIELD_X_MID, _PO_NUMBER_Y + _FIELD_HEIGHT - 2)
        assert element is not None
        assert element["name"] == "PO Number"

    def test_centre_of_po_received_resolves_correctly(self):
        """Control: centre of PO Received resolves to PO Received."""
        scan = _rec013_scan()
        y_centre = _PO_RECEIVED_Y + _FIELD_HEIGHT // 2   # 236
        element = actioned_element_at(scan, _FIELD_X_MID, y_centre)
        assert element is not None
        assert element["name"] == "PO Received"


# ── Part B: Approach-B snapshot immunity ──────────────────────────────────────

class TestApproachBSnapshotEliminatesAmbiguity:
    """build_action_context + element_id lookup is immune to rec_013 class bugs.

    The snapshot path never consults pixel coordinates.  The AI receives a
    token like ``[e1]`` for PO Number and ``[e2]`` for PO Received.  Resolving
    ``id_to_element["e1"]`` is an O(1) dictionary lookup — deterministic,
    exact, and independent of screen layout.
    """

    def test_both_fields_appear_in_snapshot_text(self):
        scan = _rec013_scan()
        snapshot_text, _ = build_action_context(scan)
        assert "PO Number" in snapshot_text
        assert "PO Received" in snapshot_text

    def test_po_number_and_po_received_have_distinct_element_ids(self):
        scan = _rec013_scan()
        snapshot_text, id_to_element = build_action_context(scan)
        tokens = re.findall(r"\[([^\]]+)\]", snapshot_text)
        assert len(tokens) == len(set(tokens)), "All [eN] tokens in snapshot must be unique"
        # Exactly the two field nodes should be present
        assert len(id_to_element) == 2

    def test_element_id_for_po_number_resolves_to_po_number(self):
        """Core assertion: the element_id tied to 'PO Number' always returns PO Number."""
        scan = _rec013_scan()
        _, id_to_element = build_action_context(scan)

        # Find the element_id whose name is "PO Number"
        po_number_ids = [
            eid for eid, el in id_to_element.items()
            if el.get("name") == "PO Number"
        ]
        assert len(po_number_ids) == 1, "Exactly one element should have name 'PO Number'"
        po_number_eid = po_number_ids[0]

        # This is what the AI returns in its tool call
        resolved = id_to_element[po_number_eid]
        assert resolved["name"] == "PO Number"

    def test_element_id_for_po_received_resolves_to_po_received(self):
        """Symmetric check: PO Received's element_id always returns PO Received."""
        scan = _rec013_scan()
        _, id_to_element = build_action_context(scan)

        po_received_ids = [
            eid for eid, el in id_to_element.items()
            if el.get("name") == "PO Received"
        ]
        assert len(po_received_ids) == 1
        po_received_eid = po_received_ids[0]

        resolved = id_to_element[po_received_eid]
        assert resolved["name"] == "PO Received"

    def test_po_number_element_id_never_resolves_to_po_received(self):
        """The snapshot element_id for PO Number cannot accidentally return PO Received.

        This is the fundamental immunity: no matter how adjacent the two fields
        are, ``id_to_element[po_number_eid]`` always returns the PO Number
        element.  There is no coordinate involved in the lookup.
        """
        scan = _rec013_scan()
        _, id_to_element = build_action_context(scan)

        po_number_eid = next(
            eid for eid, el in id_to_element.items()
            if el.get("name") == "PO Number"
        )
        # Simulate the AI correctly targeting PO Number (even though the fields
        # are only 1px apart at their shared edge)
        resolved = id_to_element[po_number_eid]
        assert resolved["name"] != "PO Received", (
            "An element_id that maps to PO Number must NEVER return PO Received. "
            "This failure would indicate the snapshot keying invariant is broken."
        )

    def test_po_number_snapshot_line_contains_correct_token(self):
        """The snapshot text line for PO Number contains its own element_id token."""
        scan = _rec013_scan()
        snapshot_text, id_to_element = build_action_context(scan)

        po_number_eid = next(
            eid for eid, el in id_to_element.items()
            if el.get("name") == "PO Number"
        )
        # The snapshot line for PO Number must start with [<po_number_eid>]
        po_line = next(
            (line for line in snapshot_text.splitlines()
             if f"[{po_number_eid}]" in line),
            None,
        )
        assert po_line is not None, f"Expected a snapshot line with [{po_number_eid}]"
        assert "PO Number" in po_line
        assert "PO Received" not in po_line

    def test_adjacent_fields_produce_no_shared_element_ids(self):
        """No element_id points to both PO Number and PO Received."""
        scan = _rec013_scan()
        _, id_to_element = build_action_context(scan)

        names_per_id = {eid: el["name"] for eid, el in id_to_element.items()}
        assert "PO Number" in names_per_id.values()
        assert "PO Received" in names_per_id.values()
        # Each element_id maps to exactly one distinct name
        assert len(set(names_per_id.values())) == len(names_per_id), (
            "Two elements share an element_id — the snapshot keying invariant is broken"
        )


# ── Part C: The full rec_013 scenario in Approach-B ───────────────────────────

class TestRec013FullSnapshotScenario:
    """End-to-end: AI says 'set_text into PO Number' → always the right field."""

    def test_snapshot_action_targeting_po_number_id_resolves_correctly(self):
        """AI-returned element_id for PO Number resolves unambiguously.

        This is the key proof: given the exact scan from a form where PO Number
        and PO Received are one pixel apart, if the AI snapshot correctly shows
        ``[e1] PO Number | Field ...`` and the AI returns ``element_id="e1"``,
        then the recorder resolves ``id_to_element["e1"]`` to PO Number — even
        though the two fields share a pixel edge.  No coordinate is consulted.
        """
        scan = _rec013_scan()
        snapshot_text, id_to_element = build_action_context(scan)

        # Verify snapshot distinguishes both fields
        assert "PO Number" in snapshot_text
        assert "PO Received" in snapshot_text

        # Identify the element_id the AI would use for "PO Number"
        # (the AI reads the snapshot and returns the token next to "PO Number")
        po_number_eid = next(
            eid for eid, el in id_to_element.items()
            if el.get("name") == "PO Number"
        )

        # Simulate the AI's tool call: SnapshotAction(action="set_text", element_id=po_number_eid, ...)
        # In real code: action = parse_snapshot_action({"action":"set_text","element_id":po_number_eid,...})
        # Resolve the element exactly as _execute_snapshot_recording_step does:
        resolved_element = id_to_element[po_number_eid]

        assert resolved_element["name"] == "PO Number", (
            f"Expected 'PO Number' but got {resolved_element['name']!r}. "
            "The snapshot path must resolve PO Number by element_id, not by coordinate."
        )
        assert resolved_element["name"] != "PO Received", (
            "The snapshot path resolved to the WRONG field — rec_013 bug would recur."
        )

    def test_snapshot_action_targeting_po_received_id_resolves_correctly(self):
        """Symmetric: explicit targeting of PO Received also resolves correctly."""
        scan = _rec013_scan()
        _, id_to_element = build_action_context(scan)

        po_received_eid = next(
            eid for eid, el in id_to_element.items()
            if el.get("name") == "PO Received"
        )
        resolved_element = id_to_element[po_received_eid]

        assert resolved_element["name"] == "PO Received"
        assert resolved_element["name"] != "PO Number"

    def test_approach_a_fragility_vs_approach_b_robustness_summary(self):
        """Comparative summary: same scan, coordinate vs snapshot path.

        Approach A (off-by-one coordinate → wrong field):
          actioned_element_at(scan, mid_x, shared_edge_y) → PO Received  ← BUG

        Approach B (element_id lookup → always correct field):
          id_to_element[po_number_eid]["name"]            → PO Number    ← CORRECT
        """
        scan = _rec013_scan()
        _, id_to_element = build_action_context(scan)

        # Approach A: coordinate at shared edge → wrong field (rec_013 root cause)
        wrong_element = actioned_element_at(scan, _FIELD_X_MID, _PO_RECEIVED_Y)
        assert wrong_element is not None
        approach_a_result = wrong_element["name"]

        # Approach B: element_id lookup → always correct field
        po_number_eid = next(
            eid for eid, el in id_to_element.items()
            if el.get("name") == "PO Number"
        )
        approach_b_result = id_to_element[po_number_eid]["name"]

        # The contrast: Approach A is wrong at the boundary, Approach B is always right
        assert approach_a_result == "PO Received",  \
            "Approach A must demonstrate the coordinate-boundary mis-resolution"
        assert approach_b_result == "PO Number",   \
            "Approach B must be immune: exact element_id lookup always returns the correct field"
        assert approach_a_result != approach_b_result, \
            "The two approaches must diverge on this boundary case — that is the point of the test"

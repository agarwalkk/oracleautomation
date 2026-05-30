"""Tests for qcs_repo.store.upsert_form_module.

Core invariants under test
--------------------------
1. Two scans of the same form with the same element labels but different
   volatile elementids (simulating a JVM restart) produce identical
   (form_ref, element_ref) primary-key pairs.
2. The second scan produces no duplicate entries — idempotent.
3. ``created_at`` is preserved from the first scan; ``updated_at`` is refreshed.
4. New entries start as ``status='candidate'``.
5. An entry that was promoted to ``status='active'`` is NOT downgraded back to
   ``'candidate'`` by a subsequent scan.
6. Empty element list returns [] without touching the DB.
7. Duplicate labels within a single batch are disambiguated consistently across
   scans (``description``, ``description_2``, …).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from qcs_repo import store as repo_store
from qcs_repo.schema import RepoEntry


# ── Shared helpers ────────────────────────────────────────────────────────────

FORM_REF = "java_po_form"

T1 = "2024-01-01T10:00:00+00:00"
T2 = "2024-01-02T10:00:00+00:00"


def _elem(
    elementid: str,
    friendly_name: str,
    name: str,
    role: str,
) -> dict:
    """Minimal repo element as produced by java_nodes_to_repo_elements."""
    return {
        "elementid": elementid,
        "friendly_name": friendly_name,
        "name": name,
        "role": role,
        "surface": "java",
        "states": ["enabled", "visible", "showing"],
        "bounds": {"x": 100, "y": 200, "width": 120, "height": 24},
        "x": 100,
        "y": 200,
        "width": 120,
        "height": 24,
        "text": "",
        "description": "",
        "xpath": f"w0/{elementid}",
        "path": f"w0/{elementid}",
        "java": {"path": f"w0/{elementid}"},
    }


# First scan: element ids e10, e11, e20
SCAN_1 = [
    _elem("e10", "po_number",   "PO Number",   "Field"),
    _elem("e11", "vendor_name", "Vendor Name", "Field"),
    _elem("e20", "find",        "Find",         "Button"),
]

# Second scan: same labels, different volatile elementids (JVM restart)
SCAN_2 = [
    _elem("e15", "po_number",   "PO Number",   "Field"),
    _elem("e16", "vendor_name", "Vendor Name", "Field"),
    _elem("e25", "find",        "Find",         "Button"),
]


# ── Test 1 + 2 + 3: stable keys, no duplicates, created_at preserved ─────────

class TestTwoScansStability:

    def test_same_element_refs_after_two_scans(self, tmp_path):
        """Same human-readable element_refs regardless of volatile elementid."""
        with patch("qcs_repo.store._now_iso", return_value=T1):
            entries1 = repo_store.upsert_form_module(FORM_REF, SCAN_1, "r:run1", tmp_path)

        with patch("qcs_repo.store._now_iso", return_value=T2):
            entries2 = repo_store.upsert_form_module(FORM_REF, SCAN_2, "r:run2", tmp_path)

        keys1 = {(e.form_ref, e.element_ref) for e in entries1}
        keys2 = {(e.form_ref, e.element_ref) for e in entries2}
        assert keys1 == keys2

    def test_element_refs_are_human_readable_not_volatile_eids(self, tmp_path):
        with patch("qcs_repo.store._now_iso", return_value=T1):
            entries = repo_store.upsert_form_module(FORM_REF, SCAN_1, "r:run1", tmp_path)

        element_refs = {e.element_ref for e in entries}
        assert element_refs == {"po_number", "vendor_name", "find"}
        # Must not contain volatile eN ids
        assert not any(e.element_ref.startswith("e") and e.element_ref[1:].isdigit()
                       for e in entries)

    def test_no_duplicate_entries_after_two_scans(self, tmp_path):
        with patch("qcs_repo.store._now_iso", return_value=T1):
            repo_store.upsert_form_module(FORM_REF, SCAN_1, "r:run1", tmp_path)

        with patch("qcs_repo.store._now_iso", return_value=T2):
            repo_store.upsert_form_module(FORM_REF, SCAN_2, "r:run2", tmp_path)

        all_entries = repo_store.list_form_entries(FORM_REF, tmp_path)
        assert len(all_entries) == 3  # one per element, no duplicates

    def test_created_at_preserved_from_first_scan(self, tmp_path):
        with patch("qcs_repo.store._now_iso", return_value=T1):
            entries1 = repo_store.upsert_form_module(FORM_REF, SCAN_1, "r:run1", tmp_path)

        with patch("qcs_repo.store._now_iso", return_value=T2):
            entries2 = repo_store.upsert_form_module(FORM_REF, SCAN_2, "r:run2", tmp_path)

        created_1 = {e.element_ref: e.created_at for e in entries1}
        created_2 = {e.element_ref: e.created_at for e in entries2}

        # created_at from scan-1 must survive scan-2
        assert created_1 == created_2
        assert all(v == T1 for v in created_2.values())

    def test_updated_at_refreshed_on_second_scan(self, tmp_path):
        with patch("qcs_repo.store._now_iso", return_value=T1):
            entries1 = repo_store.upsert_form_module(FORM_REF, SCAN_1, "r:run1", tmp_path)

        with patch("qcs_repo.store._now_iso", return_value=T2):
            entries2 = repo_store.upsert_form_module(FORM_REF, SCAN_2, "r:run2", tmp_path)

        updated_1 = {e.element_ref: e.updated_at for e in entries1}
        updated_2 = {e.element_ref: e.updated_at for e in entries2}

        assert all(v == T1 for v in updated_1.values())
        assert all(v == T2 for v in updated_2.values())

    def test_all_entries_are_form_scoped(self, tmp_path):
        with patch("qcs_repo.store._now_iso", return_value=T1):
            entries = repo_store.upsert_form_module(FORM_REF, SCAN_1, "r:run1", tmp_path)

        assert all(e.form_ref == FORM_REF for e in entries)
        assert all(e.qualified_ref == f"{FORM_REF}.{e.element_ref}" for e in entries)


# ── Test 4: new entries start as candidate ────────────────────────────────────

class TestCandidateStatus:

    def test_new_entries_have_status_candidate(self, tmp_path):
        entries = repo_store.upsert_form_module(FORM_REF, SCAN_1, repo_dir=tmp_path)
        assert all(e.status == "candidate" for e in entries)

    def test_surface_is_java_forms(self, tmp_path):
        entries = repo_store.upsert_form_module(FORM_REF, SCAN_1, repo_dir=tmp_path)
        assert all(e.surface == "java_forms" for e in entries)

    def test_object_type_reflects_role(self, tmp_path):
        entries = repo_store.upsert_form_module(FORM_REF, SCAN_1, repo_dir=tmp_path)
        by_ref = {e.element_ref: e for e in entries}
        assert by_ref["po_number"].object_type == "Field"
        assert by_ref["find"].object_type == "Button"


# ── Test 5: active status not downgraded by rescan ───────────────────────────

class TestStatusPreservation:

    def test_active_status_survives_rescan(self, tmp_path):
        """An entry promoted to 'active' must not be reset to 'candidate'."""
        # First upsert — all candidate
        entries = repo_store.upsert_form_module(FORM_REF, SCAN_1, repo_dir=tmp_path)
        assert entries[0].status == "candidate"

        # Manually promote po_number to active (simulates upsert_actioned_element path)
        po_entry = next(e for e in entries if e.element_ref == "po_number")
        po_entry.status = "active"
        repo_store.upsert_entry(po_entry, tmp_path)

        # Re-scan — status must survive
        entries2 = repo_store.upsert_form_module(FORM_REF, SCAN_2, repo_dir=tmp_path)
        po_after = next(e for e in entries2 if e.element_ref == "po_number")
        assert po_after.status == "active"

    def test_deprecated_status_survives_rescan(self, tmp_path):
        """A deprecated entry is never silently re-activated by a scan."""
        entries = repo_store.upsert_form_module(FORM_REF, SCAN_1, repo_dir=tmp_path)

        dep_entry = next(e for e in entries if e.element_ref == "find")
        dep_entry.status = "deprecated"
        repo_store.upsert_entry(dep_entry, tmp_path)

        entries2 = repo_store.upsert_form_module(FORM_REF, SCAN_2, repo_dir=tmp_path)
        find_after = next(e for e in entries2 if e.element_ref == "find")
        assert find_after.status == "deprecated"

    def test_absent_elements_left_untouched(self, tmp_path):
        """Elements from scan-1 that are absent in scan-2 keep their entries."""
        # Scan 1: three elements
        repo_store.upsert_form_module(FORM_REF, SCAN_1, repo_dir=tmp_path)

        # Scan 2: only two elements (vendor_name dropped)
        partial_scan = [e for e in SCAN_2 if e["friendly_name"] != "vendor_name"]
        repo_store.upsert_form_module(FORM_REF, partial_scan, repo_dir=tmp_path)

        all_entries = repo_store.list_form_entries(FORM_REF, tmp_path)
        assert len(all_entries) == 3  # vendor_name still present
        vendor = next((e for e in all_entries if e.element_ref == "vendor_name"), None)
        assert vendor is not None
        assert vendor.status == "candidate"  # unchanged from first scan


# ── Test 6: empty list is a no-op ─────────────────────────────────────────────

class TestEdgeCases:

    def test_empty_elements_returns_empty_list(self, tmp_path):
        result = repo_store.upsert_form_module(FORM_REF, [], repo_dir=tmp_path)
        assert result == []

    def test_empty_elements_does_not_create_db_rows(self, tmp_path):
        repo_store.upsert_form_module(FORM_REF, [], repo_dir=tmp_path)
        assert repo_store.list_form_entries(FORM_REF, tmp_path) == []

    def test_returns_one_entry_per_element(self, tmp_path):
        entries = repo_store.upsert_form_module(FORM_REF, SCAN_1, repo_dir=tmp_path)
        assert len(entries) == len(SCAN_1)


# ── Test 7: duplicate labels disambiguated consistently ───────────────────────

class TestDuplicateLabelDisambiguation:

    def test_duplicate_labels_get_distinct_element_refs(self, tmp_path):
        """Two 'Description' fields in the same form get 'description' and 'description_2'."""
        elements = [
            _elem("e10", "description", "Description", "Field"),
            _elem("e11", "description", "Description", "Field"),  # same label
        ]
        entries = repo_store.upsert_form_module(FORM_REF, elements, repo_dir=tmp_path)
        refs = {e.element_ref for e in entries}
        assert "description" in refs
        assert "description_2" in refs
        assert len(refs) == 2

    def test_duplicate_label_disambiguation_is_stable_across_scans(self, tmp_path):
        """Same disambiguation order on rescan."""
        elements1 = [
            _elem("e10", "description", "Description", "Field"),
            _elem("e11", "description", "Description", "Field"),
        ]
        elements2 = [
            _elem("e20", "description", "Description", "Field"),  # different eid
            _elem("e21", "description", "Description", "Field"),  # different eid
        ]

        with patch("qcs_repo.store._now_iso", return_value=T1):
            entries1 = repo_store.upsert_form_module(FORM_REF, elements1, repo_dir=tmp_path)

        with patch("qcs_repo.store._now_iso", return_value=T2):
            entries2 = repo_store.upsert_form_module(FORM_REF, elements2, repo_dir=tmp_path)

        keys1 = {e.element_ref for e in entries1}
        keys2 = {e.element_ref for e in entries2}
        assert keys1 == keys2  # same refs both times

        all_entries = repo_store.list_form_entries(FORM_REF, tmp_path)
        assert len(all_entries) == 2  # no duplicates

        # created_at preserved
        created1 = {e.element_ref: e.created_at for e in entries1}
        created2 = {e.element_ref: e.created_at for e in entries2}
        assert created1 == created2

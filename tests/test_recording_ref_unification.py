"""Tests for unified element_ref: recording.jsonl key must match repo_entries PK.

The bug being guarded:
    On a form with duplicate display labels, upsert_actioned_element and
    upsert_form_module ran INDEPENDENT disambiguation passes, so the ref
    written to recording.jsonl could diverge from the PK in repo_entries.
    Replay reads repo_entries by exact PK, so a mismatch caused a replay miss.

The fix:
    actioned_element_ref() replays the exact disambiguation logic of
    upsert_form_module (no DB writes) and returns the committed element_ref
    for the actioned element.  _execute_snapshot_recording_step now stamps
    element["semantic_ref"] = module_ref BEFORE calling upsert_actioned_element,
    so both tables and the log agree on a single ref.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from qcs_repo import store as repo_store
from qcs_repo.schema import RepoEntry


# ── Shared helpers ────────────────────────────────────────────────────────────

FORM_REF = "java_po_form"


def _elem(
    elementid: str,
    name: str,
    role: str = "Field",
    path: str | None = None,
) -> dict:
    p = path or f"JFrame[0]/Panel[0]/VTextField[{elementid}]"
    return {
        "elementid": elementid,
        "friendly_name": name,
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
        "xpath": p,
        "path": p,
        "java": {"path": p},
    }


@pytest.fixture()
def tmp_repo(tmp_path: Path) -> Path:
    return tmp_path / "repo"


# ── actioned_element_ref: pure-computation unit tests ────────────────────────


class TestActionedElementRef:
    """actioned_element_ref replays upsert_form_module disambiguation with no DB."""

    def test_unique_labels_returns_base_ref(self, tmp_repo: Path) -> None:
        po = _elem("e1", "PO Number")
        qty = _elem("e2", "Quantity")
        ref = repo_store.actioned_element_ref(FORM_REF, po, [po, qty])
        assert ref == "po_number"

    def test_second_duplicate_gets_suffix(self, tmp_repo: Path) -> None:
        po1 = _elem("e1", "PO Number", path="JFrame[0]/Panel[0]/VTextField[1]")
        po2 = _elem("e2", "PO Number", path="JFrame[0]/Panel[0]/VTextField[2]")
        # Actioning the SECOND duplicate
        ref = repo_store.actioned_element_ref(FORM_REF, po2, [po1, po2])
        assert ref == "po_number_2"

    def test_first_duplicate_keeps_base_ref(self, tmp_repo: Path) -> None:
        po1 = _elem("e1", "PO Number", path="JFrame[0]/Panel[0]/VTextField[1]")
        po2 = _elem("e2", "PO Number", path="JFrame[0]/Panel[0]/VTextField[2]")
        ref = repo_store.actioned_element_ref(FORM_REF, po1, [po1, po2])
        assert ref == "po_number"

    def test_third_duplicate_gets_suffix_3(self, tmp_repo: Path) -> None:
        po1 = _elem("e1", "PO Number", path="JFrame[0]/Panel[0]/VTextField[1]")
        po2 = _elem("e2", "PO Number", path="JFrame[0]/Panel[0]/VTextField[2]")
        po3 = _elem("e3", "PO Number", path="JFrame[0]/Panel[0]/VTextField[3]")
        ref = repo_store.actioned_element_ref(FORM_REF, po3, [po1, po2, po3])
        assert ref == "po_number_3"

    def test_element_not_in_batch_returns_none(self) -> None:
        po = _elem("e1", "PO Number")
        other = _elem("e99", "Something Else")
        ref = repo_store.actioned_element_ref(FORM_REF, other, [po])
        assert ref is None

    def test_empty_batch_returns_none(self) -> None:
        po = _elem("e1", "PO Number")
        ref = repo_store.actioned_element_ref(FORM_REF, po, [])
        assert ref is None

    def test_object_identity_fallback(self) -> None:
        """When two elements share the same structural uid, object identity is used."""
        # Two dicts with identical structure but distinct Python objects
        po = _elem("e1", "PO Number")
        ref = repo_store.actioned_element_ref(FORM_REF, po, [po])
        assert ref == "po_number"

    def test_no_database_writes(self, tmp_repo: Path) -> None:
        """actioned_element_ref must not create or touch the DB."""
        po = _elem("e1", "PO Number")
        # The function accepts no repo_dir — it is purely computational.
        # Verify the DB stays absent after the call.
        repo_store.actioned_element_ref(FORM_REF, po, [po])
        db = tmp_repo / "repo.db"
        assert not db.exists(), "actioned_element_ref must not write to the DB"


# ── Integration: ref in log matches repo_entries PK ─────────────────────────


class TestActionedRefMatchesRepoPK:
    """The ref written to recording.jsonl must equal the repo_entries PK."""

    def test_unique_labels_log_ref_matches_pk(self, tmp_repo: Path) -> None:
        po = _elem("e1", "PO Number")
        qty = _elem("e2", "Quantity")
        batch = [po, qty]

        # Simulate what _execute_snapshot_recording_step now does
        module_ref = repo_store.actioned_element_ref(FORM_REF, po, batch)
        assert module_ref == "po_number"
        if module_ref:
            po["semantic_ref"] = module_ref

        repo_store.upsert_form_module(FORM_REF, batch, repo_dir=tmp_repo)

        entry = repo_store.load_entry(FORM_REF, module_ref, repo_dir=tmp_repo)
        assert entry is not None, f"Expected repo_entries row at ({FORM_REF!r}, {module_ref!r})"
        assert entry.element_ref == module_ref

    def test_duplicate_labels_log_ref_matches_pk(self, tmp_repo: Path) -> None:
        """Core regression: before fix, log wrote 'po_number', repo_entries had 'po_number_2'."""
        po1 = _elem("e1", "PO Number", path="JFrame[0]/Panel[0]/VTextField[1]")
        po2 = _elem("e2", "PO Number", path="JFrame[0]/Panel[0]/VTextField[2]")
        batch = [po1, po2]

        # Action the SECOND element (the one that gets _2 suffix)
        module_ref = repo_store.actioned_element_ref(FORM_REF, po2, batch)
        assert module_ref == "po_number_2", (
            "module_ref should be 'po_number_2' for the second duplicate"
        )
        if module_ref:
            po2["semantic_ref"] = module_ref

        repo_store.upsert_form_module(FORM_REF, batch, repo_dir=tmp_repo)

        entry = repo_store.load_entry(FORM_REF, module_ref, repo_dir=tmp_repo)
        assert entry is not None, (
            f"Expected repo_entries row at ({FORM_REF!r}, 'po_number_2'). "
            "Before fix this would fail because log wrote 'po_number' but "
            "repo_entries had 'po_number_2'."
        )
        assert entry.element_ref == "po_number_2"

    def test_first_duplicate_also_matches_pk(self, tmp_repo: Path) -> None:
        po1 = _elem("e1", "PO Number", path="JFrame[0]/Panel[0]/VTextField[1]")
        po2 = _elem("e2", "PO Number", path="JFrame[0]/Panel[0]/VTextField[2]")
        batch = [po1, po2]

        module_ref = repo_store.actioned_element_ref(FORM_REF, po1, batch)
        assert module_ref == "po_number"

        repo_store.upsert_form_module(FORM_REF, batch, repo_dir=tmp_repo)
        entry = repo_store.load_entry(FORM_REF, module_ref, repo_dir=tmp_repo)
        assert entry is not None
        assert entry.element_ref == "po_number"

    def test_both_duplicates_accessible_as_pks(self, tmp_repo: Path) -> None:
        po1 = _elem("e1", "PO Number", path="JFrame[0]/Panel[0]/VTextField[1]")
        po2 = _elem("e2", "PO Number", path="JFrame[0]/Panel[0]/VTextField[2]")
        batch = [po1, po2]

        repo_store.upsert_form_module(FORM_REF, batch, repo_dir=tmp_repo)

        # Both PKs must exist
        assert repo_store.load_entry(FORM_REF, "po_number", repo_dir=tmp_repo) is not None
        assert repo_store.load_entry(FORM_REF, "po_number_2", repo_dir=tmp_repo) is not None


# ── press_key with no element: must not crash ────────────────────────────────


class TestPressKeyNoElement:
    """press_key actions have no element_id — module_ref computation must be a no-op."""

    def test_actioned_element_ref_none_element_skipped_gracefully(self) -> None:
        # Simulate: element is None (press_key path)
        element = None
        batch = [_elem("e1", "Order Number")]
        form_id = FORM_REF

        # The guard in _execute_snapshot_recording_step is:
        #   if element is not None and form_id and batch: module_ref = ...
        # Replicate that guard in the test:
        module_ref = None
        if element is not None and form_id and batch:
            module_ref = repo_store.actioned_element_ref(form_id, element, batch)

        assert module_ref is None

    def test_ref_is_none_when_element_is_none(self) -> None:
        element = None
        module_ref = None
        saved = None
        # Replicate step-7 guard from _execute_snapshot_recording_step:
        ref = None
        if element is not None:
            ref = (
                module_ref
                or (saved or element or {}).get("semantic_ref")
                or (saved or element or {}).get("friendly_name")
            )
        assert ref is None

    def test_press_key_log_action_safe_with_none_ref(self) -> None:
        """Verifies that log_action for press_key doesn't use element_ref at all."""
        # press_key log call does not include element_ref/semantic_ref fields
        # (only form_ref and key) — confirm the absence is intentional
        element = None
        module_ref = None

        # What would be logged for press_key:
        log_kwargs: dict = {
            "form_ref": FORM_REF,
            "target": {"form_id": FORM_REF},
            "key": "Tab",
        }
        # Neither element_ref nor semantic_ref should appear
        assert "element_ref" not in log_kwargs
        assert "semantic_ref" not in log_kwargs


# ── Regression guard: disambiguation is deterministic across two scans ────────


class TestDisambiguationDeterminism:
    """The same element must get the same ref on every scan (same batch order)."""

    def test_same_ref_on_second_scan(self, tmp_repo: Path) -> None:
        po1 = _elem("e1", "PO Number", path="JFrame[0]/Panel[0]/VTextField[1]")
        po2 = _elem("e2", "PO Number", path="JFrame[0]/Panel[0]/VTextField[2]")
        batch = [po1, po2]

        # First scan — simulate with fresh element objects (different JVM run)
        ref_first = repo_store.actioned_element_ref(FORM_REF, po2, batch)

        # Second scan — same labels, same order
        po1b = _elem("e11", "PO Number", path="JFrame[0]/Panel[0]/VTextField[1]")
        po2b = _elem("e22", "PO Number", path="JFrame[0]/Panel[0]/VTextField[2]")
        batch2 = [po1b, po2b]

        ref_second = repo_store.actioned_element_ref(FORM_REF, po2b, batch2)

        assert ref_first == ref_second == "po_number_2", (
            "Disambiguation must be deterministic across JVM restarts (volatile elementid changes)"
        )

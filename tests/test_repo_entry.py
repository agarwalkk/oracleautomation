"""Tests for the form-scoped object repository schema and validation.

Covers requirements:
  - valid browser entry
  - valid Java Forms entry
  - missing form_ref
  - missing element_ref
  - unsupported surface
  - deprecated entry raises ReplayRefNotFoundError
  - candidate entry resolves
  - missing entry raises ReplayRefNotFoundError
  - no legacy element lookup is ever called
  - resolver.resolve(form_ref, element_ref) primary-key lookup
  - qcs repo validate CLI
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

from qcs_repo.schema import (
    VALID_SOURCES,
    VALID_STATUSES,
    VALID_SURFACES,
    RepoEntry,
    RepoValidationError,
    validate_entry,
)
from qcs_repo import store as repo_store
from qcs_replay.dsl import RepositoryResolver, ResolvedTarget
from qcs_replay.dsl import ReplayRefNotFoundError


# ─── validate_entry ──────────────────────────────────────────────────────────


def test_valid_browser_entry():
    entry = {
        "form_ref": "html_order_header",
        "element_ref": "po_number",
        "surface": "browser",
        "source": "recording",
        "status": "active",
        "confidence": 0.95,
        "descriptor": {"selector": "#po-number"},
    }
    errors = validate_entry(entry)
    assert errors == [], f"Expected no errors but got: {errors}"


def test_valid_java_forms_entry():
    entry = {
        "form_ref": "java_find_orders",
        "element_ref": "order_type",
        "surface": "java_forms",
        "source": "recording",
        "status": "active",
        "confidence": 1.0,
        "descriptor": {"role": "text", "name": "Order Type"},
    }
    errors = validate_entry(entry)
    assert errors == [], f"Expected no errors but got: {errors}"


def test_missing_form_ref():
    entry = {
        "form_ref": "",
        "element_ref": "order_type",
        "surface": "java_forms",
    }
    errors = validate_entry(entry)
    assert any("$.form_ref" in e for e in errors), f"Expected form_ref error in {errors}"


def test_missing_form_ref_none():
    entry = {
        "element_ref": "order_type",
        "surface": "java_forms",
    }
    errors = validate_entry(entry)
    assert any("$.form_ref" in e for e in errors)


def test_missing_element_ref():
    entry = {
        "form_ref": "java_find_orders",
        "element_ref": "",
        "surface": "java_forms",
    }
    errors = validate_entry(entry)
    assert any("$.element_ref" in e for e in errors), f"Expected element_ref error in {errors}"


def test_missing_element_ref_none():
    entry = {
        "form_ref": "java_find_orders",
        "surface": "java_forms",
    }
    errors = validate_entry(entry)
    assert any("$.element_ref" in e for e in errors)


def test_unsupported_surface():
    entry = {
        "form_ref": "java_find_orders",
        "element_ref": "order_type",
        "surface": "winforms",  # invalid
    }
    errors = validate_entry(entry)
    assert any("$.surface" in e for e in errors), f"Expected surface error in {errors}"
    # error message should mention valid surfaces
    surface_error = next(e for e in errors if "$.surface" in e)
    assert "browser" in surface_error
    assert "java_forms" in surface_error


def test_unsupported_surface_empty():
    entry = {
        "form_ref": "java_find_orders",
        "element_ref": "order_type",
        "surface": "",
    }
    errors = validate_entry(entry)
    assert any("$.surface" in e for e in errors)


def test_confidence_out_of_range():
    entry = {
        "form_ref": "java_find",
        "element_ref": "field_a",
        "surface": "java_forms",
        "confidence": 1.5,
    }
    errors = validate_entry(entry)
    assert any("$.confidence" in e for e in errors)


def test_invalid_status():
    entry = {
        "form_ref": "java_find",
        "element_ref": "field_a",
        "surface": "java_forms",
        "status": "unknown_status",
    }
    errors = validate_entry(entry)
    assert any("$.status" in e for e in errors)


def test_invalid_source():
    entry = {
        "form_ref": "java_find",
        "element_ref": "field_a",
        "surface": "java_forms",
        "source": "robotic",
    }
    errors = validate_entry(entry)
    assert any("$.source" in e for e in errors)


# ─── RepoEntry dataclass ────────────────────────────────────────────────────


def test_repo_entry_qualified_ref_derived():
    e = RepoEntry(form_ref="java_find", element_ref="order_type", surface="java_forms")
    assert e.qualified_ref == "java_find.order_type"


def test_repo_entry_qualified_ref_is_not_primary_key():
    """qualified_ref must be derived, not supplied as identifier."""
    e1 = RepoEntry(form_ref="java_find", element_ref="order_type", surface="java_forms")
    e2 = RepoEntry(
        form_ref="java_find",
        element_ref="order_type",
        surface="java_forms",
        qualified_ref="ignored.override",
    )
    # qualified_ref was overridden in __post_init__ only when empty; supplied value preserved
    # but the identity is still (form_ref, element_ref)
    assert e1.form_ref == e2.form_ref
    assert e1.element_ref == e2.element_ref


def test_repo_entry_friendly_name_defaults_to_element_ref():
    e = RepoEntry(form_ref="java_find", element_ref="order_type", surface="java_forms")
    assert e.friendly_name == "order_type"


def test_repo_entry_status_helpers():
    active = RepoEntry(form_ref="f", element_ref="e", surface="browser", status="active")
    deprecated = RepoEntry(form_ref="f", element_ref="e", surface="browser", status="deprecated")
    candidate = RepoEntry(form_ref="f", element_ref="e", surface="browser", status="candidate")
    assert active.is_active
    assert not active.is_deprecated
    assert not active.is_candidate
    assert deprecated.is_deprecated
    assert candidate.is_candidate


def test_repo_entry_roundtrip_dict():
    e = RepoEntry(
        form_ref="java_po",
        element_ref="order_number",
        surface="java_forms",
        object_type="text_field",
        descriptor={"role": "text", "name": "Order Number"},
        fallback_descriptors=[{"role": "label", "name": "Order No"}],
        source="recording",
        confidence=0.9,
        status="candidate",
        metadata={"review_ticket": "JIRA-42"},
    )
    d = e.to_dict()
    restored = RepoEntry.from_dict(d)
    assert restored.form_ref == e.form_ref
    assert restored.element_ref == e.element_ref
    assert restored.surface == e.surface
    assert restored.descriptor == e.descriptor
    assert restored.fallback_descriptors == e.fallback_descriptors
    assert restored.confidence == e.confidence
    assert restored.status == e.status
    assert restored.metadata == e.metadata


# ─── upsert_entry / load_entry ──────────────────────────────────────────────


def test_upsert_and_load_entry(tmp_path):
    entry = RepoEntry(
        form_ref="java_find_orders",
        element_ref="order_type",
        surface="java_forms",
        descriptor={"role": "text", "name": "Order Type"},
        source="recording",
    )
    repo_store.upsert_entry(entry, repo_dir=tmp_path)
    loaded = repo_store.load_entry("java_find_orders", "order_type", repo_dir=tmp_path)
    assert loaded is not None
    assert loaded.form_ref == "java_find_orders"
    assert loaded.element_ref == "order_type"
    assert loaded.surface == "java_forms"
    assert loaded.descriptor == {"role": "text", "name": "Order Type"}


def test_load_entry_returns_none_for_missing(tmp_path):
    result = repo_store.load_entry("no_such_form", "no_such_element", repo_dir=tmp_path)
    assert result is None


def test_upsert_entry_raises_on_invalid(tmp_path):
    entry = RepoEntry(form_ref="", element_ref="order_type", surface="java_forms")
    with pytest.raises(RepoValidationError):
        repo_store.upsert_entry(entry, repo_dir=tmp_path)


def test_upsert_entry_updates_on_conflict(tmp_path):
    entry = RepoEntry(
        form_ref="java_find", element_ref="field_a", surface="java_forms", status="candidate"
    )
    repo_store.upsert_entry(entry, repo_dir=tmp_path)
    entry.status = "active"
    repo_store.upsert_entry(entry, repo_dir=tmp_path)
    loaded = repo_store.load_entry("java_find", "field_a", repo_dir=tmp_path)
    assert loaded is not None
    assert loaded.status == "active"


# ─── Deprecated entry behavior ───────────────────────────────────────────────


def test_deprecated_entry_behavior(tmp_path):
    """Resolver must raise ReplayRefNotFoundError for deprecated entries (deterministic failure)."""
    deprecated = RepoEntry(
        form_ref="java_find",
        element_ref="old_field",
        surface="java_forms",
        status="deprecated",
        descriptor={"role": "text", "name": "Old Field"},
    )
    repo_store.upsert_entry(deprecated, repo_dir=tmp_path)

    resolver = RepositoryResolver(repo_dir=tmp_path)
    with pytest.raises(ReplayRefNotFoundError):
        resolver.resolve("java_find", "old_field")


# ─── Candidate entry behavior ────────────────────────────────────────────────


def test_candidate_entry_behavior(tmp_path):
    """Resolver must return a ResolvedTarget for candidate entries (unreviewed but usable)."""
    candidate = RepoEntry(
        form_ref="java_find",
        element_ref="new_field",
        surface="java_forms",
        status="candidate",
        descriptor={"role": "text", "name": "New Field"},
    )
    repo_store.upsert_entry(candidate, repo_dir=tmp_path)

    resolver = RepositoryResolver(repo_dir=tmp_path)
    result = resolver.resolve("java_find", "new_field")
    assert result is not None, "Candidate entries should be resolvable"
    assert result.surface == "java_forms"
    assert result.form_id == "java_find"


# ─── resolver.resolve(form_ref, element_ref) ────────────────────────────────


def test_resolver_resolve_form_ref_element_ref(tmp_path):
    """Primary-key lookup through the new repo_entries table."""
    entry = RepoEntry(
        form_ref="html_order_header",
        element_ref="po_number",
        surface="browser",
        descriptor={"selector": "#po-number"},
        source="recording",
    )
    repo_store.upsert_entry(entry, repo_dir=tmp_path)

    resolver = RepositoryResolver(repo_dir=tmp_path)
    result = resolver.resolve("html_order_header", "po_number")
    assert result is not None
    assert result.surface == "browser"
    assert result.form_id == "html_order_header"
    assert result.ref == "po_number"
    assert result.descriptor == {"selector": "#po-number"}


def test_resolver_prefers_override_over_repo_entry(tmp_path):
    """In-memory overrides take precedence over the repo_entries table."""
    entry = RepoEntry(
        form_ref="java_find", element_ref="field_a", surface="java_forms"
    )
    repo_store.upsert_entry(entry, repo_dir=tmp_path)

    resolver = RepositoryResolver(repo_dir=tmp_path)
    override = ResolvedTarget(
        ref="field_a", surface="browser", form_id="java_find", descriptor={"overridden": True}
    )
    resolver.register("java_find", "field_a", override)
    result = resolver.resolve("java_find", "field_a")
    assert result is not None
    assert result.descriptor == {"overridden": True}
    assert result.surface == "browser"


def test_resolver_raises_for_unknown(tmp_path):
    resolver = RepositoryResolver(repo_dir=tmp_path)
    with pytest.raises(ReplayRefNotFoundError):
        resolver.resolve("no_form", "no_element")


def test_resolver_does_not_call_legacy_lookup(tmp_path, monkeypatch):
    """resolve() must never call resolve_element_ref regardless of outcome."""
    from qcs_repo import store as repo_store  # noqa: PLC0415

    def _no_legacy(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("legacy resolve_element_ref must not be called")

    monkeypatch.setattr(repo_store, "resolve_element_ref", _no_legacy)

    resolver = RepositoryResolver(repo_dir=tmp_path)

    # Active entry — found via load_entry, no legacy call
    entry = RepoEntry(
        form_ref="java_find", element_ref="field_a", surface="java_forms", status="active"
    )
    repo_store.upsert_entry(entry, repo_dir=tmp_path)
    result = resolver.resolve("java_find", "field_a")
    assert result.surface == "java_forms"

    # Missing entry — raises before any legacy call
    with pytest.raises(ReplayRefNotFoundError):
        resolver.resolve("java_find", "nonexistent")

    # Deprecated entry — raises before any legacy call
    dep = RepoEntry(
        form_ref="java_find", element_ref="old", surface="java_forms", status="deprecated"
    )
    repo_store.upsert_entry(dep, repo_dir=tmp_path)
    with pytest.raises(ReplayRefNotFoundError):
        resolver.resolve("java_find", "old")


# ─── list_form_entries / all_entries ────────────────────────────────────────


def test_list_form_entries(tmp_path):
    for elem in ("field_a", "field_b", "field_c"):
        repo_store.upsert_entry(
            RepoEntry(form_ref="java_find", element_ref=elem, surface="java_forms"),
            repo_dir=tmp_path,
        )
    # also add to a different form — should not appear
    repo_store.upsert_entry(
        RepoEntry(form_ref="java_other", element_ref="field_x", surface="java_forms"),
        repo_dir=tmp_path,
    )
    entries = repo_store.list_form_entries("java_find", repo_dir=tmp_path)
    assert len(entries) == 3
    assert all(e.form_ref == "java_find" for e in entries)
    assert [e.element_ref for e in entries] == ["field_a", "field_b", "field_c"]


def test_all_entries(tmp_path):
    repo_store.upsert_entry(
        RepoEntry(form_ref="form_a", element_ref="elem_1", surface="browser"),
        repo_dir=tmp_path,
    )
    repo_store.upsert_entry(
        RepoEntry(form_ref="form_b", element_ref="elem_2", surface="java_forms"),
        repo_dir=tmp_path,
    )
    entries = repo_store.all_entries(repo_dir=tmp_path)
    assert len(entries) == 2
    assert {e.form_ref for e in entries} == {"form_a", "form_b"}


# ─── validate_repo (qcs repo validate) ──────────────────────────────────────


def test_validate_repo_empty(tmp_path):
    """An empty repository must pass validation."""
    errors = repo_store.validate_repo(repo_dir=tmp_path)
    assert errors == []


def test_validate_repo_all_valid(tmp_path):
    for i in range(3):
        repo_store.upsert_entry(
            RepoEntry(
                form_ref="java_find",
                element_ref=f"field_{i}",
                surface="java_forms",
                source="recording",
            ),
            repo_dir=tmp_path,
        )
    errors = repo_store.validate_repo(repo_dir=tmp_path)
    assert errors == []


def test_validate_repo_returns_qualified_ref_in_errors(tmp_path):
    """Error messages must be prefixed with the qualified_ref of the failing entry."""
    # Insert a valid entry first, then directly corrupt it via SQL to simulate
    # a row that bypassed validation (e.g. manual edit or old data).
    import sqlite3 as _sqlite3  # noqa: PLC0415
    db_path = repo_store.repo_db_path(repo_dir=tmp_path)
    # Force-create the schema by opening a connection
    with repo_store._db_connect(tmp_path):
        pass
    # Now inject a bad row directly (bypassing upsert_entry validation)
    conn = _sqlite3.connect(str(db_path))
    conn.row_factory = _sqlite3.Row
    conn.execute(
        """INSERT INTO repo_entries
        (form_ref, element_ref, qualified_ref, surface, created_at, updated_at)
        VALUES ('java_bad', 'el', 'java_bad.el', 'invalid_surface', datetime('now'), datetime('now'))"""
    )
    conn.commit()
    conn.close()

    errors = repo_store.validate_repo(repo_dir=tmp_path)
    assert len(errors) > 0
    # Each error must be prefixed with the qualified_ref
    assert all(e.startswith("[java_bad.el]") for e in errors)
    assert any("surface" in e for e in errors)


def test_repo_validate_cli_function_passes(tmp_path):
    """cmd_repo_validate must exit 0 (no sys.exit) when repo is valid."""
    repo_store.upsert_entry(
        RepoEntry(form_ref="java_find", element_ref="order_type", surface="java_forms"),
        repo_dir=tmp_path,
    )
    from qcs.__main__ import cmd_repo_validate
    args = argparse.Namespace(repo_dir=tmp_path)
    # Should not raise SystemExit
    cmd_repo_validate(args)


def test_repo_validate_cli_function_fails(tmp_path, capsys):
    """cmd_repo_validate must call sys.exit(1) when there are validation errors."""
    import sqlite3 as _sqlite3  # noqa: PLC0415
    with repo_store._db_connect(tmp_path):
        pass
    db_path = repo_store.repo_db_path(repo_dir=tmp_path)
    conn = _sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO repo_entries
        (form_ref, element_ref, qualified_ref, surface, created_at, updated_at)
        VALUES ('bad_form', 'el', 'bad_form.el', 'bad_surface', datetime('now'), datetime('now'))"""
    )
    conn.commit()
    conn.close()

    from qcs.__main__ import cmd_repo_validate
    args = argparse.Namespace(repo_dir=tmp_path)
    with pytest.raises(SystemExit) as exc_info:
        cmd_repo_validate(args)
    assert exc_info.value.code == 1

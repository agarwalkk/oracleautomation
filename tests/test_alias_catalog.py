"""Tests for generator.alias_catalog — loading, validation, and conflict reporting.

All tests use tmp_path fixtures to avoid touching the real catalog directory.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from generator.alias_catalog import (
    AliasCatalog,
    CatalogConflict,
    ElementAlias,
    FormAliasFile,
)
from generator.naming import AliasResolver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_form_file(
    directory: Path,
    form_ref: str,
    *,
    display_name: str = "Test Form",
    domain: str = "test_domain",
    surface: str = "java",
    technical_forms: list[str] | None = None,
    elements: list[dict] | None = None,
) -> Path:
    """Write a minimal valid alias JSON file to *directory*/<form_ref>.json."""
    directory.mkdir(parents=True, exist_ok=True)
    data = {
        "form_ref": form_ref,
        "display_name": display_name,
        "domain": domain,
        "surface": surface,
        "technical_forms": technical_forms or [f"java_{form_ref}"],
        "elements": elements or [],
    }
    path = directory / f"{form_ref}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _valid_element(
    technical_name: str = "some_field",
    element_ref: str = "field",
    display_name: str = "Some Field",
    object_type: str = "text_field",
    review_status: str = "reviewed",
) -> dict:
    return {
        "technical_name": technical_name,
        "element_ref": element_ref,
        "display_name": display_name,
        "object_type": object_type,
        "review_status": review_status,
    }


# ---------------------------------------------------------------------------
# AliasCatalog.load
# ---------------------------------------------------------------------------


class TestAliasCatalogLoad:
    """Loading alias files from a directory tree."""

    def test_loads_single_file(self, tmp_path: Path) -> None:
        _write_form_file(tmp_path / "domain_a", "my_form")
        catalog = AliasCatalog.load(tmp_path)
        assert len(catalog.form_files) == 1
        assert catalog.form_files[0].form_ref == "my_form"

    def test_loads_multiple_files_across_domains(self, tmp_path: Path) -> None:
        _write_form_file(tmp_path / "order_management", "order_form")
        _write_form_file(tmp_path / "purchasing", "po_form")
        _write_form_file(tmp_path / "common", "confirm")
        catalog = AliasCatalog.load(tmp_path)
        assert len(catalog.form_files) == 3
        refs = {f.form_ref for f in catalog.form_files}
        assert refs == {"order_form", "po_form", "confirm"}

    def test_empty_directory_returns_empty_catalog(self, tmp_path: Path) -> None:
        catalog = AliasCatalog.load(tmp_path)
        assert catalog.form_files == []

    def test_nonexistent_directory_returns_empty_catalog(self, tmp_path: Path) -> None:
        catalog = AliasCatalog.load(tmp_path / "nonexistent")
        assert catalog.form_files == []

    def test_invalid_json_file_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "bad.json").write_text("{not valid json", encoding="utf-8")
        _write_form_file(tmp_path, "good_form")
        catalog = AliasCatalog.load(tmp_path)
        # Only the valid file is loaded
        assert len(catalog.form_files) == 1

    def test_source_file_path_stored(self, tmp_path: Path) -> None:
        path = _write_form_file(tmp_path, "my_form")
        catalog = AliasCatalog.load(tmp_path)
        assert catalog.form_files[0].source_file == path

    def test_elements_loaded_correctly(self, tmp_path: Path) -> None:
        elem = _valid_element("java_field", "field", "Field", "text_field")
        _write_form_file(tmp_path, "my_form", elements=[elem])
        catalog = AliasCatalog.load(tmp_path)
        form = catalog.form_files[0]
        assert len(form.elements) == 1
        e = form.elements[0]
        assert e.technical_name == "java_field"
        assert e.element_ref == "field"

    def test_technical_forms_list_loaded(self, tmp_path: Path) -> None:
        _write_form_file(
            tmp_path, "my_form", technical_forms=["java_foo", "java_bar"]
        )
        catalog = AliasCatalog.load(tmp_path)
        assert catalog.form_files[0].technical_forms == ["java_foo", "java_bar"]


# ---------------------------------------------------------------------------
# AliasCatalog.lookup_form / lookup_element
# ---------------------------------------------------------------------------


class TestAliasCatalogLookup:
    """lookup_form and lookup_element queries."""

    def test_lookup_form_returns_correct_file(self, tmp_path: Path) -> None:
        _write_form_file(tmp_path, "order_form", technical_forms=["java_order"])
        catalog = AliasCatalog.load(tmp_path)
        result = catalog.lookup_form("java_order")
        assert result is not None
        assert result.form_ref == "order_form"

    def test_lookup_form_returns_none_for_unknown(self, tmp_path: Path) -> None:
        _write_form_file(tmp_path, "order_form", technical_forms=["java_order"])
        catalog = AliasCatalog.load(tmp_path)
        assert catalog.lookup_form("java_unknown") is None

    def test_lookup_element_returns_correct_alias(self, tmp_path: Path) -> None:
        elem = _valid_element("java_fld", "field", "My Field", "text_field")
        _write_form_file(
            tmp_path, "order_form", technical_forms=["java_order"], elements=[elem]
        )
        catalog = AliasCatalog.load(tmp_path)
        result = catalog.lookup_element("java_order", "java_fld")
        assert result is not None
        assert result.element_ref == "field"

    def test_lookup_element_returns_none_for_unknown(self, tmp_path: Path) -> None:
        _write_form_file(tmp_path, "order_form", technical_forms=["java_order"])
        catalog = AliasCatalog.load(tmp_path)
        assert catalog.lookup_element("java_order", "no_such_element") is None

    def test_one_technical_form_maps_to_multiple_elements(self, tmp_path: Path) -> None:
        elems = [
            _valid_element("fld_a", "field_a", "Field A", "text_field"),
            _valid_element("fld_b", "field_b", "Field B", "button"),
        ]
        _write_form_file(
            tmp_path, "order_form", technical_forms=["java_order"], elements=elems
        )
        catalog = AliasCatalog.load(tmp_path)
        assert catalog.lookup_element("java_order", "fld_a") is not None
        assert catalog.lookup_element("java_order", "fld_b") is not None


# ---------------------------------------------------------------------------
# AliasCatalog.validate — duplicate detection
# ---------------------------------------------------------------------------


class TestAliasCatalogValidation:
    """validate() conflict detection."""

    def test_no_conflicts_for_clean_catalog(self, tmp_path: Path) -> None:
        elem = _valid_element("java_fld", "field", "My Field", "text_field")
        _write_form_file(tmp_path, "my_form", elements=[elem])
        catalog = AliasCatalog.load(tmp_path)
        assert catalog.validate() == []

    def test_detects_duplicate_form_ref(self, tmp_path: Path) -> None:
        # Two files in different domains with the same form_ref
        _write_form_file(tmp_path / "domain_a", "shared_form")
        _write_form_file(tmp_path / "domain_b", "shared_form")
        catalog = AliasCatalog.load(tmp_path)
        conflicts = catalog.validate()
        types = [c.conflict_type for c in conflicts]
        assert "duplicate_form_ref" in types

    def test_detects_duplicate_element_ref_within_form(self, tmp_path: Path) -> None:
        elems = [
            _valid_element("tech_a", "same_ref", "Element A", "text_field"),
            _valid_element("tech_b", "same_ref", "Element B", "button"),
        ]
        _write_form_file(tmp_path, "my_form", elements=elems)
        catalog = AliasCatalog.load(tmp_path)
        conflicts = catalog.validate()
        types = [c.conflict_type for c in conflicts]
        assert "duplicate_element_ref" in types

    def test_detects_duplicate_technical_name_within_form(self, tmp_path: Path) -> None:
        elems = [
            _valid_element("same_tech", "ref_a", "Element A", "text_field"),
            _valid_element("same_tech", "ref_b", "Element B", "button"),
        ]
        _write_form_file(tmp_path, "my_form", elements=elems)
        catalog = AliasCatalog.load(tmp_path)
        conflicts = catalog.validate()
        types = [c.conflict_type for c in conflicts]
        assert "duplicate_technical_name" in types

    def test_duplicate_element_ref_only_within_same_form(self, tmp_path: Path) -> None:
        """Same element_ref in *different* forms must NOT be flagged."""
        elem = _valid_element("tech_x", "shared_ref", "X", "text_field")
        _write_form_file(tmp_path / "d1", "form_one", elements=[elem])
        _write_form_file(tmp_path / "d2", "form_two", elements=[elem])
        catalog = AliasCatalog.load(tmp_path)
        conflicts = catalog.validate()
        types = [c.conflict_type for c in conflicts]
        assert "duplicate_element_ref" not in types

    def test_detects_technical_form_ref(self, tmp_path: Path) -> None:
        """form_ref must not match technical patterns like java_*."""
        (tmp_path / "bad.json").write_text(
            json.dumps({
                "form_ref": "java_bad_form",
                "display_name": "Bad",
                "domain": "d",
                "surface": "java",
                "technical_forms": ["java_bad_form"],
                "elements": [],
            }),
            encoding="utf-8",
        )
        catalog = AliasCatalog.load(tmp_path)
        conflicts = catalog.validate()
        types = [c.conflict_type for c in conflicts]
        assert "technical_business_ref" in types

    def test_detects_technical_element_ref(self, tmp_path: Path) -> None:
        """element_ref must not match technical patterns like _alt_X."""
        elem = _valid_element("find_alt_i", "find_alt_i", "Find", "button")
        _write_form_file(tmp_path, "my_form", elements=[elem])
        catalog = AliasCatalog.load(tmp_path)
        conflicts = catalog.validate()
        types = [c.conflict_type for c in conflicts]
        assert "technical_business_ref" in types

    def test_detects_missing_required_form_field(self, tmp_path: Path) -> None:
        data = {
            # form_ref missing
            "display_name": "My Form",
            "domain": "d",
            "surface": "java",
            "technical_forms": ["java_x"],
            "elements": [],
        }
        (tmp_path / "no_ref.json").write_text(json.dumps(data), encoding="utf-8")
        catalog = AliasCatalog.load(tmp_path)
        conflicts = catalog.validate()
        types = [c.conflict_type for c in conflicts]
        assert "missing_required_field" in types

    def test_detects_missing_required_element_field(self, tmp_path: Path) -> None:
        elem = {
            "technical_name": "fld",
            # element_ref missing
            "display_name": "Field",
            "object_type": "text_field",
        }
        _write_form_file(tmp_path, "my_form", elements=[elem])
        catalog = AliasCatalog.load(tmp_path)
        conflicts = catalog.validate()
        types = [c.conflict_type for c in conflicts]
        assert "missing_required_field" in types

    def test_detects_alias_review_pending(self, tmp_path: Path) -> None:
        elem = _valid_element("fld", "field", "Field", "text_field", "needs_review")
        _write_form_file(tmp_path, "my_form", elements=[elem])
        catalog = AliasCatalog.load(tmp_path)
        conflicts = catalog.validate()
        types = [c.conflict_type for c in conflicts]
        assert "alias_review_pending" in types

    def test_reviewed_elements_not_flagged(self, tmp_path: Path) -> None:
        elem = _valid_element("fld", "field", "Field", "text_field", "reviewed")
        _write_form_file(tmp_path, "my_form", elements=[elem])
        catalog = AliasCatalog.load(tmp_path)
        assert catalog.validate() == []


# ---------------------------------------------------------------------------
# AliasCatalog.report
# ---------------------------------------------------------------------------


class TestAliasCatalogReport:
    """report() output content."""

    def test_report_contains_stats(self, tmp_path: Path) -> None:
        _write_form_file(tmp_path, "my_form")
        catalog = AliasCatalog.load(tmp_path)
        report = catalog.report()
        assert "1 business form" in report

    def test_report_no_conflicts_message(self, tmp_path: Path) -> None:
        _write_form_file(tmp_path, "my_form")
        catalog = AliasCatalog.load(tmp_path)
        report = catalog.report()
        assert "No conflicts" in report

    def test_report_lists_conflict_type(self, tmp_path: Path) -> None:
        _write_form_file(tmp_path / "d1", "same_form")
        _write_form_file(tmp_path / "d2", "same_form")
        catalog = AliasCatalog.load(tmp_path)
        report = catalog.report()
        assert "duplicate_form_ref" in report

    def test_report_lists_domain(self, tmp_path: Path) -> None:
        _write_form_file(tmp_path, "my_form", domain="purchasing")
        catalog = AliasCatalog.load(tmp_path)
        report = catalog.report()
        assert "purchasing" in report


# ---------------------------------------------------------------------------
# AliasResolver — catalog integration
# ---------------------------------------------------------------------------


class TestAliasResolverCatalogIntegration:
    """AliasResolver using catalog_dir argument."""

    def test_resolves_form_from_catalog(self, tmp_path: Path) -> None:
        _write_form_file(tmp_path, "order_form", technical_forms=["java_order"])
        resolver = AliasResolver(catalog_dir=tmp_path)
        result = resolver.resolve_form("java_order")
        assert result.ref == "order_form"
        assert result.source == "alias"
        assert result.needs_alias_review is False

    def test_resolves_element_from_catalog(self, tmp_path: Path) -> None:
        elem = _valid_element("java_fld", "field", "My Field", "text_field")
        _write_form_file(
            tmp_path, "order_form", technical_forms=["java_order"], elements=[elem]
        )
        resolver = AliasResolver(catalog_dir=tmp_path)
        result = resolver.resolve_element("java_order", "java_fld")
        assert result.ref == "field"
        assert result.source == "alias"

    def test_sanitiser_fallback_when_no_catalog_match(self, tmp_path: Path) -> None:
        _write_form_file(tmp_path, "order_form", technical_forms=["java_order"])
        resolver = AliasResolver(catalog_dir=tmp_path)
        # This technical ref is NOT in the catalog
        result = resolver.resolve_form("java_unknown_form")
        assert result.source == "sanitized"
        assert result.needs_alias_review is True

    def test_reverse_maps_populated_from_catalog(self, tmp_path: Path) -> None:
        _write_form_file(tmp_path, "order_form", technical_forms=["java_order"])
        resolver = AliasResolver(catalog_dir=tmp_path)
        assert resolver.forms_reverse.get("order_form") == "java_order"

    def test_catalog_dir_takes_precedence_over_alias_file(self, tmp_path: Path) -> None:
        """When both catalog_dir and alias_file are provided, catalog_dir wins."""
        catalog_dir = tmp_path / "catalog"
        _write_form_file(catalog_dir, "catalog_form", technical_forms=["java_cat"])

        alias_file = tmp_path / "aliases.json"
        alias_file.write_text(
            json.dumps({"forms": {"java_cat": {"business_ref": "legacy_form"}}, "elements": {}}),
            encoding="utf-8",
        )
        # catalog_dir takes priority
        resolver = AliasResolver(catalog_dir=catalog_dir)
        result = resolver.resolve_form("java_cat")
        assert result.ref == "catalog_form"


# ---------------------------------------------------------------------------
# AliasCatalog.to_flat_forms / to_flat_elements
# ---------------------------------------------------------------------------


class TestFlatConversion:
    """to_flat_forms and to_flat_elements converters."""

    def test_flat_forms_keyed_by_technical_ref(self, tmp_path: Path) -> None:
        _write_form_file(tmp_path, "order_form", technical_forms=["java_order"])
        catalog = AliasCatalog.load(tmp_path)
        flat = catalog.to_flat_forms()
        assert "java_order" in flat
        assert flat["java_order"]["business_ref"] == "order_form"
        assert flat["java_order"]["confidence"] == 1.0

    def test_flat_forms_one_entry_per_technical_form(self, tmp_path: Path) -> None:
        _write_form_file(
            tmp_path, "multi_form", technical_forms=["java_one", "java_two"]
        )
        catalog = AliasCatalog.load(tmp_path)
        flat = catalog.to_flat_forms()
        assert "java_one" in flat
        assert "java_two" in flat
        assert flat["java_one"]["business_ref"] == "multi_form"

    def test_flat_elements_keyed_by_tech_form_dot_tech_elem(self, tmp_path: Path) -> None:
        elem = _valid_element("java_fld", "field", "Field", "text_field")
        _write_form_file(
            tmp_path, "order_form", technical_forms=["java_order"], elements=[elem]
        )
        catalog = AliasCatalog.load(tmp_path)
        flat = catalog.to_flat_elements()
        assert "java_order.java_fld" in flat
        assert flat["java_order.java_fld"]["business_ref"] == "field"


# ---------------------------------------------------------------------------
# Real catalog sanity check
# ---------------------------------------------------------------------------


class TestRealCatalog:
    """Smoke-test the real config/aliases catalog that ships with the project."""

    @pytest.fixture
    def real_catalog(self) -> AliasCatalog:
        import config
        return AliasCatalog.load(config.BUSINESS_ALIASES_DIR)

    def test_real_catalog_loads_without_error(self, real_catalog: AliasCatalog) -> None:
        assert len(real_catalog.form_files) > 0

    def test_real_catalog_has_no_conflicts(self, real_catalog: AliasCatalog) -> None:
        conflicts = real_catalog.validate()
        errors = [c for c in conflicts if c.conflict_type != "alias_review_pending"]
        assert errors == [], "\n".join(str(c) for c in errors)

    def test_real_catalog_rec013_forms_resolvable(self, real_catalog: AliasCatalog) -> None:
        """All rec_013 technical form refs must be present in the real catalog."""
        expected = {
            "java_order_typelist_of_values": "order_type_lov",
            "java_yes_alt_y": "confirm_dialog",
            "java_find_alt_i": "find_orders",
            "java_summary_tab_page_order_number": "order_summary",
            "java_po": "purchase_order",
            "java_po_datelist_of_values": "po_date_lov",
        }
        for tech_ref, expected_biz in expected.items():
            form_file = real_catalog.lookup_form(tech_ref)
            assert form_file is not None, f"Technical form ref not in catalog: {tech_ref!r}"
            assert form_file.form_ref == expected_biz, (
                f"{tech_ref!r} resolved to {form_file.form_ref!r}, expected {expected_biz!r}"
            )

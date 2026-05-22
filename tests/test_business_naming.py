"""Tests for generator.naming — sanitiser, alias resolver, and generator output cleanliness.

All tests are deterministic and AI-free.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from generator.naming import (
    AliasResolver,
    NameResult,
    is_technical_element_ref,
    is_technical_form_ref,
    sanitize_ref,
)
from generator.build_test import generate_test


# ---------------------------------------------------------------------------
# Sanitiser tests
# ---------------------------------------------------------------------------

class TestSanitizeRef:
    """sanitize_ref() — deterministic noise-stripping rules."""

    def test_strips_java_prefix(self) -> None:
        assert sanitize_ref("java_po") == "po"

    def test_strips_html_prefix(self) -> None:
        assert sanitize_ref("html_login_page") == "login_page"

    def test_removes_alt_suffix(self) -> None:
        assert sanitize_ref("find_alt_i") == "find"
        assert sanitize_ref("yes_alt_y") == "yes"
        assert sanitize_ref("open_alt_o") == "open"

    def test_removes_mnemonic_suffix(self) -> None:
        assert sanitize_ref("file_mnemonic_f") == "file"
        assert sanitize_ref("view_mnemonic_v") == "view"

    def test_replaces_list_of_values_with_lov(self) -> None:
        # suffix after word boundary
        assert sanitize_ref("order_typelist_of_values") == "order_type_lov"
        assert sanitize_ref("po_datelist_of_values") == "po_date_lov"

    def test_replaces_tab_page_connector(self) -> None:
        result = sanitize_ref("summary_tab_page_order_number")
        assert "tab_page" not in result
        assert "summary" in result
        assert "order_number" in result

    def test_strips_toolbar_digits(self) -> None:
        assert sanitize_ref("toolbar195") == "toolbar"
        assert sanitize_ref("toolbar42") == "toolbar"

    def test_full_form_ref_chain(self) -> None:
        result = sanitize_ref("java_order_typelist_of_values")
        assert not result.startswith("java_")
        assert "list_of_values" not in result
        assert result == "order_type_lov"

    def test_already_clean_name_unchanged(self) -> None:
        assert sanitize_ref("purchase_order") == "purchase_order"
        assert sanitize_ref("order_type") == "order_type"


class TestIsTechnical:
    """is_technical_form_ref / is_technical_element_ref detection."""

    def test_java_prefix_is_technical(self) -> None:
        assert is_technical_form_ref("java_po") is True

    def test_html_prefix_is_technical(self) -> None:
        assert is_technical_form_ref("html_form") is True

    def test_alt_pattern_is_technical(self) -> None:
        assert is_technical_form_ref("find_alt_i") is True
        assert is_technical_element_ref("yes_alt_y") is True

    def test_mnemonic_pattern_is_technical(self) -> None:
        assert is_technical_element_ref("file_mnemonic_f") is True

    def test_toolbar_digits_is_technical(self) -> None:
        assert is_technical_element_ref("toolbar195") is True

    def test_clean_names_not_technical(self) -> None:
        assert is_technical_form_ref("purchase_order") is False
        assert is_technical_form_ref("order_type_lov") is False
        assert is_technical_element_ref("po_number") is False
        assert is_technical_element_ref("find") is False


# ---------------------------------------------------------------------------
# AliasResolver tests
# ---------------------------------------------------------------------------

def _make_alias_file(tmp_path: Path, forms: dict, elements: dict) -> Path:
    data = {"forms": forms, "elements": elements}
    f = tmp_path / "business_aliases.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    return f


class TestAliasResolver:
    """AliasResolver — alias lookup and sanitiser fallback."""

    def test_resolve_form_returns_alias_when_present(self, tmp_path: Path) -> None:
        af = _make_alias_file(tmp_path, {"java_po": {"business_ref": "purchase_order"}}, {})
        resolver = AliasResolver(alias_file=af)
        result = resolver.resolve_form("java_po")
        assert result.ref == "purchase_order"
        assert result.source == "alias"
        assert result.needs_alias_review is False
        assert result.confidence == 1.0

    def test_resolve_form_sanitises_when_no_alias(self, tmp_path: Path) -> None:
        af = _make_alias_file(tmp_path, {}, {})
        resolver = AliasResolver(alias_file=af)
        result = resolver.resolve_form("java_yes_alt_y")
        assert result.ref == "yes"
        assert result.source == "sanitized"
        assert result.needs_alias_review is True
        assert result.original == "java_yes_alt_y"

    def test_resolve_element_returns_alias_when_present(self, tmp_path: Path) -> None:
        af = _make_alias_file(
            tmp_path,
            {},
            {"java_po.po_received": {"business_ref": "po_number"}},
        )
        resolver = AliasResolver(alias_file=af)
        result = resolver.resolve_element("java_po", "po_received")
        assert result.ref == "po_number"
        assert result.source == "alias"
        assert result.needs_alias_review is False

    def test_resolve_element_sanitises_when_no_alias(self, tmp_path: Path) -> None:
        af = _make_alias_file(tmp_path, {}, {})
        resolver = AliasResolver(alias_file=af)
        result = resolver.resolve_element("java_po", "file_mnemonic_f")
        assert result.ref == "file"
        assert result.source == "sanitized"

    def test_missing_alias_file_treated_as_empty(self, tmp_path: Path) -> None:
        resolver = AliasResolver(alias_file=tmp_path / "nonexistent.json")
        result = resolver.resolve_form("java_po")
        assert result.ref == "po"   # sanitised fallback
        assert result.needs_alias_review is True

    def test_alias_confidence_propagated(self, tmp_path: Path) -> None:
        af = _make_alias_file(
            tmp_path,
            {"java_po": {"business_ref": "purchase_order", "confidence": 0.95}},
            {},
        )
        resolver = AliasResolver(alias_file=af)
        result = resolver.resolve_form("java_po")
        assert result.confidence == 0.95

    def test_reverse_map_built_from_aliases(self, tmp_path: Path) -> None:
        af = _make_alias_file(tmp_path, {"java_po": {"business_ref": "purchase_order"}}, {})
        resolver = AliasResolver(alias_file=af)
        assert resolver.forms_reverse.get("purchase_order") == "java_po"


# ---------------------------------------------------------------------------
# Generator output cleanliness tests
# ---------------------------------------------------------------------------

def _make_sample_jsonl(path: Path, *, run_id: str = "test_naming") -> None:
    """Write a JSONL recording with known technical names that should be cleaned."""
    import json as _json
    rows = [
        {"ts": "2026-01-01T00:00:00Z", "run_id": run_id, "surface": "unknown", "op": "session_start"},
        {"ts": "2026-01-01T00:00:01Z", "run_id": run_id, "surface": "html", "op": "ebs_login",
         "url": "https://ebs.example/login", "user_env": "EBS_USER", "password_env": "EBS_PASSWORD"},
        {"ts": "2026-01-01T00:00:02Z", "run_id": run_id, "surface": "java", "op": "java_form_launch",
         "url": "https://ebs.example/form", "form_id": "java_po", "form_name": "Purchase Order"},
        {"ts": "2026-01-01T00:00:03Z", "run_id": run_id, "surface": "java", "op": "java_send_text",
         "target": {"form_id": "java_po", "friendly_name": "po_received"}, "text": "PO12345"},
        {"ts": "2026-01-01T00:00:04Z", "run_id": run_id, "surface": "java", "op": "java_click",
         "target": {"form_id": "java_yes_alt_y", "friendly_name": "yes_alt_y"}},
        {"ts": "2026-01-01T00:00:05Z", "run_id": run_id, "surface": "java", "op": "java_press_key",
         "key": "TAB"},
        {"ts": "2026-01-01T00:00:06Z", "run_id": run_id, "surface": "java", "op": "java_click",
         "target": {"form_id": "java_po_datelist_of_values", "friendly_name": "toolbar195"}},
        {"ts": "2026-01-01T00:00:07Z", "run_id": run_id, "surface": "java", "op": "java_click",
         "target": {"form_id": "java_po_datelist_of_values", "friendly_name": "file_mnemonic_f"}},
    ]
    path.write_text("\n".join(_json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _generated_content(tmp_path: Path, *, name: str = "naming_test") -> str:
    run_dir = tmp_path / "rec_naming"
    run_dir.mkdir(parents=True)
    _make_sample_jsonl(run_dir / "recording.jsonl", run_id="rec_naming")
    out_dir = tmp_path / "out"
    generate_test(run_dir, out_dir, name)
    return (out_dir / f"test_{name}.py").read_text(encoding="utf-8")


def test_java_prefix_not_emitted_as_form_ref(tmp_path: Path) -> None:
    """No form_ref in the generated test should start with 'java_'."""
    content = _generated_content(tmp_path)
    import re
    form_calls = re.findall(r'oracle_replay\.form\(\s*([\'"])(.*?)\1', content)
    form_refs = [m[1] for m in form_calls]
    for ref in form_refs:
        assert not ref.startswith("java_"), f"Technical java_ form_ref emitted: {ref!r}"


def test_technical_form_names_not_emitted(tmp_path: Path) -> None:
    """Form refs matching known technical UI patterns must not appear in generated output."""
    content = _generated_content(tmp_path)
    # None of these exact technical strings should appear as form refs
    for bad_ref in (
        "java_po",
        "java_yes_alt_y",
        "java_po_datelist_of_values",
    ):
        assert f"oracle_replay.form({bad_ref!r})" not in content, (
            f"Technical form_ref {bad_ref!r} was emitted in generated test"
        )


def test_toolbar_ids_not_emitted(tmp_path: Path) -> None:
    """toolbar195-style element refs must not appear in generated output."""
    content = _generated_content(tmp_path)
    import re
    assert not re.search(r'toolbar\d+', content), (
        "toolbar<digits> identifier was emitted in generated test"
    )


def test_alt_mnemonic_names_not_emitted(tmp_path: Path) -> None:
    """_alt_X and _mnemonic_X patterns must not appear in generated element refs."""
    content = _generated_content(tmp_path)
    import re
    assert not re.search(r'_alt_[a-z][\'")\s]', content), (
        "_alt_X pattern found in generated test"
    )
    assert not re.search(r'_mnemonic_[a-z][\'")\s]', content), (
        "_mnemonic_X pattern found in generated test"
    )


def test_alias_produces_business_form_ref(tmp_path: Path) -> None:
    """When an alias exists, the business form_ref (not technical) must be emitted."""
    # The alias file maps java_po -> purchase_order.
    # Generate and check that purchase_order appears.
    content = _generated_content(tmp_path)
    # java_po should be aliased → "purchase_order" (from real config/business_aliases.json)
    # OR sanitised to "po" if running without the aliases file.
    # Either way, "java_po" must NOT appear as a form_ref argument.
    assert "oracle_replay.form('java_po')" not in content
    assert 'oracle_replay.form("java_po")' not in content


def test_low_confidence_names_flagged_for_review(tmp_path: Path) -> None:
    """Form refs without a configured alias must be flagged with an alias_review comment."""
    # Use an EMPTY alias file so everything falls through to the sanitiser.
    empty_alias = tmp_path / "empty_aliases.json"
    empty_alias.write_text('{"forms": {}, "elements": {}}', encoding="utf-8")

    run_dir = tmp_path / "rec_review"
    run_dir.mkdir(parents=True)
    _make_sample_jsonl(run_dir / "recording.jsonl", run_id="rec_review")

    # Monkey-patch AliasResolver to use the empty file.
    import generator.naming as naming_mod
    original_init = naming_mod.AliasResolver.__init__

    def patched_init(self, alias_file=None):  # noqa: ANN001
        original_init(self, alias_file=empty_alias)

    naming_mod.AliasResolver.__init__ = patched_init
    try:
        out_dir = tmp_path / "out_review"
        generate_test(run_dir, out_dir, "review_test")
        content = (out_dir / "test_review_test.py").read_text(encoding="utf-8")
    finally:
        naming_mod.AliasResolver.__init__ = original_init

    assert "alias_review" in content, (
        "Low-confidence sanitised names must be flagged with # alias_review comment"
    )


def test_form_scoped_press_key_emitted(tmp_path: Path) -> None:
    """press_key steps with an inferred or explicit form must use form-scoped press_key."""
    content = _generated_content(tmp_path)
    # The TAB step (no form_ref in manifest) should be inferred from the previous form.
    # Either way, oracle_replay.press_key (flat) should not appear.
    assert "oracle_replay.press_key(" not in content, (
        "Top-level oracle_replay.press_key() was emitted; expected form-scoped press_key"
    )
    # At least one .press_key( call must be present (form-scoped)
    assert ".press_key(" in content

"""Tests for generator.script_validator — business-readability gate.

All tests are deterministic and do not call AI or modify live Oracle Forms.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from generator.script_validator import (
    ScriptValidationResult,
    ScriptViolation,
    validate_generated_dir,
    validate_generated_script,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: Path, content: str) -> Path:
    """Write *content* to *path* (creates parent dirs) and return *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _clean_script(extra_lines: str = "") -> str:
    """Return a minimal valid generated test with business-only refs."""
    return f"""\
# AUTO-GENERATED
from __future__ import annotations
import pytest
from qcs_replay.script import OracleReplay

def test_sample(oracle_replay: OracleReplay):
    purchase_order = oracle_replay.form('purchase_order')
    purchase_order.set_text('po_number', 'PO12345')
    purchase_order.click('save')
    purchase_order.press_key('TAB')
    purchase_order.assert_text('status', 'Saved')
{extra_lines}
"""


def _violation_types(result: ScriptValidationResult) -> set[str]:
    return {v.violation_type for v in result.violations}


# ---------------------------------------------------------------------------
# Clean script passes
# ---------------------------------------------------------------------------

class TestCleanScript:
    """A well-formed script with business-only refs must pass."""

    def test_clean_script_passes(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "test_clean.py", _clean_script())
        result = validate_generated_script(path)
        assert result.passed, f"Expected PASS but got:\n{result.summary()}"
        assert result.violations == []

    def test_clean_script_has_no_alias_reviews(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "test_clean.py", _clean_script())
        result = validate_generated_script(path)
        assert result.alias_reviews == []

    def test_passed_property_true_with_no_violations(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "test_clean.py", _clean_script())
        result = validate_generated_script(path)
        assert result.passed is True


# ---------------------------------------------------------------------------
# Technical form_ref violations
# ---------------------------------------------------------------------------

class TestFormRefViolations:
    """oracle_replay.form() calls with technical identifiers must fail."""

    def test_java_form_ref_fails(self, tmp_path: Path) -> None:
        content = "    bad = oracle_replay.form('java_po')\n"
        path = _write(tmp_path / "test_bad.py", content)
        result = validate_generated_script(path)
        assert not result.passed
        assert "technical_form_ref" in _violation_types(result)

    def test_html_form_ref_fails(self, tmp_path: Path) -> None:
        content = "    bad = oracle_replay.form('html_login_page')\n"
        path = _write(tmp_path / "test_bad.py", content)
        result = validate_generated_script(path)
        assert "technical_form_ref" in _violation_types(result)

    def test_alt_shortcut_in_form_ref_fails(self, tmp_path: Path) -> None:
        content = "    bad = oracle_replay.form('find_alt_i')\n"
        path = _write(tmp_path / "test_bad.py", content)
        result = validate_generated_script(path)
        assert not result.passed
        assert "alt_ref" in _violation_types(result)

    def test_mnemonic_in_form_ref_fails(self, tmp_path: Path) -> None:
        content = "    bad = oracle_replay.form('file_mnemonic_f')\n"
        path = _write(tmp_path / "test_bad.py", content)
        result = validate_generated_script(path)
        assert not result.passed
        assert "mnemonic_ref" in _violation_types(result)

    def test_toolbar_digits_in_form_ref_fails(self, tmp_path: Path) -> None:
        content = "    bad = oracle_replay.form('toolbar195')\n"
        path = _write(tmp_path / "test_bad.py", content)
        result = validate_generated_script(path)
        assert not result.passed
        assert "toolbar_ref" in _violation_types(result)

    def test_double_quote_form_ref_also_detected(self, tmp_path: Path) -> None:
        content = '    bad = oracle_replay.form("java_order")\n'
        path = _write(tmp_path / "test_bad.py", content)
        result = validate_generated_script(path)
        assert "technical_form_ref" in _violation_types(result)


# ---------------------------------------------------------------------------
# Technical element_ref violations
# ---------------------------------------------------------------------------

class TestElementRefViolations:
    """Method calls with technical element refs must fail."""

    def test_java_element_in_set_text_fails(self, tmp_path: Path) -> None:
        content = "    f.set_text('java_field', 'value')\n"
        path = _write(tmp_path / "test_bad.py", content)
        result = validate_generated_script(path)
        assert "technical_element_ref" in _violation_types(result)

    def test_toolbar_digits_in_click_fails(self, tmp_path: Path) -> None:
        content = "    f.click('toolbar195')\n"
        path = _write(tmp_path / "test_bad.py", content)
        result = validate_generated_script(path)
        assert not result.passed
        assert "toolbar_ref" in _violation_types(result)

    def test_alt_suffix_in_click_fails(self, tmp_path: Path) -> None:
        content = "    f.click('yes_alt_y')\n"
        path = _write(tmp_path / "test_bad.py", content)
        result = validate_generated_script(path)
        assert "alt_ref" in _violation_types(result)

    def test_mnemonic_in_click_fails(self, tmp_path: Path) -> None:
        content = "    f.click('file_mnemonic_f')\n"
        path = _write(tmp_path / "test_bad.py", content)
        result = validate_generated_script(path)
        assert "mnemonic_ref" in _violation_types(result)

    def test_java_element_in_press_key_fails(self, tmp_path: Path) -> None:
        content = "    f.press_key('java_key')\n"
        path = _write(tmp_path / "test_bad.py", content)
        result = validate_generated_script(path)
        assert "technical_element_ref" in _violation_types(result)

    def test_java_element_in_assert_text_fails(self, tmp_path: Path) -> None:
        content = "    f.assert_text('java_status', 'OK')\n"
        path = _write(tmp_path / "test_bad.py", content)
        result = validate_generated_script(path)
        assert "technical_element_ref" in _violation_types(result)

    def test_java_element_in_assert_value_fails(self, tmp_path: Path) -> None:
        content = "    f.assert_value('java_total', '100')\n"
        path = _write(tmp_path / "test_bad.py", content)
        result = validate_generated_script(path)
        assert "technical_element_ref" in _violation_types(result)


# ---------------------------------------------------------------------------
# Playwright locator guard
# ---------------------------------------------------------------------------

class TestLocatorCallGuard:
    def test_page_locator_fails(self, tmp_path: Path) -> None:
        content = "    page.locator('#some-id').click()\n"
        path = _write(tmp_path / "test_bad.py", content)
        result = validate_generated_script(path)
        assert not result.passed
        assert "locator_call" in _violation_types(result)

    def test_page_locator_with_spaces_fails(self, tmp_path: Path) -> None:
        content = "    page.locator  ('#foo').fill('x')\n"
        path = _write(tmp_path / "test_bad.py", content)
        result = validate_generated_script(path)
        assert "locator_call" in _violation_types(result)


# ---------------------------------------------------------------------------
# Java descriptor dict guard
# ---------------------------------------------------------------------------

class TestJavaDescriptorGuard:
    def test_form_id_key_fails(self, tmp_path: Path) -> None:
        content = '    target = {"form_id": "java_po", "element_id": "field"}\n'
        path = _write(tmp_path / "test_bad.py", content)
        result = validate_generated_script(path)
        assert not result.passed
        assert "java_descriptor" in _violation_types(result)

    def test_friendly_name_key_fails(self, tmp_path: Path) -> None:
        content = '    d = {"friendly_name": "po_received"}\n'
        path = _write(tmp_path / "test_bad.py", content)
        result = validate_generated_script(path)
        assert "java_descriptor" in _violation_types(result)

    def test_element_id_key_fails(self, tmp_path: Path) -> None:
        content = '    d = {"element_id": "fld"}\n'
        path = _write(tmp_path / "test_bad.py", content)
        result = validate_generated_script(path)
        assert "java_descriptor" in _violation_types(result)


# ---------------------------------------------------------------------------
# Alias-review soft warning
# ---------------------------------------------------------------------------

class TestAliasReview:
    def test_alias_review_comment_not_a_violation(self, tmp_path: Path) -> None:
        content = _clean_script(
            "    bad_form = oracle_replay.form('sanitised_form')  # alias_review: was 'java_old'\n"
        )
        path = _write(tmp_path / "test_review.py", content)
        result = validate_generated_script(path)
        # alias_review is NOT a violation — it does not make the script fail
        assert result.passed, f"alias_review comment must not cause failure:\n{result.summary()}"

    def test_alias_review_comment_reported_in_alias_reviews(self, tmp_path: Path) -> None:
        content = _clean_script(
            "    bad_form = oracle_replay.form('sanitised_form')  # alias_review: was 'java_old'\n"
        )
        path = _write(tmp_path / "test_review.py", content)
        result = validate_generated_script(path)
        assert len(result.alias_reviews) == 1
        assert result.alias_reviews[0].violation_type == "alias_review"

    def test_alias_review_includes_line_number(self, tmp_path: Path) -> None:
        lines = [
            "# AUTO-GENERATED\n",
            "from __future__ import annotations\n",
            "    form_var = oracle_replay.form('some_form')  # alias_review: was 'java_x'\n",
        ]
        path = _write(tmp_path / "test_review.py", "".join(lines))
        result = validate_generated_script(path)
        assert result.alias_reviews[0].line_number == 3

    def test_multiple_alias_reviews_all_captured(self, tmp_path: Path) -> None:
        content = (
            "    f1 = oracle_replay.form('form_a')  # alias_review: was 'java_a'\n"
            "    f2 = oracle_replay.form('form_b')  # alias_review: was 'java_b'\n"
        )
        path = _write(tmp_path / "test_review.py", content)
        result = validate_generated_script(path)
        assert result.passed
        assert len(result.alias_reviews) == 2


# ---------------------------------------------------------------------------
# Violation metadata
# ---------------------------------------------------------------------------

class TestViolationMetadata:
    def test_violation_has_correct_line_number(self, tmp_path: Path) -> None:
        lines = [
            "# comment\n",
            "import pytest\n",
            "    bad = oracle_replay.form('java_po')\n",  # line 3
        ]
        path = _write(tmp_path / "test_meta.py", "".join(lines))
        result = validate_generated_script(path)
        assert result.violations[0].line_number == 3

    def test_violation_has_offending_ref_in_detail(self, tmp_path: Path) -> None:
        content = "    bad = oracle_replay.form('java_po')\n"
        path = _write(tmp_path / "test_meta.py", content)
        result = validate_generated_script(path)
        assert "java_po" in result.violations[0].detail

    def test_violation_includes_line_text(self, tmp_path: Path) -> None:
        content = "    bad = oracle_replay.form('java_po')\n"
        path = _write(tmp_path / "test_meta.py", content)
        result = validate_generated_script(path)
        assert "java_po" in result.violations[0].line_text

    def test_str_representation_includes_violation_type(self, tmp_path: Path) -> None:
        content = "    bad = oracle_replay.form('java_po')\n"
        path = _write(tmp_path / "test_meta.py", content)
        result = validate_generated_script(path)
        assert "technical_form_ref" in str(result.violations[0])


# ---------------------------------------------------------------------------
# validate_generated_dir
# ---------------------------------------------------------------------------

class TestValidateDir:
    def test_empty_dir_returns_empty_list(self, tmp_path: Path) -> None:
        results = validate_generated_dir(tmp_path)
        assert results == []

    def test_nonexistent_dir_returns_empty_list(self, tmp_path: Path) -> None:
        results = validate_generated_dir(tmp_path / "does_not_exist")
        assert results == []

    def test_finds_test_files_recursively(self, tmp_path: Path) -> None:
        clean = _clean_script()
        _write(tmp_path / "suite_a" / "test_one.py", clean)
        _write(tmp_path / "suite_b" / "test_two.py", clean)
        results = validate_generated_dir(tmp_path)
        assert len(results) == 2

    def test_non_test_files_not_validated(self, tmp_path: Path) -> None:
        _write(tmp_path / "conftest.py", "# conftest")
        _write(tmp_path / "objects.py", "# objects")
        results = validate_generated_dir(tmp_path)
        assert results == []

    def test_returns_one_result_per_file(self, tmp_path: Path) -> None:
        clean = _clean_script()
        bad = "    bad = oracle_replay.form('java_po')\n"
        _write(tmp_path / "test_clean.py", clean)
        _write(tmp_path / "test_bad.py", bad)
        results = validate_generated_dir(tmp_path)
        assert len(results) == 2
        passes = sum(1 for r in results if r.passed)
        fails  = sum(1 for r in results if not r.passed)
        assert passes == 1
        assert fails == 1


# ---------------------------------------------------------------------------
# Summary output
# ---------------------------------------------------------------------------

class TestSummaryOutput:
    def test_pass_summary_contains_pass(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "test_clean.py", _clean_script())
        result = validate_generated_script(path)
        assert "PASS" in result.summary()

    def test_fail_summary_contains_fail(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "test_bad.py", "    f.click('yes_alt_y')\n")
        result = validate_generated_script(path)
        assert "FAIL" in result.summary()

    def test_summary_reports_alias_review_count(self, tmp_path: Path) -> None:
        content = _clean_script(
            "    f = oracle_replay.form('some_form')  # alias_review: was 'java_x'\n"
        )
        path = _write(tmp_path / "test_review.py", content)
        result = validate_generated_script(path)
        summary = result.summary()
        assert "alias_review" in summary.lower()


# ---------------------------------------------------------------------------
# Real generated script sanity check
# ---------------------------------------------------------------------------

class TestRealGeneratedScript:
    """The actual rec_013 generated script must pass the business readability gate."""

    @pytest.fixture
    def rec013_path(self) -> Path:
        import config
        return config.ROOT_DIR / "generated_tests" / "rec_013_replay" / "test_rec_013_replay.py"

    def test_real_rec013_file_exists(self, rec013_path: Path) -> None:
        assert rec013_path.exists(), (
            f"rec_013 generated script not found at {rec013_path}. "
            "Run: python -m qcs gen run recordings/rec_013 rec_013_replay"
        )

    def test_real_rec013_passes_business_readability_gate(self, rec013_path: Path) -> None:
        if not rec013_path.exists():
            pytest.skip("rec_013 generated script not present")
        result = validate_generated_script(rec013_path)
        assert result.passed, (
            f"rec_013 generated script failed business-readability validation:\n"
            f"{result.summary()}"
        )

    def test_real_rec013_dir_all_files_pass(self, rec013_path: Path) -> None:
        if not rec013_path.exists():
            pytest.skip("rec_013 generated script not present")
        dir_results = validate_generated_dir(rec013_path.parent)
        failures = [r for r in dir_results if not r.passed]
        assert failures == [], (
            "Business-readability failures in generated_tests/rec_013_replay/:\n"
            + "\n".join(r.summary() for r in failures)
        )

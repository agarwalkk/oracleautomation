"""Round-trip tests: snapshot recorder row → normalize → manifest → generated DSL.

Covers:
  - _element_ref_from_row prefers explicit element_ref field (Approach B rows)
  - _element_ref_from_row falls back to target.friendly_name (legacy rows)
  - _form_ref_from_row picks up explicit form_ref when target is absent
  - _input_from_row captures locator_params
  - _metadata_from_row captures semantic_ref and element_id
  - Full round-trip: set_text / click / press_key rows produce correct DSL
  - Legacy rows without explicit element_ref still produce correct DSL
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from qcs_manifest.normalize import (
    _element_ref_from_row,
    _form_ref_from_row,
    _input_from_row,
    _metadata_from_row,
    normalize_rows_to_manifest,
)
from generator.build_test import generate_test


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = "2026-01-01T10:00:00.000+00:00"


def _click_row(form_ref: str, element_ref: str, element_id: str = "e10",
               locator_path: str = "w0/n10") -> dict:
    return {
        "ts": _TS, "run_id": "rt001", "surface": "java",
        "op": "java_click",
        "form_ref": form_ref,
        "element_ref": element_ref,
        "semantic_ref": element_ref,
        "target": {"form_id": form_ref, "friendly_name": element_ref},
        "element_id": element_id,
        "locator_params": {"locatorPath": locator_path, "locatorText": element_ref},
    }


def _set_text_row(form_ref: str, element_ref: str, text: str,
                  element_id: str = "e10", locator_path: str = "w0/n10") -> dict:
    return {
        "ts": _TS, "run_id": "rt001", "surface": "java",
        "op": "java_send_text",
        "form_ref": form_ref,
        "element_ref": element_ref,
        "semantic_ref": element_ref,
        "target": {"form_id": form_ref, "friendly_name": element_ref},
        "text": text,
        "element_id": element_id,
        "locator_params": {"locatorPath": locator_path, "locatorText": element_ref},
    }


def _press_key_row(form_ref: str, key: str) -> dict:
    return {
        "ts": _TS, "run_id": "rt001", "surface": "java",
        "op": "java_press_key",
        "form_ref": form_ref,
        "target": {"form_id": form_ref},
        "key": key,
    }


# ---------------------------------------------------------------------------
# Unit tests: extractor functions
# ---------------------------------------------------------------------------

class TestElementRefFromRow:
    def test_prefers_explicit_element_ref_field(self):
        row = {"element_ref": "po_number", "target": {"friendly_name": "wrong_name"}}
        assert _element_ref_from_row(row) == "po_number"

    def test_falls_back_to_target_friendly_name_when_no_element_ref(self):
        row = {"target": {"form_id": "java_po_form", "friendly_name": "order_type"}}
        assert _element_ref_from_row(row) == "order_type"

    def test_falls_back_to_target_ref(self):
        row = {"target": {"ref": "submit_btn"}}
        assert _element_ref_from_row(row) == "submit_btn"

    def test_returns_none_when_no_element_info(self):
        assert _element_ref_from_row({"op": "java_press_key", "key": "TAB"}) is None

    def test_explicit_empty_string_does_not_shadow_fallback(self):
        row = {"element_ref": "", "target": {"friendly_name": "fallback"}}
        assert _element_ref_from_row(row) == "fallback"

    def test_explicit_whitespace_does_not_shadow_fallback(self):
        row = {"element_ref": "   ", "target": {"friendly_name": "fallback"}}
        assert _element_ref_from_row(row) == "fallback"


class TestFormRefFromRow:
    def test_prefers_target_form_id(self):
        row = {"form_ref": "other", "target": {"form_id": "java_po_form"}}
        assert _form_ref_from_row(row) == "java_po_form"

    def test_falls_back_to_top_level_form_ref(self):
        row = {"form_ref": "java_po_form"}
        assert _form_ref_from_row(row) == "java_po_form"

    def test_falls_back_to_top_level_form_id(self):
        row = {"form_id": "java_po_form"}
        assert _form_ref_from_row(row) == "java_po_form"

    def test_returns_empty_for_press_key_without_form(self):
        row = {"op": "java_press_key", "key": "TAB"}
        assert _form_ref_from_row(row) == ""

    def test_press_key_row_with_target_form_id(self):
        row = _press_key_row("java_po_form", "TAB")
        assert _form_ref_from_row(row) == "java_po_form"


class TestInputFromRow:
    def test_picks_up_locator_params(self):
        row = _set_text_row("java_po_form", "order_number", "PO-001")
        inp = _input_from_row(row)
        assert inp is not None
        assert inp["locator_params"] == {"locatorPath": "w0/n10", "locatorText": "order_number"}

    def test_picks_up_text(self):
        row = _set_text_row("java_po_form", "order_number", "PO-001")
        assert _input_from_row(row)["text"] == "PO-001"

    def test_picks_up_key(self):
        row = _press_key_row("java_po_form", "TAB")
        assert _input_from_row(row)["key"] == "TAB"

    def test_locator_params_absent_in_press_key_row(self):
        row = _press_key_row("java_po_form", "TAB")
        inp = _input_from_row(row)
        assert "locator_params" not in (inp or {})

    def test_returns_none_for_row_with_no_input_fields(self):
        row = {"op": "session_start", "ts": _TS}
        assert _input_from_row(row) is None


class TestMetadataFromRow:
    def test_picks_up_semantic_ref(self):
        row = _click_row("java_po_form", "find")
        meta = _metadata_from_row(row)
        assert meta["semantic_ref"] == "find"

    def test_picks_up_element_id(self):
        row = _click_row("java_po_form", "find", element_id="e42")
        meta = _metadata_from_row(row)
        assert meta["element_id"] == "e42"

    def test_returns_empty_for_press_key_row(self):
        row = _press_key_row("java_po_form", "TAB")
        assert _metadata_from_row(row) == {}

    def test_returns_empty_for_session_start(self):
        assert _metadata_from_row({"op": "session_start"}) == {}


# ---------------------------------------------------------------------------
# Normalize: manifest step fields
# ---------------------------------------------------------------------------

class TestNormalizeManifestStep:
    def _normalize(self, row: dict) -> dict:
        payload = normalize_rows_to_manifest(
            [row], run_id="rt001", source_recording_path="test.jsonl", flow_name="rt_flow"
        )
        return payload["steps"][0]

    def test_set_text_element_ref(self):
        step = self._normalize(_set_text_row("java_po_form", "order_number", "PO-001"))
        assert step["element_ref"] == "order_number"

    def test_set_text_form_ref(self):
        step = self._normalize(_set_text_row("java_po_form", "order_number", "PO-001"))
        assert step["form_ref"] == "java_po_form"

    def test_set_text_input_text(self):
        step = self._normalize(_set_text_row("java_po_form", "order_number", "PO-001"))
        assert step["input"]["text"] == "PO-001"

    def test_set_text_input_locator_params(self):
        step = self._normalize(_set_text_row("java_po_form", "order_number", "PO-001",
                                             locator_path="w0/n99"))
        assert step["input"]["locator_params"]["locatorPath"] == "w0/n99"

    def test_set_text_metadata_semantic_ref(self):
        step = self._normalize(_set_text_row("java_po_form", "order_number", "PO-001"))
        assert step["metadata"]["semantic_ref"] == "order_number"

    def test_set_text_metadata_element_id(self):
        step = self._normalize(_set_text_row("java_po_form", "order_number", "PO-001",
                                             element_id="e10"))
        assert step["metadata"]["element_id"] == "e10"

    def test_click_element_ref(self):
        step = self._normalize(_click_row("java_po_form", "find"))
        assert step["element_ref"] == "find"

    def test_press_key_form_ref_from_target(self):
        step = self._normalize(_press_key_row("java_po_form", "TAB"))
        assert step["form_ref"] == "java_po_form"

    def test_press_key_input_key(self):
        step = self._normalize(_press_key_row("java_po_form", "TAB"))
        assert step["input"]["key"] == "TAB"

    def test_press_key_no_element_ref(self):
        step = self._normalize(_press_key_row("java_po_form", "TAB"))
        assert step["element_ref"] is None

    def test_legacy_row_without_explicit_element_ref(self):
        """Legacy Approach-A style row (no explicit element_ref) still resolves."""
        legacy = {
            "ts": _TS, "run_id": "rt001", "surface": "java",
            "op": "java_click",
            "target": {"form_id": "java_po_form", "friendly_name": "clear"},
        }
        step = self._normalize(legacy)
        assert step["element_ref"] == "clear"
        assert step["form_ref"] == "java_po_form"


# ---------------------------------------------------------------------------
# Full round-trip: normalize → generate_test → DSL
# ---------------------------------------------------------------------------

def _write_manifest(tmp_path: Path, rows: list[dict], run_id: str = "rt001") -> Path:
    payload = normalize_rows_to_manifest(
        rows, run_id=run_id, source_recording_path="test.jsonl", flow_name="rt_flow"
    )
    p = tmp_path / "recording.manifest.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _generated_code(tmp_path: Path, rows: list[dict], test_name: str = "po_test") -> str:
    manifest_path = _write_manifest(tmp_path, rows)
    out_dir = tmp_path / "generated"
    generate_test(manifest_path, out_dir, test_name)
    return (out_dir / f"test_{test_name}.py").read_text(encoding="utf-8")


class TestRoundTripDSL:
    def test_set_text_emits_form_and_set_text(self, tmp_path: Path):
        code = _generated_code(
            tmp_path,
            [_set_text_row("java_po_form", "order_number", "PO-12345")],
        )
        # form() uses sanitized ref (java_ stripped by AliasResolver)
        assert "oracle_replay.form('po_form')" in code
        assert ".set_text('order_number', 'PO-12345')" in code

    def test_click_emits_form_and_click(self, tmp_path: Path):
        code = _generated_code(
            tmp_path,
            [_click_row("java_po_form", "find")],
        )
        assert "oracle_replay.form('po_form')" in code
        assert ".click('find')" in code

    def test_press_key_emits_form_scoped_press_key(self, tmp_path: Path):
        # Need a prior form-context step so the form variable exists
        rows = [
            _click_row("java_po_form", "find"),
            _press_key_row("java_po_form", "TAB"),
        ]
        code = _generated_code(tmp_path, rows)
        assert ".press_key('TAB')" in code
        # No orphan TODO comment — form_ref is resolved from the row
        assert "# TODO: form-scoped press_key" not in code

    def test_element_ref_not_volatile_scan_id(self, tmp_path: Path):
        """The DSL must reference element_ref (semantic), never element_id (e10)."""
        code = _generated_code(
            tmp_path,
            [_click_row("java_po_form", "find", element_id="e10")],
        )
        assert "'find'" in code
        assert "'e10'" not in code

    def test_legacy_row_round_trip(self, tmp_path: Path):
        """Approach-A style row (no element_ref field) still produces correct DSL."""
        legacy = {
            "ts": _TS, "run_id": "rt001", "surface": "java",
            "op": "java_click",
            "target": {"form_id": "java_po_form", "friendly_name": "clear"},
        }
        code = _generated_code(tmp_path, [legacy])
        assert ".click('clear')" in code

    def test_multiple_actions_correct_element_refs(self, tmp_path: Path):
        rows = [
            _set_text_row("java_po_form", "order_number", "PO-001"),
            _click_row("java_po_form", "find"),
            _press_key_row("java_po_form", "F11"),
        ]
        code = _generated_code(tmp_path, rows)
        assert ".set_text('order_number', 'PO-001')" in code
        assert ".click('find')" in code
        assert ".press_key('F11')" in code

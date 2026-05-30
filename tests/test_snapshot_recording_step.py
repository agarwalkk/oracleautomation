"""Tests for oracle_ai_agent._execute_snapshot_recording_step.

Strategy
--------
- Mock ``driver`` entirely with unittest.mock.MagicMock.
- Inject a canned async ``_call_llm`` that returns a controlled tool-call response,
  bypassing real Azure OpenAI calls entirely.
- Patch ``_refresh_current_form_metadata`` as a no-op to avoid fingerprint/repo I/O.
- Patch ``repo_store.upsert_actioned_element`` to return the element unchanged,
  avoiding SQLite writes.
- Inspect ``recording.jsonl`` written by the real ``session.log_action`` for assertions.

No real JVM, no Azure OpenAI, no Playwright.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import config
from oracle_ai_agent import _execute_snapshot_recording_step
from oracle_ai_agent.tools import RecorderSession, SnapshotAction


# ── Shared fake scan ──────────────────────────────────────────────────────────

def _make_scan(*nodes: dict) -> dict:
    return {"windows": list(nodes)}


def _make_node(
    node_id: int,
    semantic_type: str,
    display_name: str,
    *,
    enabled: bool = True,
) -> dict:
    return {
        "id": node_id,
        "semanticType": semantic_type,
        "displayName": display_name,
        "accessibleName": display_name,
        "path": f"w0/n{node_id}",
        "parentPath": "w0",
        "enabled": enabled,
        "visible": True,
        "showing": True,
        "focusable": enabled,
        "focused": False,
        "children": [],
        "screenBounds": {
            "screenX": 100 + node_id,
            "screenY": 200,
            "width": 120,
            "height": 24,
        },
    }


# A scan with two actionable elements: e10 (Field), e11 (Button)
_SCAN_TWO_ELEMENTS = _make_scan(
    {
        "id": 0, "semanticType": "Window", "displayName": "PO Form",
        "path": "w0", "parentPath": "",
        "enabled": True, "visible": True, "showing": True,
        "children": [
            _make_node(10, "Field",  "PO Number", enabled=True),
            _make_node(11, "Button", "Find",       enabled=True),
        ],
    }
)


# ── LLM response builder ──────────────────────────────────────────────────────

def _llm_response(action: SnapshotAction) -> dict:
    """Wrap a SnapshotAction back into the OpenAI chat-completions tool-call shape."""
    args: dict = {"action": action.action}
    if action.element_id is not None:
        args["element_id"] = action.element_id
    if action.value is not None:
        args["value"] = action.value
    if action.key is not None:
        args["key"] = action.key
    if action.assertion_kind is not None:
        args["assertion_kind"] = action.assertion_kind
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_test",
                    "type": "function",
                    "function": {
                        "name": "record_action",
                        "arguments": json.dumps(args),
                    },
                }],
            },
        }]
    }


def _no_tool_call_response(content: str = "I cannot determine the element.") -> dict:
    return {
        "choices": [{
            "message": {"role": "assistant", "content": content, "tool_calls": []},
        }]
    }


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def session(tmp_path):
    """RecorderSession whose run_dir is tmp_path/test_run."""
    with patch.object(config, "RECORDINGS_DIR", tmp_path):
        sess = RecorderSession("test_run", auto_name=True)
    sess.current_form_id = "java_po_form"
    sess.java_pid = 9999
    return sess


def _make_driver(scan: dict) -> MagicMock:
    driver = MagicMock()
    driver.scan.return_value = scan
    driver.click.return_value = {"status": "ok"}
    driver.set_text.return_value = {"status": "ok"}
    driver.press_key.return_value = {"status": "ok"}
    return driver


def _recording_rows(session: RecorderSession) -> list[dict]:
    path = session.run_dir / "recording.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── click action ─────────────────────────────────────────────────────────────

class TestClickAction:

    @pytest.mark.asyncio
    async def test_click_calls_driver_click(self, session):
        driver = _make_driver(_SCAN_TWO_ELEMENTS)
        canned = SnapshotAction(action="click", element_id="e11")

        async def _mock_llm(msgs, tools):
            return _llm_response(canned)

        with patch("oracle_ai_agent._refresh_current_form_metadata"):
            with patch("qcs_repo.store.upsert_actioned_element", side_effect=lambda fid, el, **kw: el):
                result = await _execute_snapshot_recording_step(
                    session, driver, "Click the Find button",
                    _call_llm=_mock_llm,
                )

        assert result is not None
        assert result.action == "click"
        assert result.element_id == "e11"
        driver.click.assert_called_once()
        # The descriptor passed to driver.click must correspond to e11
        call_args = driver.click.call_args[0][0]
        assert call_args.get("elementid") == "e11"

    @pytest.mark.asyncio
    async def test_click_logs_java_click_row(self, session):
        driver = _make_driver(_SCAN_TWO_ELEMENTS)
        canned = SnapshotAction(action="click", element_id="e11")

        async def _mock_llm(msgs, tools):
            return _llm_response(canned)

        with patch("oracle_ai_agent._refresh_current_form_metadata"):
            with patch("qcs_repo.store.upsert_actioned_element", side_effect=lambda fid, el, **kw: el):
                await _execute_snapshot_recording_step(
                    session, driver, "Click the Find button",
                    _call_llm=_mock_llm,
                )

        rows = _recording_rows(session)
        click_rows = [r for r in rows if r.get("op") == "java_click"]
        assert len(click_rows) == 1
        row = click_rows[0]
        assert row["target"]["form_id"] == "java_po_form"
        assert row["element_id"] == "e11"

    @pytest.mark.asyncio
    async def test_click_upserts_element_to_repo(self, session):
        driver = _make_driver(_SCAN_TWO_ELEMENTS)
        canned = SnapshotAction(action="click", element_id="e11")

        async def _mock_llm(msgs, tools):
            return _llm_response(canned)

        upserted: list[tuple] = []

        def _capture_upsert(form_id, el, **kw):
            upserted.append((form_id, el))
            return el

        with patch("oracle_ai_agent._refresh_current_form_metadata"):
            with patch("qcs_repo.store.upsert_actioned_element", side_effect=_capture_upsert):
                await _execute_snapshot_recording_step(
                    session, driver, "Click Find",
                    _call_llm=_mock_llm,
                )

        assert len(upserted) == 1
        form_id, element = upserted[0]
        assert form_id == "java_po_form"
        assert element.get("elementid") == "e11"


# ── set_text action ──────────────────────────────────────────────────────────

class TestSetTextAction:

    @pytest.mark.asyncio
    async def test_set_text_calls_driver_set_text_with_value(self, session):
        driver = _make_driver(_SCAN_TWO_ELEMENTS)
        canned = SnapshotAction(action="set_text", element_id="e10", value="PO-001")

        async def _mock_llm(msgs, tools):
            return _llm_response(canned)

        with patch("oracle_ai_agent._refresh_current_form_metadata"):
            with patch("qcs_repo.store.upsert_actioned_element", side_effect=lambda fid, el, **kw: el):
                result = await _execute_snapshot_recording_step(
                    session, driver, "Enter PO-001 in PO Number",
                    _call_llm=_mock_llm,
                )

        assert result.action == "set_text"
        driver.set_text.assert_called_once()
        descriptor, value = driver.set_text.call_args[0]
        assert descriptor.get("elementid") == "e10"
        assert value == "PO-001"

    @pytest.mark.asyncio
    async def test_set_text_logs_java_send_text_row(self, session):
        driver = _make_driver(_SCAN_TWO_ELEMENTS)
        canned = SnapshotAction(action="set_text", element_id="e10", value="12345")

        async def _mock_llm(msgs, tools):
            return _llm_response(canned)

        with patch("oracle_ai_agent._refresh_current_form_metadata"):
            with patch("qcs_repo.store.upsert_actioned_element", side_effect=lambda fid, el, **kw: el):
                await _execute_snapshot_recording_step(
                    session, driver, "Enter 12345 in PO Number",
                    _call_llm=_mock_llm,
                )

        rows = _recording_rows(session)
        text_rows = [r for r in rows if r.get("op") == "java_send_text"]
        assert len(text_rows) == 1
        assert text_rows[0]["text"] == "12345"
        assert text_rows[0]["element_id"] == "e10"


# ── press_key action ─────────────────────────────────────────────────────────

class TestPressKeyAction:

    @pytest.mark.asyncio
    async def test_press_key_calls_driver_press_key(self, session):
        driver = _make_driver(_SCAN_TWO_ELEMENTS)
        canned = SnapshotAction(action="press_key", key="TAB")

        async def _mock_llm(msgs, tools):
            return _llm_response(canned)

        with patch("oracle_ai_agent._refresh_current_form_metadata"):
            with patch("qcs_repo.store.upsert_actioned_element", side_effect=lambda fid, el, **kw: el):
                result = await _execute_snapshot_recording_step(
                    session, driver, "Press TAB to commit",
                    _call_llm=_mock_llm,
                )

        assert result.action == "press_key"
        driver.press_key.assert_called_once_with("TAB")

    @pytest.mark.asyncio
    async def test_press_key_logs_java_press_key_row(self, session):
        driver = _make_driver(_SCAN_TWO_ELEMENTS)
        canned = SnapshotAction(action="press_key", key="F10")

        async def _mock_llm(msgs, tools):
            return _llm_response(canned)

        with patch("oracle_ai_agent._refresh_current_form_metadata"):
            with patch("qcs_repo.store.upsert_actioned_element", side_effect=lambda fid, el, **kw: el):
                await _execute_snapshot_recording_step(
                    session, driver, "Save with F10",
                    _call_llm=_mock_llm,
                )

        rows = _recording_rows(session)
        key_rows = [r for r in rows if r.get("op") == "java_press_key"]
        assert len(key_rows) == 1
        assert key_rows[0]["key"] == "F10"

    @pytest.mark.asyncio
    async def test_press_key_does_not_call_driver_click_or_set_text(self, session):
        driver = _make_driver(_SCAN_TWO_ELEMENTS)
        canned = SnapshotAction(action="press_key", key="ESC")

        async def _mock_llm(msgs, tools):
            return _llm_response(canned)

        with patch("oracle_ai_agent._refresh_current_form_metadata"):
            with patch("qcs_repo.store.upsert_actioned_element", side_effect=lambda fid, el, **kw: el):
                await _execute_snapshot_recording_step(
                    session, driver, "Press ESC",
                    _call_llm=_mock_llm,
                )

        driver.click.assert_not_called()
        driver.set_text.assert_not_called()


# ── done / abort paths ───────────────────────────────────────────────────────

class TestAbortAndDone:

    @pytest.mark.asyncio
    async def test_no_tool_call_returns_none(self, session):
        driver = _make_driver(_SCAN_TWO_ELEMENTS)

        async def _mock_llm(msgs, tools):
            return _no_tool_call_response("I cannot decide.")

        with patch("oracle_ai_agent._refresh_current_form_metadata"):
            result = await _execute_snapshot_recording_step(
                session, driver, "Click something",
                _call_llm=_mock_llm,
            )

        assert result is None
        driver.click.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_snapshot_returns_none(self, session):
        empty_scan = {"windows": []}
        driver = _make_driver(empty_scan)
        called = []

        async def _mock_llm(msgs, tools):
            called.append(True)
            return {}

        result = await _execute_snapshot_recording_step(
            session, driver, "Click something",
            _call_llm=_mock_llm,
        )

        assert result is None
        assert not called, "AI should not be called when snapshot is empty"

    @pytest.mark.asyncio
    async def test_done_action_logs_step_note_when_value_present(self, session):
        driver = _make_driver(_SCAN_TWO_ELEMENTS)
        canned = SnapshotAction(action="done", value="Step already satisfied")

        async def _mock_llm(msgs, tools):
            return _llm_response(canned)

        with patch("oracle_ai_agent._refresh_current_form_metadata"):
            with patch("qcs_repo.store.upsert_actioned_element", side_effect=lambda fid, el, **kw: el):
                result = await _execute_snapshot_recording_step(
                    session, driver, "Already done step",
                    _call_llm=_mock_llm,
                )

        assert result is not None
        assert result.action == "done"
        rows = _recording_rows(session)
        note_rows = [r for r in rows if r.get("op") == "step_note"]
        assert any(r["note"] == "Step already satisfied" for r in note_rows)
        # done must not execute any driver action
        driver.click.assert_not_called()
        driver.set_text.assert_not_called()
        driver.press_key.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_element_id_from_ai_returns_none(self, session):
        """If parse_snapshot_action rejects the AI response, step is aborted cleanly."""
        driver = _make_driver(_SCAN_TWO_ELEMENTS)

        async def _mock_llm(msgs, tools):
            # Return an element_id that is NOT in the snapshot
            return {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "call_bad",
                            "type": "function",
                            "function": {
                                "name": "record_action",
                                "arguments": json.dumps({
                                    "action": "click",
                                    "element_id": "e999",  # not in snapshot
                                }),
                            },
                        }],
                    },
                }]
            }

        result = await _execute_snapshot_recording_step(
            session, driver, "Click something",
            _call_llm=_mock_llm,
        )

        assert result is None
        driver.click.assert_not_called()


# ── assert action ────────────────────────────────────────────────────────────

class TestAssertAction:

    @pytest.mark.asyncio
    async def test_assert_logs_assertion_row(self, session):
        driver = _make_driver(_SCAN_TWO_ELEMENTS)
        canned = SnapshotAction(
            action="assert",
            element_id="e10",
            assertion_kind="value",
            value="PO-001",
        )

        async def _mock_llm(msgs, tools):
            return _llm_response(canned)

        with patch("oracle_ai_agent._refresh_current_form_metadata"):
            with patch("qcs_repo.store.upsert_actioned_element", side_effect=lambda fid, el, **kw: el):
                result = await _execute_snapshot_recording_step(
                    session, driver, "Assert PO Number is PO-001",
                    _call_llm=_mock_llm,
                )

        assert result.action == "assert"
        rows = _recording_rows(session)
        assert_rows = [r for r in rows if r.get("op") == "assertion"]
        assert len(assert_rows) == 1
        assert assert_rows[0]["expected_text"] == "PO-001"
        assert assert_rows[0]["expected_state"] == "value"
        # assert must not execute any driver action
        driver.click.assert_not_called()
        driver.set_text.assert_not_called()

"""
oracle_ai_agent.tools — In-process recorder tool implementations.

Replaces the oracle_mcp_server FastMCP server.  All tools are plain async
functions called directly by the agent loop — no HTTP round-trip, no separate
process, no port 9001.

Session state that previously lived in the FastMCP per-session dict now lives
on ``RecorderSession``, one instance per recording run.

Public surface
--------------
  RecorderSession   — mutable state for one run; passed to every tool fn
  dispatch(session, tool_name, args)  — routes LLM tool calls in-process
  TOOL_SCHEMAS      — OpenAI function-calling JSON schemas (same descriptions
                      the LLM previously saw via the MCP schema endpoint)
    java_form_launch / java_get_page_snapshot use the local Java Forms agent
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import win32gui

import config
from qcs_repo import store as repo_store
from qcs_repo.fingerprint import (
    fingerprint_java_form,
    suggest_form_id,
)
from qcs_repo.naming import propose_friendly_name, confirm_name
from qcs_java_agent import (
    JavaAgentDriver,
    active_form_title,
    java_nodes_to_repo_elements,
    locator_params,
    wait_for_forms_ready,
)
from qcs_replay.java_agent import resolve_java_ref

# ── Session state ─────────────────────────────────────────────────────────────


class RecorderSession:
    """Holds all mutable state for one recording run.

    Previously scattered across ``_session[ctx.session_id]`` in the MCP
    server; now plain attributes on this object, one per ``qcs record`` run.
    """

    def __init__(self, run_id: str, auto_name: bool = False):
        self.run_id    = run_id
        self.auto_name = auto_name  # True → skip stdin prompt; confirmed_by: ai

        self.run_dir: Path = config.RECORDINGS_DIR / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "screenshots").mkdir(exist_ok=True)
        (self.run_dir / "diagnostics").mkdir(exist_ok=True)

        self.surface: str = "unknown"
        self.java_hwnd: int | None = None
        self.java_pid: int | None = None
        self.current_form_id: str | None = None
        self.snapshot_db: str | None = None
        self.snapshot_elements: list[dict] = []

    # -- logging ---------------------------------------------------------------

    def log_action(self, op: str, **kwargs) -> None:
        """Append a structured row to recording.jsonl."""
        row = {
            "ts":      datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "run_id":  self.run_id,
            "surface": self.surface,
            "op":      op,
            **kwargs,
        }
        with open(self.run_dir / "recording.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def log_diagnostic(self, event: str, **kwargs) -> None:
        """Append a structured row to diagnostics/events.jsonl."""
        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "run_id": self.run_id,
            "event": event,
            **kwargs,
        }
        log_file = self.run_dir / "diagnostics" / "events.jsonl"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _get_java_agent_driver(session: RecorderSession) -> JavaAgentDriver:
    driver = JavaAgentDriver.attach(
        pid=session.java_pid,
        contains=None if session.java_pid else config.JAVA_AGENT_PROCESS_MATCH,
    )
    session.java_pid = driver.pid
    return driver


def _save_java_snapshot_db(elements: list[dict], db_file: str) -> None:
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS page_snapshot")
    cur.execute("""
        CREATE TABLE page_snapshot (
            elementid TEXT PRIMARY KEY, name TEXT, role TEXT,
            description TEXT, xpath TEXT, x NUMBER, y NUMBER,
            width NUMBER, height NUMBER, states TEXT, text TEXT,
            filteredparentid TEXT
        )
    """)
    for el in elements:
        cur.execute(
            "INSERT OR REPLACE INTO page_snapshot VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                el.get("elementid", ""), el.get("name", ""), el.get("role", ""),
                el.get("description", ""), el.get("xpath", ""),
                el.get("x", 0), el.get("y", 0), el.get("width", 0), el.get("height", 0),
                json.dumps(el.get("states", [])), el.get("text", ""),
                el.get("filteredparentid"),
            ),
        )
    conn.commit()
    conn.close()


def _save_java_capture_artifacts(
    session: RecorderSession,
    form_id: str,
    driver: JavaAgentDriver,
    scan: dict,
    elements: list[dict],
    *,
    source: str,
) -> None:
    db_file = str(session.run_dir / "snapshot.db")
    _save_java_snapshot_db(elements, db_file)
    session.snapshot_db = db_file
    session.snapshot_elements = elements

    screenshot = session.run_dir / "screenshots" / f"{form_id}.png"
    try:
        driver.screenshot(screenshot)
    except Exception as exc:
        print(f"[JavaAgent] Warning: could not capture form screenshot: {exc}")
        screenshot = None  # type: ignore[assignment]

    repo_store.save_form_capture(
        form_id,
        elements,
        screenshot_path=screenshot,
        source=source,
    )


# ── LLM helper (naming only) ──────────────────────────────────────────────────


async def _simple_llm_call(prompt: str) -> str:
    """Async micro-LLM call used only for proposing friendly names."""
    url = (
        f"{config.AZURE_OPENAI_ENDPOINT.rstrip('/')}/openai/deployments/"
        f"{config.AZURE_OPENAI_DEPLOYMENT}/chat/completions"
        f"?api-version={config.AZURE_OPENAI_API_VERSION}"
    )
    headers = {"api-key": config.AZURE_OPENAI_API_KEY, "Content-Type": "application/json"}
    payload = {"messages": [{"role": "user", "content": prompt}], "max_tokens": 20}

    async with aiohttp.ClientSession() as s:
        async with s.post(url, headers=headers, json=payload) as r:
            resp = await r.json()
    return (resp.get("choices") or [{}])[0].get("message", {}).get("content", "")


def _snake(name: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return s.lower()


# ═════════════════════════════════════════════════════════════════════════════
# Tool implementations  (plain async functions — no MCP, no HTTP)
# ═════════════════════════════════════════════════════════════════════════════


async def session_start(session: RecorderSession, run_id: str) -> str:
    """Initialise (or re-acknowledge) the recording session."""
    session.log_action("session_start", run_id=run_id)
    return f"Session started. Artifacts will be saved to {session.run_dir}"


# ── EBS login recording marker ────────────────────────────────────────────────


async def ebs_login(
    session: RecorderSession,
    url: str,
    user_env: str = "EBS_USER",
    password_env: str = "EBS_PASSWORD",
) -> str:
    """Record the EBS login step as a structured action.

    This tool logs the login marker so codegen can emit deterministic
    Playwright login code.  After calling this tool you must ALSO drive the
    actual browser login for this recording session using Playwright MCP:
      1. browser_navigate(url=<url>)
      2. Fill the 'User Name' textbox with the value of os.environ[user_env]
      3. Fill the 'Password' textbox with the value of os.environ[password_env]
      4. Click the 'Log In' button
      5. Wait for page load
    """
    session.surface = "html"
    session.log_action(
        "ebs_login",
        url=url,
        user_env=user_env,
        password_env=password_env,
    )
    user_val  = os.environ.get(user_env, f"${user_env}")
    pass_hint = f"${password_env} (from environment)"
    return (
        f"EBS login step recorded.\n"
        f"Now use Playwright MCP to complete the actual browser login:\n"
        f"  1. browser_navigate url={url}\n"
        f"  2. Fill textbox 'User Name' with: {user_val}\n"
        f"  3. Fill textbox 'Password' with: {pass_hint}\n"
        f"  4. Click button 'Log In'\n"
        f"  5. Wait for page to load (networkidle)\n"
        f"Then call oracle_form_open."
    )


async def repo_get_form(session: RecorderSession, form_id: str) -> str:
    """Return the element catalog for a known Oracle form."""
    elements = repo_store.load_elements(form_id)
    session.current_form_id = form_id
    if not elements:
        return f"Form '{form_id}' not in repository. Use java_get_page_snapshot to scan it."
    lines = [
        f"  [{e['friendly_name']}] role={e.get('role')} name={e.get('name')}"
        for e in elements
    ]
    return f"Form '{form_id}' — {len(elements)} elements:\n" + "\n".join(lines)


async def repo_register_element(
    session: RecorderSession,
    form_id: str,
    element: dict,
) -> str:
    """Register a new element in the Object Repository."""
    # Propose friendly name via LLM
    # asyncio.run() creates a fresh event loop in the worker thread; safe here
    proposed = await asyncio.to_thread(
        propose_friendly_name,
        element,
        lambda p: asyncio.run(_simple_llm_call(p)),
    )

    if session.auto_name:
        confirmed_name, confirmed_by = proposed, "ai"
    else:
        confirmed_name, confirmed_by = await asyncio.to_thread(
            confirm_name, proposed, element, form_id
        )

    element["friendly_name"] = confirmed_name
    element["confirmed_by"]  = confirmed_by
    result = repo_store.upsert_element(form_id, element)
    session.log_action("repo_register_element",
                       form_id=form_id, friendly_name=confirmed_name, result=result)
    return (
        f"Element '{confirmed_name}' {result} in form '{form_id}' "
        f"(confirmed_by={confirmed_by})."
    )


async def repo_invoke_flow(
    session: RecorderSession,
    flow_id: str,
    params: dict,
) -> str:
    """Execute a named reusable flow from the repository."""
    flow = repo_store.load_flow(flow_id)
    if flow is None:
        return f"Flow '{flow_id}' not found in repository."

    session.log_action("repo_invoke_flow", flow_id=flow_id, params=list(params.keys()))
    results: list[str] = []

    for step in flow.get("steps", []):
        tool_name = step.get("tool", "")
        args      = dict(step.get("args", {}))
        target    = step.get("target", {})
        value     = step.get("value", "")

        for k, v in params.items():
            value = value.replace(f"{{{k}}}", str(v))
            for ak in list(args):
                args[ak] = str(args[ak]).replace(f"{{{k}}}", str(v))

        if tool_name == "pw_goto":
            results.append(f"GOTO {args.get('url', '')}")
        elif tool_name == "pw_fill":
            results.append(f"FILL [{target.get('name')}] = {value!r}")
        elif tool_name == "pw_click":
            results.append(f"CLICK [{target.get('name')}]")
        elif tool_name in ("java_send_text", "java_click"):
            results.append(
                f"{tool_name.upper()} [{target.get('friendly_name', target)}] = {value!r}"
            )
        else:
            results.append(f"STEP {tool_name} {args}")

    return "Flow executed:\n" + "\n".join(f"  {r}" for r in results)


# ── Recording annotations ─────────────────────────────────────────────────────


async def record_assertion(
    session: RecorderSession,
    target: str,
    expected_text: str | None = None,
    expected_state: str | None = None,
) -> str:
    session.log_action("assertion", target=target,
                       expected_text=expected_text, expected_state=expected_state)
    return f"Assertion recorded: {target} text={expected_text!r} state={expected_state!r}"


async def record_data_placeholder(
    session: RecorderSession,
    column_name: str,
    sample_value: str,
) -> str:
    session.log_action("data_placeholder",
                       column_name=column_name, sample_value=sample_value)
    return f"Placeholder recorded: column='{column_name}' sample={sample_value!r}"


async def record_flow_boundary(
    session: RecorderSession,
    flow_id: str,
    params: list[str],
    boundary: str,
) -> str:
    session.log_action("flow_boundary", flow_id=flow_id, params=params, boundary=boundary)
    return f"Flow boundary recorded: {flow_id} {boundary}"


# ── Oracle form URL resolution ────────────────────────────────────────────────


async def oracle_form_open(
    session: RecorderSession,
    search_text: str,
    base_url: str = "",
) -> str:
    """Resolve the Oracle EBS function URL via Azure AI Search."""
    if not base_url:
        # Derive from EBS_URL env var (strip any path component)
        raw = config.EBS_URL or ""
        from urllib.parse import urlparse  # noqa: PLC0415
        p = urlparse(raw)
        base_url = f"{p.scheme}://{p.netloc}" if p.netloc else raw.rstrip("/")
    search_endpoint = os.getenv(
        "AZURE_AI_SEARCH_SERVICE_ENDPOINT", "https://qcsaisrc.search.windows.net"
    )
    index_name = os.getenv("AZURE_AI_SEARCH_INDEX_NAME", "qcsindex3")
    search_key  = os.getenv("AZURE_AI_SEARCH_API_KEY", "")

    payload = {
        "search":       search_text,
        "searchFields": "chunk",
        "queryType":    "semantic",
        "top":          1,
        "select":       "content",
        "filter":       "context eq 'functions'",
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{search_endpoint}/indexes/{index_name}/docs/search.post.search"
            "?api-version=2024-11-01-preview",
            headers={"Content-Type": "application/json", "api-key": search_key},
            json=payload,
        ) as resp:
            if resp.status != 200:
                return f"Search failed: {await resp.text()}"
            data = await resp.json()

    content = (data.get("value") or [{}])[0].get("content", "")
    fields: dict[str, str] = {}
    for line in content.split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            fields[k.strip()] = v.strip()

    for key in ("FunctionID", "RespID", "RespApplID"):
        if not fields.get(key):
            return f"Missing field '{key}' in search result."

    final_url = (
        f"{base_url}/OA_HTML/RF.jsp"
        f"?function_id={fields['FunctionID']}"
        f"&resp_id={fields['RespID']}"
        f"&resp_appl_id={fields['RespApplID']}"
        f"&security_group_id=0&lang_code=US"
    )
    session.log_action("oracle_form_open", search_text=search_text, url=final_url)
    return final_url


# ── Java form launch ──────────────────────────────────────────────────────────


async def java_form_launch(session: RecorderSession, function_url: str) -> str:
    """Detect the Java Forms window, attach the local Java agent, and register the form."""
    from qcs_replay.web import _wait_for_oracle_window  # noqa: PLC0415

    session.surface = "java"
    hwnd = await asyncio.to_thread(_wait_for_oracle_window, 90)
    session.java_hwnd = hwnd

    driver = await asyncio.to_thread(_get_java_agent_driver, session)
    await asyncio.to_thread(wait_for_forms_ready, driver, log_prefix="[JavaAgent]")
    scan = await asyncio.to_thread(driver.scan)
    elements = java_nodes_to_repo_elements(scan)
    form_display_name = active_form_title(scan)

    title       = win32gui.GetWindowText(hwnd)
    fingerprint = fingerprint_java_form(title or form_display_name, elements[:50])
    known_form_id = repo_store.lookup_fingerprint(fingerprint)

    if known_form_id:
        session.current_form_id = known_form_id
        repo_store.update_form(known_form_id, {"surface": "java", "title": form_display_name or title})
        session.log_action("java_form_launch", url=function_url,
                           hwnd=hwnd, pid=session.java_pid, form_id=known_form_id,
                           form_name=form_display_name, repo_hit=True)
        return (
            f"Java form launched (HWND={hwnd}, PID={session.java_pid}). Known form '{known_form_id}'\n"
            f"Active form: {form_display_name or '(unknown)'}\n"
            "Recording will use screenshots for AI and local Java DOM coordinate mapping."
        )

    # New form - register and persist the canonical capture.
    suggested_form_id = suggest_form_id(form_display_name or title, surface="java")
    session.current_form_id = suggested_form_id

    repo_store.save_form({"id": suggested_form_id, "surface": "java",
                          "title": form_display_name or title, "fingerprint": fingerprint})
    repo_store.register_fingerprint(fingerprint, suggested_form_id)

    session.log_action("java_form_launch", url=function_url,
                       hwnd=hwnd, pid=session.java_pid, form_id=suggested_form_id,
                       form_name=form_display_name, repo_hit=False)

    return (
        f"Java form launched (HWND={hwnd}, PID={session.java_pid}). New form registered as '{suggested_form_id}'.\n"
        f"Active form: {form_display_name or '(unknown)'}\n"
        "Recording will use screenshots for AI and local Java DOM coordinate mapping."
    )


# ── Pre-flight: close stale Oracle windows ───────────────────────────────────


def close_existing_oracle_windows() -> list[int]:
    """
    Find every top-level window whose title starts with "Oracle Applications"
    and attempt a graceful close (same sequence as java_form_close).
    Returns the list of HWNDs that were found and targeted.
    """
    found: list[int] = []

    def _enum_cb(hwnd: int, _: None) -> bool:
        title = win32gui.GetWindowText(hwnd)
        if title.startswith("Oracle Applications") and win32gui.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    win32gui.EnumWindows(_enum_cb, None)

    for hwnd in found:
        print(f"[PreFlight] Closing stale Oracle window HWND={hwnd} …")
        try:
            win32gui.PostMessage(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        except Exception as exc:
            print(f"[PreFlight] Warning: could not close HWND={hwnd}: {exc}")

    if found:
        time.sleep(1)  # let JVM process exit
    return found


# ── Java form interactions ────────────────────────────────────────────────────


async def java_send_text(session: RecorderSession, ref: str, text: str) -> str:
    try:
        driver = await asyncio.to_thread(_get_java_agent_driver, session)
        element = resolve_java_ref(driver, session.current_form_id or "", ref)
        await asyncio.to_thread(element.send_text, text)
        session.log_action("java_send_text",
                           target={"form_id": session.current_form_id, "friendly_name": ref},
                           text=text)
        return (
            f"Typed {text!r} into '{ref}'.\n"
            f"### Replay code:\n```python\n"
            f"page.{_snake(ref)}.send_text({text!r}, simulate=True)\n```"
        )
    except Exception as exc:
        return f"Error in java_send_text: {exc}"


async def java_click(session: RecorderSession, ref: str) -> str:
    try:
        driver = await asyncio.to_thread(_get_java_agent_driver, session)
        element = resolve_java_ref(driver, session.current_form_id or "", ref)
        previous_form_id = session.current_form_id
        await asyncio.to_thread(element.click)
        session.log_action("java_click",
                           target={"form_id": session.current_form_id, "friendly_name": ref})
        post_click_snapshot = ""
        if session.java_pid:
            try:
                scan = await asyncio.to_thread(driver.scan)
                elements = java_nodes_to_repo_elements(scan)
                form_display_name = active_form_title(scan)
                title = win32gui.GetWindowText(session.java_hwnd or 0)
                fingerprint = fingerprint_java_form(title or form_display_name, elements[:50])
                form_id = repo_store.lookup_fingerprint(fingerprint)
                if not form_id:
                    form_id = suggest_form_id(form_display_name or title, surface="java")
                    repo_store.save_form({
                        "id": form_id,
                        "surface": "java",
                        "title": form_display_name or title,
                        "fingerprint": fingerprint,
                    })
                    repo_store.register_fingerprint(fingerprint, form_id)
                if form_id != previous_form_id:
                    session.current_form_id = form_id
                    post_click_snapshot = (
                        f"\n\nActive form changed after click: {previous_form_id!r} -> {form_id!r}\n"
                        f"Active form: {form_display_name or '(unknown)'}\n"
                        "Java DOM was refreshed locally; no DOM snapshot is sent to AI."
                    )
            except Exception as capture_exc:
                    print(f"[JavaAgent] Warning: post-click form capture failed: {capture_exc}")
        return (
            f"Clicked '{ref}'.\n"
            f"### Replay code:\n```python\npage.{_snake(ref)}.click()\n```"
            + post_click_snapshot
        )
    except Exception as exc:
        return f"Error in java_click: {exc}"


async def java_get_page_snapshot(session: RecorderSession) -> str:
    """Full Java-agent snapshot — only for unknown forms or major nav changes."""
    if not session.java_pid and not session.java_hwnd:
        return "No Java window in session."

    driver = await asyncio.to_thread(_get_java_agent_driver, session)
    scan = await asyncio.to_thread(driver.scan)
    elements = java_nodes_to_repo_elements(scan)
    form_display_name = active_form_title(scan)

    # Always fingerprint the actual active form so that popups / dialogs
    # that open on top of the main form are registered under their own
    # form_id and never overwrite the main form's element catalog.
    title = win32gui.GetWindowText(session.java_hwnd or 0)
    fingerprint = fingerprint_java_form(title or form_display_name, elements[:50])
    form_id = repo_store.lookup_fingerprint(fingerprint)
    if not form_id:
        form_id = suggest_form_id(form_display_name or title, surface="java")
        repo_store.save_form({
            "id": form_id,
            "surface": "java",
            "title": form_display_name or title,
            "fingerprint": fingerprint,
        })
        repo_store.register_fingerprint(fingerprint, form_id)
    if form_id != session.current_form_id:
        print(f"[JavaAgent] Active form changed: {session.current_form_id!r} -> {form_id!r}")
    session.current_form_id = form_id

    _save_java_capture_artifacts(session, form_id, driver, scan, elements, source="java_get_page_snapshot")

    session.log_action("java_get_page_snapshot", element_count=len(elements),
                       form_name=form_display_name, form_id=form_id)
    return (
        f"Active form: {form_display_name or '(unknown)'}\n"
        + "Java DOM captured locally; no DOM snapshot is sent to AI."
    )


async def java_form_close(session: RecorderSession) -> str:
    if not session.java_pid and not session.java_hwnd:
        return "No Java window in session."
    try:
        driver = await asyncio.to_thread(_get_java_agent_driver, session)
        await asyncio.to_thread(driver.press_key, "ALT+F4")
    except Exception as exc:
        return f"Could not close form: {exc}"

    session.log_action("java_form_close")
    return "Form close requested via Java agent (ALT+F4)."


# ── Approach B: snapshot-based action schema + parser ─────────────────────────

_VALID_ACTIONS: frozenset[str] = frozenset({"set_text", "click", "press_key", "assert", "done", "tree_action", "set_checkbox", "press_button", "set_poplist"})
_ELEMENT_ID_REQUIRED: frozenset[str] = frozenset({"set_text", "click", "assert", "tree_action", "set_checkbox", "press_button", "set_poplist"})
_TREE_OPS: frozenset[str] = frozenset({"select", "expand", "collapse", "activate"})
_ASSERTION_KINDS: frozenset[str] = frozenset({"text", "value", "visible", "enabled"})


class SnapshotActionError(ValueError):
    """Raised when the AI response cannot be parsed into a valid SnapshotAction."""


@dataclass
class SnapshotAction:
    """Validated AI response for one step in the Approach B recorder loop.

    Produced by ``parse_snapshot_action``; consumed by the Approach B
    execution layer (not yet wired into the recorder — Approach A is still live).
    """

    action: str                    # set_text | click | press_key | assert | done
    element_id: str | None = None  # required for set_text, click, assert
    value: str | None = None       # text to type / expected value / done note
    key: str | None = None         # key name for press_key
    assertion_kind: str | None = None  # text | value | visible | enabled


def parse_snapshot_action(
    raw_args: dict,
    valid_element_ids: set[str],
) -> SnapshotAction:
    """Parse and validate the AI's ``record_action`` tool-call arguments.

    Parameters
    ----------
    raw_args:
        The already-JSON-decoded ``arguments`` dict from the LLM tool call.
    valid_element_ids:
        The set of ``elementid`` keys returned by ``build_action_context`` for
        the current scan.  Any ``element_id`` not in this set is rejected.

    Returns
    -------
    SnapshotAction
        Validated action ready for deterministic execution.

    Raises
    ------
    SnapshotActionError
        When the action is unknown, a required field is missing, the
        element_id is absent from the snapshot, or assertion_kind is invalid.
    """
    action = str(raw_args.get("action") or "").strip()
    if not action:
        raise SnapshotActionError(
            "record_action: 'action' field is required but was empty or missing"
        )
    if action not in _VALID_ACTIONS:
        raise SnapshotActionError(
            f"record_action: unknown action {action!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_ACTIONS))}"
        )

    raw_eid = raw_args.get("element_id")
    element_id: str | None = str(raw_eid).strip() if raw_eid is not None else None
    if not element_id:
        element_id = None

    if action in _ELEMENT_ID_REQUIRED:
        if not element_id:
            raise SnapshotActionError(
                f"record_action: 'element_id' is required for action={action!r}"
            )
        if element_id not in valid_element_ids:
            raise SnapshotActionError(
                f"record_action: element_id={element_id!r} is not in the current "
                f"action snapshot. Use only element_ids that appear in the snapshot text."
            )

    raw_value = raw_args.get("value")
    value: str | None = str(raw_value) if raw_value is not None else None

    if action == "set_text" and not value:
        raise SnapshotActionError(
            "record_action: 'value' is required and must be non-empty for action='set_text'"
        )

    if action == "tree_action":
        if not value:
            raise SnapshotActionError(
                "record_action: 'value' is required and must be non-empty for action='tree_action'"
            )
        if value.strip().lower() not in _TREE_OPS:
            raise SnapshotActionError(
                f"record_action: unknown tree operation {value!r}. "
                f"Must be one of: {', '.join(sorted(_TREE_OPS))}"
            )

    if action == "set_checkbox":
        if not value:
            raise SnapshotActionError(
                "record_action: 'value' is required and must be non-empty for action='set_checkbox'"
            )
        if value.strip().lower() not in ("true", "false", "1", "0"):
            raise SnapshotActionError(
                f"record_action: unknown checked state {value!r}. "
                "Must be true or false."
            )

    if action == "set_poplist" and not value:
        raise SnapshotActionError(
            "record_action: 'value' is required and must be non-empty for action='set_poplist'"
        )

    raw_key = raw_args.get("key")
    key: str | None = str(raw_key).strip() if raw_key is not None else None
    if not key:
        key = None

    if action == "press_key" and not key:
        raise SnapshotActionError(
            "record_action: 'key' is required for action='press_key'"
        )

    raw_kind = raw_args.get("assertion_kind")
    assertion_kind: str | None = str(raw_kind).strip() if raw_kind is not None else None
    if not assertion_kind:
        assertion_kind = None

    if action == "assert":
        if not assertion_kind:
            raise SnapshotActionError(
                "record_action: 'assertion_kind' is required for action='assert'"
            )
        if assertion_kind not in _ASSERTION_KINDS:
            raise SnapshotActionError(
                f"record_action: unknown assertion_kind={assertion_kind!r}. "
                f"Must be one of: {', '.join(sorted(_ASSERTION_KINDS))}"
            )

    return SnapshotAction(
        action=action,
        element_id=element_id,
        value=value,
        key=key,
        assertion_kind=assertion_kind,
    )


def execute_resolved_action(
    driver: Any,
    element: dict,
    action: SnapshotAction,
) -> dict:
    """Execute a resolved repo element action via the Java-agent locator command path.

    Shared deterministic execution core for Approach B recording and replay:

    1. Build Java-agent locator params from *element* via
       ``locator_params(element)`` — the same function the driver and replay
       engine use internally.
    2. Dispatch ``click``, ``set_text``, or ``press_key`` through
       ``driver._run(command)`` — the identical wire path as
       ``JavaAgentDriver.click`` / ``.set_text`` and the replay engine
       ``JavaAgentElement.click`` / ``.send_text``.
    3. Return the raw agent result dict.

    ``assert`` and ``done`` are recording markers with no Java agent
    side-effect and return ``{}`` immediately.

    Parameters
    ----------
    driver:
        Attached ``JavaAgentDriver`` instance.
    element:
        Repo element descriptor (from ``build_action_context`` or
        ``repo_store.resolve_element_ref``).  Pass ``{}`` for
        ``press_key`` actions that carry no element context.
    action:
        Parsed and validated ``SnapshotAction``.

    Returns
    -------
    dict
        Raw agent result dict, or ``{}`` for assert/done.

    Raises
    ------
    CommandError
        If the Java agent rejects the command.  No coordinate fallback is
        performed here; healing handles that separately.
    ValueError
        If ``action.action`` is not a recognised command.
    """
    params = locator_params(element)
    act = action.action

    if act == "click":
        return driver._run({"command": "click", **params})

    if act == "set_text":
        encoded = base64.b64encode(
            (action.value or "").encode("utf-8")
        ).decode("ascii")
        return driver._run({"command": "settext", "text64": encoded, **params})

    if act == "press_key":
        cmd: dict[str, Any] = {"command": "presskey", "key": action.key or ""}
        cmd.update(params)  # element context for active field (empty dict is a no-op)
        return driver._run(cmd)

    if act == "tree_action":
        return driver._run({
            "command": "treeaction",
            "op": (action.value or "").strip().lower(),
            **params
        })

    if act == "set_checkbox":
        is_checked = (action.value or "").strip().lower() in ("true", "1")
        return driver._run({
            "command": "setcheckbox",
            "checked": "true" if is_checked else "false",
            **params
        })

    if act == "press_button":
        return driver._run({
            "command": "pressbutton",
            **params
        })

    if act == "set_poplist":
        return driver._run({
            "command": "setpoplist",
            "value": action.value or "",
            **params
        })

    if act in ("assert", "done"):
        return {}  # recording markers — no Java agent call at record time

    raise ValueError(
        f"execute_resolved_action: unsupported action {act!r}; "
        f"expected one of: click, set_text, press_key, tree_action, set_checkbox, press_button, set_poplist, assert, done"
    )


#: Tool schema for the Approach B AI contract.  The Approach B recorder loop
#: passes this as the sole tool so the model is forced to call it.
SNAPSHOT_ACTION_TOOL_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "record_action",
        "description": (
            "Declare the next UI action to perform on the Oracle Forms window. "
            "Call this ONCE per step. Use element_ids EXACTLY as they appear in "
            "the action snapshot — do not invent element_ids or use coordinates."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["set_text", "click", "press_key", "assert", "done", "tree_action", "set_checkbox", "press_button", "set_poplist"],
                    "description": (
                        "set_text — type a value into a field; "
                        "click — click a button/tab/menu item; "
                        "press_key — press a keyboard shortcut (no element target); "
                        "assert — verify an element's text, value, or state; "
                        "done — step is already complete or FAIL note; "
                        "tree_action — perform a tree operation (select, expand, collapse, activate) on a tree node; "
                        "set_checkbox — check or uncheck a checkbox; "
                        "press_button — press a button; "
                        "set_poplist — select an option in a poplist/dropdown."
                    ),
                },
                "element_id": {
                    "type": "string",
                    "description": (
                        "The element_id from the action snapshot, e.g. 'e12'. "
                        "Required for set_text, click, assert, tree_action, set_checkbox, press_button, and set_poplist. "
                        "Must appear verbatim in the snapshot — do NOT invent ids."
                    ),
                },
                "value": {
                    "type": "string",
                    "description": (
                        "For set_text: the exact text to type (required). "
                        "For assert: the expected text or value. "
                        "For done: optional note; prefix 'FAIL: ' to signal a failure. "
                        "For tree_action: the tree operation, one of: select, expand, collapse, activate (required). "
                        "For set_checkbox: the target checked state, one of: true, false (required). "
                        "For set_poplist: the option text or numeric index to select (required)."
                    ),
                },
                "key": {
                    "type": "string",
                    "description": (
                        "For press_key: canonical key name — TAB, ENTER, F10, F11, "
                        "ESC, ctrl+s, ctrl+F11, ALT+F4, etc. (required)."
                    ),
                },
                "assertion_kind": {
                    "type": "string",
                    "enum": ["text", "value", "visible", "enabled"],
                    "description": (
                        "For assert: text — inner display text; "
                        "value — field input value; "
                        "visible — element is shown; "
                        "enabled — element is editable/enabled."
                    ),
                },
            },
            "required": ["action"],
        },
    },
}


# ── Dispatcher ────────────────────────────────────────────────────────────────


async def dispatch(session: RecorderSession, tool_name: str, args: dict) -> str:
    """Route an LLM tool call to the right in-process function."""
    _map: dict[str, Any] = {
        "session_start":           session_start,
        "repo_get_form":           repo_get_form,
        "repo_register_element":   repo_register_element,
        "repo_invoke_flow":        repo_invoke_flow,
        "record_assertion":        record_assertion,
        "record_data_placeholder": record_data_placeholder,
        "record_flow_boundary":    record_flow_boundary,
        "oracle_form_open":        oracle_form_open,
        "java_form_launch":        java_form_launch,
        "java_send_text":          java_send_text,
        "java_click":              java_click,
        "java_get_page_snapshot":  java_get_page_snapshot,
        "java_form_close":         java_form_close,
        "ebs_login":               ebs_login,
    }
    fn = _map.get(tool_name)
    if fn is None:
        raise ValueError(f"Unknown in-process tool: {tool_name!r}")
    return await fn(session, **args)


# ── OpenAI function-calling schemas ───────────────────────────────────────────
# These are what the LLM sees — same names and descriptions as before.

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "ebs_login",
            "description": (
                "Record the Oracle EBS login step. Call this SECOND (right after "
                "session_start) with the login URL and the env-var names that hold "
                "the username and password. After calling this tool, follow its "
                "instructions to drive the actual browser login via Playwright MCP."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Full EBS login page URL.",
                    },
                    "user_env": {
                        "type": "string",
                        "description": "Environment variable name holding the username (default: EBS_USER).",
                    },
                    "password_env": {
                        "type": "string",
                        "description": "Environment variable name holding the password (default: EBS_PASSWORD).",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "session_start",
            "description": (
                "Call this at the beginning of every recording session to initialise "
                "the run directory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "run_id": {
                        "type": "string",
                        "description": "Unique run identifier, e.g. 'run_001'",
                    },
                },
                "required": ["run_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "repo_get_form",
            "description": (
                "Return the element catalog for a known Oracle form. "
                "Call this immediately after a form opens to avoid a full snapshot. "
                "Returns an empty list if the form is not yet in the repository."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "form_id": {
                        "type": "string",
                        "description": "The form_id from the repository index.",
                    },
                },
                "required": ["form_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "repo_register_element",
            "description": (
                "Review/debug helper for registering an element in the repository. "
                "Normal recording stores actioned elements automatically by mapping "
                "computer-use coordinates to Java DOM locally."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "form_id": {
                        "type": "string",
                        "description": "The form_id this element belongs to.",
                    },
                    "element": {
                        "type": "object",
                        "description": (
                            "Element dict with at least: role, name. "
                            "Optional: label_neighbor, ancestors, xpath, bounds."
                        ),
                    },
                },
                "required": ["form_id", "element"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "repo_invoke_flow",
            "description": (
                "Execute a named reusable flow from the repository "
                "(e.g. betsy_login, open_responsibility). "
                "Prefer this over re-driving known sequences step-by-step."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_id": {
                        "type": "string",
                        "description": "The flow_id from the repository.",
                    },
                    "params": {
                        "type": "object",
                        "description": "Parameter values for the flow's params list.",
                    },
                },
                "required": ["flow_id", "params"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_assertion",
            "description": (
                "Record a verification point: assert that an element has the expected "
                "text or state. Call after every significant action."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "friendly_name or eXX ref of the element to assert.",
                    },
                    "expected_text": {
                        "type": "string",
                        "description": "Expected visible text or value.",
                    },
                    "expected_state": {
                        "type": "string",
                        "description": "Expected accessibility state, e.g. 'enabled'.",
                    },
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_data_placeholder",
            "description": (
                "Mark a value as a test-data placeholder that will come from the Excel "
                "data file. Call this before or instead of hardcoding a value. "
                "Example: record_data_placeholder('Order Type', 'Standard_ISVUS')"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "column_name": {
                        "type": "string",
                        "description": "Excel column header name.",
                    },
                    "sample_value": {
                        "type": "string",
                        "description": "The actual value used in this recording run.",
                    },
                },
                "required": ["column_name", "sample_value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_flow_boundary",
            "description": (
                "Declare the start or end of a reusable flow. "
                "Use 'begin' before the first step and 'end' after the last."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_id": {
                        "type": "string",
                        "description": "Unique id for the flow, e.g. 'betsy_login'.",
                    },
                    "params": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of parameter names the flow accepts.",
                    },
                    "boundary": {
                        "type": "string",
                        "enum": ["begin", "end"],
                        "description": "'begin' or 'end'.",
                    },
                },
                "required": ["flow_id", "params", "boundary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "oracle_form_open",
            "description": (
                "Resolve the Oracle EBS function URL for a given search text using "
                "Azure AI Search. Call IMMEDIATELY after login is successful — do NOT "
                "navigate to a responsibility or menu first. This tool handles "
                "responsibility selection and function navigation internally. Pass the "
                "returned URL to the browser, then call java_form_launch if JNLP starts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "search_text": {
                        "type": "string",
                        "description": "Description of the form to open.",
                    },
                    "base_url": {
                        "type": "string",
                        "description": "EBS base URL (no trailing slash). Omit to use the EBS_URL env var.",
                    },
                },
                "required": ["search_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "java_form_launch",
            "description": (
                "DO NOT CALL — the Python agent launches the Java form automatically "
                "after oracle_form_open. The form is already open when you receive "
                "the oracle_form_open result. Recording uses screenshots for AI and "
                "local Java DOM coordinate mapping; no Java DOM snapshot is returned."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "function_url": {
                        "type": "string",
                        "description": "The RF.jsp URL returned by oracle_form_open.",
                    },
                },
                "required": ["function_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "java_send_text",
            "description": (
                "Type text into a field in the Oracle Java form. "
                "Prefer friendly_name (from repo_get_form) over eXX refs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {
                        "type": "string",
                        "description": "friendly_name or eXX element reference.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to type into the field.",
                    },
                },
                "required": ["ref", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "java_click",
            "description": "Click a button or element in the Oracle Java form.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {
                        "type": "string",
                        "description": "friendly_name or eXX element reference.",
                    },
                },
                "required": ["ref"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "java_get_page_snapshot",
            "description": (
                "Debug-only local Java DOM refresh. Do not call during normal recording; "
                "Java DOM is not sent to AI."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "java_form_close",
            "description": "Close the Oracle Java form after all steps are complete.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]

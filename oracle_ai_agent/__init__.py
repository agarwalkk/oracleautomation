"""
oracle_ai_agent — AI recorder orchestrator.

Reads English instructions from a file, connects to the Playwright MCP server
(external Node process), runs an Azure OpenAI LLM in an agentic loop, and
produces a recording.jsonl artifact plus a repo delta.

Java Forms / Oracle tools are dispatched **in-process** via
``oracle_ai_agent.tools`` — no separate MCP server required, no port 9001.

The LLM is instructed to:
  1. Use repo_get_form / repo_invoke_flow when a form/flow is already known.
  2. Register new elements via repo_register_element (not guess locators).
  3. Mark per-test values as placeholders via record_data_placeholder.
  4. Record verification points via record_assertion.
    5. NOT generate Python code — deterministic codegen runs after recording.

Usage:
    python -m oracle_ai_agent --instructions instructions.txt \\
                               --run-id run_001 \\
                               [--data data.xlsx] \\
                               [--auto-name]
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp
import httpx
import win32gui
from fastmcp import Client
from fastmcp.client.transports import NpxStdioTransport, SSETransport, StreamableHttpTransport

import config
from qcs_java_agent import active_form_title, analyze_forms_readiness, java_nodes_to_repo_elements, wait_for_forms_ready
from qcs_repo import store as repo_store
from qcs_repo.fingerprint import fingerprint_java_form, suggest_form_id
from oracle_ai_agent.tools import (
    RecorderSession, dispatch, TOOL_SCHEMAS,
    close_existing_oracle_windows,
)

# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an Oracle EBS test automation recording agent.

Your job is to faithfully execute a set of English test instructions against a
live Oracle EBS instance, recording every UI interaction as structured actions.

## Rules

1. **Session and login are already complete.** The Python agent called
   `session_start` and performed the full EBS browser login before this AI
   loop started. Do NOT call `session_start`, `ebs_login`, `browser_navigate`
   to the login page, or fill any login credentials — that is already done.
   The browser is authenticated and on the EBS home page.

2. **Your first action must be `oracle_form_open`** with the form / function
   name from the instructions. Do not navigate anywhere first.

3. **Opening an Oracle Form**:
    a. Call `oracle_form_open` with the form / function name from the
        instructions — do NOT navigate to a responsibility or menu item first.
        The tool handles responsibility selection and function navigation
        internally.
    b. The Python agent automatically navigates the browser, downloads the JNLP,
        launches the Java form, and uses screenshots plus local Java DOM
        coordinate mapping. Do NOT ask for or rely on Java DOM snapshots.

4. **Reusable flows**: call `repo_invoke_flow` ONLY if a tool response has
   already confirmed that the flow exists in the repository. Do NOT call it
   speculatively on names like "login" or "ebs_login" for a fresh recording —
   those flows do not exist until they are tagged and committed.

5. **Interacting with elements**: use screenshot computer-use actions. The
     Python recorder maps action coordinates to Java DOM locally and records repo
     refs. Do not request Java DOM or raw element refs.

6. **Placeholders**: for any value that is test-data (order number, date, user
   id, …), call `record_data_placeholder(column_name, sample_value)` before
   the fill action.

7. **Assertions**: after every significant action, call `record_assertion` with
   the element and the expected result.

8. **Do NOT generate Python code**. The deterministic generator handles that
    after recording completes.

9. **Closing**: close Java forms with `java_form_close` only if the instructions
    say to close.

11. **Screenshots and snapshots**: screenshots are the AI surface for Forms.
    Java DOM stays local to the Python/Java-agent mapper and is not sent to AI.

## Flow boundary tagging

When you execute a block of steps that you recognise as a reusable sequence
(login, oracle_form_open, …), bracket it with:
  `record_flow_boundary(flow_id, params, "begin")`
  … steps …
  `record_flow_boundary(flow_id, params, "end")`

The codegen step will extract it as a shared flow callable.
"""

# ── LLM call ──────────────────────────────────────────────────────────────────

async def call_llm(messages: list[dict], tools: list[dict]) -> dict:
    if not config.AZURE_OPENAI_ENDPOINT:
        raise RuntimeError(
            "AZURE_OPENAI_ENDPOINT is not set. "
            "Export it before running qcs record:\n"
            "  $env:AZURE_OPENAI_ENDPOINT = 'https://<resource>.openai.azure.com'"
        )
    if not config.AZURE_OPENAI_API_KEY:
        raise RuntimeError(
            "AZURE_OPENAI_API_KEY is not set. "
            "Export it before running qcs record:\n"
            "  $env:AZURE_OPENAI_API_KEY = '<key>'"
        )
    url = (
        f"{config.AZURE_OPENAI_ENDPOINT.rstrip('/')}/openai/deployments/"
        f"{config.AZURE_OPENAI_DEPLOYMENT}/chat/completions"
        f"?api-version={config.AZURE_OPENAI_API_VERSION}"
    )
    headers = {
        "api-key":      config.AZURE_OPENAI_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {"messages": messages, "tools": tools, "max_tokens": 8000}

    for attempt in range(3):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status >= 400:
                        body = await resp.text()
                        raise RuntimeError(
                            f"Azure OpenAI returned {resp.status}: {body[:500]}"
                        )
                    return await resp.json()
        except (aiohttp.ClientConnectorError, aiohttp.ClientConnectorDNSError) as exc:
            if attempt == 2:
                raise
            print(f"  [LLM] Network error (attempt {attempt + 1}/3): {exc}. Retrying in 3 s…")
            await asyncio.sleep(3)
    raise RuntimeError("Azure OpenAI: all 3 connection attempts failed.")


# ── Playwright MCP transport ──────────────────────────────────────────────────

def _make_pw_transport():
    """Return a Playwright MCP transport.

    Strategy (in order):
    1. If QCS_PLAYWRIGHT_MCP_USE_STDIO=1 (or default), use NpxStdioTransport —
       runs @playwright/mcp as a subprocess over stdio. No HTTP server needed,
       no CORS / --allowed-origins issues.
    2. If QCS_PLAYWRIGHT_MCP_URL is set to a custom URL, probe it:
       - 200/405  → StreamableHttpTransport
       - 403      → SSETransport (fallback to /sse endpoint)
       - unreachable (15 s timeout) → RuntimeError
    """
    import os, time
    use_stdio = os.environ.get("QCS_PLAYWRIGHT_MCP_USE_STDIO", "1")
    if use_stdio.strip() not in ("0", "false", "no"):
        print("[Playwright MCP] Using stdio transport (NpxStdioTransport) — no HTTP server needed.")
        return NpxStdioTransport(
            "@playwright/mcp@latest",
            use_package_lock=False,
        )

    # HTTP mode (opt-in via QCS_PLAYWRIGHT_MCP_USE_STDIO=0)
    mcp_url = config.PLAYWRIGHT_MCP_URL
    sse_url  = mcp_url.replace("/mcp", "/sse")
    deadline = time.monotonic() + 15
    last_exc = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(mcp_url, timeout=3)
            if r.status_code == 403:
                print(
                    f"[Playwright MCP] 403 at {mcp_url} — falling back to SSE at {sse_url}.\n"
                    f"  To fix: restart Playwright MCP with --allowed-origins '*'\n"
                )
                return SSETransport(sse_url)
            return StreamableHttpTransport(mcp_url)
        except httpx.RequestError as exc:
            last_exc = exc
            time.sleep(1)
    raise RuntimeError(
        f"Playwright MCP at {mcp_url} not ready after 15 s: {last_exc}"
    )


# ── Deterministic login (no AI) ───────────────────────────────────────────────

def _find_ref(snapshot_text: str, label: str) -> str | None:
    """Extract the accessibility ref token for a labelled element.

    @playwright/mcp snapshot format examples:
      - textbox "User Name" [ref=e12]
      - button "Log In" [ref=e45]
    """
    for pattern in [
        rf'"{re.escape(label)}"[^\[]*\[ref=(e\d+)\]',
        rf'\b{re.escape(label)}\b[^\[]*\[ref=(e\d+)\]',
    ]:
        m = re.search(pattern, snapshot_text, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


async def _deterministic_login(
    session: RecorderSession,
    pw_client,
) -> None:
    """Perform EBS login deterministically using Playwright MCP — zero AI calls.

    Reads URL/credentials from config (which loads .env).  Records the
    ebs_login op so the code generator can emit deterministic replay code.
    """
    url      = config.EBS_URL
    user_val = config.EBS_USER
    pass_val = config.EBS_PASSWORD

    if not url:
        raise RuntimeError("EBS_URL is not set. Add it to .env")
    if not user_val:
        raise RuntimeError("EBS_USER is not set. Add it to .env")
    if not pass_val:
        raise RuntimeError("EBS_PASSWORD is not set. Add it to .env")

    # ── 1. Navigate ───────────────────────────────────────────────────────────
    print(f"[Login] Navigating to {url} …")
    await pw_client.call_tool("browser_navigate", {"url": url})

    # ── 2. Snapshot to locate field refs (target is required by browser_fill_form)
    print("[Login] Capturing snapshot to locate field refs …")
    snap_result = await pw_client.call_tool("browser_snapshot", {})
    snap_text   = str(snap_result)

    user_ref  = _find_ref(snap_text, "User Name")
    pass_ref  = _find_ref(snap_text, "Password")
    login_ref = _find_ref(snap_text, "Log In")

    if not user_ref or not pass_ref:
        raise RuntimeError(
            f"Could not find login field refs in snapshot "
            f"(user_ref={user_ref}, pass_ref={pass_ref}). "
            "Check that the EBS login page loaded correctly."
        )
    print(f"[Login] Found refs: user={user_ref} pass={pass_ref} login={login_ref}")

    # ── 3. Fill both fields in one call ───────────────────────────────────────
    print("[Login] Filling credentials …")
    await pw_client.call_tool("browser_fill_form", {
        "fields": [
            {"target": user_ref, "name": "User Name", "type": "textbox", "value": user_val},
            {"target": pass_ref, "name": "Password",  "type": "textbox", "value": pass_val},
        ]
    })

    # ── 4. Click Log In ───────────────────────────────────────────────────────
    print("[Login] Clicking Log In …")
    click_args: dict = {"element": "Log In button"}
    if login_ref:
        click_args["target"] = login_ref
    await pw_client.call_tool("browser_click", click_args)

    # ── 5. Wait for post-login page ───────────────────────────────────────────
    print("[Login] Waiting for post-login page …")
    try:
        await pw_client.call_tool("browser_wait_for", {"time": 5})
    except Exception:
        await asyncio.sleep(5)

    # ── 6. Record the marker so codegen can emit deterministic replay ─────────
    await dispatch(session, "ebs_login", {
        "url":          url,
        "user_env":     "EBS_USER",
        "password_env": "EBS_PASSWORD",
    })
    print("[Login] Login complete.")


# ── Shared: deterministic oracle_form_open → JNLP → java_form_launch ─────────

async def _open_oracle_form(
    session: "RecorderSession",
    pw_client,
    form_search_text: str,
) -> str:
    """Deterministically open an Oracle Forms function and return the launch result.

    1. Calls oracle_form_open to resolve the function URL.
    2. Navigates the browser to the RF.jsp URL.
    3. Detects the JNLP download and launches javaws.
    4. Calls java_form_launch to attach the Java agent, fingerprint, and capture the form.
    Returns the java_form_launch result text (includes element snapshot).
    """
    import subprocess  # noqa: PLC0415

    form_url = await dispatch(session, "oracle_form_open", {"search_text": form_search_text})
    if not form_url.startswith("http"):
        raise RuntimeError(f"oracle_form_open failed: {form_url}")

    print(f"[FormOpen] browser_navigate -> {form_url}")
    nav_text = str(await pw_client.call_tool("browser_navigate", {"url": form_url}))

    jnlp_match = re.search(r'Downloaded file \S+\.jnlp to "([^"]+)"', nav_text)
    if jnlp_match:
        jnlp_path = Path(jnlp_match.group(1))
        if not jnlp_path.is_absolute():
            jnlp_path = Path.cwd() / jnlp_path
        print(f"[FormOpen] Launching javaws {jnlp_path} ...")
        subprocess.Popen(["javaws", str(jnlp_path)], shell=True)
    else:
        print("[FormOpen] No JNLP download detected — assuming embedded JVM launch.")

    print("[FormOpen] Waiting for Oracle Forms window ...")
    launch_result = await dispatch(session, "java_form_launch", {"function_url": form_url})
    print(f"[FormOpen] java_form_launch: {launch_result[:120]}")
    return launch_result


def _numbered_instruction_lines(instructions: str) -> list[str]:
    return [
        line.strip()
        for line in instructions.splitlines()
        if line.strip() and line.strip()[0].isdigit()
    ]


def _write_diag_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _diag_step_dir(session: RecorderSession, label: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", label).strip("_") or "step"
    step_dir = session.run_dir / "diagnostics" / safe
    step_dir.mkdir(parents=True, exist_ok=True)
    return step_dir


def _redact_ai_images(value: Any, screenshot_name: str) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == "image_url" and isinstance(item, str) and item.startswith("data:image/"):
                result[key] = f"<saved screenshot: {screenshot_name}>"
            else:
                result[key] = _redact_ai_images(item, screenshot_name)
        return result
    if isinstance(value, list):
        return [_redact_ai_images(item, screenshot_name) for item in value]
    return value


def _save_ai_request(step_dir: Path, payload: dict, screenshot_name: str) -> None:
    redacted = _redact_ai_images(copy.deepcopy(payload), screenshot_name)
    _write_diag_json(step_dir / "ai_request.json", redacted)


def _save_ai_response(step_dir: Path, response: dict) -> None:
    _write_diag_json(step_dir / "ai_response.json", response)


def _save_pre_ai_diagnostics(
    session: RecorderSession,
    driver: Any,
    label: str,
    b64: str,
    win_rect: tuple[int, int, int, int],
) -> Path:
    from oracle_ai_agent.play import _save_screenshot  # noqa: PLC0415

    step_dir = _diag_step_dir(session, label)
    screenshot_name = "screen_before_ai.png"
    _save_screenshot(b64, step_dir / screenshot_name)
    try:
        scan = driver.scan()
        readiness = analyze_forms_readiness(scan)
        _write_diag_json(step_dir / "java_dom.json", scan)
        _write_diag_json(step_dir / "java_elements.json", java_nodes_to_repo_elements(scan))
        _write_diag_json(step_dir / "readiness.json", readiness.__dict__)
        _write_diag_json(step_dir / "window.json", {
            "hwnd": session.java_hwnd,
            "pid": session.java_pid,
            "rect": win_rect,
            "current_form_id": session.current_form_id,
            "active_title": active_form_title(scan),
            "window_title": win32gui.GetWindowText(session.java_hwnd or 0),
        })
        session.log_diagnostic(
            "pre_ai_capture",
            label=label,
            directory=str(step_dir.relative_to(session.run_dir)),
            title=readiness.title,
            actionable_count=readiness.actionable_count,
            busy=readiness.busy,
            ready=readiness.ready,
            reason=readiness.reason,
        )
    except Exception as exc:
        _write_diag_json(step_dir / "java_dom_error.json", {"error": str(exc)})
        session.log_diagnostic("pre_ai_capture_error", label=label, error=str(exc))
    return step_dir


def _action_screen_xy(action: dict, win_rect: tuple[int, int, int, int]) -> tuple[int, int] | None:
    act = (action.get("type") or action.get("action") or "").lower()
    if act not in {"click", "left_click", "right_click", "double_click", "dblclick", "move", "scroll"}:
        return None
    return win_rect[0] + int(action.get("x", 0)), win_rect[1] + int(action.get("y", 0))


def _record_actioned_element(
    session: RecorderSession,
    driver: Any,
    screen_x: int,
    screen_y: int,
    diagnostic_dir: Path | None = None,
) -> dict | None:
    from qcs_java_agent import actioned_element_at, java_component_result_to_repo_element  # noqa: PLC0415

    if not session.current_form_id:
        return None
    diagnostic: dict[str, Any] = {
        "screen_x": screen_x,
        "screen_y": screen_y,
        "form_id": session.current_form_id,
    }
    try:
        scan = driver.scan()
        scan_element = actioned_element_at(scan, screen_x, screen_y)
        diagnostic["scan_candidate"] = scan_element
        result = driver.element_at(screen_x, screen_y)
        diagnostic["java_elementat_result"] = result
        element = java_component_result_to_repo_element(result)
        if scan_element and _is_better_action_target(scan_element, element):
            element = scan_element
            diagnostic["chosen_source"] = "scan_actionable_candidate"
        else:
            diagnostic["chosen_source"] = "java_elementat"
        diagnostic["chosen_element"] = element
        saved = repo_store.upsert_actioned_element(
            session.current_form_id,
            element,
            source=f"recording:{session.run_id}",
        )
        diagnostic["saved_element"] = saved
        if diagnostic_dir:
            _write_diag_json(diagnostic_dir / "coordinate_mapping.json", diagnostic)
        return saved
    except Exception as exc:
        diagnostic["error"] = str(exc)
        if diagnostic_dir:
            _write_diag_json(diagnostic_dir / "coordinate_mapping.json", diagnostic)
        print(f"[Record:CU] Warning: could not map click at {screen_x},{screen_y}: {exc}")
        return None


def _is_better_action_target(candidate: dict, current: dict) -> bool:
    actionable = {"Field", "TextArea", "Button", "List", "LOV", "ComboBox", "Checkbox", "RadioButton", "Menu", "MenuItem", "Tab", "Grid", "Table", "Tree"}
    generic = {"Panel", "Canvas", "DrawnPanel", "Component", "Unknown"}
    candidate_role = str(candidate.get("role") or "")
    current_role = str(current.get("role") or "")
    if candidate_role not in actionable:
        return False
    if current_role in generic or current_role not in actionable:
        return True
    cand_area = int(candidate.get("width") or 0) * int(candidate.get("height") or 0)
    curr_area = int(current.get("width") or 0) * int(current.get("height") or 0)
    return bool(cand_area and curr_area and cand_area < curr_area)


def _refresh_current_form_metadata(session: RecorderSession, driver: Any) -> None:
    """Refresh active form id/fingerprint locally without saving full element catalogs."""
    try:
        scan = driver.scan()
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
        if form_id != session.current_form_id:
            print(f"[Record:CU] Active form changed: {session.current_form_id!r} -> {form_id!r}")
        session.current_form_id = form_id
    except Exception as exc:
        print(f"[Record:CU] Warning: active form refresh failed: {exc}")


def _execute_recording_action(
    session: RecorderSession,
    driver: Any,
    action: dict,
    win_rect: tuple[int, int, int, int],
    next_action: dict | None,
    last_target: dict | None,
    diagnostic_dir: Path | None = None,
) -> dict | None:
    from oracle_ai_agent.play import _execute_single_action  # noqa: PLC0415

    act = (action.get("type") or action.get("action") or "").lower()
    xy = _action_screen_xy(action, win_rect)
    if diagnostic_dir:
        _write_diag_json(diagnostic_dir / "action_before.json", {
            "action": action,
            "window_rect": win_rect,
            "screen_xy": xy,
            "last_target": last_target,
            "current_form_id": session.current_form_id,
        })
    mapped = None
    if xy is not None:
        mapped = _record_actioned_element(session, driver, xy[0], xy[1], diagnostic_dir=diagnostic_dir)

    _execute_single_action(action, win_rect)

    next_act = (next_action or {}).get("type") or (next_action or {}).get("action") or ""
    next_is_type = str(next_act).lower() == "type"
    if mapped and act in {"click", "left_click", "right_click", "double_click", "dblclick"} and not next_is_type:
        session.log_action(
            "java_click",
            target={
                "form_id": session.current_form_id,
                "friendly_name": mapped.get("semantic_ref") or mapped.get("friendly_name"),
            },
        )

    if act == "type":
        text = str(action.get("text") or "")
        target = last_target
        if target and text:
            session.log_action(
                "java_send_text",
                target={
                    "form_id": session.current_form_id,
                    "friendly_name": target.get("semantic_ref") or target.get("friendly_name"),
                },
                text=text,
            )
    elif act in {"keypress", "key_press", "key"}:
        keys = action.get("keys") or action.get("text") or ""
        if isinstance(keys, list):
            key_text = "+".join(str(key) for key in keys)
        else:
            key_text = str(keys)
        if key_text:
            session.log_action("java_press_key", key=key_text)

    time.sleep(0.2)
    _refresh_current_form_metadata(session, driver)
    if diagnostic_dir:
        try:
            scan = driver.scan()
            _write_diag_json(diagnostic_dir / "java_dom_after_action.json", scan)
            _write_diag_json(diagnostic_dir / "action_after.json", {
                "mapped_target": mapped,
                "returned_target": mapped or last_target,
                "current_form_id": session.current_form_id,
                "active_title": active_form_title(scan),
            })
        except Exception as exc:
            _write_diag_json(diagnostic_dir / "action_after_error.json", {"error": str(exc)})
    return mapped or last_target


async def _run_computer_use_recorder(
    session: RecorderSession,
    pw_client,
    instructions: str,
) -> None:
    from oracle_ai_agent.play import (  # noqa: PLC0415
        _MAX_ITERATIONS,
        _capture_b64,
        _extract_actions,
        _extract_computer_calls,
        _final_text,
        _is_preview_model,
        _load_system_prompt,
        _responses_call,
        _save_screenshot,
        _tool_def,
    )
    from qcs_java_agent import JavaAgentDriver  # noqa: PLC0415

    numbered = _numbered_instruction_lines(instructions)
    if not numbered:
        raise RuntimeError("No numbered steps found in instructions.txt")

    form_step = numbered[0]
    remaining_steps = numbered[1:]
    print(f"[Record:CU] Opening form deterministically from: {form_step}")
    await _open_oracle_form(session, pw_client, form_step)
    if not session.java_hwnd or not session.java_pid:
        raise RuntimeError("No Java Forms window after deterministic form open")

    driver = JavaAgentDriver(pid=session.java_pid)
    driver.health()
    await asyncio.to_thread(wait_for_forms_ready, driver, log_prefix="[Record:CU]")
    _refresh_current_form_metadata(session, driver)

    shots_dir = session.run_dir / "screenshots"
    await asyncio.to_thread(wait_for_forms_ready, driver, log_prefix="[Record:CU]")
    b64, win_rect = await asyncio.to_thread(_capture_b64, session.java_hwnd)
    _save_screenshot(b64, shots_dir / "record_000_initial.png")
    current_response_dir = _save_pre_ai_diagnostics(session, driver, "000_initial", b64, win_rect)

    model = config.CU_MODEL
    tool = _tool_def(win_rect[2] - win_rect[0], win_rect[3] - win_rect[1], model)
    is_preview = _is_preview_model(model)
    task_text = (
        "The Oracle form is already open. Complete these steps using the computer tool. "
        "Use screenshots only. Do not ask for Java DOM or element refs.\n\n"
        "Steps:\n" + "\n".join(f"  {step}" for step in remaining_steps)
    )

    input_messages: list[dict] = []
    system_prompt = _load_system_prompt()
    if system_prompt:
        input_messages.append({"role": "system", "content": system_prompt})
    input_messages.append({
        "role": "user",
        "content": [
            {"type": "input_image", "image_url": f"data:image/png;base64,{b64}", "detail": "auto"},
            {"type": "input_text", "text": task_text},
        ],
    })
    payload: dict = {"model": model, "input": input_messages, "tools": [tool]}
    if is_preview:
        payload["truncation"] = "auto"
    _save_ai_request(current_response_dir, payload, "screen_before_ai.png")

    print("[Record:CU] Sending screenshot-only task to computer-use model ...")
    async with aiohttp.ClientSession() as http:
        response = await _responses_call(http, payload)
        _save_ai_response(current_response_dir, response)
        session.log_diagnostic(
            "ai_response",
            label="000_initial",
            response_id=response.get("id"),
            directory=str(current_response_dir.relative_to(session.run_dir)),
        )
        response_id = response.get("id")
        last_target: dict | None = None

        for iteration in range(_MAX_ITERATIONS):
            calls = _extract_computer_calls(response.get("output", []))
            _write_diag_json(current_response_dir / "computer_calls.json", calls)
            if not calls:
                final = _final_text(response.get("output", []))
                if final:
                    _write_diag_json(current_response_dir / "final_answer.json", {"text": final})
                    session.log_diagnostic(
                        "ai_final_answer",
                        label=current_response_dir.name,
                        text=final,
                        directory=str(current_response_dir.relative_to(session.run_dir)),
                    )
                    print(f"[Record:CU] Model final answer:\n{final}")
                break

            for call_index, call in enumerate(calls):
                call_id = call.get("call_id") or call.get("id") or f"call_{call_index}"
                actions = _extract_actions(call)
                label = f"record_iter{iteration + 1}_call{call_index + 1}"
                _write_diag_json(current_response_dir / f"{label}_actions.json", actions)
                rect = win32gui.GetWindowRect(session.java_hwnd)
                for index, action in enumerate(actions):
                    next_action = actions[index + 1] if index + 1 < len(actions) else None
                    action_name = str(action.get("type") or action.get("action") or "action")
                    action_dir = current_response_dir / f"action_{index + 1:02d}_{re.sub(r'[^a-zA-Z0-9_.-]+', '_', action_name)}"
                    print(f"  [{label}.{index + 1}] {action.get('type') or action.get('action')}")
                    last_target = _execute_recording_action(
                        session, driver, action, rect, next_action, last_target, diagnostic_dir=action_dir
                    )
                    session.log_diagnostic(
                        "computer_action",
                        label=label,
                        action_index=index + 1,
                        action=action_name,
                        directory=str(action_dir.relative_to(session.run_dir)),
                    )
                    rect = win32gui.GetWindowRect(session.java_hwnd)

                await asyncio.to_thread(wait_for_forms_ready, driver, log_prefix="[Record:CU]")
                b64, win_rect = await asyncio.to_thread(_capture_b64, session.java_hwnd)
                _save_screenshot(b64, shots_dir / f"{label}.png")
                next_response_dir = _save_pre_ai_diagnostics(session, driver, label, b64, win_rect)
                followup: dict = {
                    "model": model,
                    "previous_response_id": response_id,
                    "input": [{
                        "type": "computer_call_output",
                        "call_id": call_id,
                        "output": {
                            "type": "computer_screenshot",
                            "image_url": f"data:image/png;base64,{b64}",
                            "detail": "auto",
                        },
                    }],
                    "tools": [tool],
                }
                if is_preview:
                    followup["truncation"] = "auto"
                _save_ai_request(next_response_dir, followup, "screen_before_ai.png")
                response = await _responses_call(http, followup)
                _save_ai_response(next_response_dir, response)
                session.log_diagnostic(
                    "ai_response",
                    label=label,
                    response_id=response.get("id"),
                    directory=str(next_response_dir.relative_to(session.run_dir)),
                )
                response_id = response.get("id")
                current_response_dir = next_response_dir
        else:
            print(f"[Record:CU] Reached max iterations ({_MAX_ITERATIONS}).")


# ── Agentic loop ──────────────────────────────────────────────────────────────

async def run_agent(
    instructions: str,
    run_id: str,
    data_columns: list[str] | None = None,
    auto_name: bool = False,
) -> None:
    """
    Run the AI recorder loop.

    Connects to Playwright MCP (external, browser); Oracle/Java-agent tools run
    in-process via oracle_ai_agent.tools.dispatch — no separate server needed.
    """
    # Close any stale Oracle Forms windows left over from a previous run
    closed = close_existing_oracle_windows()
    if closed:
        print(f"[Agent] Closed {len(closed)} stale Oracle window(s) before starting.")

    session = RecorderSession(run_id, auto_name=auto_name)

    pw_transport = _make_pw_transport()

    async with Client(pw_transport) as pw_client:
        pw_tools      = await pw_client.list_tools()
        pw_tool_names = {t.name for t in pw_tools}

        def _pw_schema(t) -> dict:
            return {
                "type": "function",
                "function": {
                    "name":        t.name,
                    "description": t.description or "",
                    "parameters":  t.inputSchema or {"type": "object", "properties": {}},
                },
            }

        # Playwright tools from MCP + Oracle/Java-agent tools defined in-process
        all_tools = [_pw_schema(t) for t in pw_tools] + TOOL_SCHEMAS

        # ── Deterministic preamble (no AI calls) ──────────────────────────────
        print(f"[Agent] session_start → {run_id}")
        await dispatch(session, "session_start", {"run_id": run_id})

        print("[Agent] Performing deterministic EBS login …")
        await _deterministic_login(session, pw_client)

        await _run_computer_use_recorder(session, pw_client, instructions)

        with open(session.run_dir / "messages.json", "w", encoding="utf-8") as f:
            json.dump([
                {"role": "system", "content": "screenshot-only computer-use recorder"},
                {"role": "user", "content": instructions},
            ], f, indent=2, ensure_ascii=False)

        print(f"\nRecording complete. Artifacts in: {session.run_dir}")
        return

        # Tell the AI that login is done; feed back the original instructions
        # so it can proceed from oracle_form_open onwards.
        user_content = (
            f"Run ID: {run_id}\n\n"
            "EBS login has been completed automatically by the Python agent.\n"
            "The browser is authenticated and on the EBS home page.\n"
            "Your first action must be `oracle_form_open`.\n\n"
            f"Original instructions (login steps are already done):\n{instructions}"
        )
        if data_columns:
            user_content += f"\n\nExcel columns (placeholders): {', '.join(data_columns)}"

        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_content},
        ]

        max_iterations = 60
        iteration = 0

        while iteration < max_iterations:
            iteration += 1
            print(f"\n── Iteration {iteration} ──────────────────────────────")

            resp = await call_llm(messages, all_tools)

            if "error" in resp:
                print(f"LLM error: {resp['error']}")
                break

            usage = resp.get("usage", {})
            print(f"  Tokens: {usage.get('total_tokens', 0)}")

            choice  = resp["choices"][0]
            message = choice["message"]
            messages.append(message)

            if "tool_calls" not in message:
                print("\n── Agent finished ────────────────────────────────")
                print(message.get("content", ""))
                break

            # Execute tool calls
            for tc in message["tool_calls"]:
                tool_name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    args = {}

                print(f"  → {tool_name}({json.dumps(args, ensure_ascii=False)})")

                try:
                    if tool_name in pw_tool_names:
                        result      = await pw_client.call_tool(tool_name, args)
                        result_text = str(result)
                    else:
                        # In-process: no HTTP, no serialisation overhead
                        result_text = await dispatch(session, tool_name, args)
                except Exception as exc:
                    result_text = f"ERROR: {exc}"

                print(f"     ↳ {result_text[:300]}")

                # ── Auto-navigate + JNLP launch after oracle_form_open ────────
                # The tool returns the RF.jsp URL.  Navigate, extract the JNLP
                # path from the MCP response, launch javaws, then call
                # java_form_launch — all deterministic, zero AI calls.
                if (
                    tool_name == "oracle_form_open"
                    and result_text.startswith("http")
                    and "ERROR" not in result_text
                ):
                    import subprocess  # noqa: PLC0415
                    form_url = result_text.strip()
                    print(f"  [Auto] browser_navigate → {form_url}")
                    jnlp_launch_result = ""
                    try:
                        nav_result  = await pw_client.call_tool("browser_navigate", {"url": form_url})
                        nav_text    = str(nav_result)

                        # Extract JNLP path from MCP download event line:
                        # Downloaded file frmservlet.jnlp to ".playwright-mcp\frmservlet.jnlp"
                        jnlp_match = re.search(
                            r'Downloaded file \S+\.jnlp to "([^"]+)"', nav_text
                        )
                        if jnlp_match:
                            jnlp_rel  = jnlp_match.group(1)
                            jnlp_path = Path(jnlp_rel)
                            if not jnlp_path.is_absolute():
                                jnlp_path = Path.cwd() / jnlp_path
                            print(f"  [Auto] Launching javaws {jnlp_path} …")
                            subprocess.Popen(["javaws", str(jnlp_path)], shell=True)
                        else:
                            print("  [Auto] No JNLP download detected — assuming embedded JVM launch.")

                        # Call java_form_launch in-process: waits for Oracle window,
                        # fingerprints the form, returns element snapshot for AI.
                        print("  [Auto] Waiting for Oracle Forms window …")
                        jnlp_launch_result = await dispatch(
                            session, "java_form_launch", {"function_url": form_url}
                        )
                        print(f"  [Auto] java_form_launch: {jnlp_launch_result[:120]}")

                    except Exception as nav_exc:
                        print(f"  [Auto] Error during form launch: {nav_exc}")
                        jnlp_launch_result = f"ERROR during auto form launch: {nav_exc}"

                    # Augment the oracle_form_open result with the launch outcome
                    # so the AI knows the form is open and sees the element snapshot.
                    result_text = (
                        f"oracle_form_open returned: {form_url}\n"
                        f"Browser navigated and Java form launched automatically.\n\n"
                        f"{jnlp_launch_result}"
                    )

                # ── Save snapshot to file before sending to AI ────────────
                _SNAPSHOT_TOOLS = {
                    "java_form_launch", "java_get_page_snapshot",
                    "java_click",
                    "oracle_form_open",  # includes launch result after auto-block
                }
                if tool_name in _SNAPSHOT_TOOLS and len(result_text) > 500:
                    snap_idx = sum(
                        1 for p in session.run_dir.glob("snapshot_iter_*.txt")
                    ) + 1
                    snap_file = session.run_dir / f"snapshot_iter_{snap_idx:03d}_{tool_name}.txt"
                    snap_file.write_text(result_text, encoding="utf-8")
                    print(f"  [Debug] Snapshot saved → {snap_file.name}")

                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc["id"],
                    "name":         tool_name,
                    "content":      result_text[:8000],
                })

        # Save messages for debugging
        with open(session.run_dir / "messages.json", "w", encoding="utf-8") as f:
            json.dump(messages, f, indent=2, ensure_ascii=False)

        print(f"\nRecording complete. Artifacts in: {session.run_dir}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="QCS Oracle AI Recorder")
    parser.add_argument("--instructions", required=True, help="Path to instructions .txt file")
    parser.add_argument("--run-id",       required=True, help="Unique run identifier")
    parser.add_argument("--data",         default=None,  help="Optional Excel data file (for column hints)")
    parser.add_argument("--auto-name",    action="store_true",
                        help="Skip stdin prompts for element naming; save with confirmed_by=ai")
    args = parser.parse_args()

    instructions = Path(args.instructions).read_text(encoding="utf-8")

    data_columns: list[str] | None = None
    if args.data:
        from qcs_replay.data import load_excel_rows  # noqa: PLC0415
        rows = load_excel_rows(args.data)
        data_columns = list(rows[0].keys()) if rows else None

    asyncio.run(run_agent(instructions, args.run_id, data_columns, auto_name=args.auto_name))


if __name__ == "__main__":
    main()

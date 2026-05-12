"""
oracle_ai_agent.play — Computer-Use Play mode.

Implements the multi-turn Responses API loop described at:
  https://developers.openai.com/api/docs/guides/tools-computer-use

Flow
----
1. Close stale Oracle windows + deterministic EBS login via Playwright MCP.
2. Deterministic oracle_form_open → JNLP → java_form_launch.
3. Capture Oracle Forms HWND screenshot; send the full task to GPT computer-use.
4. Loop (stateful via previous_response_id):
     a. Execute every action in the returned computer_call.actions[] in order.
     b. After the batch, capture a fresh screenshot.
     c. Send computer_call_output back with previous_response_id.
     d. Stop when the model returns no computer_call (task complete / final answer).

Supports both tool shapes:
  - Legacy "computer_use_preview" (Azure preview deployments, single action per call)
  - GA "computer" tool (gpt-5.5+, batched actions[])

The mode does NOT write to recording.jsonl or touch the repo.

Usage:
    qcs play instructions.txt --run-id play_001
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import win32gui
from PIL import ImageGrab

import config
from qcs_java_agent import JavaAgentDriver, wait_for_forms_ready
from oracle_ai_agent.tools import (
    RecorderSession,
    dispatch,
    close_existing_oracle_windows,
)
from oracle_ai_agent import _deterministic_login, _open_oracle_form, _make_pw_transport
from fastmcp import Client



# ── Constants ─────────────────────────────────────────────────────────────────

_MAX_ITERATIONS = 50   # hard cap to prevent runaway loops

_SYSTEM_PROMPT_FILE = Path(__file__).with_name("cu_system_prompt.txt")


def _load_system_prompt() -> str:
    """Load the fixed system prompt shipped with the package."""
    if _SYSTEM_PROMPT_FILE.exists():
        return _SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()
    return ""


# ── Screenshot capture ────────────────────────────────────────────────────────

def _capture_b64(hwnd: int) -> tuple[str, tuple[int, int, int, int]]:
    """Return (base64-PNG, (left, top, right, bottom)) for the Oracle window."""
    rect = win32gui.GetWindowRect(hwnd)
    img = ImageGrab.grab(bbox=rect)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode(), rect


def _save_screenshot(b64: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(base64.b64decode(b64))


# ── Key name normalization (model keys → pyautogui keys) ─────────────────────

_KEY_MAP: dict[str, str] = {
    "CTRL":       "ctrl",
    "CONTROL":    "ctrl",
    "ALT":        "alt",
    "SHIFT":      "shift",
    "WIN":        "win",
    "META":       "win",
    "RETURN":     "return",
    "ENTER":      "return",
    "ESC":        "escape",
    "ESCAPE":     "escape",
    "TAB":        "tab",
    "BACKSPACE":  "backspace",
    "DELETE":     "delete",
    "DEL":        "delete",
    "HOME":       "home",
    "END":        "end",
    "PAGEUP":     "pageup",
    "PAGE_UP":    "pageup",
    "PAGEDOWN":   "pagedown",
    "PAGE_DOWN":  "pagedown",
    "ARROWUP":    "up",
    "ARROWDOWN":  "down",
    "ARROWLEFT":  "left",
    "ARROWRIGHT": "right",
    "SPACE":      "space",
    "F1": "f1", "F2": "f2", "F3": "f3", "F4": "f4",
    "F5": "f5", "F6": "f6", "F7": "f7", "F8": "f8",
    "F9": "f9", "F10": "f10", "F11": "f11", "F12": "f12",
}


def _norm_key(k: str) -> str:
    return _KEY_MAP.get(k.upper(), k.lower())


# ── Action executor ───────────────────────────────────────────────────────────

def _execute_single_action(action: dict, win_rect: tuple[int, int, int, int]) -> None:
    """Execute one computer-use action using pyautogui.

    The model returns window-relative coordinates (origin at window top-left).
    We offset by the window's screen position so pyautogui clicks the right pixel.

    Supports both old API shape (action["action"]) and new shape (action["type"]).
    """
    import pyautogui  # noqa: PLC0415

    pyautogui.FAILSAFE = False

    win_left, win_top = win_rect[0], win_rect[1]
    # Both old ("action") and new ("type") shapes
    act = (action.get("type") or action.get("action") or "").lower()

    def _screen_xy() -> tuple[int, int]:
        return win_left + int(action.get("x", 0)), win_top + int(action.get("y", 0))

    if act in ("click", "left_click"):
        sx, sy = _screen_xy()
        btn = (action.get("button") or "left").lower()
        held = [_norm_key(k) for k in (action.get("keys") or [])]
        for k in held:
            pyautogui.keyDown(k)
        pyautogui.click(sx, sy, button=btn if btn in ("left", "right", "middle") else "left")
        for k in reversed(held):
            pyautogui.keyUp(k)

    elif act == "right_click":
        sx, sy = _screen_xy()
        pyautogui.rightClick(sx, sy)

    elif act in ("double_click", "dblclick"):
        sx, sy = _screen_xy()
        pyautogui.doubleClick(sx, sy)

    elif act == "move":
        sx, sy = _screen_xy()
        pyautogui.moveTo(sx, sy)

    elif act == "drag":
        path = action.get("path") or []
        if len(path) >= 2:
            sx = win_left + int(path[0].get("x", 0))
            sy = win_top  + int(path[0].get("y", 0))
            pyautogui.moveTo(sx, sy)
            pyautogui.mouseDown()
            for pt in path[1:]:
                pyautogui.moveTo(win_left + int(pt.get("x", 0)),
                                 win_top  + int(pt.get("y", 0)))
            pyautogui.mouseUp()

    elif act == "type":
        text = action.get("text", "")
        if text:
            pyautogui.typewrite(text, interval=0.04)

    elif act in ("keypress", "key_press", "key"):
        keys = action.get("keys") or []
        if isinstance(keys, str):
            keys = [keys]
        text = action.get("text", "")
        if keys:
            pyautogui.hotkey(*[_norm_key(k) for k in keys])
        elif text:
            parts = text.upper().split("+")
            if len(parts) > 1:
                pyautogui.hotkey(*[_norm_key(p) for p in parts])
            else:
                pyautogui.press(_norm_key(text))

    elif act == "scroll":
        sx, sy = _screen_xy()
        pyautogui.moveTo(sx, sy)
        direction = (action.get("scroll_direction") or action.get("direction") or "down").lower()
        distance = int(action.get("scroll_distance") or action.get("amount") or 3)
        pyautogui.scroll(distance if direction == "up" else -distance)

    elif act in ("screenshot", "wait"):
        pass  # handled by caller — no physical action

    else:
        # Unknown: attempt click at coordinates as fallback
        try:
            sx, sy = _screen_xy()
            pyautogui.click(sx, sy)
        except Exception:
            pass

    time.sleep(0.25)


def _execute_actions_batch(
    actions: list[dict],
    hwnd: int,
    shots_dir: Path,
    label: str,
) -> tuple[str, tuple[int, int, int, int]]:
    """Execute a batch of actions and return a (screenshot_b64, win_rect) after them all."""
    rect = win32gui.GetWindowRect(hwnd)

    for i, action in enumerate(actions):
        act = (action.get("type") or action.get("action") or "").lower()
        print(f"    [{label}.{i + 1}] {act}: "
              f"{json.dumps({k: v for k, v in action.items() if k not in ('type', 'action')})}")
        _execute_single_action(action, rect)

        # Refresh rect in case window moved/resized
        try:
            rect = win32gui.GetWindowRect(hwnd)
        except Exception:
            pass

    # Settle, then capture
    time.sleep(0.5)
    b64, rect = _capture_b64(hwnd)
    return b64, rect


# ── Tool definition ───────────────────────────────────────────────────────────

def _tool_def(display_width: int, display_height: int, model: str) -> dict:
    """Return the correct tool definition for the configured model.

    "computer_use_preview" is required for Azure preview deployments.
    "computer" is the GA type for gpt-5.5+.
    """
    if "preview" in model.lower():
        return {
            "type": "computer_use_preview",
            "display_width": display_width,
            "display_height": display_height,
            "environment": "windows",
        }
    return {"type": "computer"}


def _is_preview_model(model: str) -> bool:
    return "preview" in model.lower()


# ── Responses API helper ──────────────────────────────────────────────────────

async def _responses_call(
    http: aiohttp.ClientSession,
    payload: dict,
    timeout_s: int = 120,
) -> dict:
    """POST to the Azure OpenAI Responses API; return parsed JSON or raise."""
    # Azure Responses API: model is in the body, NOT in the URL path.
    # Correct path: /openai/responses  (no /deployments/{name}/)
    url = (
        f"{config.AZURE_OPENAI_ENDPOINT.rstrip('/')}/openai/responses"
        f"?api-version={config.CU_API_VERSION}"
    )
    headers = {
        "api-key": config.AZURE_OPENAI_API_KEY,
        "Content-Type": "application/json",
    }
    last_error = ""
    for attempt in range(3):
        async with http.post(
            url,
            headers=headers,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=timeout_s),
        ) as resp:
            raw = await resp.text()
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                body = {"error": raw[:1000]}
            if resp.status < 400:
                return body
            last_error = f"Responses API {resp.status}: {json.dumps(body)[:500]}"
            if resp.status in {408, 409, 429, 500, 502, 503, 504} and attempt < 2:
                await asyncio.sleep(2 + attempt * 3)
                continue
            raise RuntimeError(last_error)
    raise RuntimeError(last_error or "Responses API request failed")


# ── Response parsing helpers ──────────────────────────────────────────────────

def _extract_computer_calls(output: list[dict]) -> list[dict]:
    return [item for item in output if item.get("type") == "computer_call"]


def _extract_actions(computer_call: dict) -> list[dict]:
    """Extract actions list from a computer_call item.

    New GA API: actions[] (list).
    Old preview API: action (single dict) — wrap in a list.
    """
    if "actions" in computer_call:
        return list(computer_call["actions"])
    if "action" in computer_call:
        return [computer_call["action"]]
    return []


def _final_text(output: list[dict]) -> str:
    for item in output:
        if item.get("type") == "message":
            content = item.get("content", [])
            if isinstance(content, list):
                return " ".join(
                    c.get("text", "") for c in content if c.get("type") == "output_text"
                )
            return str(content)
    return ""


# ── Main play entry point ─────────────────────────────────────────────────────

async def run_play(instructions: str, run_id: str) -> None:
    """Deterministic login + form open, then GPT computer-use drives the UI."""
    closed = close_existing_oracle_windows()
    if closed:
        print(f"[Play] Closed {len(closed)} stale Oracle window(s).")

    session = RecorderSession(run_id, auto_name=True)
    run_dir = session.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    shots_dir = run_dir / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    # Parse steps: first numbered line = form to open; rest = task for GPT
    step_lines = [
        l.strip()
        for l in instructions.splitlines()
        if l.strip() and not l.startswith("#")
    ]
    numbered = [l for l in step_lines if l and l[0].isdigit()]
    if not numbered:
        print("[Play] ERROR: no numbered steps found in instructions.")
        return

    form_step      = numbered[0]
    remaining_steps = numbered[1:]

    pw_transport = _make_pw_transport()

    async with Client(pw_transport) as pw_client:
        # ── 1. Login ──────────────────────────────────────────────────────────
        await dispatch(session, "session_start", {"run_id": run_id})
        print("[Play] Logging in ...")
        await _deterministic_login(session, pw_client)

        # ── 2. Open form ──────────────────────────────────────────────────────
        print(f"[Play] Opening form via: {form_step}")
        await _open_oracle_form(session, pw_client, form_step)

        hwnd = session.java_hwnd
        if not hwnd:
            print("[Play] ERROR: No Java window after form launch.")
            return
        if not session.java_pid:
            print("[Play] ERROR: No Java PID after form launch.")
            return
        java_driver = JavaAgentDriver(pid=session.java_pid)
        await asyncio.to_thread(wait_for_forms_ready, java_driver, log_prefix="[Play]")

        # ── 3. Build GPT task from remaining steps ────────────────────────────
        if not remaining_steps:
            print("[Play] No remaining steps — nothing for GPT to do.")
            return

        system_prompt = _load_system_prompt()

        task_text = (
            "The Oracle form is already open. Complete ALL of the following steps "
            "using the computer tool. After each action, wait for the screen to "
            "update before the next.\n\n"
            "Steps:\n" + "\n".join(f"  {s}" for s in remaining_steps)
        )
        print(f"[Play] Task ({len(remaining_steps)} step(s)):\n{task_text}\n")

        # ── 4. Initial screenshot ─────────────────────────────────────────────
        await asyncio.to_thread(wait_for_forms_ready, java_driver, log_prefix="[Play]")
        b64, win_rect = await asyncio.to_thread(_capture_b64, hwnd)
        _save_screenshot(b64, shots_dir / "step_000_initial.png")

        model      = config.CU_MODEL
        display_w  = win_rect[2] - win_rect[0]
        display_h  = win_rect[3] - win_rect[1]
        tool       = _tool_def(display_w, display_h, model)
        is_preview = _is_preview_model(model)

        # ── 5. Initial Responses API request ──────────────────────────────────
        input_messages: list[dict] = []
        if system_prompt:
            input_messages.append({"role": "system", "content": system_prompt})
        input_messages.append({
            "role": "user",
            "content": [
                {
                    "type": "input_image",
                    "image_url": f"data:image/png;base64,{b64}",
                    "detail": "auto",
                },
                {"type": "input_text", "text": task_text},
            ],
        })

        initial_payload: dict = {
            "model": model,
            "input": input_messages,
            "tools": [tool],
        }
        if is_preview:
            initial_payload["truncation"] = "auto"

        print("[Play] Sending initial request to GPT computer-use ...")
        async with aiohttp.ClientSession() as http:
            response    = await _responses_call(http, initial_payload)
            response_id = response.get("id")
            print(f"[Play] Response id={response_id}  "
                  f"tokens={response.get('usage', {}).get('total_tokens', 0)}")

            # ── 6. Multi-turn action loop ─────────────────────────────────────
            for iteration in range(_MAX_ITERATIONS):
                output         = response.get("output", [])
                computer_calls = _extract_computer_calls(output)

                if not computer_calls:
                    final = _final_text(output)
                    if final:
                        if final.upper().startswith("FAIL:"):
                            print(f"\n[Play] *** TEST FAILED ***\n{final}")
                            raise RuntimeError(final)
                        print(f"\n[Play] Model final answer:\n{final}")
                    else:
                        print("[Play] No computer_call in response — task complete.")
                    break

                print(f"\n[Play] Iteration {iteration + 1}: "
                      f"{len(computer_calls)} computer_call(s)")

                # Process each computer_call; send one computer_call_output per call
                for ci, cc in enumerate(computer_calls):
                    call_id = cc.get("call_id") or cc.get("id") or f"call_{ci}"
                    actions = _extract_actions(cc)
                    label   = f"iter{iteration + 1}_call{ci + 1}"
                    print(f"  [{label}] call_id={call_id}  {len(actions)} action(s)")

                    b64, win_rect = await asyncio.to_thread(
                        _execute_actions_batch, actions, hwnd, shots_dir, label
                    )
                    await asyncio.to_thread(wait_for_forms_ready, java_driver, log_prefix="[Play]")
                    b64, win_rect = await asyncio.to_thread(_capture_b64, hwnd)
                    _save_screenshot(b64, shots_dir / f"{label}.png")
                    print(f"  [{label}] Screenshot saved.")

                    # Send computer_call_output back with the fresh screenshot
                    followup: dict = {
                        "model": model,
                        "previous_response_id": response_id,
                        "input": [
                            {
                                "type": "computer_call_output",
                                "call_id": call_id,
                                "output": {
                                    "type": "computer_screenshot",
                                    "image_url": f"data:image/png;base64,{b64}",
                                    "detail": "auto",
                                },
                            }
                        ],
                        "tools": [tool],
                    }
                    if is_preview:
                        followup["truncation"] = "auto"

                    response    = await _responses_call(http, followup)
                    response_id = response.get("id")
                    print(f"  [{label}] Follow-up id={response_id}  "
                          f"tokens={response.get('usage', {}).get('total_tokens', 0)}")

            else:
                print(f"[Play] Reached max iterations ({_MAX_ITERATIONS}).")

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"\n[Play] Done at {ts}. Screenshots: {shots_dir}")

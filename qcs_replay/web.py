"""
qcs_replay.web — Playwright browser + Oracle Forms JNLP launch helpers.

Provides:
  - new_browser_page()         — launch Chromium, return (browser, page)
  - ebs_login(page, url, user, password) — navigate + login
  - launch_java_form(page, function_url) — trigger JNLP download + launch
  - wait_for_oracle_window()   — poll win32gui for "Oracle Applications"
"""
from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path
from typing import Any

import win32gui

import config


# ── Browser session ───────────────────────────────────────────────────────────

async def new_browser_page(
    headless: bool = False,
    playwright: Any = None,
) -> tuple[Any, Any, Any]:
    """
    Returns (playwright_instance, browser, page).
    If playwright is supplied (already started), reuses it.
    """
    from playwright.async_api import async_playwright  # noqa: PLC0415

    if playwright is None:
        pw = await async_playwright().__aenter__()
        own = True
    else:
        pw = playwright
        own = False

    browser = await pw.chromium.launch(headless=headless)
    context = await browser.new_context()
    page    = await context.new_page()
    return (pw if own else None), browser, page


async def ebs_login(page: Any, url: str, user: str, password: str) -> None:
    """Navigate to the EBS login page and sign in."""
    await page.goto(url)
    await page.wait_for_load_state("networkidle", timeout=30_000)
    await page.get_by_role("textbox", name="User Name").fill(user)
    await page.get_by_role("textbox", name="Password").fill(password)
    await page.get_by_role("button", name="Log In").click()
    await page.wait_for_load_state("networkidle", timeout=30_000)


# ── Java Forms JNLP launcher ──────────────────────────────────────────────────

async def launch_java_form(page: Any, function_url: str, poll_s: int = 60) -> int:
    """
    Evaluate a launchForm() JS call (or navigate to function_url if it's a direct URL),
    wait for the JNLP download, launch it via javaws, and return the HWND of the
    resulting Oracle Applications window.

    Raises RuntimeError if the window doesn't appear within poll_s seconds.
    """
    # Navigate or launch depending on URL shape
    if function_url.startswith("javascript:") or function_url.startswith("launchForm"):
        await page.evaluate(function_url)
    else:
        await page.goto(function_url)

    # Wait for JNLP download
    try:
        download = await asyncio.wait_for(
            asyncio.ensure_future(page.wait_for_event("download")),
            timeout=30,
        )
        jnlp_path = await download.path()
        if jnlp_path:
            subprocess.Popen(["javaws", jnlp_path], shell=True)
    except asyncio.TimeoutError:
        pass  # Some EBS configs launch without a download (embedded JVM)

    return await asyncio.to_thread(_wait_for_oracle_window, poll_s)


def _wait_for_oracle_window(max_wait_s: int = 60) -> int:
    """Blocking poll for an 'Oracle Applications' window. Returns HWND."""
    deadline = time.monotonic() + max_wait_s
    while time.monotonic() < deadline:
        found: list[tuple[str, int]] = []

        def _cb(hwnd: int, acc: list) -> None:
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title.startswith("Oracle Applications"):
                    win32gui.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
                    acc.append((title, hwnd))

        win32gui.EnumWindows(_cb, found)
        if found:
            return found[0][1]
        time.sleep(2)

    raise RuntimeError(
        f"Oracle Applications window did not appear within {max_wait_s}s"
    )


def open_java_form_sync(page: Any, function_url: str, poll_s: int = 90) -> int:
    """
    Sync version for use in generated deterministic tests (sync_playwright).

    Navigates *page* to *function_url*, captures the JNLP download if one
    occurs, launches it via javaws, then returns the HWND of the Oracle
    Applications window.

    Some EBS configurations launch Java Forms without a JNLP download
    (embedded JVM / ICE); in that case the download wait is skipped and the
    function still polls for the Oracle window.
    """
    if function_url.startswith("javascript:") or function_url.startswith("launchForm"):
        page.evaluate(function_url)
        try:
            page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:
            pass
    else:
        jnlp_dest = Path(config.REPO_DIR).parent / ".playwright-mcp" / "frmservlet.jnlp"
        jnlp_dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"[Replay] Navigating to {function_url} …")
        try:
            # Some EBS configs launch without a JNLP download (embedded JVM)
            page.goto(function_url, wait_until="commit", timeout=15_000)
        except Exception as nav_exc:
            # ERR_ABORTED is expected when Chromium treats RF.jsp as a file
            # response instead of an HTML document. The exact URL is still
            # valid; fetch it through the authenticated browser context below.
            if "ERR_ABORTED" not in str(nav_exc) and "net::" not in str(nav_exc):
                raise
            print(f"[Replay] Browser navigation reached RF.jsp ({nav_exc.__class__.__name__}: {nav_exc!s:.120})")

        print(f"[Replay] Downloading JNLP from recorded RF.jsp URL …")
        response = page.context.request.get(function_url, timeout=30_000)
        content_type = response.headers.get("content-type", "")
        body = response.body()
        if not response.ok:
            raise RuntimeError(
                f"JNLP request failed: HTTP {response.status} {response.status_text}; "
                f"content-type={content_type!r}"
            )
        if not body:
            raise RuntimeError("JNLP request returned an empty response body")
        jnlp_dest.write_bytes(body)
        print(
            f"[Replay] JNLP saved → {jnlp_dest} "
            f"({len(body)} bytes, content-type={content_type!r})"
        )
        print(f"[Replay] Launching javaws {jnlp_dest} …")
        subprocess.Popen(["javaws", str(jnlp_dest)], shell=True)

    return _wait_for_oracle_window(poll_s)

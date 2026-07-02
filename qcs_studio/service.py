from __future__ import annotations

import tempfile
import ctypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import config
from qcs_java_agent.driver import JavaAgentDriver
from qcs_java_agent.process import list_java_processes
from qcs_java_agent.snapshot import (
    _oracle_forms_active_frame,
    active_form_title,
    active_window_scan,
    build_action_payload,
    build_full_overlay_elements,
    full_view_scan,
    java_nodes_to_repo_elements,
    merge_scans,
    flatten_nodes
)
from qcs_repo import fingerprint as repo_fingerprint
from qcs_repo import identity as repo_identity
from qcs_repo import snapshot as repo_snapshot
from qcs_repo import store as repo_store
from qcs_java_agent.snapshot import attribute_unique_tab_fields


def _restore_foreground() -> None:
    """Switch the foreground back to the previously-active application.

    Simulates Alt+Esc which is the OS-level "switch to next window" —
    the most reliable way to move focus away from the Java form back to
    whichever application was active before the scan.  Does NOT use
    programmatic SetForegroundWindow which often fails across process
    boundaries when a maximized window is on top.
    """
    if not hasattr(ctypes, "windll"):
        return
    try:
        user32 = ctypes.windll.user32
        # Alt+Esc: activates the next window in the Z-order.
        # Unlike Alt+Tab, it doesn't show the switcher UI.
        VK_MENU = 0x12   # Alt
        VK_ESCAPE = 0x1B # Esc
        KEYEVENTF_KEYUP = 0x0002
        user32.keybd_event(VK_MENU, 0, 0, 0)              # Alt down
        user32.keybd_event(VK_ESCAPE, 0, 0, 0)             # Esc down
        user32.keybd_event(VK_ESCAPE, 0, KEYEVENTF_KEYUP, 0) # Esc up
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)   # Alt up
    except Exception:
        pass


def _save_window_placement(hwnd: int) -> bytes | None:
    """Save a window's current placement (position, size, show state) as raw bytes."""
    if not hasattr(ctypes, "windll") or not hwnd:
        return None
    try:
        user32 = ctypes.windll.user32
        placement = (ctypes.c_ubyte * 44)()
        placement[0] = 44  # length field
        if user32.GetWindowPlacement(ctypes.c_void_p(hwnd), ctypes.byref(placement)):
            return bytes(placement)
    except Exception:
        pass
    return None


def _restore_window_placement(hwnd: int, raw: bytes | None) -> None:
    """Restore a window's placement from raw bytes saved by _save_window_placement."""
    if not hasattr(ctypes, "windll") or not hwnd or not raw or len(raw) < 44:
        return
    try:
        user32 = ctypes.windll.user32
        placement = (ctypes.c_ubyte * 44).from_buffer_copy(raw)
        user32.SetWindowPlacement(ctypes.c_void_p(hwnd), ctypes.byref(placement))
    except Exception:
        pass


def _bring_process_window_to_front(pid: int) -> int:
    """Bring a process' top-level visible window to front and return hwnd."""
    if not hasattr(ctypes, "windll"):
        return 0
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    target: list[int] = []

    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _enum(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(ctypes.c_void_p(hwnd)):
            return True
        if user32.GetWindow(ctypes.c_void_p(hwnd), 4):
            # Has owner window; skip tool/child-like top-level windows.
            return True
        proc_id = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(proc_id))
        if int(proc_id.value) != int(pid):
            return True
        title_len = user32.GetWindowTextLengthW(ctypes.c_void_p(hwnd))
        if title_len <= 0:
            return True
        target.append(int(hwnd))
        return False

    callback = EnumWindowsProc(_enum)
    user32.EnumWindows(callback, 0)
    if not target:
        return 0

    hwnd = target[0]
    try:
        # Maximize the Oracle form window so the screenshot captures the full
        # form (not a small floating window) and element bounds are scanned in
        # the maximized layout.
        SW_MAXIMIZE = 3
        user32.ShowWindow(ctypes.c_void_p(hwnd), SW_MAXIMIZE)

        # Cross-thread foreground handoff for more reliable activation.
        fg_hwnd = user32.GetForegroundWindow()
        fg_pid = ctypes.c_ulong(0)
        fg_thread = user32.GetWindowThreadProcessId(ctypes.c_void_p(fg_hwnd), ctypes.byref(fg_pid))
        this_thread = kernel32.GetCurrentThreadId()
        user32.AttachThreadInput(fg_thread, this_thread, True)
        user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
        user32.BringWindowToTop(ctypes.c_void_p(hwnd))
        user32.SetFocus(ctypes.c_void_p(hwnd))
        user32.AttachThreadInput(fg_thread, this_thread, False)
    except Exception:
        pass
    return int(hwnd)


def _window_rect(hwnd: int) -> dict | None:
    """Return the **client area** bounds in screen coordinates.

    WHY: GetWindowRect returns the outer window frame (title bar + borders
    + invisible shadow padding on Win10/11). Java element coordinates are
    relative to the client area (inside the window chrome). Using the outer
    rect for crop/origin causes both vertical offset (title bar height ≈30px)
    and horizontal offset (left border + shadow padding ≈8-10px).

    We use GetClientRect for dimensions and ClientToScreen to convert the
    (0,0) origin to screen space, giving the true pixel-aligned client origin.
    """
    if not hasattr(ctypes, "windll") or not hwnd:
        return None
    try:
        user32 = ctypes.windll.user32
        # Get client rect (width/height of content area)
        client_rect = (ctypes.c_long * 4)()
        ok = user32.GetClientRect(ctypes.c_void_p(hwnd), ctypes.byref(client_rect))
        if not ok:
            return None
        width = int(client_rect[2])
        height = int(client_rect[3])
        if width <= 0 or height <= 0:
            return None

        # Convert client (0,0) to screen coordinates to get the true origin
        pt = (ctypes.c_long * 2)(0, 0)
        ok = user32.ClientToScreen(ctypes.c_void_p(hwnd), ctypes.byref(pt))
        if not ok:
            return None
        x, y = int(pt[0]), int(pt[1])
        return {"x": x, "y": y, "width": width, "height": height}
    except Exception:
        return None


def _java_window_bounds(raw_dom: dict, frame: dict | None) -> dict | None:
    """Return the screen bounds of the top-level Java window to screenshot.

    WHY: The screenshot must show only the Java application window (not the
    whole desktop) AND element overlays must align to it. We therefore pick a
    single window whose screen origin becomes the coordinate origin for all
    element bounds, then crop the fullscreen capture to that window.

    Selection: the top-level window in ``raw_dom['windows']`` whose ``path`` is
    an ancestor of the active frame; otherwise the largest showing window.
    """
    windows = raw_dom.get("windows") or []
    if not windows:
        return None

    def _bounds(node: dict) -> dict:
        b = node.get("screenBounds") or node.get("bounds") or {}
        return {
            "x": int(b.get("x", 0) or 0),
            "y": int(b.get("y", 0) or 0),
            "width": int(b.get("width", 0) or 0),
            "height": int(b.get("height", 0) or 0),
        }

    frame_path = str((frame or {}).get("path") or "")
    if frame_path:
        for w in windows:
            w_path = str(w.get("path") or "")
            if w_path and (frame_path == w_path or frame_path.startswith(w_path + "/")):
                b = _bounds(w)
                if b["width"] > 0 and b["height"] > 0:
                    return b

    # Fallback: largest showing window.
    showing = [w for w in windows if w.get("showing")] or windows
    best = max(showing, key=lambda w: _bounds(w)["width"] * _bounds(w)["height"])
    b = _bounds(best)
    return b if b["width"] > 0 and b["height"] > 0 else None


def _crop_to_bounds(image_path: Path, bounds: dict) -> bool:
    """Crop the screenshot file in place to the given screen bounds.

    WHY: We always capture fullscreen (reliable across capture backends) and
    then crop to the Java window so the preview shows only that window and the
    overlay coordinate origin matches the cropped image.
    """
    try:
        from PIL import Image
    except Exception:
        return False

    x = int(bounds.get("x", 0) or 0)
    y = int(bounds.get("y", 0) or 0)
    w = int(bounds.get("width", 0) or 0)
    h = int(bounds.get("height", 0) or 0)
    if w <= 0 or h <= 0:
        return False

    try:
        with Image.open(image_path) as img:
            left = max(0, x)
            top = max(0, y)
            right = min(img.width, left + w)
            bottom = min(img.height, top + h)
            if right <= left or bottom <= top:
                return False
            cropped = img.crop((left, top, right, bottom))
            cropped.save(image_path)
        return True
    except Exception:
        return False


def _frame_form_title(frame: dict | None) -> str:
    """Return the Oracle Forms frame caption (the form/window name).

    WHY: The container name should default to the form name (e.g. "EMR Global
    Find Orders/Quotes"), not the currently-focused field. ``active_form_title``
    falls back to the focused element, so prefer the frame's own caption first.
    """
    if not frame:
        return ""
    for key in ("title", "displayName", "accessibleName", "name"):
        value = str(frame.get(key) or "").strip()
        if value and value != "null":
            return value
    return ""


@dataclass
class ScanBundle:
    scan_id: str
    title: str
    container_ref: str
    raw_dom: dict
    snapshot_text: str
    tree: list[dict]
    raw_elements: list[dict]
    full_elements: list[dict]
    screenshot_path: Path
    screenshot_origin: dict
    capture_mode: str
    created_at: str
    tab_screenshots: dict[str, str]
    tab_doms: dict[str, dict] | None = None
class StudioService:
    """Application service for Studio scan and container operations."""

    # Drafts survive StudioService re-creation within the same process
    # (e.g. on uvicorn reload). Each draft holds raw_dom + screenshot so
    # the tree can be recalculated later without a live Oracle window.
    _drafts: dict[str, ScanBundle] = {}

    def __init__(self) -> None:
        self._scan_cache: dict[str, ScanBundle] = StudioService._drafts

    def list_windows(self) -> list[dict]:
        windows: list[dict] = []
        for proc in list_java_processes():
            title = ""
            class_name = ""
            if hasattr(ctypes, "windll"):
                try:
                    user32 = ctypes.windll.user32
                    found_windows = []
                    EnumWindowsProc = ctypes.WINFUNCTYPE(
                        ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
                    )

                    def _enum(hwnd: int, _lparam: int) -> bool:
                        if not user32.IsWindowVisible(ctypes.c_void_p(hwnd)):
                            return True
                        if user32.GetWindow(ctypes.c_void_p(hwnd), 4): # GW_OWNER
                            return True
                        proc_id = ctypes.c_ulong(0)
                        user32.GetWindowThreadProcessId(
                            ctypes.c_void_p(hwnd), ctypes.byref(proc_id)
                        )
                        if int(proc_id.value) == proc.pid:
                            t_len = user32.GetWindowTextLengthW(ctypes.c_void_p(hwnd))
                            t_val = ""
                            if t_len > 0:
                                buf = ctypes.create_unicode_buffer(t_len + 1)
                                user32.GetWindowTextW(
                                    ctypes.c_void_p(hwnd), buf, t_len + 1
                                )
                                t_val = buf.value
                            
                            c_buf = ctypes.create_unicode_buffer(256)
                            user32.GetClassNameW(ctypes.c_void_p(hwnd), c_buf, 256)
                            c_val = c_buf.value
                            found_windows.append((t_val, c_val))
                        return True

                    callback = EnumWindowsProc(_enum)
                    user32.EnumWindows(callback, 0)

                    if found_windows:
                        with_title = [w for w in found_windows if w[0]]
                        if with_title:
                            title, class_name = with_title[0]
                        else:
                            title, class_name = found_windows[0]
                except Exception:
                    pass

            windows.append(
                {
                    "source": "java_forms",
                    "pid": proc.pid,
                    "label": f"[{proc.pid}] {proc.main}",
                    "main": proc.main,
                    "jvm_args": proc.jvm_args,
                    "title": title,
                    "type": class_name,
                }
            )
        return windows

    def run_scan(self, pid: int | None = None, contains: str | None = None) -> ScanBundle:
        """Phase 1: capture raw DOM + screenshot from a live Oracle window.

        Returns a ScanBundle with raw_dom and screenshot populated but tree/
        full_elements empty. Call ``compute_tree(scan_id)`` afterwards to
        build the tree from the cached raw DOM (can be done later, even after
        the window is closed).
        """
        driver = JavaAgentDriver.attach(pid=pid, contains=contains)
        hwnd = _bring_process_window_to_front(driver.pid)
        # Save the Java form's original placement. After the screenshot we
        # restore it so the form goes back to exactly how it looked before
        # the scan.
        java_placement = _save_window_placement(hwnd)

        raw_dom = driver.scan()
        scoped = active_window_scan(raw_dom)

        # Resolve the active frame and the top-level Java window.
        frame = _oracle_forms_active_frame(raw_dom)
        window_bounds = _window_rect(hwnd) or _java_window_bounds(raw_dom, frame)
        title = _frame_form_title(frame) or active_form_title(scoped)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        temp_dir = Path(tempfile.gettempdir())
        
        tab_screenshots: dict[str, str] = {}
        tab_doms: dict[str, dict] = {}
        merged_dom = raw_dom
        temp_screenshot = None

        # Helper to find all TabBar nodes sorted by y-coordinate
        def _get_sorted_tab_bars(dom_tree):
            sc = active_window_scan(dom_tree)
            nodes = flatten_nodes(sc)
            tab_bars = [n for n in nodes if str(n.get("simpleClassName") or "") == "TabBar"]
            
            # Filter out tab bars with only 1 title if there are other tab bars with >1 titles
            tab_bars_with_multi_titles = []
            for tb in tab_bars:
                tb_attrs = tb.get("attributes") or {}
                tb_titles = [t.strip() for t in str(tb_attrs.get("tabTitles", "")).split("|") if t.strip()]
                if len(tb_titles) > 1:
                    tab_bars_with_multi_titles.append(tb)
            if tab_bars_with_multi_titles:
                tab_bars = tab_bars_with_multi_titles

            tab_bars.sort(key=lambda n: (n.get("screenBounds") or {}).get("y", 0))
            return tab_bars, nodes

        scoped_nodes = flatten_nodes(scoped)
        tab_bars, _ = _get_sorted_tab_bars(scoped)
        tab_info = bool(tab_bars)
        screenshot_result = {}

        try:
            if tab_info:
                # Multi-tab DFS scan
                from qcs_java_agent.settle import settle_forms
                
                # Find initial tab paths/selections so we can restore them at the end
                initial_tab_bars, initial_nodes = _get_sorted_tab_bars(raw_dom)
                original_path = []
                for tb in initial_tab_bars:
                    attrs = tb.get("attributes") or {}
                    original_path.append(str(attrs.get("tabSelectedTitle", "")).strip())
                
                visited = set()
                results = {}

                def dfs(current_path):
                    # 1. Click to select the tab path sequentially from Level 1
                    for level, tab_title in enumerate(current_path):
                        state_dom = driver.scan()
                        tab_bars, nodes = _get_sorted_tab_bars(state_dom)
                        if level < len(tab_bars):
                            tb = tab_bars[level]
                            attrs = tb.get("attributes") or {}
                            titles = [t.strip() for t in str(attrs.get("tabTitles", "")).split("|") if t.strip()]
                            selected = str(attrs.get("tabSelectedTitle", "")).strip()
                            if selected != tab_title and tab_title in titles:
                                idx = titles.index(tab_title)
                                driver.click(tb, tab_index=idx)
                                settle_forms(driver, timeout_s=3.0)

                    # 2. Scan the current fully selected tab state
                    state_dom = driver.scan()
                    tab_bars, nodes = _get_sorted_tab_bars(state_dom)
                    
                    # Check if there is a nested tab bar at the next level
                    depth = len(current_path)
                    if depth < len(tab_bars):
                        tb = tab_bars[depth]
                        attrs = tb.get("attributes") or {}
                        titles = [t.strip() for t in str(attrs.get("tabTitles", "")).split("|") if t.strip()]
                        tab_states_raw = str(attrs.get("tabStates", ""))
                        enabled_flags = []
                        visible_flags = []
                        for part in tab_states_raw.split("|"):
                            bits = [b.strip() for b in part.strip().split(",")]
                            enabled_flags.append(bits[0] == "1" if len(bits) > 0 else True)
                            visible_flags.append(bits[1] == "1" if len(bits) > 1 else True)

                        for idx, t in enumerate(titles):
                            if idx < len(enabled_flags) and not enabled_flags[idx]:
                                continue # skip disabled tabs
                            if idx < len(visible_flags) and not visible_flags[idx]:
                                continue # skip invisible tabs
                            
                            next_path = current_path + (t,)
                            if next_path not in visited:
                                visited.add(next_path)
                                dfs(next_path)
                    else:
                        # Leaf state - capture screenshot
                        safe_path_name = "_".join(current_path).replace("/", "_").replace("\\", "_")
                        shot_path = temp_dir / f"qcs_studio_scan_{ts}_{uuid4().hex[:8]}_{safe_path_name}.png"
                        res_shot = driver.screenshot(shot_path)
                        screenshot_result.update(res_shot)
                        results[current_path] = {
                            "dom": state_dom,
                            "screenshot_path": shot_path
                        }

                # Start DFS on each top-level tab
                top_tb = initial_tab_bars[0]
                top_attrs = top_tb.get("attributes") or {}
                top_titles = [t.strip() for t in str(top_attrs.get("tabTitles", "")).split("|") if t.strip()]
                top_states_raw = str(top_attrs.get("tabStates", ""))
                top_enabled = []
                top_visible = []
                for part in top_states_raw.split("|"):
                    bits = [b.strip() for b in part.strip().split(",")]
                    top_enabled.append(bits[0] == "1" if len(bits) > 0 else True)
                    top_visible.append(bits[1] == "1" if len(bits) > 1 else True)

                for idx, t in enumerate(top_titles):
                    if idx < len(top_enabled) and not top_enabled[idx]:
                        continue
                    if idx < len(top_visible) and not top_visible[idx]:
                        continue
                    path = (t,)
                    visited.add(path)
                    dfs(path)

                # Restore original tab state
                for level, tab_title in enumerate(original_path):
                    state_dom = driver.scan()
                    tab_bars, nodes = _get_sorted_tab_bars(state_dom)
                    if level < len(tab_bars):
                        tb = tab_bars[level]
                        attrs = tb.get("attributes") or {}
                        titles = [t.strip() for t in str(attrs.get("tabTitles", "")).split("|") if t.strip()]
                        selected = str(attrs.get("tabSelectedTitle", "")).strip()
                        if selected != tab_title and tab_title in titles:
                            idx = titles.index(tab_title)
                            driver.click(tb, tab_index=idx)
                            settle_forms(driver, timeout_s=3.0)

                # Merge all raw DOMs and collect screenshots mapping
                if results:
                    first_path = list(results.keys())[0]
                    merged_dom = results[first_path]["dom"]
                    _stamp_owner_tab_paths(merged_dom, first_path)          # <-- NEW
                    for path, res in results.items():
                        if path != first_path:
                            _stamp_owner_tab_paths(res["dom"], path)        # <-- NEW
                            merged_dom = merge_scans(merged_dom, res["dom"])
                        tab_path_str = " -> ".join(path)
                        tab_screenshots[tab_path_str] = str(res["screenshot_path"])
                        tab_doms[tab_path_str] = res["dom"]
                    temp_screenshot = Path(results[first_path]["screenshot_path"])
                    # Attribute tab-unique fields the agent left ownerTab=None, so
                    # the tree + overlay link them to the right tab/screenshot.
                    attribute_unique_tab_fields(merged_dom, tab_doms)
                else:
                    # Fallback to standard capture if DFS produced no results
                    temp_screenshot = temp_dir / f"qcs_studio_scan_{ts}_{uuid4().hex[:8]}.png"
                    screenshot_result = driver.screenshot(temp_screenshot)
                    tab_screenshots["default"] = str(temp_screenshot)
                    tab_doms["default"] = raw_dom
            else:
                # Standard scan flow
                temp_screenshot = temp_dir / f"qcs_studio_scan_{ts}_{uuid4().hex[:8]}.png"
                screenshot_result = driver.screenshot(temp_screenshot)
                tab_screenshots["default"] = str(temp_screenshot)
                tab_doms["default"] = raw_dom
        finally:
            # Restore the Java form to its original size/position first,
            # then switch away from it back to the previous application.
            _restore_window_placement(hwnd, java_placement)
            _restore_foreground()

        capture_mode = str(screenshot_result.get("captureMode") or "fullscreen")
        scan_id = uuid4().hex

        # Compute fingerprint from the DOM elements (lightweight, no AI payload).
        elements = java_nodes_to_repo_elements(active_window_scan(merged_dom))
        enriched = repo_snapshot.enrich_java_elements(elements)
        title_for_fp = _frame_form_title(frame) or active_form_title(active_window_scan(merged_dom))
        fingerprint = repo_fingerprint.fingerprint_java_form(
            title_for_fp,
            [{"role": str(el.get("role") or ""), "name": str(el.get("name") or "")} for el in enriched],
        )
        container_ref = repo_fingerprint.suggest_form_id(title_for_fp, surface="java")
        container_ref = f"{repo_identity.normalize_ref(container_ref)}_{fingerprint[-6:]}"

        # Build bundle with Phase 1 data only — tree is empty until computed.
        bundle = ScanBundle(
            scan_id=scan_id,
            title=title,
            container_ref=container_ref,
            raw_dom=merged_dom,
            snapshot_text="",  # populated by compute_tree
            tree=[],           # populated by compute_tree
            raw_elements=enriched,
            full_elements=[],  # populated by compute_tree
            screenshot_path=temp_screenshot,
            screenshot_origin={"x": 0, "y": 0},
            capture_mode=capture_mode,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            tab_screenshots=tab_screenshots,
            tab_doms=tab_doms,
        )
        StudioService._drafts[scan_id] = bundle
        self._scan_cache[scan_id] = bundle

        return bundle

    def compute_tree(self, scan_id: str) -> ScanBundle:
        """Phase 2: compute AI snapshot + tree + full_elements from cached raw DOM.

        WHY: Separated from run_scan so tree calculation can be repeated
        later (e.g. after AI prompt tuning) without a live Oracle window.
        The raw DOM and screenshot origin are already captured in Phase 1.
        """
        bundle = self._scan_cache.get(scan_id)
        if bundle is None:
            raise KeyError(f"Unknown scan_id {scan_id!r}")

        raw_dom = bundle.raw_dom

        scoped = active_window_scan(raw_dom)
        enriched = repo_snapshot.enrich_java_elements(java_nodes_to_repo_elements(scoped))

        payload = build_action_payload(scoped, all_tabs=True)

        # Full overlay uses the FULL raw DOM filtered to active form window +
        # toolbar / menu bar only (exclude non-active ExtendedFrame subtrees).
        # The curated tree + AI snapshot remain scoped to the active form window.
        full_view_nodes = full_view_scan(raw_dom)
        all_elements = java_nodes_to_repo_elements({"windows": full_view_nodes})
        # WHY: Some Oracle ListView row items can appear in the curated tree
        # (built from scoped DOM) but be absent from full-view flattening,
        # which breaks overlay hover + info icon lookup for those nodes.
        # Merge scoped elements so every tree element_ref has a full_elements
        # entry with bounds and metadata for Studio UI interactions.
        scoped_elements = java_nodes_to_repo_elements(scoped)
        by_id = {str(e.get("elementid") or ""): e for e in all_elements}
        for e in scoped_elements:
            eid = str(e.get("elementid") or "")
            if eid and eid not in by_id:
                by_id[eid] = e
        full_elements = build_full_overlay_elements(list(by_id.values()))

        # Update the bundle in-place with tree data.
        bundle.snapshot_text = str(payload.get("text") or "")
        bundle.tree = list(payload.get("tree") or [])
        bundle.raw_elements = enriched
        bundle.full_elements = full_elements

        # Auto-dump the scan details (raw DOM, screenshot, and snapshot text)
        # into tests/testdata/aisnapshot/new/<timestamp>/
        try:
            import json
            import shutil
            import sys
            ts_folder = datetime.now().strftime("%Y%m%d_%H%M%S")
            target_dir = Path("tests/testdata/aisnapshot/new") / ts_folder
            target_dir.mkdir(parents=True, exist_ok=True)
            
            # 1. java_scan_dump.json
            with open(target_dir / "java_scan_dump.json", "w", encoding="utf-8") as f:
                json.dump(bundle.raw_dom, f, indent=2, ensure_ascii=False)
            
            # 2. screenshot.png (main/first screenshot)
            if bundle.screenshot_path and Path(bundle.screenshot_path).exists():
                shutil.copy2(bundle.screenshot_path, target_dir / "screenshot.png")
            
            # 3. Per-tab screenshots (when multi-tab scan produced multiple)
            # WHY: Multi-tab DFS scans capture a screenshot for each leaf tab
            # state. These must be saved alongside the main screenshot so the
            # aisnapshot directory contains the complete scan output.
            if bundle.tab_screenshots:
                tab_dir = target_dir / "tab_screenshots"
                for tab_path, shot_path_str in bundle.tab_screenshots.items():
                    shot_path = Path(shot_path_str)
                    if shot_path.exists():
                        tab_dir.mkdir(parents=True, exist_ok=True)
                        safe_name = tab_path.replace(" -> ", "_").replace("/", "_").replace("\\", "_")
                        shutil.copy2(shot_path, tab_dir / f"{safe_name}.png")
            
            # 4. ai_snapshot.txt
            with open(target_dir / "ai_snapshot.txt", "w", encoding="utf-8") as f:
                f.write(bundle.snapshot_text)
            
        except Exception as e:
            import sys
            print(f"Error auto-dumping scan files: {e}", file=sys.stderr)

        # Persist the updated bundle back into the shared draft cache.
        StudioService._drafts[scan_id] = bundle
        self._scan_cache[scan_id] = bundle

        return bundle

    def _persist_container(
        self,
        bundle: "ScanBundle",
        *,
        status: str,
        container_ref: str | None = None,
        title: str | None = None,
        saved_from: str = "qcs_studio_web",
        extra_metadata: dict | None = None,
        display_tree: list[dict] | None = None,
    ) -> dict:
        """Persist a scan bundle as a container with the given lifecycle status."""
        metadata: dict = {
            "screenshot_origin": bundle.screenshot_origin,
            "capture_mode": bundle.capture_mode,
            "snapshot_text": bundle.snapshot_text,
            "display_tree": display_tree if display_tree is not None else bundle.tree,
            "saved_from": saved_from,
        }
        if extra_metadata:
            metadata.update(extra_metadata)

        final_container_ref = repo_identity.normalize_ref(container_ref or bundle.container_ref)
        final_title = title or bundle.title
        return repo_store.save_container_scan(
            final_container_ref,
            title=final_title,
            raw_dom=bundle.raw_dom,
            tree_elements=bundle.raw_elements,
            screenshot_path=bundle.screenshot_path,
            surface="java_forms",
            source="recording",
            status=status,
            metadata=metadata,
            repo_dir=config.REPO_DIR,
        )


    def save_scan(
        self,
        scan_id: str,
        *,
        container_ref: str | None = None,
        title: str | None = None,
        metadata: dict | None = None,
        display_tree: list[dict] | None = None,
    ) -> dict:
        bundle = self._scan_cache.get(scan_id)
        if bundle is None:
            raise KeyError(f"Unknown scan_id {scan_id!r}")

        saved = self._persist_container(
            bundle,
            status="active",
            container_ref=container_ref,
            title=title,
            saved_from="qcs_studio_web",
            extra_metadata=dict(metadata or {}),
            display_tree=display_tree,
        )
        # Once saved, remove the draft so it no longer appears as a draft.
        # The container is now in the persisted repo.
        self.delete_draft(scan_id)
        return saved

    def get_cached_scan(self, scan_id: str) -> ScanBundle | None:
        return self._scan_cache.get(scan_id)

    def list_drafts(self) -> list[dict]:
        """Return all in-memory draft scans as lightweight summaries."""
        out: list[dict] = []
        for scan_id, bundle in StudioService._drafts.items():
            out.append({
                "scan_id": scan_id,
                "title": bundle.title,
                "container_ref": bundle.container_ref,
                "has_tree": bool(bundle.tree),
                "capture_mode": bundle.capture_mode,
                "created_at": bundle.created_at,
            })
        out.sort(key=lambda d: d["created_at"] or "", reverse=True)
        return out

    def delete_draft(self, scan_id: str) -> bool:
        """Delete an in-memory draft scan. Returns True if it existed."""
        bundle = StudioService._drafts.pop(scan_id, None)
        self._scan_cache.pop(scan_id, None)
        if bundle is not None:
            # Clean up the temp screenshot file.
            try:
                if bundle.screenshot_path.exists():
                    bundle.screenshot_path.unlink()
            except Exception:
                pass
            return True
        return False

    def load_draft(self, scan_id: str) -> ScanBundle | None:
        """Return a cached draft scan bundle, or None if not found."""
        return self._scan_cache.get(scan_id)

    def full_elements_for_container(self, container: dict) -> list[dict]:
        """Compute the full hoverable element overlay for a stored container.

        WHY: Reloaded containers must support the same hover-inspect and
        drag-to-tree workflow as a live scan. We rebuild the overlay from the
        persisted raw DOM using raw (unadjusted) coordinates that align
        directly with the fullscreen screenshot.
        """
        raw_dom = container.get("raw_dom")
        if not isinstance(raw_dom, dict) or not raw_dom:
            return []
        try:
            # Full overlay uses raw DOM filtered to active form window +
            # toolbar / menu bar only (exclude non-active ExtendedFrame subtrees).
            nodes = full_view_scan(raw_dom)
            elements = java_nodes_to_repo_elements({"windows": nodes})
        except Exception:
            return []
        return build_full_overlay_elements(elements)

def _stamp_owner_tab_paths(dom: dict, path: tuple) -> None:
    """Stamp the full tab path onto the content of ONE captured leaf tab.

    `path` is the exact tab path that was active when `dom` was scanned
    (e.g. ("Order Information", "Main")). A node belongs to that leaf when its
    accessibleName carries the "<leaf> tab page " marker Oracle Forms stamps on
    the innermost tab's content. This is the only point in the pipeline where the
    parent tab is known, so we record the whole path; the renderer groups on it.
    """
    if not path:
        return
    leaf = str(path[-1])
    marker = f"{leaf} tab page "
    stack = list(dom.get("windows") or [dom])
    while stack:
        n = stack.pop()
        an = str(n.get("accessibleName") or "")
        i = an.find(" tab page ")
        if an.startswith(marker) or (i > 0 and an[:i] == leaf):
            n["ownerTabPath"] = list(path)
        stack.extend(n.get("children") or [])
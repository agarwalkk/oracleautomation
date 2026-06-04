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
from qcs_java_agent.settle import settle_forms
from qcs_java_agent.snapshot import (
    _oracle_forms_active_frame,
    active_form_title,
    active_window_scan,
    build_action_payload,
    java_nodes_to_repo_elements,
)
from qcs_repo import fingerprint as repo_fingerprint
from qcs_repo import identity as repo_identity
from qcs_repo import snapshot as repo_snapshot
from qcs_repo import store as repo_store


def _foreground_window() -> tuple[int, int]:
    """Return (hwnd, window_state) of current foreground window or (0, 0) on non-Windows.
    Window state: 0=unknown, 1=normal, 2=minimized, 3=maximized.
    """
    if not hasattr(ctypes, "windll"):
        return (0, 0)
    try:
        user32 = ctypes.windll.user32
        hwnd = int(user32.GetForegroundWindow())
        if hwnd == 0:
            return (0, 0)
        # Get window placement to preserve state.
        placement = ctypes.c_ubyte * 44
        pm = placement()
        pm[0] = 44
        res = user32.GetWindowPlacement(ctypes.c_void_p(hwnd), ctypes.byref(pm))
        if res:
            showCmd = int(pm[4])
            return (hwnd, showCmd)
        return (hwnd, 0)
    except Exception:
        return (0, 0)


def _restore_foreground(hwnd_and_state: tuple[int, int]) -> None:
    """Best-effort restore focus to a window with its prior state."""
    if not hasattr(ctypes, "windll"):
        return
    hwnd, state = hwnd_and_state
    if hwnd == 0:
        return
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        # State constants: 0=hide, 1=normal, 2=minimized, 3=maximized, 9=restore.
        # Map to show command that reflects prior state.
        show_cmd = state if state in (1, 2, 3) else 9
        user32.ShowWindow(ctypes.c_void_p(hwnd), show_cmd)

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


_ROLE_ACTIONS: dict[str, list[str]] = {
    "Button": ["click"],
    "Field": ["type", "clear"],
    "ComboBox": ["select"],
    "Checkbox": ["toggle"],
    "RadioButton": ["select"],
    "List": ["select"],
    "Tree": ["select", "expand"],
    "Table": ["select"],
    "Menu": ["open"],
    "MenuItem": ["click"],
    "Tab": ["activate"],
}


def _element_actions(element: dict) -> list[str]:
    """Derive the plausible user actions for a scanned element.

    WHY: The full-element hover overlay shows users what they can do with any
    element on screen. Actions are inferred from the element role plus a couple
    of Oracle-specific signals (LOV name suffix, editability) so the tooltip is
    helpful without invoking AI.
    """
    role = str(element.get("role") or "")
    name = str(element.get("name") or "").lower()
    states = element.get("states") or []
    if "list of values" in name or "(lov)" in name:
        return ["click", "open list"]
    base = _ROLE_ACTIONS.get(role, [])
    if not base:
        return ["inspect"]  # read-only display / unknown: inspectable only
    if role == "Field" and "editable" not in states:
        return ["inspect"]
    return base


def _full_overlay_elements(
    elements: list[dict], *, origin_x: int, origin_y: int
) -> list[dict]:
    """Build the full hoverable element overlay from all scanned elements.

    WHY: Requirement is that EVERY element from the full scan is hoverable and
    inspectable (id, name, type, possible actions) and can be dragged into the
    curated tree. Bounds are origin-adjusted to align with the cropped
    Java-window screenshot. Zero-size elements are skipped. We intentionally do
    NOT require a "showing"/"visible" state here: Oracle Forms omits those flags
    on many real, on-screen widgets, and filtering on them would hide most of
    the elements the user expects to hover over.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for el in elements:
        ref = str(el.get("elementid") or "")
        if not ref or ref in seen:
            continue
        bounds = el.get("bounds") or {}
        w = int(bounds.get("width", el.get("width", 0)) or 0)
        h = int(bounds.get("height", el.get("height", 0)) or 0)
        if w <= 0 or h <= 0:
            continue
        x = int(bounds.get("x", el.get("x", 0)) or 0) - int(origin_x)
        y = int(bounds.get("y", el.get("y", 0)) or 0) - int(origin_y)
        # Skip elements fully outside the captured window region.
        if x + w <= 0 or y + h <= 0:
            continue
        java = el.get("java") or {}
        states = el.get("states") or []
        out.append(
            {
                "element_ref": ref,
                "name": str(el.get("name") or el.get("friendly_name") or ""),
                "role": str(el.get("role") or ""),
                "type": str(java.get("simpleClassName") or el.get("role") or ""),
                "actions": _element_actions(el),
                "value": str(java.get("value") or el.get("text") or ""),
                "enabled": "enabled" in states,
                "bounds": {"x": x, "y": y, "width": w, "height": h},
                # WHY: Java locator data (descriptor + locator_params) is needed by
                # the Studio UI to display tooltips showing how an element is
                # addressed by the Java agent, enabling users to understand and
                # debug locator strategies.
                "descriptor": str(java.get("descriptor") or ""),
                "locator_params": java.get("locator_params") or {},
            }
        )
        seen.add(ref)
    return out


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
            windows.append(
                {
                    "source": "java_forms",
                    "pid": proc.pid,
                    "label": f"[{proc.pid}] {proc.main}",
                    "main": proc.main,
                    "jvm_args": proc.jvm_args,
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
        previous_state = _foreground_window()
        hwnd = _bring_process_window_to_front(driver.pid)

        # After maximize/foreground handoff, Oracle Forms can spend a short
        # period re-laying out. Settling first avoids scanning bounds from one
        # frame state and capturing a screenshot from another (vertical offset).
        try:
            settle_forms(driver, timeout_s=4.0, poll_interval_s=0.15, stable_polls=2, log_prefix="[StudioScan]")
        except Exception:
            # Best-effort only: scan should proceed even if settle fails.
            pass

        raw_dom = driver.scan()
        scoped = active_window_scan(raw_dom)

        # Resolve the active frame and the top-level Java window. The window's
        # screen origin becomes the coordinate origin so element overlays align
        # to the cropped Java-window screenshot.
        frame = _oracle_forms_active_frame(raw_dom)
        # Prefer OS window rect (pixel-truth for screenshot crop/origin). Fallback
        # to Java DOM-derived bounds only if Win32 rect is unavailable.
        window_bounds = _window_rect(hwnd) or _java_window_bounds(raw_dom, frame)
        origin_x = int((window_bounds or {}).get("x", 0) or 0)
        origin_y = int((window_bounds or {}).get("y", 0) or 0)

        # Container name defaults to the form name (frame caption), falling back
        # to the generic active-form title only when the frame has no caption.
        title = _frame_form_title(frame) or active_form_title(scoped)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        temp_screenshot = Path(tempfile.gettempdir()) / f"qcs_studio_scan_{ts}_{uuid4().hex[:8]}.png"
        try:
            # One more short settle right before capture: guards against late
            # repaint/resize churn after scan-time processing.
            try:
                settle_forms(driver, timeout_s=1.5, poll_interval_s=0.1, stable_polls=2, log_prefix="[StudioShot]")
            except Exception:
                pass

            # Always capture fullscreen, then crop to the Java window region.
            screenshot_result = driver.screenshot(temp_screenshot)
        finally:
            _restore_foreground(previous_state)

        capture_mode = str(screenshot_result.get("captureMode") or "fullscreen")
        if window_bounds and _crop_to_bounds(temp_screenshot, window_bounds):
            capture_mode = "java-window"

        scan_id = uuid4().hex

        # Compute fingerprint from the DOM elements (lightweight, no AI payload).
        elements = java_nodes_to_repo_elements(scoped)
        enriched = repo_snapshot.enrich_java_elements(elements)
        title_for_fp = _frame_form_title(frame) or active_form_title(scoped)
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
            raw_dom=raw_dom,
            snapshot_text="",  # populated by compute_tree
            tree=[],           # populated by compute_tree
            raw_elements=enriched,
            full_elements=[],  # populated by compute_tree
            screenshot_path=temp_screenshot,
            screenshot_origin={"x": origin_x, "y": origin_y},
            capture_mode=capture_mode,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
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
        origin_x = bundle.screenshot_origin.get("x", 0) or 0
        origin_y = bundle.screenshot_origin.get("y", 0) or 0

        scoped = active_window_scan(raw_dom)
        enriched = repo_snapshot.enrich_java_elements(java_nodes_to_repo_elements(scoped))

        payload = build_action_payload(scoped, origin_x=origin_x, origin_y=origin_y)

        elements = java_nodes_to_repo_elements(scoped)
        full_elements = _full_overlay_elements(elements, origin_x=origin_x, origin_y=origin_y)

        # Update the bundle in-place with tree data.
        bundle.snapshot_text = str(payload.get("text") or "")
        bundle.tree = list(payload.get("tree") or [])
        bundle.raw_elements = enriched
        bundle.full_elements = full_elements

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
        persisted raw DOM, using the stored screenshot origin so bounds align
        with the saved screenshot.
        """
        raw_dom = container.get("raw_dom")
        if not isinstance(raw_dom, dict) or not raw_dom:
            return []
        metadata = container.get("metadata") or {}
        origin = metadata.get("screenshot_origin") or {}
        origin_x = int(origin.get("x", 0) or 0)
        origin_y = int(origin.get("y", 0) or 0)
        try:
            scoped = active_window_scan(raw_dom)
            elements = java_nodes_to_repo_elements(scoped)
        except Exception:
            return []
        return _full_overlay_elements(elements, origin_x=origin_x, origin_y=origin_y)


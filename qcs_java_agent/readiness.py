"""Local Oracle Forms readiness checks based on Java-agent scans.

These helpers keep Java DOM analysis inside the automation runtime. They are used
before sending screenshots to AI so the model sees an idle, fully rendered form
instead of an intermediate loading state.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import config

from .snapshot import active_form_title, flatten_nodes


_ACTIONABLE_ROLES = {
    "Field",
    "Button",
    "List",
    "ComboBox",
    "Checkbox",
    "RadioButton",
    "Menu",
    "MenuItem",
    "Tab",
    "Table",
    "Tree",
}
_BUSY_TEXT = (
    "please wait",
    "working",
    "busy",
    "processing",
    "query caused no records",
    "frm-40735",
)


@dataclass(frozen=True)
class FormsReadiness:
    ready: bool
    title: str
    signature: tuple[Any, ...]
    actionable_count: int
    busy: bool
    reason: str


def analyze_forms_readiness(scan: dict) -> FormsReadiness:
    """Return readiness signals from one Java-agent scan."""
    nodes = flatten_nodes(scan)
    title = active_form_title(scan)
    actionable: list[dict] = []
    busy = False
    status_texts: list[str] = []

    for node in nodes:
        role = str(node.get("semanticType") or node.get("accessibleRole") or "")
        name = str(
            node.get("displayName")
            or node.get("accessibleName")
            or node.get("name")
            or node.get("text")
            or node.get("title")
            or ""
        ).strip()
        cursor_name = str(node.get("cursorName") or "").lower()
        cursor_type = node.get("cursorType")
        if cursor_type == 3 or "wait" in cursor_name:
            busy = True
        if role == "StatusBar" and name:
            status_texts.append(name.lower())
        if role in _ACTIONABLE_ROLES and node.get("showing") and node.get("enabled"):
            actionable.append(node)

    status_blob = "\n".join(status_texts)
    if any(marker in status_blob for marker in _BUSY_TEXT):
        busy = True

    pairs = []
    for node in actionable:
        role = str(node.get("semanticType") or node.get("accessibleRole") or "")
        name = str(node.get("displayName") or node.get("accessibleName") or node.get("text") or "")
        path = str(node.get("path") or "")
        pairs.append((role, name, path))
    signature = (title, len(nodes), len(actionable), tuple(sorted(pairs)[:80]))

    if not title:
        return FormsReadiness(False, title, signature, len(actionable), busy, "no active title")
    if busy:
        return FormsReadiness(False, title, signature, len(actionable), busy, "busy cursor/status")
    if not actionable:
        return FormsReadiness(False, title, signature, len(actionable), busy, "no actionable components")
    return FormsReadiness(True, title, signature, len(actionable), busy, "ready")


def wait_for_forms_ready(
    driver: Any,
    *,
    expected_title: str | None = None,
    timeout_s: int = 120,
    idle_ms: int | None = None,
    poll_ms: int | None = None,
    log_prefix: str = "[JavaAgent]",
) -> FormsReadiness:
    """Wait until Forms looks locally idle and structurally stable."""
    deadline = time.monotonic() + timeout_s
    idle_s = (idle_ms if idle_ms is not None else config.FORMS_IDLE_MS) / 1000
    poll_s = max((poll_ms if poll_ms is not None else config.FORMS_POLL_MS) / 1000, 0.1)
    last: FormsReadiness | None = None
    last_signature: tuple[Any, ...] | None = None
    stable_since = time.monotonic()

    while time.monotonic() < deadline:
        scan = driver.scan()
        current = analyze_forms_readiness(scan)
        last = current
        title_ok = not expected_title or expected_title.lower() in current.title.lower()
        if current.signature != last_signature:
            last_signature = current.signature
            stable_since = time.monotonic()
        stable_for = time.monotonic() - stable_since
        if current.ready and title_ok and stable_for >= idle_s:
            print(
                f"{log_prefix} Forms ready: {current.title} "
                f"({current.actionable_count} actionable, stable {stable_for:.1f}s)"
            )
            return current
        time.sleep(poll_s)

    detail = last.reason if last else "no scan"
    title = last.title if last else ""
    count = last.actionable_count if last else 0
    raise RuntimeError(
        f"Oracle Forms did not become ready within {timeout_s}s "
        f"(last_title={title!r}, actionable={count}, reason={detail})"
    )

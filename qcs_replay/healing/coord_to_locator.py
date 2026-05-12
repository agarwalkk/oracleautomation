"""Reverse-map screen coordinates to deterministic locator descriptors.

For Java Forms, use the local Java agent scan and element bounds to identify
the element under a computer-use action. For HTML, use Playwright DOM lookup.
"""
from __future__ import annotations

from typing import Any

from qcs_java_agent import java_nodes_to_repo_elements


def java_coord_to_descriptor(driver: Any, x: int, y: int) -> dict | None:
    """Return the Java-agent repo descriptor whose bounds contain ``(x, y)``."""
    try:
        scan = driver.scan()
        elements = java_nodes_to_repo_elements(scan)
    except Exception:
        return None

    matches: list[dict] = []
    for element in elements:
        bounds = element.get("bounds") or {}
        left = int(bounds.get("x", element.get("x", 0)) or 0)
        top = int(bounds.get("y", element.get("y", 0)) or 0)
        width = int(bounds.get("width", element.get("width", 0)) or 0)
        height = int(bounds.get("height", element.get("height", 0)) or 0)
        if left <= x <= left + width and top <= y <= top + height:
            matches.append(element)

    if not matches:
        return None

    matches.sort(key=lambda el: int(el.get("width", 0) or 0) * int(el.get("height", 0) or 0))
    return matches[0]


def html_coord_to_descriptor(page: Any, x: int, y: int) -> dict | None:
    """
    Evaluate JavaScript on the page to identify the element at (x, y)
    and extract the relevant attributes for a LocatorDescriptor.
    """
    import asyncio  # noqa: PLC0415

    script = """
    ([x, y]) => {
        const el = document.elementFromPoint(x, y);
        if (!el) return null;
        return {
            role:      el.getAttribute('role') || el.tagName.toLowerCase(),
            name:      el.getAttribute('aria-label') || el.textContent?.trim()?.slice(0, 60) || '',
            element_id: el.id || '',
            test_id:   el.getAttribute('data-testid') || '',
            css:       '',
            label_neighbor: '',
        };
    }
    """

    async def _eval():
        return await page.evaluate(script, [x, y])

    try:
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(_eval())
        return result or None
    except Exception:
        return None

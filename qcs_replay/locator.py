"""
qcs_replay.locator — Locator resolvers for Java-agent and Playwright replay.

Priority order
--------------
Java Forms:
    Resolve repository descriptors through qcs_java_agent locator candidates.

HTML (Playwright):
  1. get_by_role(role, name=name)
  2. get_by_label(label_neighbor)
  3. get_by_test_id / stable id attribute
  4. locator(css_selector)
  5. locator(xpath)

Usage
-----
    from qcs_replay.locator import JavaAgentResolver, PlaywrightResolver

    r = JavaAgentResolver(driver)
    element = r.resolve(descriptor)
    element.send_text("Standard_ISVUS", simulate=True)

    rp = PlaywrightResolver(page)
    loc = rp.resolve(descriptor)
    loc.fill("Standard_ISVUS")
"""
from __future__ import annotations

from typing import Any

import config


# ── Descriptor helpers ────────────────────────────────────────────────────────

class LocatorDescriptor:
    """Structured locator pulled from repo/elements/<form_id>.yaml."""

    def __init__(self, data: dict):
        data = data or {}
        self.raw              = data or {}
        self.friendly_name    = data.get("friendly_name", "")
        self.role             = data.get("role", "")
        self.name             = data.get("name", "")
        self.ancestors        = data.get("ancestors", [])
        self.label_neighbor   = data.get("label_neighbor", "")
        self.xpath            = data.get("xpath", "")
        self.css              = data.get("css", "")
        self.test_id          = data.get("test_id", "")
        self.element_id       = data.get("element_id", "")
        self.table_locator    = data.get("table_locator")  # {table_role, row_key, col_name}

    def to_dict(self) -> dict:
        merged = dict(self.raw)
        merged.update({k: v for k, v in self.__dict__.items() if k != "raw" and v})
        return merged


# ── Java Agent Resolver ─────────────────────────────────────────────────────

class JavaAgentResolver:
    def __init__(self, driver: Any):
        self._driver = driver

    def resolve(self, descriptor: LocatorDescriptor) -> Any:
        from qcs_replay.java_agent import JavaAgentElement  # noqa: PLC0415

        return JavaAgentElement(self._driver, descriptor.to_dict())


# ── Playwright Resolver ───────────────────────────────────────────────────────

class PlaywrightResolver:
    def __init__(self, page: Any, timeout_ms: int = config.LOCATOR_TIMEOUT_S * 1000):
        self._page = page
        self._timeout = timeout_ms

    def resolve(self, descriptor: LocatorDescriptor) -> Any:
        """
        Return a Playwright Locator using the first working strategy.
        The locator is lazy — it is only evaluated when an action is called on it.
        Falls back through strategies by checking visibility / count.
        """
        strategies = [
            self._by_role_name(descriptor),
            self._by_label(descriptor),
            self._by_test_id(descriptor),
            self._by_id(descriptor),
            self._by_css(descriptor),
            self._by_xpath(descriptor),
        ]

        for loc in strategies:
            if loc is None:
                continue
            try:
                loc.wait_for(state="attached", timeout=2000)
                return loc
            except Exception:
                continue

        raise RuntimeError(
            f"PlaywrightResolver: could not locate '{descriptor.friendly_name}' "
            "using any strategy."
        )

    def _by_role_name(self, d: LocatorDescriptor):
        if d.role and d.name:
            return self._page.get_by_role(d.role, name=d.name)
        return None

    def _by_label(self, d: LocatorDescriptor):
        if d.label_neighbor:
            return self._page.get_by_label(d.label_neighbor)
        return None

    def _by_test_id(self, d: LocatorDescriptor):
        if d.test_id:
            return self._page.get_by_test_id(d.test_id)
        return None

    def _by_id(self, d: LocatorDescriptor):
        if d.element_id:
            return self._page.locator(f"#{d.element_id}")
        return None

    def _by_css(self, d: LocatorDescriptor):
        if d.css:
            return self._page.locator(d.css)
        return None

    def _by_xpath(self, d: LocatorDescriptor):
        if d.xpath:
            return self._page.locator(d.xpath)
        return None


# ── Convenience: resolve from repo descriptor dict ────────────────────────────

def resolve_playwright(page: Any, descriptor: dict, *, timeout_ms: int | None = None) -> Any:
    kw = {"timeout_ms": timeout_ms} if timeout_ms else {}
    return PlaywrightResolver(page, **kw).resolve(LocatorDescriptor(descriptor))

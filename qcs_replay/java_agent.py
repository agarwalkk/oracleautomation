"""Replay helpers backed by the local QCS Java Forms agent.

It exposes a small element API (`click`, `send_text`, `get_element_information`)
for generated replay code while executing through the Java agent.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import config
from qcs_java_agent.exceptions import AttachError, CommandError, ProcessNotFoundError
from qcs_java_agent import JavaAgentDriver, active_form_title, java_nodes_to_repo_elements, wait_for_forms_ready
from qcs_repo import store as repo_store


@dataclass
class JavaAgentElement:
    driver: JavaAgentDriver
    descriptor: dict

    def click(self, simulate: bool = True) -> dict:
        return self.driver.click(self.descriptor)

    def send_text(self, text: str, simulate: bool = True) -> dict:
        return self.driver.set_text(self.descriptor, text)

    def clear(self) -> dict:
        return self.driver.clear(self.descriptor)

    def press_key(self, key: str) -> dict:
        return self.driver.press_key(key, self.descriptor)

    def double_click(self) -> dict:
        return self.driver.double_click(self.descriptor)

    def select_option(self, value: str) -> dict:
        return self.driver.select_option(self.descriptor, value)

    def set_check(self, checked: bool) -> dict:
        return self.driver.set_check(self.descriptor, checked)

    def expand_tree(self, tree_row: int | None = None) -> dict:
        return self.driver.expand_tree(self.descriptor, expand=True, tree_row=tree_row)

    def collapse_tree(self, tree_row: int | None = None) -> dict:
        return self.driver.collapse_tree(self.descriptor, tree_row=tree_row)

    def screenshot(self, path: str | Path) -> dict:
        return self.driver.screenshot(path, self.descriptor)

    def get_element_information(self) -> dict:
        fresh = self._fresh_element_information()
        if fresh is not None:
            return fresh
        return {
            "_found": False,
            "elementid": self.descriptor.get("elementid", ""),
            "name": self.descriptor.get("name", ""),
            "role": self.descriptor.get("role", ""),
            "description": self.descriptor.get("description", ""),
            "states": self.descriptor.get("states", []),
            "text": self.descriptor.get("text", ""),
            "bounds": self.descriptor.get("bounds", {}),
            "java": self.descriptor.get("java", {}),
        }

    def _fresh_element_information(self) -> dict | None:
        try:
            elements = java_nodes_to_repo_elements(self.driver.scan())
        except Exception:
            return None

        descriptor_path = _descriptor_path(self.descriptor)
        descriptor_elementid = str(self.descriptor.get("elementid") or "")
        descriptor_name = str(self.descriptor.get("name") or "")
        descriptor_role = str(self.descriptor.get("role") or "")

        for element in elements:
            if descriptor_path and _descriptor_path(element) == descriptor_path:
                return {**element, "_found": True}
        if descriptor_elementid:
            for element in elements:
                if str(element.get("elementid") or "") == descriptor_elementid:
                    return {**element, "_found": True}
        if descriptor_name or descriptor_role:
            for element in elements:
                if (
                    (not descriptor_name or element.get("name") == descriptor_name)
                    and (not descriptor_role or element.get("role") == descriptor_role)
                ):
                    return {**element, "_found": True}
        return None


def _descriptor_path(descriptor: dict) -> str:
    java = descriptor.get("java") or {}
    return str(java.get("path") or descriptor.get("path") or descriptor.get("xpath") or "")


def attach_java_agent(
    *,
    pid: int | None = None,
    contains: str | None = None,
    timeout_s: int = 120,
) -> JavaAgentDriver:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    match = contains or config.JAVA_AGENT_PROCESS_MATCH

    while time.monotonic() < deadline:
        try:
            driver = JavaAgentDriver.attach(
                pid=pid,
                contains=match,
                timeout_s=timeout_s,
            )
            driver.health()
            driver.scan()
            return driver
        except (AttachError, CommandError, ProcessNotFoundError) as exc:
            last_error = exc
            time.sleep(1)

    raise RuntimeError(
        f"Could not attach to a live Java Forms process matching {match!r} within {timeout_s}s"
        + (f": {last_error}" if last_error else "")
    )


def resolve_java_ref(driver: JavaAgentDriver, form_id: str, ref: str) -> JavaAgentElement:
    resolved = repo_store.resolve_element_ref(ref, form_id=form_id)
    if not resolved and form_id:
        try:
            resolved = repo_store.resolve_element_ref(ref)
            if resolved:
                fallback_form_id, _ = resolved
                print(
                    f"[Replay:JavaAgent] Ref {ref!r} not found in {form_id!r}; "
                    f"using unique repo match in {fallback_form_id!r}."
                )
        except RuntimeError:
            resolved = None
    descriptor = resolved[1] if resolved else None
    if not descriptor:
        raise RuntimeError(f"Java agent ref {ref!r} not found in repo form {form_id!r}")
    if descriptor.get("enabled") is False:
        raise RuntimeError(f"Java agent ref {ref!r} is disabled in repo form {form_id!r}")
    return JavaAgentElement(driver, descriptor)


def wait_for_active_form(
    driver: JavaAgentDriver,
    expected_name: str | None = None,
    timeout_s: int = 120,
) -> str:
    try:
        ready = wait_for_forms_ready(
            driver,
            expected_title=expected_name,
            timeout_s=timeout_s,
            log_prefix="[Replay:JavaAgent]",
        )
        return ready.title
    except RuntimeError:
        pass

    deadline = time.monotonic() + timeout_s
    last_signature: tuple[Any, ...] | None = None
    stable_since = time.monotonic()
    idle_s = config.FORMS_IDLE_MS / 1000
    poll_s = max(config.FORMS_POLL_MS / 1000, 0.1)
    last_title = ""

    while time.monotonic() < deadline:
        scan = driver.scan()
        title = active_form_title(scan)
        node_count = _count_nodes(scan)
        signature = (title, node_count)
        if signature != last_signature:
            last_signature = signature
            stable_since = time.monotonic()
        if title:
            last_title = title
            if (not expected_name or expected_name in title) and time.monotonic() - stable_since >= idle_s:
                print(f"[Replay:JavaAgent] Active form idle: {title}")
                return title
        time.sleep(poll_s)

    raise RuntimeError(
        f"Active Oracle form did not appear within {timeout_s}s"
        + (f" (expected {expected_name!r}, last seen {last_title!r})" if expected_name else "")
    )


def _count_nodes(scan: dict) -> int:
    count = 0

    def walk(node: dict) -> None:
        nonlocal count
        count += 1
        for child in node.get("children") or []:
            walk(child)

    for window in scan.get("windows") or []:
        walk(window)
    return count

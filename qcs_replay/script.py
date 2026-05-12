"""Human-readable replay DSL for generated pytest scripts.

Generated tests should read like business replay steps while this module keeps
the deterministic Playwright and Java-agent plumbing in one place.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from qcs_replay.java_agent import attach_java_agent, resolve_java_ref, wait_for_active_form
from qcs_replay.web import open_java_form_sync
from qcs_repo import store as repo_store


class OracleReplay:
    """Top-level replay facade used by generated pytest scripts."""

    def __init__(
        self,
        page: Any,
        healer: Any | None = None,
        object_repository: dict[str, Any] | None = None,
    ) -> None:
        self.page = page
        self.healer = healer
        self.object_repository = object_repository or {}
        self.java_driver: Any | None = None
        self.current_java_form_id = ""

    def login(
        self,
        url: str | None = None,
        *,
        user_env: str = "EBS_USER",
        password_env: str = "EBS_PASSWORD",
    ) -> None:
        login_url = url or os.environ.get("EBS_URL")
        if not login_url:
            raise RuntimeError("EBS login URL was not provided and EBS_URL is not set")
        self.page.goto(login_url)
        self.page.wait_for_load_state("networkidle")
        self.page.get_by_role("textbox", name="User Name").fill(os.environ[user_env])
        self.page.get_by_role("textbox", name="Password").fill(os.environ[password_env])
        self.page.get_by_role("button", name="Log In").click()
        self.page.wait_for_load_state("networkidle")

    def open_form(
        self,
        *,
        url: str,
        name: str | None = None,
        form_id: str | None = None,
        expected_name: str | None = None,
        objects: dict[str, Any] | None = None,
    ) -> "JavaFormReplay":
        object_repository = objects or self.object_repository
        form_metadata = object_repository.get("__form__") or {}
        resolved_form_id = form_id or form_metadata.get("form_id")

        resolved_name = name or form_metadata.get("name") or form_metadata.get("expected_name") or resolved_form_id
        resolved_expected = expected_name or form_metadata.get("expected_name") or name
        open_java_form_sync(self.page, url)
        self.java_driver = attach_java_agent()
        active_name = wait_for_active_form(self.java_driver, expected_name=resolved_expected)
        if not resolved_form_id:
            resolved_form_id = _resolve_form_id_by_title(active_name)
        if not resolved_form_id:
            raise RuntimeError(f"Could not resolve repository form for active Oracle form {active_name!r}")
        self.current_java_form_id = resolved_form_id
        return JavaFormReplay(
            self,
            name=resolved_name or active_name,
            default_form_id=resolved_form_id,
            objects=object_repository,
        )

    def form(
        self,
        name: str,
        *,
        form_id: str | None = None,
        objects: dict[str, Any] | None = None,
    ) -> "JavaFormReplay":
        object_repository = objects or self.object_repository.get(name) or {}
        form_metadata = object_repository.get("__form__") or {}
        resolved_form_id = form_id or form_metadata.get("form_id")
        if not resolved_form_id:
            raise RuntimeError(f"No form_id supplied for Oracle form {name!r}")
        return JavaFormReplay(
            self,
            name=name,
            default_form_id=resolved_form_id,
            objects=object_repository,
        )

    def window(
        self,
        name: str,
        *,
        form_id: str | None = None,
        objects: dict[str, Any] | None = None,
    ) -> "JavaFormReplay":
        return self.form(name, form_id=form_id, objects=objects)

    def key(self, key_name: str) -> None:
        self._java_driver().press_key(key_name)

    def _java_driver(self) -> Any:
        if self.java_driver is None:
            self.java_driver = attach_java_agent()
        return self.java_driver


def _resolve_form_id_by_title(title: str) -> str | None:
    wanted = _normalize_title(title)
    for form_id in repo_store.list_form_ids():
        form = repo_store.load_form(form_id) or {}
        candidate = str(form.get("title") or form.get("name") or "")
        if candidate and _normalize_title(candidate) == wanted:
            return form_id
    return None


def _normalize_title(title: str) -> str:
    return " ".join(str(title or "").casefold().split())


@dataclass
class JavaFormReplay:
    """Readable handle for one Oracle Java Forms screen or dialog."""

    oracle: OracleReplay
    name: str
    default_form_id: str
    objects: dict[str, Any] | None = None

    def textbox(self, label: str, *, ref: str | None = None, form_id: str | None = None) -> "JavaControlReplay":
        return self.element(label, ref=ref, form_id=form_id, kind="field")

    def field(self, label: str, *, ref: str | None = None, form_id: str | None = None) -> "JavaControlReplay":
        return self.element(label, ref=ref, form_id=form_id, kind="field")

    def button(self, label: str, *, ref: str | None = None, form_id: str | None = None) -> "JavaControlReplay":
        return self.element(label, ref=ref, form_id=form_id, kind="button")

    def menu(self, label: str, *, ref: str | None = None, form_id: str | None = None) -> "JavaControlReplay":
        return self.element(label, ref=ref, form_id=form_id, kind="menu")

    def toolbar(self, label: str, *, ref: str | None = None, form_id: str | None = None) -> "JavaControlReplay":
        return self.element(label, ref=ref, form_id=form_id, kind="toolbar")

    def element(self, label: str, *, ref: str | None = None, form_id: str | None = None, kind: str = "element") -> "JavaControlReplay":
        definition = self._object_definition(label)
        return JavaControlReplay(
            oracle=self.oracle,
            label=label,
            ref=ref or definition.get("ref") or label,
            form_id=form_id or definition.get("form_id") or self.default_form_id,
            kind=definition.get("kind") or kind,
        )

    def key(self, key_name: str) -> None:
        self.oracle.key(key_name)

    def press_key(self, key_name: str) -> None:
        self.oracle.key(key_name)

    def close(self) -> None:
        self.oracle.key("ALT+F4")

    def _object_definition(self, label: str) -> dict[str, Any]:
        repository = self.objects or {}
        definition = repository.get(label)
        if isinstance(definition, dict):
            return definition
        return {}


@dataclass
class JavaControlReplay:
    """Readable control wrapper backed by a deterministic repository ref."""

    oracle: OracleReplay
    label: str
    ref: str
    form_id: str
    kind: str = "element"

    def click(self) -> None:
        self._target().click(simulate=True)

    def set(self, value: Any) -> None:
        self._target().send_text(str(value), simulate=True)

    def clear(self) -> None:
        self._target().clear()

    def press_key(self, key_name: str) -> None:
        self._target().press_key(key_name)

    def get_element_information(self) -> dict:
        return self._target().get_element_information()

    def _target(self) -> Any:
        return resolve_java_ref(self.oracle._java_driver(), self.form_id, self.ref)
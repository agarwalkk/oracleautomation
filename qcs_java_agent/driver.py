"""Python client for the local QCS Java Forms agent command protocol."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import base64

import config

from .command import run_agent_command
from .process import find_java_process
from .snapshot import locator_params


class JavaAgentDriver:
    """One-shot client for the Java agent loaded into an Oracle Forms JVM."""

    def __init__(
        self,
        *,
        pid: int,
        java_exe: str | None = None,
        agent_jar: str | Path | None = None,
        timeout_s: int = 120,
        debug: bool = False,
    ) -> None:
        self.pid = int(pid)
        self.java_exe = java_exe or config.JAVA_AGENT_JAVA_EXE
        self.agent_jar = Path(agent_jar or config.JAVA_AGENT_JAR)
        self.timeout_s = timeout_s
        self.debug = debug

    @classmethod
    def attach(
        cls,
        *,
        pid: int | None = None,
        contains: str | None = None,
        java_exe: str | None = None,
        agent_jar: str | Path | None = None,
        timeout_s: int = 120,
        debug: bool = False,
    ) -> "JavaAgentDriver":
        if pid is None:
            process = find_java_process(contains or config.JAVA_AGENT_PROCESS_MATCH)
            pid = process.pid
        return cls(
            pid=pid,
            java_exe=java_exe,
            agent_jar=agent_jar,
            timeout_s=timeout_s,
            debug=debug,
        )

    def health(self) -> dict:
        return self._run({"command": "health"})

    def scan(self, *, probe: bool = False, target: str | None = None) -> dict:
        """Run a scan. With probe=True, matched elements (per `target`, or the
        default first-field-per-region) carry an `attributes._probe` reflection
        inventory — used by scripts/probe_scan.py + probe_report.py to discover
        which non-robotic methods a widget actually exposes."""
        command: dict[str, Any] = {"command": "scan"}
        if probe:
            command["probe"] = "true"
        if target:
            command["target"] = str(target)
        return self._run(command)

    def raw(self) -> dict:
        return self._run({"command": "raw"})

    def layout(self) -> str:
        return self._run({"command": "layout"}, text_output=True).get("text", "")

    def tables(self) -> dict:
        return self._run({"command": "tables"})

    def focus(self, descriptor: dict) -> dict:
        return self._run({"command": "focus", **locator_params(descriptor)})

    def click(self, descriptor: dict, tab_index: int | None = None, tab_count: int | None = None) -> dict:
        command = {"command": "click", **locator_params(descriptor)}
        if tab_index is not None:
            command["tab_index"] = str(tab_index)
        # tab_count is no longer required (non-robotic tab select uses the model),
        # but is still accepted for backward compatibility with older callers.
        if tab_count is not None:
            command["tab_count"] = str(tab_count)
        return self._run(command)

    def double_click(self, descriptor: dict) -> dict:
        """Non-robotic double-click (e.g. open a record from a grid/list)."""
        return self._run({"command": "doubleclick", **locator_params(descriptor)})

    def set_text(self, descriptor: dict, text: str) -> dict:
        encoded_text = base64.b64encode(text.encode("utf-8")).decode("ascii")
        return self._run({"command": "settext", "text64": encoded_text, **locator_params(descriptor)})

    def clear(self, descriptor: dict) -> dict:
        return self._run({"command": "clear", **locator_params(descriptor)})

    def select_option(self, descriptor: dict, value: str) -> dict:
        """Select a combo / poplist / list option by value (model-level)."""
        encoded = base64.b64encode(str(value).encode("utf-8")).decode("ascii")
        return self._run(
            {"command": "selectoption", "value64": encoded, **locator_params(descriptor)}
        )

    def set_check(self, descriptor: dict, checked: bool) -> dict:
        """Drive a checkbox / toggle to a specific state, idempotently."""
        return self._run(
            {
                "command": "setcheck",
                "value": "true" if checked else "false",
                **locator_params(descriptor),
            }
        )

    def expand_tree(
        self, descriptor: dict, *, expand: bool = True, tree_row: int | None = None
    ) -> dict:
        """Expand (or collapse) a tree node by model row.

        The row is taken from ``tree_row`` when given, otherwise the agent
        derives it from the leaf of the descriptor's ``locatorTreePath``.
        """
        command = {
            "command": "expandtree" if expand else "collapsetree",
            **locator_params(descriptor),
        }
        if tree_row is not None:
            command["tree_row"] = str(int(tree_row))
        return self._run(command)

    def collapse_tree(self, descriptor: dict, *, tree_row: int | None = None) -> dict:
        return self.expand_tree(descriptor, expand=False, tree_row=tree_row)

    def press_key(self, key: str, descriptor: dict | None = None) -> dict:
        command: dict[str, Any] = {"command": "presskey", "key": key}
        if descriptor:
            command.update(locator_params(descriptor))
        return self._run(command)

    def screenshot(self, path: str | Path, descriptor: dict | None = None) -> dict:
        command: dict[str, Any] = {"command": "screenshot", "screenshotout": str(path)}
        if descriptor:
            command.update(locator_params(descriptor))
        return self._run(command)

    def highlight(self, descriptor: dict) -> dict:
        return self._run({"command": "highlight", **locator_params(descriptor)})

    def element_at(self, x: int, y: int) -> dict:
        return self._run({"command": "elementat", "x": int(x), "y": int(y)})

    def _run(self, command: dict[str, Any], *, text_output: bool = False) -> dict:
        return run_agent_command(
            pid=self.pid,
            agent_jar=self.agent_jar,
            command=command,
            java_exe=self.java_exe,
            timeout_s=self.timeout_s,
            text_output=text_output,
            debug=self.debug,
        )

"""One-shot command execution for the local QCS Java Forms agent."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from .attach import build_attach_command, format_agent_args
from .exceptions import AgentOutputError, AttachError, CommandError


def run_agent_command(
    *,
    pid: int,
    agent_jar: str | Path,
    command: dict[str, object],
    java_exe: str = "java",
    timeout_s: int = 120,
    text_output: bool = False,
    debug: bool = False,
) -> dict:
    jar = str(Path(agent_jar).resolve())
    fd, output_path = tempfile.mkstemp(prefix="qcs_java_agent_", suffix=".out")
    os.close(fd)
    try:
        os.unlink(output_path)
    except OSError:
        pass

    full_command = dict(command)
    full_command["out"] = output_path
    agent_args = format_agent_args(full_command)
    cmd = build_attach_command(
        pid=pid,
        agent_jar=jar,
        agent_args=agent_args,
        java_exe=java_exe,
    )

    try:
        proc = subprocess.run(
            cmd,
            capture_output=not debug,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise AttachError(f"Java agent command timed out after {timeout_s}s") from exc
    except FileNotFoundError as exc:
        raise AttachError(f"Failed to launch Java: {exc}") from exc

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip() if not debug else ""
        raise AttachError(
            f"AttachLauncher exited with code {proc.returncode}"
            + (f"\nstderr:\n{stderr}" if stderr else "")
        )

    try:
        result = _read_output(output_path, text_output=text_output)
    finally:
        if not debug:
            try:
                os.unlink(output_path)
            except OSError:
                pass

    if not text_output and result.get("status") == "error":
        raise CommandError(_error_message(result), result)
    return result


def _read_output(output_path: str, *, text_output: bool) -> dict:
    if not os.path.isfile(output_path):
        raise AgentOutputError(f"Java agent did not create output file: {output_path}")
    try:
        raw = Path(output_path).read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentOutputError(f"Could not read Java agent output {output_path}: {exc}") from exc

    if text_output:
        stripped = raw.strip()
        if stripped.startswith("{") and '"status":"error"' in stripped.replace(" ", ""):
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError:
                return {"status": "ok", "text": raw}
            raise CommandError(_error_message(data), data)
        return {"status": "ok", "text": raw}

    if not raw.strip():
        raise AgentOutputError(f"Java agent output file was empty: {output_path}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        preview = raw[:300].replace("\n", " ")
        raise AgentOutputError(f"Java agent output was not valid JSON: {preview!r}") from exc


def _error_message(result: dict) -> str:
    message = str(result.get("message") or "Java agent returned an error")
    error = result.get("error") if isinstance(result.get("error"), dict) else {}
    error_type = str(error.get("type") or "").strip()
    stack = str(error.get("stackTrace") or "")
    stack_lines: list[str] = []
    for line in stack.splitlines()[1:]:
        line = line.strip()
        if line:
            stack_lines.append(line)
        if len(stack_lines) >= 8:
            break
    parts = [message]
    if error_type and error_type not in message:
        parts.append(error_type)
    if stack_lines:
        parts.append(" <- ".join(stack_lines))
    return " | ".join(parts)

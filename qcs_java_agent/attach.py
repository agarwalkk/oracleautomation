"""Build commands for loading the local QCS Java agent via the Java Attach API."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from functools import lru_cache

from .exceptions import AttachError, JavaNotFoundError

_CP_SEP = ";" if os.name == "nt" else ":"


def _probe_java_major_version(java_exe: str) -> int:
    try:
        proc = subprocess.run(
            [java_exe, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError as exc:
        raise JavaNotFoundError(f"Java executable not found: {java_exe!r}") from exc
    except subprocess.TimeoutExpired as exc:
        raise JavaNotFoundError(f"{java_exe!r} -version timed out") from exc

    output = proc.stderr or proc.stdout or ""
    match = re.search(r'"([^"]+)"', output)
    if not match:
        raise JavaNotFoundError(f"Could not parse Java version from: {output}")
    parts = match.group(1).split(".")
    first = int(parts[0])
    return int(parts[1]) if first == 1 and len(parts) > 1 else first


def _find_modern_java(java_exe: str) -> str:
    if java_exe != "java":
        return java_exe
    path_java = shutil.which("java") or "java"
    try:
        if _probe_java_major_version(path_java) >= 9:
            return path_java
    except Exception:
        pass
    java_home = os.environ.get("JAVA_HOME", "")
    if java_home:
        for name in ("java.exe", "java"):
            candidate = os.path.join(java_home, "bin", name)
            if os.path.isfile(candidate):
                try:
                    if _probe_java_major_version(candidate) >= 9:
                        return candidate
                except Exception:
                    pass
    for tool_name in ("jps", "javac"):
        tool_path = shutil.which(tool_name)
        if not tool_path:
            continue
        candidate = os.path.join(os.path.dirname(tool_path), "java.exe" if os.name == "nt" else "java")
        if os.path.isfile(candidate):
            try:
                if _probe_java_major_version(candidate) >= 9:
                    return candidate
            except Exception:
                pass
    return java_exe


@lru_cache(maxsize=8)
def get_java_major_version(java_exe: str = "java") -> int:
    return _probe_java_major_version(shutil.which(java_exe) or java_exe)


def _find_tools_jar(java_exe: str) -> str | None:
    java_home = os.environ.get("JAVA_HOME", "")
    if java_home:
        candidate = os.path.join(java_home, "lib", "tools.jar")
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)

    resolved = shutil.which(java_exe) or java_exe
    if os.path.isfile(resolved):
        jdk_root = os.path.dirname(os.path.dirname(os.path.realpath(resolved)))
        for root in (jdk_root, os.path.dirname(jdk_root)):
            candidate = os.path.join(root, "lib", "tools.jar")
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)
    return None


def format_agent_args(command: dict[str, object]) -> str:
    return ";".join(f"{key.lower()}={value}" for key, value in command.items() if value is not None)


def build_attach_command(
    *,
    pid: int,
    agent_jar: str,
    agent_args: str,
    java_exe: str = "java",
) -> list[str]:
    java_exe = _find_modern_java(java_exe)
    resolved_java = shutil.which(java_exe) or java_exe
    major = get_java_major_version(java_exe)

    if major <= 8:
        tools_jar = _find_tools_jar(java_exe)
        if not tools_jar:
            raise AttachError(
                f"Java {major} detected but tools.jar was not found. Set JAVA_HOME to a JDK."
            )
        return [
            resolved_java,
            "-cp",
            agent_jar + _CP_SEP + tools_jar,
            "com.pyebsdom.agent.attach.AttachLauncher",
            str(pid),
            agent_jar,
            agent_args,
        ]

    return [
        resolved_java,
        "--add-modules",
        "jdk.attach",
        "-cp",
        agent_jar,
        "com.pyebsdom.agent.attach.AttachLauncher",
        str(pid),
        agent_jar,
        agent_args,
    ]

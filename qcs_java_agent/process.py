"""Java process discovery for Oracle Forms JVMs."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

from .exceptions import JavaNotFoundError, ProcessNotFoundError


@dataclass(frozen=True)
class JavaProcess:
    pid: int
    main: str
    jvm_args: str = ""


def _locate_jps() -> str:
    jps = shutil.which("jps")
    if jps:
        return jps

    java_home = os.environ.get("JAVA_HOME", "")
    if java_home:
        candidate = os.path.join(java_home, "bin", "jps")
        for suffix in ("", ".exe"):
            path = candidate + suffix
            if os.path.isfile(path):
                return path

    raise JavaNotFoundError(
        "jps was not found. Install a JDK and ensure JAVA_HOME/bin is on PATH."
    )


def _parse_jps(output: str) -> list[JavaProcess]:
    processes: list[JavaProcess] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        try:
            pid = int(parts[0])
        except (ValueError, IndexError):
            continue
        main = parts[1] if len(parts) >= 2 else ""
        if main in {"Jps", "sun.tools.jps.Jps"}:
            continue
        processes.append(JavaProcess(pid=pid, main=main, jvm_args=parts[2] if len(parts) == 3 else ""))
    return processes


def list_java_processes(timeout_s: int = 15) -> list[JavaProcess]:
    jps = _locate_jps()
    try:
        proc = subprocess.run(
            [jps, "-lv"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        raise JavaNotFoundError(f"Failed to run {jps!r}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise JavaNotFoundError(f"jps -lv timed out after {timeout_s}s") from exc

    processes = _parse_jps(proc.stdout or "")
    if proc.returncode != 0 and not processes:
        stderr = (proc.stderr or "").strip()
        raise JavaNotFoundError(
            f"jps -lv exited with code {proc.returncode}" + (f": {stderr}" if stderr else "")
        )
    return processes


def find_java_process(contains: str) -> JavaProcess:
    needle = contains.lower()
    processes = list_java_processes()
    for process in processes:
        if needle in process.main.lower() or needle in process.jvm_args.lower():
            return process
    seen = ", ".join(f"[{p.pid}] {p.main}" for p in processes) or "(none)"
    raise ProcessNotFoundError(f"No Java process matching {contains!r}. Running: {seen}")

"""Execute a QCS recording job in a subprocess.

Writes the instructions to ``recordings/{run_id}/instructions.txt``, invokes
``python -m qcs record --run-id {run_id} --auto-name`` (which records and then
generates the replay suite), then reads back ``recordings/{run_id}/recording.jsonl``
for upload.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import config

log = logging.getLogger("qcs.agent.executor")


async def execute_job(job: dict) -> dict:
    """Run ``qcs record`` for *job* and return a result dict.

    Result keys: ``exit_code``, ``stdout``, ``stderr``, ``recording_jsonl``.
    """
    run_id: str = job["run_id"]
    instructions_text: str = job["instructions"]
    auto_name: bool = bool(job.get("auto_name", True))

    # Ensure run directory exists and write instructions
    run_dir = Path(config.RECORDINGS_DIR) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    instr_path = run_dir / "instructions.txt"
    instr_path.write_text(instructions_text, encoding="utf-8")

    # Build the command
    python_exe = _find_python()
    cmd = [python_exe, "-m", "qcs", "record", "--run-id", run_id]
    if auto_name:
        cmd.append("--auto-name")
    cmd.append(str(instr_path))

    log.info("Executing: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(config.ROOT_DIR),
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    exit_code = proc.returncode or 0

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")

    # Read recording.jsonl if produced
    recording_jsonl = ""
    jsonl_path = run_dir / "recording.jsonl"
    if jsonl_path.exists():
        recording_jsonl = jsonl_path.read_text(encoding="utf-8")

    log.info(
        "Job run_id=%s finished  exit=%s  recording_lines=%d",
        run_id,
        exit_code,
        recording_jsonl.count("\n"),
    )
    return {
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "recording_jsonl": recording_jsonl,
    }


def _find_python() -> str:
    """Return the Python executable to use.

    Priority:
    1. ``QCS_PYTHON_EXE`` config / env var (explicit path)
    2. ``.venv/Scripts/python.exe`` next to ROOT_DIR (standard venv layout)
    3. The interpreter that is running the agent itself (sys.executable)
    """
    configured = str(config.QCS_PYTHON_EXE)
    if configured and Path(configured).exists():
        return configured

    venv_python = config.ROOT_DIR / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)

    return sys.executable

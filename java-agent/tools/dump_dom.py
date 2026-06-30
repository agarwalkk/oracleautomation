#!/usr/bin/env python3
"""Dump the Oracle Forms DOM using the Java agent ONLY (no qcs stack).

Attaches ebs-dom-agent.jar to a running Oracle Forms JVM and writes the
extraction outputs (scan / raw / layout / tables, plus an optional screenshot)
to a timestamped folder for review.

Thin wrapper around:
    java [attach flags] -cp <jar>[;tools.jar] com.pyebsdom.agent.attach.AttachLauncher \
         <pid> <jar> "command=<cmd>;out=<file>"

Examples
--------
    python dump_dom.py                       # auto-detect Forms JVM, dump all
    python dump_dom.py --pid 13728 --shot    # explicit PID + screenshot
    python dump_dom.py --match oracle.forms  # different jps match token
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path


def detect_major(java: str) -> int:
    out = subprocess.run([java, "-version"], capture_output=True, text=True).stderr
    m = re.search(r'version "(\d+)(?:\.(\d+))?', out)
    if not m:
        return 8
    return int(m.group(2)) if m.group(1) == "1" else int(m.group(1))


def auto_detect_pid(jps: str, match: str) -> int:
    proc = subprocess.run([jps, "-l", "-v"], capture_output=True, text=True)
    rows = [
        ln for ln in proc.stdout.splitlines()
        if match.lower() in ln.lower()
        and "AttachLauncher" not in ln
        and "sun.tools.jps" not in ln
    ]
    pids = [ln.split()[0] for ln in rows if ln.split() and ln.split()[0].isdigit()]
    if len(pids) == 1:
        print(f"[dump-dom] auto PID   : {pids[0]}  ({rows[0]})")
        return int(pids[0])
    if len(pids) > 1:
        print(f"[dump-dom] Multiple JVMs matched {match!r} — re-run with --pid <pid>:")
        for ln in rows:
            print("    " + ln)
        sys.exit(2)
    print(f"[dump-dom] No JVM matched {match!r}. All JVMs:")
    for ln in subprocess.run([jps, "-l", "-v"], capture_output=True, text=True).stdout.splitlines():
        print("    " + ln)
    sys.exit(2)


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="Java-agent-only Oracle Forms DOM dumper")
    ap.add_argument("--pid", type=int, default=0, help="Forms JVM PID (auto-detect if omitted)")
    ap.add_argument("--jar", default=str(here / ".." / "target" / "ebs-dom-agent.jar"))
    ap.add_argument("--java-home", default=os.environ.get("JAVA_HOME", ""))
    ap.add_argument("--out", default="", help="output folder (default: ./dom-dumps/<timestamp>)")
    ap.add_argument("--match", default="forms", help="jps match token for auto-detect")
    ap.add_argument("--shot", action="store_true", help="also capture a screenshot.png")
    args = ap.parse_args()

    jar = str(Path(args.jar).resolve())
    if not Path(jar).is_file():
        sys.exit(f"agent jar not found: {jar}")
    if not args.java_home:
        sys.exit("JAVA_HOME not set; pass --java-home <jdk> (a JDK, not a JRE).")

    jhome = Path(args.java_home)
    java = str(jhome / "bin" / ("java.exe" if os.name == "nt" else "java"))
    jps = str(jhome / "bin" / ("jps.exe" if os.name == "nt" else "jps"))
    tools_jar = jhome / "lib" / "tools.jar"

    major = detect_major(java)
    sep = ";" if os.name == "nt" else ":"
    if major >= 9:
        cp, attach_flags = jar, ["--add-modules", "jdk.attach"]
    else:
        if not tools_jar.is_file():
            sys.exit(f"Java 8 attach needs tools.jar: {tools_jar} (use a JDK, not a JRE).")
        cp, attach_flags = f"{jar}{sep}{tools_jar}", []

    print(f"[dump-dom] agent jar : {jar}")
    print(f"[dump-dom] jdk       : {jhome}  (java {major})")

    pid = args.pid or auto_detect_pid(jps, args.match)

    out_dir = Path(args.out) if args.out else here / "dom-dumps" / time.strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir = out_dir.resolve()
    print(f"[dump-dom] out dir    : {out_dir}\n")

    def run(cmd: str, out_file: Path, extra: str = "") -> None:
        agent_args = f"command={cmd};out={out_file}"
        if extra:
            agent_args += f";{extra}"
        print(f"[dump-dom] -> {cmd}")
        proc = subprocess.run(
            [java, *attach_flags, "-cp", cp,
             "com.pyebsdom.agent.attach.AttachLauncher", str(pid), jar, agent_args],
            capture_output=True, text=True,
        )
        for ln in (proc.stdout + proc.stderr).splitlines():
            print("      " + ln)
        if out_file.exists():
            print(f"      wrote {out_file.name}  ({out_file.stat().st_size/1024:.1f} KB)")
        else:
            print(f"      WARNING: {cmd} produced no output file")

    run("health", out_dir / "health.json")
    run("scan", out_dir / "scan.json")
    run("raw", out_dir / "raw.json")
    run("layout", out_dir / "layout.txt")
    run("tables", out_dir / "tables.json")
    if args.shot:
        run("screenshot", out_dir / "screenshot.result.json",
            extra=f"screenshotout={out_dir / 'screenshot.png'}")

    print(f"\n[dump-dom] DONE. Review/share the folder:\n    {out_dir}")
    print("[dump-dom] Start with layout.txt (human-readable) and scan.json (schema-2.0).")


if __name__ == "__main__":
    main()

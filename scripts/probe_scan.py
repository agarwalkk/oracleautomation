#!/usr/bin/env python3
"""Run ONE raw Java-agent scan (no tab navigation, no focus dependency) and save
the dump for the reflection probe.

This deliberately bypasses the Studio multi-tab capture (qcs_studio.service.
run_scan), which is the thing that clicks across tabs and steals focus. The raw
agent `scan` command just walks the current live component tree — it navigates
nothing — and with the per-FScrollBox probe wiring it samples every tab region
(Q/O, Line, Advanced, Holds, ...) in this single pass.

Run from the repo root (so `config` and `qcs_java_agent` import):

    python scripts/probe_scan.py
    python scripts/probe_scan.py --out probe_scan.json
    python scripts/probe_scan.py --pid 12345          # if auto-attach misses
    python scripts/probe_scan.py --contains "frmweb"  # match a different process

Then:

    python scripts/probe_report.py probe_scan.json
"""
import argparse
import json
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description="One raw Java-agent scan for the probe.")
    ap.add_argument("--out", default="probe_scan.json", help="output dump path")
    ap.add_argument("--pid", type=int, default=None, help="Forms JVM pid (optional)")
    ap.add_argument("--contains", default=None,
                    help="process-name substring to match (optional)")
    args = ap.parse_args()

    try:
        from qcs_java_agent.driver import JavaAgentDriver
    except Exception as e:  # noqa: BLE001
        print("Could not import qcs_java_agent — run this from the repo root.",
              file=sys.stderr)
        print("  ", e, file=sys.stderr)
        return 2

    try:
        driver = JavaAgentDriver.attach(pid=args.pid, contains=args.contains)
    except Exception as e:  # noqa: BLE001
        print("Attach failed. Is the Oracle Forms applet running?", file=sys.stderr)
        print("  Try --pid <pid> or --contains <name>.  Detail:", e, file=sys.stderr)
        return 1

    print(f"Attached to pid {driver.pid}. Running one raw scan (no tab navigation)…")
    dump = driver.scan(probe=True)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(dump, fh)

    # quick confirmation of how many regions got probed
    probed = 0

    def _count(n):
        nonlocal probed
        if "_probe" in (n.get("attributes") or {}):
            probed += 1
        for c in n.get("children") or []:
            _count(c)

    for w in dump.get("windows") or []:
        _count(w)

    print(f"Saved {args.out}.  Probed {probed} field(s) (one per FScrollBox).")
    if probed == 0:
        print("No _probe attributes — did you rebuild the agent with the "
              "ReflectionProbe wiring (DomScanner.PROBE.patch.java)?")
    else:
        print("Next:  python scripts/probe_report.py", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run ONE raw Java-agent scan (no tab navigation, no focus dependency) and save
the dump for the reflection probe.

This bypasses the Studio multi-tab capture (which clicks across tabs and steals
focus). The raw agent `scan` command navigates nothing.

Default (no --target): probes the first field of every FScrollBox — one sample
per tab region.

Targeted (--target): probes only elements matching a selector, so you can mine
ANY element's full method/field surface — a specific button, all checkboxes, one
LOV, etc. Selector syntax:

    --target "label:Held By"     any item whose accessibleName/name contains it
    --target "class:VButton"     every button (by simpleClassName)
    --target "class:LWCheckbox"  every checkbox
    --target "role:LOV"          every LOV the agent typed
    --target "role:Button"       every Button
    --target "handler:1204"      the item with that Forms handler id
    --target "Held By"           (no mode: → treated as a label substring)

Run from the repo root:

    python scripts/probe_scan.py
    python scripts/probe_scan.py --target "class:VButton" --out buttons.json
    python scripts/probe_scan.py --pid 12345 --target "role:Checkbox"

Then:

    python scripts/probe_report.py <out.json> --full
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
    ap.add_argument("--target", default=None,
                    help="probe only elements matching this selector "
                         "(e.g. 'class:VButton', 'role:LOV', 'label:Held By'); "
                         "default samples first field per FScrollBox")
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

    where = f" targeting '{args.target}'" if args.target else " (first field per FScrollBox)"
    print(f"Attached to pid {driver.pid}. Running one raw scan{where}…")
    # driver.scan must accept probe=bool and target=str|None (see driver patch below).
    dump = driver.scan(probe=True, target=args.target)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(dump, fh)

    probed = []

    def _count(n):
        if "_probe" in (n.get("attributes") or {}):
            probed.append(n)
        for c in n.get("children") or []:
            _count(c)

    for w in dump.get("windows") or []:
        _count(w)

    print(f"Saved {args.out}.  Probed {len(probed)} element(s).")
    for n in probed[:12]:
        label = (n.get("canonicalLabel") or n.get("accessibleName")
                 or n.get("name") or f"e{n.get('id')}")
        print(f"   - {label}  [{n.get('simpleClassName')} / {n.get('semanticType')}]")
    if not probed:
        print("No matches. Check the agent has the targeted-probe wiring, and that "
              "your selector matches something on the current screen.")
    else:
        print("Next:  python scripts/probe_report.py", args.out, "--full")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Enumerate the Forms item types on the current screen(s) so we know exactly
which handler classes to cover for handler-based execution.

Groups live nodes by `formsType` (the handler class, e.g. "TextFieldItem",
"CheckboxItem", "PoplistItem", "ButtonItem") with a count and a sample locator,
and flags which already have an actuator vs. which need a probe.

Requires the agent rebuilt with the formsType extraction. (Falls back to
grouping by widget class when formsType is absent.)

Run from the repo root:
    python scripts/scan_item_types.py --contains frmweb
    python scripts/scan_item_types.py --pid 12345
"""
import argparse
import sys
from collections import defaultdict


# Handler classes that already have a handler-based actuator.
COVERED = {
    "TextFieldItem": "setText (FieldActuator)",
    "CheckboxItem": "setCheckbox (FieldActuator)",
    # DTree rows are driven by treeAction (TreeItemActuator), not a handler item.
}


def _scan(driver):
    try:
        return driver.scan(probe=False, target=None)
    except TypeError:
        return driver.scan()


def _walk(dump):
    out = []

    def go(n):
        out.append(n)
        for c in n.get("children") or []:
            go(c)

    for w in dump.get("windows") or []:
        go(w)
    return out


def _label(n):
    return (n.get("canonicalLabel") or n.get("accessibleName")
            or n.get("displayName") or n.get("name") or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, default=None)
    ap.add_argument("--contains", default=None, help="process-name substring")
    args = ap.parse_args()

    try:
        from qcs_java_agent.driver import JavaAgentDriver
    except Exception as e:  # noqa: BLE001
        print("Could not import qcs_java_agent — run from the repo root.", file=sys.stderr)
        print(" ", e, file=sys.stderr)
        return 2

    driver = JavaAgentDriver.attach(pid=args.pid, contains=args.contains)
    print("Attached to pid", driver.pid)

    nodes = _walk(_scan(driver))
    groups = defaultdict(list)
    for n in nodes:
        ft = n.get("formsType")
        if ft:
            groups[ft].append(n)
        elif n.get("handlerId"):
            # has a handler but formsType missing (older agent) — group by widget
            groups[f"(rebuild agent) {n.get('simpleClassName')}"].append(n)
        # nodes with no handler at all (containers/chrome) are skipped

    if not groups:
        print("\nNo Forms-item handlers found. Is a form open, and is the agent"
              " rebuilt with FormsHandler? Try navigating to a data-entry screen.")
        return 0

    rows = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    print(f"\n{'formsType (handler)':24} {'count':>5}  {'role':12} {'widgetClass':16} sample")
    print("-" * 92)
    todo = []
    for ft, ns in rows:
        s = next((x for x in ns if _label(x)), ns[0])
        widget = str(s.get("simpleClassName") or "")
        mark = "OK " if ft in COVERED else "›› "
        print(f"{mark}{ft:21} {len(ns):5d}  {str(s.get('semanticType') or ''):12} "
              f"{widget:16} {_label(s)!r}")
        if ft not in COVERED:
            todo.append((ft, widget))

    if todo:
        print("\nNeed a probe (one per type) — focus an instance, then run:")
        for ft, widget in todo:
            sel = f'class:{widget}' if widget else 'label:<prompt>'
            print(f"   python scripts/probe_scan.py --target \"{sel}\" --out {ft.lower()}.json"
                  f"  &&  python scripts/probe_report.py {ft.lower()}.json --full   # {ft}")
        print("\nSend me each TARGET block + its 'via mHandler -> oracle.forms.handler.<X>' block.")
    else:
        print("\nAll handler types on this screen already have an actuator. 🎉")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

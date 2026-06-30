#!/usr/bin/env python3
"""Inspect an open LOV (or any) popup window so we know what to probe.

With the LOV open (e.g. the "Document Types" window), this finds the matching
window and prints its widget tree — each node's widget class, role, formsType,
label, child count and size — and flags the likely LIST and the OK/Find/Cancel
buttons, with a ready-to-run probe command for the list.

Run from the repo root, with the LOV popup OPEN on screen:
    python scripts/inspect_lov.py --contains frmweb
    python scripts/inspect_lov.py --pid 12345 --window "Document Types"
"""
import argparse
import sys


def _scan(driver):
    try:
        return driver.scan(probe=False, target=None)
    except TypeError:
        return driver.scan()


def _label(n):
    return str(n.get("canonicalLabel") or n.get("accessibleName")
               or n.get("displayName") or n.get("name") or n.get("title")
               or n.get("text") or "").strip()


def _is_list_like(n):
    cls = str(n.get("simpleClassName") or "").lower()
    role = str(n.get("semanticType") or "").lower()
    return (any(k in cls for k in ("list", "table", "tree", "grid", "view"))
            or role in ("list", "table", "tree", "grid"))


def _is_button(n):
    cls = str(n.get("simpleClassName") or "").lower()
    role = str(n.get("semanticType") or "").lower()
    return "button" in cls or role == "button"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, default=None)
    ap.add_argument("--contains", default=None, help="process-name substring")
    ap.add_argument("--window", default="Document Types",
                    help="title/label substring of the popup to inspect")
    args = ap.parse_args()

    try:
        from qcs_java_agent.driver import JavaAgentDriver
    except Exception as e:  # noqa: BLE001
        print("Could not import qcs_java_agent — run from the repo root.", file=sys.stderr)
        print(" ", e, file=sys.stderr)
        return 2

    driver = JavaAgentDriver.attach(pid=args.pid, contains=args.contains)
    print("Attached to pid", driver.pid)

    dump = _scan(driver)
    q = args.window.strip().lower()

    # Find the node whose label matches the window; inspect its whole subtree.
    roots = []

    def find(n):
        if q in _label(n).lower():
            roots.append(n)
        for c in n.get("children") or []:
            find(c)

    for w in dump.get("windows") or []:
        find(w)

    if not roots:
        print(f"\nNo node matching {args.window!r}. Top-level windows seen:")
        for w in dump.get("windows") or []:
            print(f"   {_label(w)!r:40} {w.get('simpleClassName')}")
        print("\nIs the LOV popup actually open and on screen? Re-run with the"
              " exact window title via --window.")
        return 0

    root = roots[0]
    print(f"\n=== '{_label(root)}'  ({root.get('simpleClassName')}) ===")
    lists, buttons = [], []

    def dump_tree(n, depth):
        b = n.get("screenBounds") or n.get("bounds") or {}
        sz = f"{b.get('width', 0)}x{b.get('height', 0)}"
        kids = len(n.get("children") or [])
        ft = n.get("formsType") or ""
        flag = "LIST" if _is_list_like(n) else ("BTN " if _is_button(n) else "    ")
        print(f"  {'  ' * depth}{flag} {str(n.get('simpleClassName') or ''):18} "
              f"{str(n.get('semanticType') or ''):10} {ft:14} {sz:9} "
              f"kids={kids:<3} {_label(n)!r}")
        if _is_list_like(n):
            lists.append(n)
        if _is_button(n):
            buttons.append(n)
        for c in n.get("children") or []:
            dump_tree(c, depth + 1)

    dump_tree(root, 0)

    print("\n--- likely LIST widget(s) to probe ---")
    if lists:
        for n in lists:
            cls = n.get("simpleClassName")
            print(f"   {cls}  (kids={len(n.get('children') or [])})")
            print(f"     python scripts/probe_scan.py --target \"class:{cls}\" --out lovlist.json"
                  f"  &&  python scripts/probe_report.py lovlist.json --full")
    else:
        print("   none auto-detected — eyeball the tree above for the rows container.")
    print("\n--- buttons in the popup ---")
    for n in buttons:
        print(f"   {_label(n)!r:14} {n.get('simpleClassName')}")
    print("\nSend me: the list's TARGET + via-mHandler block, and the field-side"
          " probe ('Type' field) confirming sendLOVButtonPressedMessage().")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

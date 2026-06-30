#!/usr/bin/env python3
"""Inspect the menu bar + toolbar so we can choose which items snapshot reports.

No reflection probe / rebuild needed — both are already extracted:
  - menus: every LWMenu node carries an `accessibleMenuItems` attribute
           ("label<TAB>type<TAB>state || ..."), the same data the Tools group uses;
  - toolbar buttons: oracle.forms.ui.VButton -> IconicButtonItem handler items
           (formsType=IconicButtonItem, formsActions=pressButton).

The menu/toolbar live OUTSIDE the form frame, which is why the scoped snapshot
doesn't show them today. This dumps them from a FULL scan with their label
sources so we can pick which to surface.

Run from the repo root:
    python scripts/inspect_chrome.py --contains frmweb
"""
import argparse
import sys


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
    return str(n.get("accessibleName") or n.get("tooltip") or n.get("canonicalLabel")
               or n.get("displayName") or n.get("text") or n.get("name") or "").strip()


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

    # ── menus ─────────────────────────────────────────────────────────────
    print("\n===================== MENUS (LWMenu) =====================")
    menus = [n for n in nodes
             if str(n.get("simpleClassName") or "") == "LWMenu"
             or str(n.get("semanticType") or "") == "Menu"]
    if not menus:
        print("  (no LWMenu nodes found)")
    for m in menus:
        nm = str(m.get("accessibleName") or m.get("name") or "")
        attrs = m.get("attributes") or {}
        ami = attrs.get("accessibleMenuItems") or attrs.get("menuItems") or ""
        print(f"\n  MENU: {nm!r}  ({m.get('simpleClassName')})")
        if ami:
            for entry in str(ami).split(" || "):
                parts = entry.split("\t")
                label = parts[0].strip() if parts else entry
                kind = parts[1] if len(parts) > 1 else ""
                state = parts[2] if len(parts) > 2 else ""
                print(f"      - {label!r:40} {kind} {state}")
        else:
            print("      (no accessibleMenuItems captured — may need the menu opened once)")

    # ── toolbar / iconic buttons ──────────────────────────────────────────
    print("\n================= TOOLBAR / ICONIC BUTTONS =================")
    btns = [n for n in nodes
            if str(n.get("formsType") or "") in ("IconicButtonItem", "ButtonItem")
            or str(n.get("simpleClassName") or "") in ("ToolBarButton", "VButton")]
    if not btns:
        print("  (none found)")
    else:
        print(f"  {'label':30} {'formsType':17} {'action':12} {'hId':6} class")
        print("  " + "-" * 78)
        for b in btns:
            print(f"  {(_label(b) or '<no label>'):30.30} "
                  f"{str(b.get('formsType') or ''):17} "
                  f"{str(b.get('formsActions') or ''):12} "
                  f"{str(b.get('handlerId') or ''):6} "
                  f"{b.get('simpleClassName')}")

    print("\nTell me which menus (and which of their items) + which toolbar buttons"
          " to surface (by label), and I'll wire snapshot to read them from the"
          " full scan as named groups.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

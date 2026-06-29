#!/usr/bin/env python3
"""Smoke-test the handler/model executors — treeAction, setText, setCheckbox —
against a LIVE Oracle Forms screen.

SAFETY
  These commands write REAL data and fire REAL triggers. Use a test instance or
  a throwaway record, stay on the tab that holds the targets, and DO NOT save /
  commit while testing — re-query or discard to revert. The script writes only
  after you press Enter at the prompt.

PREREQUISITES
  1. Agent jar rebuilt with TreeItemActuator, FieldActuator, the ActionExecutor
     changes, and the dispatcher branches. (No rebuild needed for THIS script
     revision — it only changes how fields are located.)
  2. Open the Forms applet and navigate to the screen/tab with the targets.

LOCATORS
  The tree resolves from its treePath. Text fields / checkboxes are resolved to
  their exact DOM path via a scan (their on-screen labels are derived prompts,
  not the widget's accessible name, so accessibleName won't resolve them).
  Override with --text-path / --checkbox-path if you already have a path.

RUN (from the repo root, like probe_scan.py)
    python scripts/test_actuators.py --contains frmweb --skip-writes
    python scripts/test_actuators.py --pid 12345 \
        --tree-path "Orders Tree/Personal Folders" \
        --text-field "Customer PO" --text-value "TEST-123" --checkbox "On Hold"
"""
import argparse
import json
import sys


def agent(driver, command, **params):
    """Send ONE agent command and return the parsed JSON response (dict)."""
    payload = {k.lower(): ("" if v is None else str(v)) for k, v in params.items()}
    raw = driver._run({"command": command, **payload})   # your driver's send call
    return raw if isinstance(raw, dict) else json.loads(raw)


def run(driver, title, command, **params):
    print(f"\n=== {title} ===")
    try:
        res = agent(driver, command, **params)
    except Exception as e:  # noqa: BLE001
        print(f"  CALL FAILED: {e.__class__.__name__}: {e}")
        return None
    print("  status :", res.get("status"))
    print("  via    :", res.get("via"))
    if res.get("matchedLabel") is not None:
        print("  matched:", res.get("matchedLabel"))
    if res.get("before") is not None or res.get("after") is not None:
        print("  value  :", repr(res.get("before")), "->", repr(res.get("after")))
    print("  detail :", res.get("detail") or res.get("error") or res.get("message"))
    if res.get("status") not in ("ok", None):
        print("  RAW    :", json.dumps(res)[:600])
    return res


# ── locator resolution via scan ──────────────────────────────────────────────

def _scan(driver):
    try:
        return driver.scan(probe=False, target=None)
    except TypeError:
        return driver.scan()


def _walk_nodes(dump):
    out = []

    def go(n):
        out.append(n)
        for c in n.get("children") or []:
            go(c)

    for w in (dump.get("windows") or []):
        go(w)
    return out


def _node_label(n):
    return (n.get("canonicalLabel") or n.get("accessibleName")
            or n.get("displayName") or n.get("name") or "")


def field_locator(nodes, label, kind, explicit=None):
    """Return {'locatorPath': <path>} for the best on-screen node matching
    `label`. kind = 'text' | 'checkbox' disambiguates the prompt from the input.
    """
    if explicit:
        return {"locatorPath": explicit}

    t = label.strip().lower()
    exact = [n for n in nodes if _node_label(n).strip().lower() == t]
    cand = exact or [n for n in nodes if t in _node_label(n).strip().lower()]
    if not cand:
        raise RuntimeError(f"no node matching label {label!r} found in scan")

    def score(n):
        cls = (n.get("simpleClassName") or "").lower()
        typ = (n.get("type") or n.get("className") or "").lower()
        sem = (n.get("semanticType") or n.get("containerRole") or "").lower()
        s = 0
        if n.get("showing"):
            s += 1
        if n.get("visible"):
            s += 1
        if kind == "text":
            if n.get("editable"):
                s += 4
            if "textfield" in cls or "textfield" in typ or "text" in sem:
                s += 3
        else:  # checkbox
            if "checkbox" in cls or "checkbox" in typ or "checkbox" in sem:
                s += 4
        return s

    best = max(cand, key=score)
    path = best.get("path")
    if not path:
        raise RuntimeError(f"matched node for {label!r} has no path")
    print(f"  resolved {label!r} -> {best.get('simpleClassName')} @ {path}")
    return {"locatorPath": path}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pid", type=int, default=None)
    ap.add_argument("--contains", default=None, help="process-name substring")
    ap.add_argument("--tree-path", default="Orders Tree/Personal Folders")
    ap.add_argument("--text-field", default="Customer PO",
                    help="on-screen label of a SCRATCH text field")
    ap.add_argument("--text-value", default="TEST-123")
    ap.add_argument("--text-path", default=None,
                    help="explicit DOM path for the text field (skips scan lookup)")
    ap.add_argument("--checkbox", default="On Hold",
                    help="on-screen label of a checkbox you can toggle")
    ap.add_argument("--checkbox-path", default=None,
                    help="explicit DOM path for the checkbox (skips scan lookup)")
    ap.add_argument("--skip-writes", action="store_true",
                    help="run only the non-destructive tree tests")
    args = ap.parse_args()

    try:
        from qcs_java_agent.driver import JavaAgentDriver
    except Exception as e:  # noqa: BLE001
        print("Could not import qcs_java_agent — run from the repo root.", file=sys.stderr)
        print(" ", e, file=sys.stderr)
        return 2

    driver = JavaAgentDriver.attach(pid=args.pid, contains=args.contains)
    print("Attached to pid", driver.pid)

    # ── 1) NON-DESTRUCTIVE: tree expand/collapse/select (resolves by treePath) ─
    run(driver, "treeAction expand", "treeaction", op="expand", locatorTreePath=args.tree_path)
    run(driver, "treeAction collapse", "treeaction", op="collapse", locatorTreePath=args.tree_path)
    run(driver, "treeAction select", "treeaction", op="select", locatorTreePath=args.tree_path)

    if args.skip_writes:
        print("\n--skip-writes set; stopping before data writes.")
        return 0

    # ── 2) DATA WRITES: only on a throwaway record; do NOT commit ─────────────
    try:
        input("\n>> Next steps WRITE to fields. Ctrl-C to stop, Enter to proceed... ")
    except KeyboardInterrupt:
        print("\nStopped before writes.")
        return 0

    # Resolve fields to exact DOM paths from one scan.
    nodes = _walk_nodes(_scan(driver))
    try:
        text_loc = field_locator(nodes, args.text_field, "text", args.text_path)
        run(driver, "setText", "settext", text=args.text_value, **text_loc)
    except Exception as e:  # noqa: BLE001
        print("  setText LOCATE FAILED:", e)

    try:
        cb_loc = field_locator(nodes, args.checkbox, "checkbox", args.checkbox_path)
        run(driver, "setCheckbox true", "setcheckbox", checked="true", **cb_loc)
        run(driver, "setCheckbox false", "setcheckbox", checked="false", **cb_loc)
    except Exception as e:  # noqa: BLE001
        print("  setCheckbox LOCATE FAILED:", e)

    print("\nDone. Verify on-screen, then DISCARD/re-query — do not save.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

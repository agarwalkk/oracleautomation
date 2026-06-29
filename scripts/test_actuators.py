#!/usr/bin/env python3
"""Smoke-test the handler/model executors — treeAction, setText, setCheckbox —
against a LIVE Oracle Forms screen.

SAFETY
  These commands write REAL data and fire REAL triggers. Use a test instance or
  a throwaway record, stay on the tab that holds the targets, and DO NOT save /
  commit while testing — re-query or discard to revert. The script writes only
  after you press Enter at the prompt.

PREREQUISITES
  1. Rebuild the agent jar with TreeItemActuator, FieldActuator, the
     ActionExecutor changes, AND the dispatcher branches
     (case "treeaction" / "setcheckbox"). e.g.  mvn -q -DskipTests package
  2. Make sure attach injects that freshly built jar.
  3. Open the Forms applet and navigate to the screen/tab with the targets.

RUN (from the repo root, like probe_scan.py)
    python scripts/test_actuators.py --contains frmweb
    python scripts/test_actuators.py --pid 12345 \
        --tree-name "Orders Tree" --tree-path "Orders Tree/Personal Folders" \
        --text-field "Customer PO" --text-value "TEST-123" --checkbox "On Hold"
"""
import argparse
import json
import sys


def agent(driver, command, **params):
    """Send ONE agent command and return the parsed JSON response (dict).

    >>> EDIT THE ONE LINE BELOW to match how YOUR JavaAgentDriver sends a
        command. Mirror what scan() does internally — open
        qcs_java_agent/driver.py and copy the call scan() uses to talk to the
        agent (it returns the JSON the agent's command handler produced).

    Discover the method name with:
        python -c "from qcs_java_agent.driver import JavaAgentDriver as D; \
print([m for m in dir(D) if not m.startswith('_')])"

    Param keys are lowercased here to match AgentCommandParser; values stringified.
    """
    payload = {k.lower(): ("" if v is None else str(v)) for k, v in params.items()}

    # ▼▼▼ EDIT THIS to your driver's real send call ▼▼▼
    raw = driver._run({"command": command, **payload})
    # Common alternatives (uncomment the one that matches driver.py):
    #   raw = driver.send_command(command, **payload)
    #   raw = driver.run_command(command, payload)
    #   raw = driver.command({"command": command, **payload})
    # ▲▲▲ EDIT THIS ▲▲▲

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


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pid", type=int, default=None)
    ap.add_argument("--contains", default=None, help="process-name substring")
    # Tune these to YOUR screen:
    ap.add_argument("--tree-name", default="Orders Tree",
                    help="accessibleName of the DTree (resolves the tree component)")
    ap.add_argument("--tree-path", default="Orders Tree/Personal Folders")
    ap.add_argument("--text-field", default="Customer PO",
                    help="accessibleName/prompt of a SCRATCH text field")
    ap.add_argument("--text-value", default="TEST-123")
    ap.add_argument("--checkbox", default="On Hold",
                    help="accessibleName of a checkbox you can toggle")
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

    # ── 1) NON-DESTRUCTIVE: tree expand/collapse/select (UI only) ───────────
    run(driver, "treeAction expand", "treeaction",
        op="expand", locatorTreePath=args.tree_path, locatorAccessibleName=args.tree_name)
    run(driver, "treeAction collapse", "treeaction",
        op="collapse", locatorTreePath=args.tree_path, locatorAccessibleName=args.tree_name)
    run(driver, "treeAction select", "treeaction",
        op="select", locatorTreePath=args.tree_path, locatorAccessibleName=args.tree_name)

    if args.skip_writes:
        print("\n--skip-writes set; stopping before data writes.")
        return 0

    # ── 2) DATA WRITES: only on a throwaway record; do NOT commit ───────────
    try:
        input("\n>> Next steps WRITE to fields. Ctrl-C to stop, Enter to proceed... ")
    except KeyboardInterrupt:
        print("\nStopped before writes.")
        return 0

    run(driver, "setText", "settext",
        text=args.text_value, locatorAccessibleName=args.text_field)
    run(driver, "setCheckbox true", "setcheckbox",
        checked="true", locatorAccessibleName=args.checkbox)
    run(driver, "setCheckbox false", "setcheckbox",
        checked="false", locatorAccessibleName=args.checkbox)

    print("\nDone. Verify on-screen, then DISCARD/re-query — do not save.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

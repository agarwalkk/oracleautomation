#!/usr/bin/env python3
"""Smoke-test EVERY actuator in ActionExecutor against a LIVE Oracle Forms screen.

Actuators exercised (CommandRouter command -> ActionExecutor.execute*):

  Non-destructive (read / navigate — safe to run any time):
    focus        highlight      elementat      screenshot
    expandtree   collapsetree   activatetab

  Destructive (WRITE data / fire triggers — gated behind an Enter prompt):
    settext      clear          setcheck       selectoption
    presskey     click          doubleclick

SAFETY
  The destructive group writes REAL data and fires REAL triggers. Use a test
  instance or a throwaway record, stay on the tab that holds the targets, and DO
  NOT save/commit while testing — re-query or discard to revert. Nothing is
  written until you press Enter at the prompt. Each destructive actuator only
  runs when you supply its target (e.g. --text-field, --button); otherwise it is
  skipped and reported as SKIP.

PREREQUISITES
  1. Agent jar rebuilt with the current ActionExecutor (focus/click/doubleclick/
     activatetab/settext/clear/selectoption/setcheck/expandtree/collapsetree/
     presskey/screenshot/highlight/elementat) and the CommandRouter branches.
  2. Open the Forms applet and navigate to the screen/tab holding the targets.

RUN (from the repo root, like probe_scan.py)
    python scripts/test_actuators.py --contains frmweb                 # non-destructive only
    python scripts/test_actuators.py --pid 12345 \
        --tree-path "Orders Tree/Personal Folders" \
        --tab-title "Line Information" \
        --text-field "Customer PO" --text-value "TEST-123" \
        --checkbox "On Hold" --combo "Order Type" --combo-value "Standard" \
        --button "Find"
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

# actuator -> "ok" | "error" | "skip(reason)" | "fail(reason)"
RESULTS: dict[str, str] = {}


# ── agent call + pretty printer ───────────────────────────────────────────────

def agent(driver, command, **params):
    """Send ONE agent command and return the parsed JSON response (dict)."""
    payload = {k.lower(): ("" if v is None else str(v)) for k, v in params.items()}
    raw = driver._run({"command": command, **payload})
    return raw if isinstance(raw, dict) else json.loads(raw)


def _component_name(res):
    comp = res.get("component")
    if isinstance(comp, dict):
        return (comp.get("accessibleName") or comp.get("name")
                or comp.get("simpleName") or comp.get("className"))
    return None


def run(actuator, driver, title, command, **params):
    """Invoke one actuator, print the outcome, and record it for the summary."""
    print(f"\n=== {title}  [{command}] ===")
    try:
        res = agent(driver, command, **params)
    except Exception as e:  # noqa: BLE001
        print(f"  CALL FAILED: {e.__class__.__name__}: {e}")
        RESULTS[actuator] = f"fail({e.__class__.__name__})"
        return None

    status = res.get("status")
    print("  status   :", status)
    if res.get("technique"):
        print("  technique:", res.get("technique"))
    name = _component_name(res)
    if name:
        print("  component:", name)
    # command-specific extras
    for k in ("key", "screenshotOut", "captureMode", "width", "height", "x", "y", "path"):
        if res.get(k) is not None:
            print(f"  {k:9s}:", res.get(k))
    if status == "error":
        print("  detail   :", res.get("message") or res.get("detail"))
        print("  RAW      :", json.dumps(res)[:500])
        RESULTS[actuator] = "error"
    else:
        RESULTS[actuator] = "ok"
    return res


def skip(actuator, why):
    print(f"\n=== SKIP {actuator}: {why} ===")
    RESULTS[actuator] = f"skip({why})"


# ── locator resolution via one scan ───────────────────────────────────────────

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
            or n.get("name") or "")


def field_locator(nodes, label, kind, explicit=None):
    """Return {'locatorPath': <path>} for the best on-screen node matching label.

    kind disambiguates the prompt from the input widget:
        'text' | 'checkbox' | 'combo' | 'button'
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
        typ = (n.get("className") or "").lower()
        sem = (n.get("semanticType") or n.get("containerRole") or "").lower()
        s = 1 if n.get("showing") else 0
        s += 1 if n.get("visible") else 0
        if kind == "text":
            s += 4 if n.get("editable") else 0
            s += 3 if ("textfield" in cls or "textfield" in typ or "field" in sem) else 0
        elif kind == "checkbox":
            s += 4 if ("checkbox" in cls or "checkbox" in typ or "checkbox" in sem) else 0
        elif kind == "combo":
            s += 4 if ("poplist" in cls or "combo" in cls or "combobox" in sem or "list" in sem) else 0
        elif kind == "button":
            s += 4 if ("button" in cls or "button" in sem) else 0
        return s

    best = max(cand, key=score)
    path = best.get("path")
    if not path:
        raise RuntimeError(f"matched node for {label!r} has no path")
    print(f"  resolved {label!r} -> {best.get('simpleClassName')} @ ...{str(path)[-48:]}")
    return {"locatorPath": path, "_node": best}


def _find_tab_container(nodes):
    """Path of a FormsTabPanel / TabBar to drive activatetab."""
    for n in nodes:
        if (n.get("simpleClassName") or "") in ("FormsTabPanel", "TabBar") and n.get("path"):
            return n["path"]
    return None


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pid", type=int, default=None)
    ap.add_argument("--contains", default=None, help="process-name substring")
    # tree (expandtree / collapsetree)
    ap.add_argument("--tree-path", default="Orders Tree/Personal Folders")
    # tab (activatetab)
    ap.add_argument("--tab-title", default=None, help="title of a tab to activate")
    ap.add_argument("--tab-index", type=int, default=None)
    # text (focus/highlight/elementat/screenshot/settext/clear/presskey)
    ap.add_argument("--text-field", default="Customer PO", help="label of a SCRATCH text field")
    ap.add_argument("--text-value", default="TEST-123")
    ap.add_argument("--text-path", default=None, help="explicit DOM path for the text field")
    # checkbox (setcheck)
    ap.add_argument("--checkbox", default="On Hold", help="label of a togglable checkbox")
    ap.add_argument("--checkbox-path", default=None)
    # combo (selectoption)
    ap.add_argument("--combo", default=None, help="label of a combo/poplist")
    ap.add_argument("--combo-value", default=None, help="option value to select")
    ap.add_argument("--combo-path", default=None)
    # button (click / doubleclick)
    ap.add_argument("--button", default=None, help="label of a SAFE button to click")
    ap.add_argument("--button-path", default=None)
    # presskey
    ap.add_argument("--key", default="TAB", help="named key for presskey (default TAB — benign)")
    # screenshot
    ap.add_argument("--screenshot-out", default=str(Path(tempfile.gettempdir()) / "actuator_shot.png"))
    ap.add_argument("--skip-writes", action="store_true",
                    help="run only the non-destructive actuators")
    args = ap.parse_args()

    try:
        from qcs_java_agent.driver import JavaAgentDriver
    except Exception as e:  # noqa: BLE001
        print("Could not import qcs_java_agent — run from the repo root.", file=sys.stderr)
        print(" ", e, file=sys.stderr)
        return 2

    driver = JavaAgentDriver.attach(pid=args.pid, contains=args.contains)
    print("Attached to pid", driver.pid)

    # One scan up front to resolve the locators + coordinates we need.
    nodes = _walk_nodes(_scan(driver))

    def locate(label, kind, explicit):
        try:
            return field_locator(nodes, label, kind, explicit)
        except Exception as e:  # noqa: BLE001
            print(f"  locate {label!r} ({kind}) failed: {e}")
            return None

    text_loc = locate(args.text_field, "text", args.text_path) if args.text_field else None

    # ── 1) NON-DESTRUCTIVE actuators ─────────────────────────────────────────
    if text_loc:
        path = {"locatorPath": text_loc["locatorPath"]}
        run("focus", driver, "focus a field", "focus", **path)
        run("highlight", driver, "highlight a field", "highlight", **path)
        node = text_loc.get("_node") or {}
        sb = node.get("screenBounds") or {}
        if sb.get("x") not in (None, -1):
            cx = int(sb["x"]) + int(sb.get("width", 0)) // 2
            cy = int(sb["y"]) + int(sb.get("height", 0)) // 2
            run("elementat", driver, "elementat at field centre", "elementat", x=cx, y=cy)
        else:
            skip("elementat", "field has no screen bounds")
    else:
        for a in ("focus", "highlight", "elementat"):
            skip(a, "no text field resolved")

    run("screenshot", driver, "full-screen screenshot", "screenshot",
        screenshotOut=args.screenshot_out)

    run("expandtree", driver, "tree expand", "expandtree", locatorTreePath=args.tree_path)
    run("collapsetree", driver, "tree collapse", "collapsetree", locatorTreePath=args.tree_path)

    tab_container = _find_tab_container(nodes)
    if tab_container and (args.tab_title or args.tab_index is not None):
        params = {"locatorPath": tab_container}
        if args.tab_index is not None:
            params["tab_index"] = args.tab_index
        else:
            params["tab_title"] = args.tab_title
        run("activatetab", driver, "activate tab", "activatetab", **params)
    else:
        skip("activatetab", "no tab container or --tab-title/--tab-index")

    if args.skip_writes:
        print("\n--skip-writes set; stopping before destructive actuators.")
        return _summary()

    # ── 2) DESTRUCTIVE actuators (gated) ─────────────────────────────────────
    try:
        input("\n>> Next actuators WRITE data / fire triggers. "
              "Ctrl-C to stop, Enter to proceed... ")
    except KeyboardInterrupt:
        print("\nStopped before writes.")
        return _summary()

    if text_loc:
        path = {"locatorPath": text_loc["locatorPath"]}
        run("settext", driver, "setText", "settext", text=args.text_value, **path)
        run("clear", driver, "clear", "clear", **path)
    else:
        skip("settext", "no text field resolved")
        skip("clear", "no text field resolved")

    cb_loc = locate(args.checkbox, "checkbox", args.checkbox_path) if args.checkbox else None
    if cb_loc:
        p = {"locatorPath": cb_loc["locatorPath"]}
        run("setcheck", driver, "setCheck true", "setcheck", value="true", **p)
        run("setcheck", driver, "setCheck false", "setcheck", value="false", **p)
    else:
        skip("setcheck", "no checkbox resolved")

    if args.combo and args.combo_value:
        combo_loc = locate(args.combo, "combo", args.combo_path)
        if combo_loc:
            run("selectoption", driver, "selectOption", "selectoption",
                value=args.combo_value, locatorPath=combo_loc["locatorPath"])
        else:
            skip("selectoption", "combo not resolved")
    else:
        skip("selectoption", "need --combo and --combo-value")

    # presskey — benign by default (TAB); warn on a save-like key.
    if args.key:
        if args.key.upper() in ("CTRL+S", "F10", "F11"):
            print(f"  WARNING: --key {args.key} may SAVE/commit; skipping for safety.")
            skip("presskey", f"refused save-like key {args.key}")
        else:
            kp = {"locatorPath": text_loc["locatorPath"]} if text_loc else {}
            run("presskey", driver, f"pressKey {args.key}", "presskey", key=args.key, **kp)
    else:
        skip("presskey", "no --key")

    if args.button or args.button_path:
        btn = locate(args.button, "button", args.button_path)
        if btn:
            p = {"locatorPath": btn["locatorPath"]}
            run("click", driver, "click a button", "click", **p)
            run("doubleclick", driver, "double-click", "doubleclick", **p)
        else:
            skip("click", "button not resolved")
            skip("doubleclick", "button not resolved")
    else:
        skip("click", "no --button (avoids firing an arbitrary trigger)")
        skip("doubleclick", "no --button")

    print("\nDone. Verify on-screen, then DISCARD / re-query — do not save.")
    return _summary()


# ── coverage summary ──────────────────────────────────────────────────────────

ALL_ACTUATORS = [
    "focus", "click", "doubleclick", "activatetab", "settext", "clear",
    "selectoption", "setcheck", "expandtree", "collapsetree", "presskey",
    "screenshot", "highlight", "elementat",
]


def _summary():
    print("\n" + "=" * 60)
    print("ACTUATOR COVERAGE")
    print("=" * 60)
    ok = err = 0
    for a in ALL_ACTUATORS:
        r = RESULTS.get(a, "not-run")
        mark = {"ok": "PASS", "error": "ERROR"}.get(r, "—")
        if mark == "PASS":
            ok += 1
        if r == "error":
            err += 1
        print(f"  {a:14s} {mark:6s} {r if r not in ('ok',) else ''}")
    print("-" * 60)
    print(f"  {ok}/{len(ALL_ACTUATORS)} exercised OK, {err} error(s)")
    return 1 if err else 0


if __name__ == "__main__":
    raise SystemExit(main())

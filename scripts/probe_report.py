#!/usr/bin/env python3
"""Read the ReflectionProbe output from a live scan or scan dump and surface
tab/canvas/block ownership candidates.

Usage:
    python scripts/probe_report.py [<java_scan_dump.json>]

For each probed field it prints: the field's label, then — for the field, each
ancestor canvas, and any nested Forms handler — every method/field whose VALUE
looks like it could identify the owning tab page / canvas / block. The goal is to
find ONE member that returns a stable per-tab identifier (e.g. "HOLDS", a canvas
name, or a tab-page object whose name differs per tab). If found, that member
becomes the agent's extracted `formsTabPage` signal and the Python box-dedication
heuristic can be retired.
"""
import json
import sys

# Members whose value is worth showing prominently (strong ownership hints).
STRONG = ("canvas", "tabpage", "tab_page", "page", "block", "sheet", "module")
# Noise to de-emphasise (present on everything, rarely the discriminator).
WEAK = ("name", "label", "title", "parent", "container")


def _hits(blob: str, keys) -> bool:
    low = blob.lower()
    return any(k in low for k in keys)


FULL = False  # set by --full: also print the complete method/field inventory


def _print_obj(obj: dict, indent: str, tag: str = "") -> None:
    cls = obj.get("class", "?")
    print(f"{indent}{tag}{cls}")
    for key in ("methodValues", "fieldValues"):
        raw = str(obj.get(key) or "")
        if not raw:
            continue
        for item in raw.split(" | "):
            if not item.strip():
                continue
            mark = "  ★" if _hits(item, STRONG) else ("   " if _hits(item, WEAK) else "  •")
            print(f"{indent}  {mark} {item}")
    if FULL:
        # The complete inventory (names + types, no values) — mine this for
        # possible actions / richer metadata (required, queryable, LOV, nav).
        for key, lbl in (("methods", "all methods"), ("fields", "all fields")):
            raw = str(obj.get(key) or "")
            if raw:
                print(f"{indent}    [{lbl}] {raw}")
    for nest in obj.get("nested") or []:
        _print_obj(nest.get("obj") or {}, indent + "    ", tag=f"via {nest.get('via')} -> ")


def _walk(node, out):
    attrs = node.get("attributes") or {}
    if "_probe" in attrs:
        out.append(node)
    if "_probeError" in attrs:
        out.append(node)
    for c in node.get("children") or []:
        _walk(c, out)


def main() -> int:
    global FULL
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    import argparse
    ap = argparse.ArgumentParser(
        description="Report reflection probe data from a live scan or scan dump."
    )
    ap.add_argument(
        "dump_file",
        nargs="?",
        default=None,
        help="Path to the JSON scan dump. If omitted, performs a live scan.",
    )
    ap.add_argument(
        "--full",
        action="store_true",
        help="Print the complete method/field name inventory.",
    )
    ap.add_argument(
        "--pid",
        type=int,
        default=None,
        help="Forms JVM pid (optional, for live scan)",
    )
    ap.add_argument(
        "--contains",
        default=None,
        help="process-name substring to match (optional, for live scan)",
    )
    parsed_args = ap.parse_args()

    FULL = parsed_args.full

    if parsed_args.dump_file:
        try:
            with open(parsed_args.dump_file, "r", encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception as e:
            print(f"Failed to read {parsed_args.dump_file}: {e}", file=sys.stderr)
            return 1
    else:
        try:
            from qcs_java_agent.driver import JavaAgentDriver
        except Exception as e:
            print("Could not import qcs_java_agent — run this from the repo root.",
                  file=sys.stderr)
            print("  ", e, file=sys.stderr)
            return 2

        try:
            driver = JavaAgentDriver.attach(pid=parsed_args.pid, contains=parsed_args.contains)
        except Exception as e:
            print("Attach failed. Is the Oracle Forms applet running?", file=sys.stderr)
            print("  Try --pid <pid> or --contains <name>.  Detail:", e, file=sys.stderr)
            return 1

        print(f"Attached to pid {driver.pid}. Running live scan with probe=True (setting PROBE_ENABLED as true in DomScanner)...")
        try:
            d = driver.scan(probe=True)
        except Exception as e:
            print("Scan failed. Detail:", e, file=sys.stderr)
            return 1

    probed = []
    for w in d.get("windows") or []:
        _walk(w, probed)
    if not probed:
        print("No _probe attributes found. Did you rebuild the agent with the "
              "ReflectionProbe wiring and focus a field before scanning?")
        return 1

    print(f"Found {len(probed)} probed field(s). ★ = strong ownership candidate.\n")
    for node in probed:
        label = (node.get("canonicalLabel") or node.get("accessibleName")
                 or node.get("name") or f"e{node.get('id')}")
        focused = " (FOCUSED)" if node.get("focused") else ""
        print("=" * 78)
        print(f"FIELD: {label}{focused}   ownerTab={node.get('ownerTab')!r}")
        attrs = node.get("attributes") or {}
        if "_probeError" in attrs:
            print("  PROBE ERROR:", attrs["_probeError"])
            continue
        report = json.loads(attrs["_probe"])
        _print_obj(report.get("target") or {}, "  ", tag="TARGET  ")
        print("  --- ancestor canvases ---")
        for anc in report.get("ancestors") or []:
            _print_obj(anc, "  ", tag="ANCESTOR ")
        print()

    print("\nWhat to look for: a ★ line (or any line) whose VALUE differs per tab "
          "and is stable across that tab's fields — e.g. a getCanvas()/getTabPage() "
          "returning the page name, or a handler object whose name is 'HOLDS' etc. "
          "Send me this output and I'll wire it into extraction.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

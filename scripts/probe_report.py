#!/usr/bin/env python3
"""Read the ReflectionProbe output from a scan dump and surface tab/canvas/block
ownership candidates.

Usage:
    python scripts/probe_report.py <java_scan_dump.json>

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
    if "_runtime" in attrs:
        out.append(node)
    for c in node.get("children") or []:
        _walk(c, out)


def main() -> int:
    global FULL
    args = [a for a in sys.argv[1:] if a != "--full"]
    FULL = "--full" in sys.argv
    if not args:
        print(__doc__)
        print("\nAdd --full to also print every method/field name (mine for "
              "possible actions / richer item metadata).")
        return 2
    d = json.load(open(args[0], encoding="utf-8"))
    probed = []
    for w in d.get("windows") or []:
        _walk(w, probed)
    if not probed:
        print("No _probe/_runtime attributes found. Did you rebuild the agent "
              "with the ReflectionProbe wiring? (For --target runtime the data "
              "is stored under _runtime on the first Forms item.)")
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
        if "_runtime" in attrs:
            rt = json.loads(attrs["_runtime"])
            for key in ("dispatcher", "formCanvas", "applet"):
                obj = rt.get(key)
                if obj:
                    _print_obj(obj, "  ", tag=key.upper() + "  ")
            print()
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

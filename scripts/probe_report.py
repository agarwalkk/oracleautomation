#!/usr/bin/env python3
"""Parses and prints the diagnostic reflection probe results from a scan dump."""
import argparse
import json
import sys
from typing import Any, Dict, List


def find_probed_nodes(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    nodes = []
    attrs = node.get("attributes") or {}
    if "_probe" in attrs or "_probeError" in attrs:
        nodes.append(node)
    for child in node.get("children") or []:
        nodes.extend(find_probed_nodes(child))
    return nodes


def main() -> int:
    ap = argparse.ArgumentParser(description="Report reflection probe data from a scan dump.")
    ap.add_argument("dump_file", help="Path to the JSON scan dump")
    ap.add_argument("--out", default=None, help="Output file path (default: stdout)")
    args = ap.parse_args()

    try:
        with open(args.dump_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as e:
        print(f"Failed to read {args.dump_file}: {e}", file=sys.stderr)
        return 1

    windows = data.get("windows") or []
    probed = []
    for w in windows:
        probed.extend(find_probed_nodes(w))

    # Determine output stream/file
    out_fh = sys.stdout
    if args.out:
        try:
            out_fh = open(args.out, "w", encoding="utf-8")
        except Exception as e:
            print(f"Failed to open output file {args.out}: {e}", file=sys.stderr)
            return 1

    def write_line(*args_print, **kwargs):
        kwargs["file"] = out_fh
        print(*args_print, **kwargs)

    try:
        write_line(f"Found {len(probed)} probed node(s) in {args.dump_file}\n")

        for idx, node in enumerate(probed, 1):
            write_line("=" * 80)
            write_line(f"PROBED FIELD #{idx}")
            write_line(f"  ID: {node.get('id')}")
            write_line(f"  Path: {node.get('path')}")
            write_line(f"  Display Name: {node.get('displayName')}")
            write_line(f"  Semantic Type: {node.get('semanticType')}")
            write_line(f"  Class: {node.get('type')}")
            write_line("-" * 80)

            attrs = node.get("attributes") or {}
            if "_probeError" in attrs:
                write_line(f"  [ERROR] {attrs['_probeError']}")
                continue

            probe_str = attrs.get("_probe")
            if not probe_str:
                write_line("  [Empty _probe attribute]")
                continue

            try:
                probe = json.loads(probe_str)
            except Exception as e:
                write_line(f"  [ERROR] Failed to parse _probe JSON: {e}")
                write_line(f"  Raw: {probe_str[:200]}...")
                continue

            # Print target probe details
            target = probe.get("target") or {}
            write_line("  TARGET DETAILS:")
            write_line(f"    Class: {target.get('class')}")
            write_line(f"    Method Values: {target.get('methodValues')}")
            write_line(f"    Field Values: {target.get('fieldValues')}")
            nested = target.get("nested") or []
            if nested:
                write_line("    Nested:")
                for n in nested:
                    via = n.get("via")
                    obj = n.get("obj") or {}
                    write_line(f"      via {via}:")
                    write_line(f"        Class: {obj.get('class')}")
                    write_line(f"        Method Values: {obj.get('methodValues')}")
                    write_line(f"        Field Values: {obj.get('fieldValues')}")

            write_line("-" * 80)

            # Print ancestor chain
            ancestors = probe.get("ancestors") or []
            write_line(f"  ANCESTOR CHAIN ({len(ancestors)} levels):")
            for a_idx, anc in enumerate(ancestors):
                write_line(f"    [{a_idx}] Class: {anc.get('class')}")
                m_vals = anc.get("methodValues")
                f_vals = anc.get("fieldValues")
                if m_vals:
                    write_line(f"        Method Values: {m_vals}")
                if f_vals:
                    write_line(f"        Field Values: {f_vals}")
                nested = anc.get("nested") or []
                if nested:
                    write_line("        Nested:")
                    for n in nested:
                        via = n.get("via")
                        obj = n.get("obj") or {}
                        write_line(f"          via {via}:")
                        write_line(f"            Class: {obj.get('class')}")
                        write_line(f"            Method Values: {obj.get('methodValues')}")
                        write_line(f"            Field Values: {obj.get('fieldValues')}")
            write_line("=" * 80 + "\n")
    finally:
        if args.out:
            out_fh.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
generator.build_flows — Regenerate flows_gen/<flow_id>.py from repo/flows/<flow_id>.yaml.

Each flow becomes a typed Python function callable from generated tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

import config
from qcs_repo import store as repo_store


def _snake(name: str) -> str:
    import re
    s = re.sub(r"[^a-z0-9_]", "_", name.lower())
    return re.sub(r"_+", "_", s).strip("_")


def generate_flow_file(flow_id: str, out_dir: Path = config.FLOWS_GEN_DIR) -> Path:
    flow = repo_store.load_flow(flow_id)
    if flow is None:
        raise ValueError(f"Flow '{flow_id}' not found in repository.")

    params = flow.get("params", [])
    steps  = flow.get("steps", [])
    fn_name = _snake(flow_id)

    lines = [
        "# AUTO-GENERATED — do not edit. Run `qcs flows` to regenerate.",
        "from __future__ import annotations",
        "from typing import Any",
        "",
        f"def {fn_name}(",
    ]

    param_sigs = ["    driver_or_page: Any,"] + [f"    {p}: str," for p in params]
    lines += param_sigs
    lines += [") -> None:"]
    lines += [f'    """Reusable flow: {flow_id}"""']

    for step in steps:
        tool   = step.get("tool", "")
        args   = step.get("args", {})
        target = step.get("target", {})
        value  = step.get("value", "")

        # Substitute param placeholders
        def _sub(s: str) -> str:
            for p in params:
                s = s.replace(f"{{{p}}}", f"{{{p}}}")
            return s

        if tool == "pw_goto":
            url = args.get("url", "")
            lines.append(f'    driver_or_page.goto(f"{url}")')
        elif tool == "pw_fill":
            tname = target.get("name", "")
            val   = _sub(value)
            lines.append(
                f'    driver_or_page.get_by_role("{target.get("role","textbox")}",'
                f' name="{tname}").fill(f"{val}")'
            )
        elif tool == "pw_click":
            tname = target.get("name", "")
            lines.append(
                f'    driver_or_page.get_by_role("{target.get("role","button")}",'
                f' name="{tname}").click()'
            )
        elif tool == "java_send_text":
            fn  = target.get("friendly_name", target.get("name", ""))
            val = _sub(value)
            lines.append(f'    # java_send_text: {fn!r} = f"{val}"')
        elif tool == "java_click":
            fn = target.get("friendly_name", target.get("name", ""))
            lines.append(f"    # java_click: {fn!r}")
        else:
            lines.append(f"    # {tool}: {args}")

    lines.append("")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "__init__.py").touch(exist_ok=True)
    out_file = out_dir / f"{flow_id}.py"
    out_file.write_text("\n".join(lines), encoding="utf-8")
    return out_file


def build_all(out_dir: Path = config.FLOWS_GEN_DIR) -> list[Path]:
    flow_ids = repo_store.list_flow_ids()
    generated = []
    for fid in flow_ids:
        path = generate_flow_file(fid, out_dir)
        print(f"  Generated: {path}")
        generated.append(path)
    return generated


if __name__ == "__main__":
    targets = sys.argv[1:]
    if targets:
        for fid in targets:
            p = generate_flow_file(fid)
            print(f"Generated: {p}")
    else:
        paths = build_all()
        print(f"Total: {len(paths)} flow functions regenerated.")

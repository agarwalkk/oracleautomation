"""
generator.build_pages — Regenerate pages/<form_id>.py from repo/elements/<form_id>.yaml.

Each form gets one Python file with a single Page Object class.
The class exposes every element as a property that delegates to the locator resolver.
This file is regenerated on every `qcs pages` invocation — never hand-edit it.

Usage:
    python -m generator.build_pages               # regenerate all forms
    python -m generator.build_pages find_orders   # regenerate one form
"""
from __future__ import annotations

import sys
from pathlib import Path

import config
from qcs_repo import store as repo_store

# ── Class name helpers ────────────────────────────────────────────────────────

def _class_name(form_id: str) -> str:
    """'java_find_orders' → 'FindOrdersPage'"""
    # strip leading surface prefix (java_ / html_)
    slug = form_id.removeprefix("java_").removeprefix("html_")
    return "".join(w.capitalize() for w in slug.split("_")) + "Page"


def _snake(name: str) -> str:
    """'OrderType' → 'order_type'"""
    import re
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return re.sub(r"[^a-z0-9_]", "_", s.lower()).strip("_")


# ── Per-form file generation ──────────────────────────────────────────────────

def generate_page_file(form_id: str, out_dir: Path = config.PAGES_DIR) -> Path:
    elements = repo_store.load_elements(form_id)
    form     = repo_store.load_form(form_id) or {}
    surface  = form.get("surface", "java")
    cls_name = _class_name(form_id)

    lines = [
        "# AUTO-GENERATED — do not edit. Run `qcs pages` to regenerate.",
        "from __future__ import annotations",
        "from typing import Any",
        "import config",
        "from qcs_repo import store as repo_store",
        "",
    ]

    if surface == "java":
        lines += [
            "from qcs_replay.locator import JavaAgentResolver, LocatorDescriptor",
            "",
            f"class {cls_name}:",
            f'    """Page Object for Oracle form: {form_id}"""',
            "",
            "    def __init__(self, driver: Any):",
            "        self._driver = driver",
            f'        self._form_id = "{form_id}"',
            "        self._resolver = JavaAgentResolver(driver)",
            "",
        ]
        for el in elements:
            fn   = el.get("friendly_name", "unknown")
            prop = _snake(fn)
            lines += [
                "    @property",
                f"    def {prop}(self) -> Any:",
                f'        """friendly_name: {fn}"""',
                f'        descriptor = repo_store.find_element(self._form_id, "{fn}")',
                "        return self._resolver.resolve(LocatorDescriptor(descriptor))",
                "",
            ]
    else:
        lines += [
            "from qcs_replay.locator import PlaywrightResolver, LocatorDescriptor",
            "",
            f"class {cls_name}:",
            f'    """Page Object for OAF page: {form_id}"""',
            "",
            "    def __init__(self, page: Any):",
            "        self._page = page",
            f'        self._form_id = "{form_id}"',
            "        self._resolver = PlaywrightResolver(page)",
            "",
        ]
        for el in elements:
            fn   = el.get("friendly_name", "unknown")
            prop = _snake(fn)
            lines += [
                "    @property",
                f"    def {prop}(self) -> Any:",
                f'        """friendly_name: {fn}"""',
                f'        descriptor = repo_store.find_element(self._form_id, "{fn}")',
                "        return self._resolver.resolve(LocatorDescriptor(descriptor))",
                "",
            ]

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "__init__.py").touch(exist_ok=True)
    out_file = out_dir / f"{form_id}.py"
    out_file.write_text("\n".join(lines), encoding="utf-8")
    return out_file


# ── Entry point ───────────────────────────────────────────────────────────────

def build_all(out_dir: Path = config.PAGES_DIR) -> list[Path]:
    form_ids = repo_store.list_form_ids()
    generated = []
    for fid in form_ids:
        path = generate_page_file(fid, out_dir)
        print(f"  Generated: {path}")
        generated.append(path)
    return generated


if __name__ == "__main__":
    targets = sys.argv[1:]
    if targets:
        for fid in targets:
            p = generate_page_file(fid)
            print(f"Generated: {p}")
    else:
        paths = build_all()
        print(f"Total: {len(paths)} page objects regenerated.")

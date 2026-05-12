"""
qcs_repo.fingerprint — Compute a stable, deterministic form_id from a live
Java Forms or HTML/OAF snapshot.

Java Forms fingerprint
    SHA-256 of: window_title + sorted top-2-depth (role, name) pairs

HTML fingerprint
    SHA-256 of: URL function_id segment + page <title> + sorted top-1-depth
                accessibility node (role, name) pairs
"""
from __future__ import annotations

import hashlib
import re
from typing import Any


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _canonical_pairs(nodes: list[dict], max_depth: int = 2) -> list[tuple[str, str]]:
    """
    Flatten a nested accessibility tree into (role, name) pairs up to max_depth.
    Accepts either:
      - a list of elements already filtered by depth (common from get_page_snapshot)
      - or a nested tree with 'children' key
    Returns a sorted, deduplicated list.
    """
    pairs: set[tuple[str, str]] = set()

    def _walk(node: dict, depth: int) -> None:
        if depth > max_depth:
            return
        role = (node.get("role") or "").strip()
        name = (node.get("name") or "").strip()
        if role:
            pairs.add((role, name))
        for child in node.get("children", []):
            _walk(child, depth + 1)

    for node in nodes:
        _walk(node, 1)

    return sorted(pairs)


# ── Java Forms fingerprint ───────────────────────────────────────────────────

def fingerprint_java_form(window_title: str, top_elements: list[dict]) -> str:
    """
    Parameters
    ----------
    window_title  : GetWindowText() result, e.g. "Oracle Applications"
    top_elements  : list of element dicts from get_page_snapshot at depth ≤ 2
                    Each must have at least 'role' and 'name' keys.

    Returns
    -------
    16-hex-char fingerprint usable as form_id prefix.
    """
    pairs = _canonical_pairs(top_elements, max_depth=2)
    raw = window_title.strip() + "|" + str(pairs)
    return "java_" + _sha(raw)


# ── HTML / OAF fingerprint ───────────────────────────────────────────────────

_FUNCTION_ID_RE = re.compile(r"function_id=(\d+)", re.IGNORECASE)


def fingerprint_html_page(url: str, page_title: str, top_nodes: list[dict]) -> str:
    """
    Parameters
    ----------
    url        : Full page URL (function_id extracted if present)
    page_title : document.title
    top_nodes  : list of top-level accessibility nodes (depth ≤ 1)

    Returns
    -------
    16-hex-char fingerprint usable as form_id prefix.
    """
    m = _FUNCTION_ID_RE.search(url)
    func_id = m.group(1) if m else _sha(url)[:8]

    pairs = _canonical_pairs(top_nodes, max_depth=1)
    raw = func_id + "|" + page_title.strip() + "|" + str(pairs)
    return "html_" + _sha(raw)


# ── Suggest a human-friendly form_id from the title ─────────────────────────

def suggest_form_id(window_title: str, surface: str = "java") -> str:
    """
    Derive a readable, slug-style form_id from a window title.
    E.g. "Find Orders (OEOL)" -> "java_find_orders"
    """
    slug = re.sub(r"[^a-z0-9]+", "_", window_title.lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)[:40]
    return f"{surface}_{slug}"

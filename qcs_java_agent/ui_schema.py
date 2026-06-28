"""Typed contract for the enriched Java UI snapshot.

This is the boundary between the Java agent (the single authority on UI
structure and identity) and Python (a thin renderer + action dispatcher).

The Java agent emits each node already carrying:
  - identity:   semanticId, primaryLocator (verified-unique), locatorAmbiguous
  - structure:  containerRole, ownerTab, recordIndex, columnKey, current,
                isMirror, treePath, expanded
  - canonical:  canonicalLabel (un-prefixed business label)

Python no longer infers tabs, tables, frozen columns, the selected record,
read-only mirrors, or identity. It validates the contract and renders it.

Keep this in lockstep with DomNode.toJson() on the Java side. Bump
SCHEMA_VERSION when the contract changes; the agent should stamp the same
version into the scan envelope so a mismatch fails loud instead of silently
falling back to the legacy geometry heuristics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "2.0"

# containerRole vocabulary (None for ordinary leaf controls)
CONTAINER_ROLES = frozenset({
    "TabFolder", "TabPage", "Grid", "GridRow", "GridCell",
    "TreeItem", "FieldGroup", "Mirror",
})

# Locator strategies the Java ComponentResolver understands, mapped to the
# command param key it reads. Used by build_locator_params().
LOCATOR_PARAM_KEYS = {
    "semanticId":     "locatorSemanticId",
    "treePath":       "locatorTreePath",
    "canonicalLabel": "locatorCanonicalLabel",
    "accessibleName": "locatorAccessibleName",
    "name":           "locatorName",
    "text":           "locatorText",
    "path":           "locatorPath",
}


@dataclass
class Locator:
    strategy: str
    value: str
    confidence: float = 0.0
    verified_unique: bool = False
    scope: str | None = None
    ordinal: int = -1

    @classmethod
    def from_json(cls, d: dict | None) -> "Locator | None":
        if not d:
            return None
        return cls(
            strategy=str(d.get("strategy") or ""),
            value=str(d.get("value") or ""),
            confidence=float(d.get("confidence") or 0.0),
            verified_unique=bool(d.get("verifiedUnique")),
            scope=d.get("scope"),
            ordinal=int(d.get("ordinal", -1)),
        )


def is_enriched(scan: dict) -> bool:
    """True when the scan carries the v2 enriched fields (Java is authoritative).

    Lets callers route to the thin renderer when present and fall back to the
    legacy snapshot.py path otherwise — a safe, incremental rollout.
    """
    for w in scan.get("windows") or []:
        if _node_has_enrichment(w):
            return True
    return False


def _node_has_enrichment(node: dict) -> bool:
    if node.get("semanticId") or node.get("containerRole") or node.get("primaryLocator"):
        return True
    for c in node.get("children") or []:
        if _node_has_enrichment(c):
            return True
    return False


def validate_node(node: dict, path: str = "$") -> list[str]:
    """Return a list of contract violations for one node (empty = valid)."""
    errs: list[str] = []
    cr = node.get("containerRole")
    if cr is not None and cr not in CONTAINER_ROLES:
        errs.append(f"{path}: unknown containerRole {cr!r}")
    if cr == "GridCell":
        if node.get("recordIndex", -1) < 0:
            errs.append(f"{path}: GridCell missing recordIndex")
        if not node.get("columnKey"):
            errs.append(f"{path}: GridCell missing columnKey")
    if cr == "TreeItem" and not node.get("treePath"):
        errs.append(f"{path}: TreeItem missing treePath")
    pl = node.get("primaryLocator")
    if pl is not None and not pl.get("verifiedUnique") and not node.get("locatorAmbiguous"):
        errs.append(f"{path}: primaryLocator not verifiedUnique and not flagged ambiguous")
    for i, c in enumerate(node.get("children") or []):
        errs.extend(validate_node(c, f"{path}/{node.get('semanticType','?')}[{i}]"))
    return errs


def validate_scan(scan: dict) -> list[str]:
    errs: list[str] = []
    for i, w in enumerate(scan.get("windows") or []):
        errs.extend(validate_node(w, f"$.windows[{i}]"))
    return errs


def build_locator_params(node: dict) -> dict[str, str]:
    """Translate a node's verified identity into ComponentResolver params.

    Replay should send these. The primary locator goes first; scope + ordinal
    are included so even a non-globally-unique label resolves deterministically.
    Bounds are included as a Robot-click fallback for rows/tabs without a model
    selector.
    """
    params: dict[str, str] = {}
    sem = node.get("semanticId")
    if sem:
        params["locatorSemanticId"] = str(sem)

    pl = Locator.from_json(node.get("primaryLocator"))
    if pl and pl.value:
        key = LOCATOR_PARAM_KEYS.get(pl.strategy)
        if key:
            params[key] = pl.value
        if pl.scope:
            params["locatorScope"] = pl.scope
        if pl.ordinal >= 0:
            params["locatorOrdinal"] = str(pl.ordinal)

    if node.get("treePath"):
        params.setdefault("locatorTreePath", str(node["treePath"]))
    if node.get("recordIndex", -1) >= 0:
        params.setdefault("locatorRecordIndex", str(node["recordIndex"]))

    sb = node.get("screenBounds") or node.get("bounds") or {}
    x = sb.get("screenX", sb.get("x"))
    y = sb.get("screenY", sb.get("y"))
    w = sb.get("width")
    h = sb.get("height")
    if None not in (x, y, w, h) and (w or 0) > 0 and (h or 0) > 0:
        params.setdefault("locatorBounds", f"{x},{y},{w},{h}")
    return params

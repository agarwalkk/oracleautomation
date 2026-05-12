"""Stable repository identity helpers for screens and elements.

The Java agent exposes volatile capture-local ids such as ``e12``.  These helpers
derive stable, reviewable identities that generated scripts can use indirectly
through the object repository.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any


def stable_hash(text: str, length: int = 16) -> str:
    """Return a short deterministic hash for repository ids."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def normalize_ref(text: str, *, fallback: str = "element") -> str:
    """Normalize display text into a Python/YAML friendly semantic ref."""
    value = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    value = re.sub(r"_+", "_", value)
    return (value[:60].strip("_") or fallback)


def element_label(element: dict[str, Any]) -> str:
    """Best human-facing label for a Java/Web element record."""
    return str(
        element.get("label")
        or element.get("friendly_name")
        or element.get("name")
        or element.get("text")
        or element.get("description")
        or element.get("elementid")
        or ""
    ).strip()


def semantic_ref(element: dict[str, Any]) -> str:
    """Return the preferred semantic ref for an element."""
    existing = str(element.get("semantic_ref") or element.get("friendly_name") or "").strip()
    if existing and not re.fullmatch(r"e\d+", existing):
        return normalize_ref(existing)
    role = normalize_ref(str(element.get("role") or ""), fallback="element")
    label = normalize_ref(element_label(element), fallback=role)
    if label and label != "element":
        return label
    return role


def screen_structure_hash(elements: list[dict[str, Any]]) -> str:
    """Hash the stable visible structure of a captured screen."""
    parts: list[str] = []
    for element in elements:
        role = str(element.get("role") or "").strip().lower()
        name = str(element.get("name") or element.get("text") or "").strip().lower()
        parent = str(element.get("filteredparentid") or "").strip().lower()
        if role or name:
            parts.append(f"{role}|{name}|{parent}")
    return stable_hash("\n".join(sorted(set(parts))))


def element_identity_key(form_id: str, element: dict[str, Any]) -> str:
    """Build a stable matching key for deduping captures of the same element."""
    java = element.get("java") or {}
    locators = java.get("locators") or []
    locator_parts: list[str] = []
    for locator in locators:
        strategy = str(locator.get("strategy") or "").strip().lower()
        value = str(locator.get("value") or "").strip().lower()
        if strategy and value:
            locator_parts.append(f"{strategy}:{value}")

    path = str(java.get("path") or element.get("path") or element.get("xpath") or "").strip().lower()
    role = str(element.get("role") or "").strip().lower()
    name = str(element.get("name") or "").strip().lower()
    text = str(element.get("text") or "").strip().lower()
    ancestors = "/".join(str(a).strip().lower() for a in (element.get("ancestors") or []) if a)
    class_name = str(java.get("className") or java.get("simpleClassName") or "").strip().lower()

    if locator_parts:
        raw = f"{form_id}|locators|{'|'.join(sorted(locator_parts))}|{role}|{ancestors}"
    elif path:
        raw = f"{form_id}|path|{path}|{role}"
    else:
        raw = f"{form_id}|semantic|{role}|{name}|{text}|{ancestors}|{class_name}"
    return stable_hash(raw)


def element_uid(form_id: str, element: dict[str, Any]) -> str:
    """Return a stable repo element uid scoped by form/screen."""
    return f"el_{element_identity_key(form_id, element)}"


def locator_candidates(element: dict[str, Any]) -> list[dict[str, Any]]:
    """Build ranked Java-agent locator candidates from an element descriptor."""
    java = element.get("java") or {}
    candidates: list[dict[str, Any]] = []

    def add(strategy: str, value: Any, priority: int, confidence: float = 1.0) -> None:
        if value is None:
            return
        text = str(value).strip()
        if not text:
            return
        candidate = {
            "strategy": strategy,
            "value": text,
            "priority": priority,
            "confidence": confidence,
        }
        if candidate not in candidates:
            candidates.append(candidate)

    add("java_path", java.get("path") or element.get("path") or element.get("xpath"), 10, 0.95)
    add("component_name", java.get("name") or element.get("component_name"), 20, 0.9)
    add("accessible_name", java.get("accessibleName"), 30, 0.9)
    add("text", element.get("name") or element.get("text"), 40, 0.8)
    add("class_name", java.get("simpleClassName") or java.get("className"), 50, 0.65)

    bounds = element.get("bounds") or {}
    if bounds:
        add(
            "bounds",
            f"{bounds.get('x', 0)},{bounds.get('y', 0)},{bounds.get('width', 0)},{bounds.get('height', 0)}",
            90,
            0.35,
        )

    return sorted(candidates, key=lambda item: (int(item.get("priority", 100)), item.get("strategy", "")))


def merge_locator_candidates(
    existing: list[dict[str, Any]] | None,
    incoming: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Merge locator candidates without duplicating strategy/value pairs."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in [*(existing or []), *(incoming or [])]:
        strategy = str(candidate.get("strategy") or "").strip()
        value = str(candidate.get("value") or "").strip()
        if not strategy or not value:
            continue
        key = (strategy, value)
        previous = merged.get(key)
        if previous is None:
            merged[key] = dict(candidate)
            continue
        previous_priority = int(previous.get("priority", 100))
        candidate_priority = int(candidate.get("priority", 100))
        if candidate_priority < previous_priority:
            merged[key] = {**previous, **candidate, "priority": candidate_priority}
    return sorted(merged.values(), key=lambda item: (int(item.get("priority", 100)), item.get("strategy", "")))

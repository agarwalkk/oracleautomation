"""
generator.alias_catalog -- Scalable, validated alias catalog for Oracle EBS business names.

Catalog layout on disk::

    config/aliases/
        <domain>/
            <form_ref>.json    ← one file per business form
            ...
        ...

Each JSON file defines one business form with its technical_forms list and a
per-element alias table.

Public API
----------
ElementAlias        dataclass — one element alias entry.
FormAliasFile       dataclass — one alias file loaded into memory.
CatalogConflict     dataclass — a single validation issue.
AliasCatalog        loads/validates/queries the full catalog directory.

Validation rules
----------------
1.  ``form_ref`` unique across the whole catalog.
2.  ``element_ref`` unique within a single form.
3.  ``technical_name`` unique within a single form.
4.  Business refs (``form_ref``, ``element_ref``) must NOT match technical patterns
    (``java_*``, ``html_*``, ``_alt_X``, ``_mnemonic_X``, ``toolbar<digits>``).
5.  Required fields must be present and non-empty.
6.  ``review_status == "needs_review"`` is flagged for human follow-up.

Conflict types (``CatalogConflict.conflict_type``)
---------------------------------------------------
duplicate_form_ref         — same form_ref in multiple files.
duplicate_element_ref      — same element_ref within a form.
duplicate_technical_name   — same technical_name within a form.
technical_business_ref     — form_ref or element_ref matches a technical pattern.
missing_required_field     — required field absent or empty.
alias_review_pending       — element has review_status == "needs_review".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import json
except ImportError:  # pragma: no cover
    raise


# ---------------------------------------------------------------------------
# Business-ref technical-pattern guard
# ---------------------------------------------------------------------------

_BUSINESS_REF_FORBIDDEN: re.Pattern[str] = re.compile(
    r"^java_|^html_|_alt_[a-z](?:_|$)|_mnemonic_[a-z](?:_|$)|toolbar\d"
)

_REQUIRED_FORM_FIELDS: tuple[str, ...] = (
    "form_ref",
    "display_name",
    "domain",
    "surface",
    "technical_forms",
)
_REQUIRED_ELEMENT_FIELDS: tuple[str, ...] = (
    "technical_name",
    "element_ref",
    "display_name",
    "object_type",
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ElementAlias:
    """One element alias entry inside a form alias file."""

    technical_name: str
    element_ref: str
    display_name: str
    object_type: str
    synonyms: list[str] = field(default_factory=list)
    review_status: str = ""   # "" | "reviewed" | "needs_review"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ElementAlias":
        return cls(
            technical_name=str(data.get("technical_name") or "").strip(),
            element_ref=str(data.get("element_ref") or "").strip(),
            display_name=str(data.get("display_name") or "").strip(),
            object_type=str(data.get("object_type") or "").strip(),
            synonyms=[str(s) for s in (data.get("synonyms") or [])],
            review_status=str(data.get("review_status") or "").strip(),
        )


@dataclass
class FormAliasFile:
    """One alias file loaded into memory — represents a single business form."""

    form_ref: str
    display_name: str
    domain: str
    surface: str
    technical_forms: list[str]
    elements: list[ElementAlias]
    source_file: Path | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], source_file: Path | None = None) -> "FormAliasFile":
        raw_elements = data.get("elements") or []
        return cls(
            form_ref=str(data.get("form_ref") or "").strip(),
            display_name=str(data.get("display_name") or "").strip(),
            domain=str(data.get("domain") or "").strip(),
            surface=str(data.get("surface") or "").strip(),
            technical_forms=[
                str(t).strip() for t in (data.get("technical_forms") or []) if str(t).strip()
            ],
            elements=[ElementAlias.from_dict(e) for e in raw_elements if isinstance(e, dict)],
            source_file=source_file,
        )


@dataclass
class CatalogConflict:
    """A single validation issue found in the alias catalog."""

    conflict_type: str
    form_ref: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.conflict_type}] {self.form_ref}: {self.detail}"


# ---------------------------------------------------------------------------
# Alias catalog
# ---------------------------------------------------------------------------


class AliasCatalog:
    """Loads, validates, and queries the full alias catalog directory.

    Usage::

        catalog = AliasCatalog.load(Path("config/aliases"))
        conflicts = catalog.validate()
        form_file = catalog.lookup_form("java_po")  # returns FormAliasFile
    """

    def __init__(self) -> None:
        self._form_files: list[FormAliasFile] = []
        # Fast-lookup indexes built by _build_indexes()
        # technical_form_ref -> FormAliasFile
        self._by_technical_form: dict[str, FormAliasFile] = {}
        # (technical_form_ref, technical_element_ref) -> ElementAlias
        self._by_technical_element: dict[tuple[str, str], ElementAlias] = {}

    # -- Factories -------------------------------------------------------------

    @classmethod
    def load(cls, catalog_dir: Path | str) -> "AliasCatalog":
        """Recursively load all ``*.json`` alias files under *catalog_dir*.

        Files that fail to parse are silently skipped (invalid JSON) — callers
        should run ``validate()`` to surface structural errors.
        """
        catalog = cls()
        catalog_dir = Path(catalog_dir)
        if not catalog_dir.exists():
            return catalog
        for json_file in sorted(catalog_dir.rglob("*.json")):
            try:
                data: dict[str, Any] = json.loads(json_file.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(data, dict):
                continue
            form_file = FormAliasFile.from_dict(data, source_file=json_file)
            catalog._form_files.append(form_file)
        catalog._build_indexes()
        return catalog

    # -- Internal index --------------------------------------------------------

    def _build_indexes(self) -> None:
        self._by_technical_form = {}
        self._by_technical_element = {}
        for form_file in self._form_files:
            for tech_form in form_file.technical_forms:
                if tech_form:
                    self._by_technical_form[tech_form] = form_file
            for elem in form_file.elements:
                for tech_form in form_file.technical_forms:
                    if tech_form and elem.technical_name:
                        self._by_technical_element[(tech_form, elem.technical_name)] = elem

    # -- Queries ---------------------------------------------------------------

    def lookup_form(self, technical_form_ref: str) -> FormAliasFile | None:
        """Return the ``FormAliasFile`` that maps *technical_form_ref*, or ``None``."""
        return self._by_technical_form.get(technical_form_ref)

    def lookup_element(
        self, technical_form_ref: str, technical_element_ref: str
    ) -> ElementAlias | None:
        """Return the ``ElementAlias`` for the given technical form/element pair, or ``None``."""
        return self._by_technical_element.get((technical_form_ref, technical_element_ref))

    @property
    def form_files(self) -> list[FormAliasFile]:
        """All loaded ``FormAliasFile`` objects (one per alias JSON file)."""
        return list(self._form_files)

    # -- Flat-format converters (for AliasResolver compatibility) -------------

    def to_flat_forms(self) -> dict[str, dict[str, Any]]:
        """Return a ``{technical_ref: {business_ref, confidence}}`` dict matching
        the legacy ``business_aliases.json`` ``"forms"`` section format."""
        result: dict[str, dict[str, Any]] = {}
        for form_file in self._form_files:
            entry: dict[str, Any] = {
                "business_ref": form_file.form_ref,
                "confidence": 1.0,
            }
            for tech_form in form_file.technical_forms:
                if tech_form:
                    result[tech_form] = entry
        return result

    def to_flat_elements(self) -> dict[str, dict[str, Any]]:
        """Return a ``{tech_form.tech_elem: {business_ref, confidence}}`` dict
        matching the legacy ``business_aliases.json`` ``"elements"`` section format."""
        result: dict[str, dict[str, Any]] = {}
        for form_file in self._form_files:
            for elem in form_file.elements:
                if not elem.technical_name or not elem.element_ref:
                    continue
                entry: dict[str, Any] = {
                    "business_ref": elem.element_ref,
                    "confidence": 1.0,
                }
                for tech_form in form_file.technical_forms:
                    if tech_form:
                        result[f"{tech_form}.{elem.technical_name}"] = entry
        return result

    # -- Validation ------------------------------------------------------------

    def validate(self) -> list[CatalogConflict]:
        """Run all validation rules and return a (possibly empty) list of conflicts."""
        conflicts: list[CatalogConflict] = []
        seen_form_refs: dict[str, Path | None] = {}

        for form_file in self._form_files:
            form_ref = form_file.form_ref
            src = str(form_file.source_file or "<unknown>")

            # Rule 5: required form-level fields
            for req in _REQUIRED_FORM_FIELDS:
                val = getattr(form_file, req)
                if not val or (isinstance(val, list) and len(val) == 0):
                    conflicts.append(CatalogConflict(
                        conflict_type="missing_required_field",
                        form_ref=form_ref or src,
                        detail=f"Required field '{req}' is missing or empty in {src}",
                    ))

            if not form_ref:
                continue  # can't validate further without a form_ref

            # Rule 1: form_ref unique across catalog
            if form_ref in seen_form_refs:
                conflicts.append(CatalogConflict(
                    conflict_type="duplicate_form_ref",
                    form_ref=form_ref,
                    detail=(
                        f"form_ref '{form_ref}' already defined in "
                        f"{seen_form_refs[form_ref]}; redefined in {src}"
                    ),
                ))
            else:
                seen_form_refs[form_ref] = form_file.source_file

            # Rule 4: form_ref must not be a technical name
            if _BUSINESS_REF_FORBIDDEN.search(form_ref):
                conflicts.append(CatalogConflict(
                    conflict_type="technical_business_ref",
                    form_ref=form_ref,
                    detail=(
                        f"form_ref '{form_ref}' in {src} matches a technical naming pattern "
                        "(java_*, html_*, _alt_X, _mnemonic_X, toolbar<digits>)"
                    ),
                ))

            # Per-element validation
            seen_element_refs: dict[str, str] = {}
            seen_technical_names: dict[str, str] = {}

            for elem in form_file.elements:
                tech_name = elem.technical_name
                elem_ref = elem.element_ref

                # Rule 5: required element fields
                for req in _REQUIRED_ELEMENT_FIELDS:
                    val = getattr(elem, req)
                    if not val:
                        conflicts.append(CatalogConflict(
                            conflict_type="missing_required_field",
                            form_ref=form_ref,
                            detail=(
                                f"Element in {src} missing required field '{req}' "
                                f"(technical_name={tech_name!r})"
                            ),
                        ))

                if not elem_ref or not tech_name:
                    continue  # can't validate further

                # Rule 2: element_ref unique within form
                if elem_ref in seen_element_refs:
                    conflicts.append(CatalogConflict(
                        conflict_type="duplicate_element_ref",
                        form_ref=form_ref,
                        detail=(
                            f"element_ref '{elem_ref}' duplicated in form '{form_ref}' "
                            f"(also used by technical_name='{seen_element_refs[elem_ref]}')"
                        ),
                    ))
                else:
                    seen_element_refs[elem_ref] = tech_name

                # Rule 3: technical_name unique within form
                if tech_name in seen_technical_names:
                    conflicts.append(CatalogConflict(
                        conflict_type="duplicate_technical_name",
                        form_ref=form_ref,
                        detail=(
                            f"technical_name '{tech_name}' duplicated in form '{form_ref}' "
                            f"(also maps to element_ref='{seen_technical_names[tech_name]}')"
                        ),
                    ))
                else:
                    seen_technical_names[tech_name] = elem_ref

                # Rule 4: element_ref must not be a technical name
                if _BUSINESS_REF_FORBIDDEN.search(elem_ref):
                    conflicts.append(CatalogConflict(
                        conflict_type="technical_business_ref",
                        form_ref=form_ref,
                        detail=(
                            f"element_ref '{elem_ref}' (technical_name='{tech_name}') in "
                            f"form '{form_ref}' matches a technical naming pattern"
                        ),
                    ))

                # Rule 6: alias_review_pending
                if elem.review_status == "needs_review":
                    conflicts.append(CatalogConflict(
                        conflict_type="alias_review_pending",
                        form_ref=form_ref,
                        detail=(
                            f"Element '{elem_ref}' (technical_name='{tech_name}') in "
                            f"form '{form_ref}' has review_status='needs_review'"
                        ),
                    ))

        return conflicts

    # -- Report ----------------------------------------------------------------

    def report(self, *, include_stats: bool = True) -> str:
        """Return a human-readable validation and summary report."""
        lines: list[str] = []
        conflicts = self.validate()

        if include_stats:
            n_forms = len(self._form_files)
            n_elems = sum(len(f.elements) for f in self._form_files)
            n_tech_forms = sum(len(f.technical_forms) for f in self._form_files)
            lines.append("=== Alias Catalog Report ===")
            lines.append(
                f"  {n_forms} business form(s), "
                f"{n_tech_forms} technical form mapping(s), "
                f"{n_elems} element alias(es)"
            )
            domains = sorted({f.domain for f in self._form_files if f.domain})
            lines.append(f"  Domains: {', '.join(domains) if domains else '(none)'}")
            lines.append("")

        if not conflicts:
            lines.append("No conflicts or issues found.")
            return "\n".join(lines)

        by_type: dict[str, list[CatalogConflict]] = {}
        for c in conflicts:
            by_type.setdefault(c.conflict_type, []).append(c)

        lines.append(f"Found {len(conflicts)} issue(s):")
        lines.append("")
        for ctype, items in sorted(by_type.items()):
            lines.append(f"  [{ctype}] — {len(items)} issue(s):")
            for item in items:
                lines.append(f"    • {item.detail}")
            lines.append("")

        return "\n".join(lines)

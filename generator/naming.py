"""
generator.naming -- Business naming layer for generated Oracle EBS replay scripts.

Maps technical Java Forms IDs (e.g. ``java_order_typelist_of_values``) to
business-readable names (e.g. ``order_type_lov``) via a deterministic sanitiser
and a catalog-backed alias table.  No AI or LLM is used.

Alias sources (in priority order)
----------------------------------
1. ``catalog_dir`` argument   → ``AliasCatalog.load(dir)`` (preferred, scalable).
2. ``alias_file`` argument    → legacy flat ``business_aliases.json`` (migration path).
3. Auto-discovery from config → ``config.BUSINESS_ALIASES_DIR`` (catalog) or
                                 ``config.BUSINESS_ALIASES_FILE`` (legacy).

A missing or unreadable alias source is silently treated as an empty table —
the sanitiser is always applied as a fallback.

Public API
----------
sanitize_ref(name)           Deterministic technical-noise stripper.
is_technical_form_ref(name)  True if name still looks like a UI artifact.
is_technical_element_ref(name)
NameResult                   dataclass — resolved name + confidence + review flag.
AliasResolver                resolves form/element refs via catalog or legacy file.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Sanitiser
# ---------------------------------------------------------------------------

def sanitize_ref(name: str) -> str:
    """Strip technical noise from a form/element ref string deterministically.

    Rules applied in order:

    1. Remove ``java_`` / ``html_`` surface prefixes.
    2. Remove ``_alt_[a-z]`` keyboard-shortcut suffix  (``find_alt_i`` → ``find``).
    3. Remove ``_alt_[a-z]`` infix before another word (``ctrl_alt_c_key`` → ``ctrl_c_key``).
    4. Remove ``_mnemonic_[a-z]`` suffix  (``file_mnemonic_f`` → ``file``).
    5. Remove ``_mnemonic_[a-z]`` infix.
    6. Replace ``_?list_of_values`` suffix with ``_lov``  (``order_typelist_of_values`` → ``order_type_lov``).
    7. Replace ``_tab_page_`` connector with ``_``  (``summary_tab_page_order_number`` → ``summary_order_number``).
    8. Strip trailing digits from bare ``toolbar`` identifiers  (``toolbar195`` → ``toolbar``).
    9. Collapse repeated underscores; strip leading/trailing underscores.
    """
    s = name

    # 1. Surface prefixes
    s = re.sub(r'^java_', '', s)
    s = re.sub(r'^html_', '', s)

    # 2-3. _alt_X shortcuts
    s = re.sub(r'_alt_[a-z]$', '', s)
    s = re.sub(r'_alt_[a-z](?=_)', '', s)

    # 4-5. _mnemonic_X
    s = re.sub(r'_mnemonic_[a-z]$', '', s)
    s = re.sub(r'_mnemonic_[a-z](?=_)', '', s)

    # 6. list_of_values → _lov  (handles both order_typelist_of_values and order_type_list_of_values)
    s = re.sub(r'_?list_of_values$', '_lov', s)

    # 7. _tab_page_ structural connector
    s = re.sub(r'_tab_page_', '_', s)

    # 8. toolbar + trailing digits
    s = re.sub(r'^toolbar\d+$', 'toolbar', s)
    s = re.sub(r'(?<=\btoolbar)\d+', '', s)

    # 9. Normalise
    s = re.sub(r'_+', '_', s).strip('_')

    return s or name


# ---------------------------------------------------------------------------
# Technical name detection
# ---------------------------------------------------------------------------

_TECHNICAL_FORM_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r'^java_'),
    re.compile(r'^html_'),
    re.compile(r'_alt_[a-z](?:_|$)'),
    re.compile(r'_mnemonic_[a-z](?:_|$)'),
    re.compile(r'toolbar\d'),
]

_TECHNICAL_ELEMENT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r'_alt_[a-z](?:_|$)'),
    re.compile(r'_mnemonic_[a-z](?:_|$)'),
    re.compile(r'toolbar\d'),
]


def is_technical_form_ref(name: str) -> bool:
    """Return ``True`` if *name* still matches a known technical UI-artifact pattern."""
    return any(p.search(name) for p in _TECHNICAL_FORM_PATTERNS)


def is_technical_element_ref(name: str) -> bool:
    """Return ``True`` if *name* still matches a known technical UI-artifact pattern."""
    return any(p.search(name) for p in _TECHNICAL_ELEMENT_PATTERNS)


# ---------------------------------------------------------------------------
# Name resolution result
# ---------------------------------------------------------------------------

@dataclass
class NameResult:
    """Outcome of resolving a form or element name through the alias table or sanitiser.

    Attributes
    ----------
    ref:                Resolved business name.
    confidence:         ``1.0`` = aliased and reviewed; ``0.7`` = sanitised (clean);
                        ``0.5`` = sanitised (still has technical patterns).
    needs_alias_review: ``True`` when the name was produced by the sanitiser and
                        has not been confirmed by a human alias entry.
    source:             ``"alias"`` or ``"sanitized"``.
    original:           The raw technical name before resolution.
    """

    ref: str
    confidence: float
    needs_alias_review: bool
    source: str    # "alias" | "sanitized"
    original: str


# ---------------------------------------------------------------------------
# Alias resolver
# ---------------------------------------------------------------------------

_ALIAS_FILE_NAME = "business_aliases.json"
_ALIAS_CATALOG_DIR = "aliases"


class AliasResolver:
    """Resolves technical form/element IDs to business names.

    Sources (in priority order):

    1. *catalog_dir* explicit arg → ``AliasCatalog.load(catalog_dir)`` (scalable).
    2. *alias_file* explicit arg  → legacy flat ``business_aliases.json``.
    3. Auto-discovery from config → ``config.BUSINESS_ALIASES_DIR`` (catalog) or
       ``config.BUSINESS_ALIASES_FILE`` (legacy flat file).

    A missing or unreadable source is silently treated as an empty alias table —
    the sanitiser is always applied as a fallback.

    The :attr:`forms_reverse` and :attr:`elements_reverse` dicts expose the
    reverse mapping (business_ref → technical_ref) for runtime resolver use.
    """

    def __init__(
        self,
        alias_file: Path | str | None = None,
        catalog_dir: Path | str | None = None,
    ) -> None:
        self._forms: dict[str, dict[str, Any]] = {}
        self._elements: dict[str, dict[str, Any]] = {}
        # Reverse maps: business_ref -> technical_ref  (for runtime resolver use)
        self.forms_reverse: dict[str, str] = {}
        self.elements_reverse: dict[str, tuple[str, str]] = {}

        if catalog_dir is not None:
            self._load_from_catalog(Path(catalog_dir))
        elif alias_file is not None:
            self._load_from_file(Path(alias_file))
        else:
            self._auto_discover()

    # -- Source discovery ------------------------------------------------------

    def _auto_discover(self) -> None:
        try:
            import config as _cfg  # noqa: PLC0415
            biz_dir = getattr(_cfg, "BUSINESS_ALIASES_DIR", None)
            if biz_dir and Path(biz_dir).exists():
                self._load_from_catalog(Path(biz_dir))
                return
            biz_file = getattr(_cfg, "BUSINESS_ALIASES_FILE", None) or (
                Path(_cfg.ROOT_DIR) / "config" / _ALIAS_FILE_NAME
            )
            if biz_file:
                self._load_from_file(Path(biz_file))
        except (ImportError, AttributeError):
            pass

    # -- Catalog loader --------------------------------------------------------

    def _load_from_catalog(self, catalog_dir: Path) -> None:
        from generator.alias_catalog import AliasCatalog  # noqa: PLC0415

        catalog = AliasCatalog.load(catalog_dir)
        self._forms = catalog.to_flat_forms()
        self._elements = catalog.to_flat_elements()
        self._build_reverse_maps()

    # -- Legacy flat-file loader -----------------------------------------------

    def _load_from_file(self, alias_file: Path) -> None:
        if not alias_file.exists():
            return
        try:
            data: dict[str, Any] = json.loads(alias_file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return
        self._forms = data.get("forms") or {}
        self._elements = data.get("elements") or {}
        self._build_reverse_maps()

    # -- Shared reverse-map builder --------------------------------------------

    def _build_reverse_maps(self) -> None:
        for tech_ref, entry in self._forms.items():
            biz = str(entry.get("business_ref") or "").strip()
            if biz:
                self.forms_reverse[biz] = tech_ref

        for key, entry in self._elements.items():
            biz = str(entry.get("business_ref") or "").strip()
            if biz and "." in key:
                tech_form, tech_elem = key.split(".", 1)
                # Index both technical and sanitised form ref for flexible lookup
                for form_key in (tech_form, sanitize_ref(tech_form)):
                    self.elements_reverse[f"{form_key}.{biz}"] = (tech_form, tech_elem)

    # -- Resolution ------------------------------------------------------------

    def resolve_form(self, technical_ref: str) -> NameResult:
        """Resolve a technical ``form_ref`` to a business name.

        Resolution order:

        1. Alias table lookup (returns immediately on hit).
        2. Deterministic sanitiser (always succeeds; marks result for review).
        """
        if technical_ref in self._forms:
            entry = self._forms[technical_ref]
            return NameResult(
                ref=str(entry.get("business_ref") or technical_ref),
                confidence=float(entry.get("confidence", 1.0)),
                needs_alias_review=False,
                source="alias",
                original=technical_ref,
            )

        sanitized = sanitize_ref(technical_ref)
        still_technical = is_technical_form_ref(sanitized)
        return NameResult(
            ref=sanitized,
            confidence=0.5 if still_technical else 0.7,
            needs_alias_review=True,
            source="sanitized",
            original=technical_ref,
        )

    def resolve_element(
        self, technical_form_ref: str, technical_element_ref: str
    ) -> NameResult:
        """Resolve a technical ``element_ref`` scoped to its form.

        Resolution order:

        1. Qualified key ``<form_ref>.<element_ref>`` in the alias ``elements`` table.
        2. Unqualified ``<element_ref>`` key (surface-agnostic shorthand).
        3. Deterministic sanitiser.
        """
        qualified = f"{technical_form_ref}.{technical_element_ref}"
        for key in (qualified, technical_element_ref):
            if key in self._elements:
                entry = self._elements[key]
                return NameResult(
                    ref=str(entry.get("business_ref") or technical_element_ref),
                    confidence=float(entry.get("confidence", 1.0)),
                    needs_alias_review=False,
                    source="alias",
                    original=technical_element_ref,
                )

        sanitized = sanitize_ref(technical_element_ref)
        still_technical = is_technical_element_ref(sanitized)
        return NameResult(
            ref=sanitized,
            confidence=0.5 if still_technical else 0.7,
            needs_alias_review=still_technical,
            source="sanitized",
            original=technical_element_ref,
        )

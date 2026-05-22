"""
generator.script_validator -- Business-readability gate for generated replay scripts.

Validates that generated ``test_*.py`` files contain only business-readable
Oracle EBS identifiers — no Java/HTML surface prefixes, no keyboard-shortcut
suffixes, no raw Java descriptor dicts, and no Playwright locator calls.

Detected issues are split into two categories:

violations
    Hard errors that must be fixed before the script is trustworthy.
    ``ScriptValidationResult.passed`` is ``False`` when any violations are present.

alias_reviews
    Soft warnings: ``# alias_review: was '...'`` comments that the generator
    emits for names resolved only by the sanitiser (no alias entry).  These
    should be confirmed and added to the alias catalog, but do not block replay.

Violation types
---------------
technical_form_ref     oracle_replay.form('java_...')  or html_ prefix
technical_element_ref  .click('java_...')  etc.  with java_/html_ prefix
toolbar_ref            toolbar<digits> in any ref argument
alt_ref                _alt_[a-z] pattern in any ref argument
mnemonic_ref           _mnemonic_[a-z] pattern in any ref argument
locator_call           page.locator( detected — Playwright locator in generated step
java_descriptor        raw Java descriptor dict keys (form_id / element_id /
                       friendly_name) in generated step lines

Public API
----------
ScriptViolation         dataclass — one issue at a specific line.
ScriptValidationResult  dataclass — per-file result with violations + alias_reviews.
validate_generated_script(path)     Validate one .py file.
validate_generated_dir(dir_path)    Validate all test_*.py files under a directory.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Extract the ref argument from oracle_replay.form('...')
_FORM_REF_RE: re.Pattern[str] = re.compile(
    r"\boracle_replay\.form\(\s*['\"]([^'\"]+)['\"]\s*\)"
)

# Extract the first string argument from FormReplay method calls
# Covers: .set_text('ref', ...), .click('ref'), .press_key('ref'),
#         .assert_text('ref', ...), .assert_value('ref', ...)
_ELEM_REF_RE: re.Pattern[str] = re.compile(
    r"\.\s*(?:set_text|click|press_key|assert_text|assert_value)\s*\(\s*['\"]([^'\"]+)['\"]"
)

# Direct-line guards
_PAGE_LOCATOR_RE: re.Pattern[str] = re.compile(r"\bpage\.locator\s*\(")
_JAVA_DESCRIPTOR_RE: re.Pattern[str] = re.compile(
    r'"(?:form_id|element_id|friendly_name|target)"\s*:'
)

# Alias-review soft warning comment emitted by the generator
_ALIAS_REVIEW_RE: re.Pattern[str] = re.compile(r"#\s*alias_review\b", re.IGNORECASE)

# Technical-ref checkers  {violation_type: pattern}
_FORM_TECH_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("technical_form_ref",  re.compile(r"^java_")),
    ("technical_form_ref",  re.compile(r"^html_")),
    ("alt_ref",             re.compile(r"_alt_[a-z](?:_|$)")),
    ("mnemonic_ref",        re.compile(r"_mnemonic_[a-z](?:_|$)")),
    ("toolbar_ref",         re.compile(r"toolbar\d")),
]

_ELEM_TECH_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("technical_element_ref", re.compile(r"^java_")),
    ("technical_element_ref", re.compile(r"^html_")),
    ("alt_ref",               re.compile(r"_alt_[a-z](?:_|$)")),
    ("mnemonic_ref",          re.compile(r"_mnemonic_[a-z](?:_|$)")),
    ("toolbar_ref",           re.compile(r"toolbar\d")),
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ScriptViolation:
    """A single business-readability issue found in a generated script.

    Attributes
    ----------
    violation_type: Short category key (see module docstring).
    file_path:      Absolute path of the validated file.
    line_number:    1-based line number where the issue was found.
    line_text:      Full text of the offending line (stripped).
    detail:         Human-readable description including the offending ref.
    """

    violation_type: str
    file_path: Path
    line_number: int
    line_text: str
    detail: str

    def __str__(self) -> str:
        return (
            f"[{self.violation_type}] {self.file_path.name}:{self.line_number}"
            f" — {self.detail}"
        )


@dataclass
class ScriptValidationResult:
    """Validation outcome for a single generated test file.

    Attributes
    ----------
    file_path:      Absolute path of the validated file.
    violations:     Hard errors (any presence makes ``passed`` False).
    alias_reviews:  Soft warnings from ``# alias_review:`` comments.
    """

    file_path: Path
    violations: list[ScriptViolation] = field(default_factory=list)
    alias_reviews: list[ScriptViolation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True when no hard violations are present."""
        return len(self.violations) == 0

    def summary(self) -> str:
        """Return a compact, human-readable result summary."""
        status = "PASS" if self.passed else "FAIL"
        parts = [f"{status}  {self.file_path.name}"]
        if self.violations:
            parts.append(f"  {len(self.violations)} violation(s):")
            for v in self.violations:
                parts.append(f"    • {v}")
        if self.alias_reviews:
            parts.append(
                f"  {len(self.alias_reviews)} alias_review item(s) pending human confirmation:"
            )
            for r in self.alias_reviews:
                parts.append(f"    ⚑ line {r.line_number}: {r.line_text}")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Core validator
# ---------------------------------------------------------------------------


def validate_generated_script(path: Path | str) -> ScriptValidationResult:
    """Validate a single generated ``.py`` file for business readability.

    Reads *path* line by line and checks every ref argument and line pattern.
    Returns a :class:`ScriptValidationResult` with all issues collected —
    never raises on content problems.
    """
    path = Path(path)
    result = ScriptValidationResult(file_path=path)

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        result.violations.append(
            ScriptViolation(
                violation_type="unreadable_file",
                file_path=path,
                line_number=0,
                line_text="",
                detail=f"Could not read file: {exc}",
            )
        )
        return result

    for lineno, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()

        # ── Alias-review soft warning ──────────────────────────────────────
        if _ALIAS_REVIEW_RE.search(line):
            result.alias_reviews.append(
                ScriptViolation(
                    violation_type="alias_review",
                    file_path=path,
                    line_number=lineno,
                    line_text=line,
                    detail=f"Alias review pending (line {lineno}): {line}",
                )
            )

        # ── page.locator call guard ────────────────────────────────────────
        if _PAGE_LOCATOR_RE.search(line):
            result.violations.append(
                ScriptViolation(
                    violation_type="locator_call",
                    file_path=path,
                    line_number=lineno,
                    line_text=line,
                    detail=(
                        f"page.locator() call found at line {lineno} — "
                        "generated steps must use FormReplay DSL, not Playwright locators"
                    ),
                )
            )

        # ── Raw Java descriptor dict guard ────────────────────────────────
        if _JAVA_DESCRIPTOR_RE.search(line):
            result.violations.append(
                ScriptViolation(
                    violation_type="java_descriptor",
                    file_path=path,
                    line_number=lineno,
                    line_text=line,
                    detail=(
                        f"Java descriptor dict key at line {lineno} — "
                        "generated steps must use business alias refs, not raw Java descriptors"
                    ),
                )
            )

        # ── Form ref checks ───────────────────────────────────────────────
        for form_ref_match in _FORM_REF_RE.finditer(line):
            ref = form_ref_match.group(1)
            for vtype, pattern in _FORM_TECH_PATTERNS:
                if pattern.search(ref):
                    result.violations.append(
                        ScriptViolation(
                            violation_type=vtype,
                            file_path=path,
                            line_number=lineno,
                            line_text=line,
                            detail=(
                                f"Technical form_ref {ref!r} at line {lineno} — "
                                "add a business alias to config/aliases/"
                            ),
                        )
                    )
                    break  # one violation per ref is enough

        # ── Element ref checks ────────────────────────────────────────────
        for elem_ref_match in _ELEM_REF_RE.finditer(line):
            ref = elem_ref_match.group(1)
            for vtype, pattern in _ELEM_TECH_PATTERNS:
                if pattern.search(ref):
                    result.violations.append(
                        ScriptViolation(
                            violation_type=vtype,
                            file_path=path,
                            line_number=lineno,
                            line_text=line,
                            detail=(
                                f"Technical element_ref {ref!r} at line {lineno} — "
                                "add an element alias to the relevant config/aliases/<domain>/<form>.json"
                            ),
                        )
                    )
                    break  # one violation per ref is enough

    return result


def validate_generated_dir(dir_path: Path | str) -> list[ScriptValidationResult]:
    """Validate all ``test_*.py`` files found recursively under *dir_path*.

    Returns one :class:`ScriptValidationResult` per file.  The list is empty
    when the directory does not exist or contains no matching files.
    """
    dir_path = Path(dir_path)
    if not dir_path.exists():
        return []
    return [
        validate_generated_script(py_file)
        for py_file in sorted(dir_path.rglob("test_*.py"))
    ]

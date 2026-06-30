"""
generator.build_test -- Generate a pytest suite from a normalized recording manifest.

Produces in <out_dir>/:
    test_<name>.py    -- replay pytest file with lifecycle + business steps
    conftest.py       -- session fixtures: browser lifecycle and artifact dir
  README.txt        -- run command and originating recording steps
"""
from __future__ import annotations

import builtins
import keyword
import re
from pathlib import Path
from typing import Any

from qcs_manifest import MANIFEST_FILE_NAME, load_manifest, normalize_recording
from generator.naming import AliasResolver


# -- Name helpers --------------------------------------------------------------

def _snake(name: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    s = re.sub(r"[^a-z0-9_]", "_", s.lower())
    return re.sub(r"_+", "_", s).strip("_")


def _form_var_base(form_ref: str) -> str:
    slug = form_ref.removeprefix("java_").removeprefix("html_")
    return _snake(slug) if slug else "form"


def _unique_var(base: str, used: set[str]) -> str:
    candidate = base or "form"
    if not re.match(r"[a-zA-Z_]", candidate):
        candidate = f"form_{candidate}"
    if keyword.iskeyword(candidate) or candidate in dir(builtins):
        candidate = f"{candidate}_form"
    if candidate not in used:
        used.add(candidate)
        return candidate
    i = 2
    while f"{candidate}_{i}" in used:
        i += 1
    used.add(f"{candidate}_{i}")
    return f"{candidate}_{i}"


# -- Manifest loading ----------------------------------------------------------

def _resolve_manifest_path(recording_input: Path) -> tuple[Path, Path]:
    """Return (manifest_path, run_dir) from dir/jsonl/manifest input."""
    recording_input = Path(recording_input)

    if recording_input.is_dir():
        run_dir = recording_input
        manifest_path = run_dir / MANIFEST_FILE_NAME
        if not manifest_path.exists():
            manifest_path = normalize_recording(run_dir)
        return manifest_path, run_dir

    if not recording_input.exists():
        raise FileNotFoundError(f"Recording input not found: {recording_input}")

    suffix = recording_input.suffix.lower()
    if suffix == ".jsonl":
        manifest_path = normalize_recording(recording_input)
        return manifest_path, recording_input.parent

    if suffix == ".json":
        return recording_input, recording_input.parent

    raise ValueError(
        "Unsupported recording input. Expected run directory, .jsonl, or .json manifest: "
        f"{recording_input}"
    )


def _extract_lifecycle_params(
    steps: list[dict[str, Any]],
) -> tuple[str, str, str, str]:
    """Return (login_url, user_env, password_env, form_url) from manifest steps."""
    login_url = ""
    user_env = "EBS_USER"
    password_env = "EBS_PASSWORD"
    form_url = ""
    for step in steps:
        action = step.get("action", "")
        inp = step.get("input") or {}
        if action == "ebs_login" and not login_url:
            login_url = inp.get("url", "")
            user_env = inp.get("user_env", "EBS_USER")
            password_env = inp.get("password_env", "EBS_PASSWORD")
        elif action == "java_form_launch" and not form_url:
            form_url = inp.get("url", "")
    return login_url, user_env, password_env, form_url


# -- Code generation -----------------------------------------------------------

def generate_test(recording_input: Path, out_dir: Path, test_name: str) -> None:
    manifest_path, run_dir = _resolve_manifest_path(Path(recording_input))
    manifest = load_manifest(manifest_path)
    steps: list[dict[str, Any]] = manifest.get("steps") or []
    login_url, user_env, password_env, form_url = _extract_lifecycle_params(steps)

    has_java = any(s.get("surface") == "java_forms" for s in steps)
    has_html = any(s.get("surface") in ("browser", "system") for s in steps) or any(
        s.get("action") == "java_form_launch" for s in steps
    )

    # Marker list
    markers = ["pytest.mark.oracle", "pytest.mark.ebs", "pytest.mark.generated"]
    if has_html:
        markers.append("pytest.mark.browser")
    if has_java:
        markers.append("pytest.mark.java_forms")

    # Build test file
    lines = [
        "# AUTO-GENERATED -- do not edit the step section by hand.",
        "# Update the repository/generator source and rerun `qcs gen run` for lasting changes.",
        "# Source mode: manifest-native",
        "# Manifest steps are the generation source of truth.",
        "from __future__ import annotations",
        "",
        "import pytest",
        "from qcs_replay.script import OracleReplay",
        "",
        "# -- EBS session parameters (from recording) ----------------------------------",
        f"_EBS_LOGIN_URL = {login_url!r}",
        f"_EBS_USER_ENV = {user_env!r}",
        f"_EBS_PASSWORD_ENV = {password_env!r}",
        f"_INITIAL_FORM_URL = {form_url!r}",
        "",
        "pytestmark = [",
    ]
    for marker in markers:
        lines.append(f"    {marker},")
    lines += [
        "]",
        "",
        "",
        f"def test_{_snake(test_name)}(oracle_replay: OracleReplay):",
        f'    """Generated from: {run_dir.name}"""',
        "",
        "    oracle_replay.step('Open browser')",
        "    if _EBS_LOGIN_URL:",
        "        oracle_replay.login(",
        "            url=_EBS_LOGIN_URL,",
        "            user_env=_EBS_USER_ENV,",
        "            password_env=_EBS_PASSWORD_ENV,",
        "        )",
        "    if _INITIAL_FORM_URL:",
        "        oracle_replay.open_form(url=_INITIAL_FORM_URL)",
        "",
    ]

    form_vars: dict[str, str] = {}
    used_vars: set[str] = {"oracle_replay"}
    alias_resolver = AliasResolver()
    last_business_form_ref: str = ""  # inferred current form for press_key steps

    def _get_form_var(
        business_form_ref: str,
        *,
        technical_ref: str = "",
        needs_review: bool = False,
    ) -> str:
        """Return the Python variable name for business_form_ref, creating it on first use."""
        if business_form_ref not in form_vars:
            var = _unique_var(_form_var_base(business_form_ref), used_vars)
            form_vars[business_form_ref] = var
            comment = (
                f"  # alias_review: was {technical_ref!r}"
                if needs_review and technical_ref
                else ""
            )
            lines.append(f"    {var} = oracle_replay.form({business_form_ref!r}){comment}")
        return form_vars[business_form_ref]

    for step in steps:
        action = step.get("action", "")
        tech_form_ref: str = step.get("form_ref") or ""
        tech_element_ref: str | None = step.get("element_ref")
        inp = step.get("input") or {}

        # Lifecycle steps are emitted explicitly in the test file.
        if action in ("ebs_login", "java_form_launch"):
            continue

        # Resolve technical names → business names via aliases / sanitiser.
        if tech_form_ref:
            form_result = alias_resolver.resolve_form(tech_form_ref)
            business_form_ref = form_result.ref
            last_business_form_ref = business_form_ref
        else:
            form_result = None
            business_form_ref = ""

        if tech_element_ref and tech_form_ref:
            elem_result = alias_resolver.resolve_element(tech_form_ref, tech_element_ref)
            business_element_ref: str = elem_result.ref
        else:
            business_element_ref = tech_element_ref or ""

        needs_review = form_result.needs_alias_review if form_result else False

        if action == "java_send_text" and business_form_ref and business_element_ref:
            var = _get_form_var(
                business_form_ref, technical_ref=tech_form_ref, needs_review=needs_review
            )
            text = inp.get("text", "")
            lines.append(f"    {var}.set_text({business_element_ref!r}, {text!r})")

        elif action == "java_click" and business_form_ref and business_element_ref:
            var = _get_form_var(
                business_form_ref, technical_ref=tech_form_ref, needs_review=needs_review
            )
            lines.append(f"    {var}.click({business_element_ref!r})")

        elif action == "java_double_click" and business_form_ref and business_element_ref:
            var = _get_form_var(business_form_ref, technical_ref=tech_form_ref, needs_review=needs_review)
            lines.append(f"    {var}.double_click({business_element_ref!r})")

        elif action == "java_select_value" and business_form_ref and business_element_ref:
            var = _get_form_var(business_form_ref, technical_ref=tech_form_ref, needs_review=needs_review)
            value = inp.get("value", inp.get("text", ""))
            lines.append(f"    {var}.select_value({business_element_ref!r}, {value!r})")

        elif action == "java_set_check" and business_form_ref and business_element_ref:
            var = _get_form_var(business_form_ref, technical_ref=tech_form_ref, needs_review=needs_review)
            checked = bool(inp.get("checked", True))
            lines.append(f"    {var}.set_check({business_element_ref!r}, {checked!r})")

        elif action == "java_expand_tree" and business_form_ref and business_element_ref:
            var = _get_form_var(business_form_ref, technical_ref=tech_form_ref, needs_review=needs_review)
            lines.append(f"    {var}.expand_tree({business_element_ref!r})")

        elif action == "java_collapse_tree" and business_form_ref and business_element_ref:
            var = _get_form_var(business_form_ref, technical_ref=tech_form_ref, needs_review=needs_review)
            lines.append(f"    {var}.collapse_tree({business_element_ref!r})")

        elif action == "java_activate_tab" and business_form_ref and business_element_ref:
            var = _get_form_var(business_form_ref, technical_ref=tech_form_ref, needs_review=needs_review)
            tab_index = inp.get("tab_index")
            tab_title = inp.get("tab_title")
            args = []
            if tab_index is not None:
                args.append(f"tab_index={tab_index!r}")
            if tab_title is not None:
                args.append(f"tab_title={tab_title!r}")
            args_str = ", ".join(args)
            if args_str:
                lines.append(f"    {var}.activate_tab({business_element_ref!r}, {args_str})")
            else:
                lines.append(f"    {var}.activate_tab({business_element_ref!r})")

        elif action == "java_press_key":
            key = inp.get("key", "")
            # Use step's form_ref when available; otherwise infer from previous step.
            effective_form_ref = business_form_ref or last_business_form_ref
            effective_tech_ref = tech_form_ref  # may be empty for inferred steps
            if effective_form_ref:
                var = _get_form_var(
                    effective_form_ref,
                    technical_ref=effective_tech_ref,
                    needs_review=needs_review,
                )
                lines.append(f"    {var}.press_key({key!r})")
            else:
                # No form context for this press_key step — emit a reminder.
                lines.append(f"    # TODO: form-scoped press_key({key!r}) — add form_ref to manifest step")

        elif action == "assertion" and business_form_ref and business_element_ref:
            assertions = step.get("assertions") or []
            var = _get_form_var(
                business_form_ref, technical_ref=tech_form_ref, needs_review=needs_review
            )
            for a in assertions:
                if isinstance(a, dict):
                    if "expected_text" in a:
                        lines.append(
                            f"    {var}.assert_text({business_element_ref!r}, {a['expected_text']!r})"
                        )
                    elif "expected_value" in a:
                        lines.append(
                            f"    {var}.assert_value({business_element_ref!r}, {a['expected_value']!r})"
                        )

    lines += ["", ""]

    out_dir.mkdir(parents=True, exist_ok=True)
    test_file = out_dir / f"test_{_snake(test_name)}.py"
    test_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Test file: {test_file}")

    _write_conftest(out_dir, steps=steps, run_dir=run_dir, has_html=has_html, has_java=has_java)
    _write_readme(out_dir, test_name, run_dir)
    print(f"  Done -> {out_dir}")


# -- Conftest ------------------------------------------------------------------

def _write_conftest(
    out_dir: Path,
    *,
    steps: list[dict[str, Any]],
    run_dir: Path,
    has_html: bool,
    has_java: bool,
) -> None:
    lines = [
        "# AUTO-GENERATED conftest.py -- do not edit by hand.",
        "# Regenerate with: qcs gen run <recording_dir> <test_name>",
        "from __future__ import annotations",
        "",
        "import pytest",
    ]

    if has_html:
        lines += [
            "",
            "",
            "# -- Playwright / browser lifecycle -------------------------------------------",
            "@pytest.fixture(scope='session')",
            "def _browser_page():",
            '    """Session-scoped Playwright page. One browser instance for the entire suite."""',
            "    from playwright.sync_api import sync_playwright",
            "    with sync_playwright() as pw:",
            "        browser = pw.chromium.launch(headless=False)",
            "        ctx = browser.new_context(accept_downloads=True)",
            "        page = ctx.new_page()",
            "        yield page",
            "        browser.close()",
        ]

    lines += [
        "",
        "",
        "# -- Artifact directory -------------------------------------------------------",
        "@pytest.fixture(scope='session')",
        "def _artifact_dir(tmp_path_factory):",
        '    """Session-scoped directory for screenshots and run artefacts."""',
        "    return tmp_path_factory.mktemp('qcs_artifacts')",
    ]

    page_arg = "_browser_page" if has_html else "None"
    oracle_replay_args = "_browser_page, _artifact_dir, request" if has_html else "_artifact_dir, request"

    lines += [
        "",
        "",
        "# -- OracleReplay --------------------------------------------------------------",
        "@pytest.fixture",
        f"def oracle_replay({oracle_replay_args}):",
        '    """OracleReplay with browser/page and artifacts configured."""',
        "    from qcs_replay.script import OracleReplay",
        "    from qcs_replay.dsl import RepositoryResolver",
        "    resolver = RepositoryResolver()",
        "    _test_artifact_dir = _artifact_dir / request.node.name",
        f"    replay = OracleReplay(page={page_arg}, resolver=resolver,",
        "                          artifact_dir=_test_artifact_dir,",
        "                          test_name=request.node.name)",
        "    yield replay",
        "",
    ]

    (out_dir / "conftest.py").write_text("\n".join(lines), encoding="utf-8")
    print(f"  conftest.py: {out_dir / 'conftest.py'}")


# -- README --------------------------------------------------------------------

def _write_readme(out_dir: Path, test_name: str, run_dir: Path) -> None:
    lines = [
        f"Test: {test_name}",
        f"Generated from recording: {run_dir.name}",
        "",
        "Run command:",
        f"    pytest {out_dir.name} --html=reports/report.html",
        "",
    ]
    (out_dir / "README.txt").write_text("\n".join(lines), encoding="utf-8")

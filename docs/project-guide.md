# QCS Oracle Automation Project Guide

This is the single human/team handoff document for the current codebase. Copilot-specific rules live in `.github/copilot-instructions.md`.

## Mission

QCS turns natural-language Oracle EBS R12 test instructions into deterministic, repeatable pytest automation for mixed OAF/web and Java Forms UI.

AI is an authoring and repair assistant. It is allowed during recording and after deterministic replay failure only. Normal replay must be AI-free.

## Current Workflow

```text
instructions.txt
  -> qcs record instructions.txt --run-id <run_id> --auto-name
  -> deterministic EBS login
  -> oracle_form_open immediately after login
  -> JNLP download and Java Forms launch when needed
  -> screenshot-only AI recording
  -> local coordinate-to-Java-DOM mapping
  -> recordings/<run_id>/recording.jsonl and diagnostics
  -> recordings/<run_id>/recording.manifest.json (normalized + validated)
  -> actioned-element repository updates through qcs_repo.store
  -> automatic deterministic generation
  -> generated_tests/<run_id>/test_<run_id>.py
  -> pytest replay without AI
```

`qcs gen run` still exists for manual regeneration after reviewed repository edits. A normal `qcs record` run generates replay code immediately and prints the script path.

## Local Commands

Record:

```powershell
python -m qcs record instructions.txt --run-id rec_014 --auto-name
```

Replay:

```powershell
python -m pytest generated_tests\rec_014 -q -s
```

Manual regeneration:

```powershell
python -m qcs gen run recordings\rec_014 rec_014 --out generated_tests\rec_014
```

Manual regeneration from direct files (compatibility):

```powershell
python -m qcs gen run recordings\rec_014\recording.jsonl rec_014 --out generated_tests\rec_014
python -m qcs gen run recordings\rec_014\recording.manifest.json rec_014 --out generated_tests\rec_014
```

Normalize recording log to manifest:

```powershell
python -m qcs normalize recordings\rec_014 --out recordings\rec_014\recording.manifest.json
```

Useful validation:

```powershell
python -m compileall qcs oracle_ai_agent qcs_java_agent qcs_repo qcs_replay generator
python test_local.py
python -m qcs record --help
python -m qcs gen run recordings\rec_013 rec_013_replay --out generated_tests\rec_013_replay
python -m pytest generated_tests\rec_013_replay -q -s

# Alias catalog
python -m qcs aliases validate
python -m qcs aliases report

# Business-readability gate
python -m qcs gen validate generated_tests\rec_013_replay

# Repository validation
python -m qcs repo validate
```

For Java-agent changes:

```powershell
& 'C:\apache-maven-3.9.15\bin\mvn.cmd' -f java-agent\pom.xml -DskipTests package
```

## Code Map

```text
qcs/                         CLI: record, gen run, gen validate, aliases, repo, pages, flows, center, agent
oracle_ai_agent/             AI recorder orchestration and screenshot-only recording
oracle_ai_agent/cu_system_prompt.txt
                             Stable computer-use system prompt
java-agent/                  Java Attach API agent loaded into Oracle Forms JVM
qcs_java_agent/              Python client for Java agent commands, readiness, snapshots
qcs_repo/                    Repository storage, identity, naming, fingerprints, schema
qcs_replay/                  Deterministic replay runtime and failure-only healing
qcs_replay/script.py         User-facing OracleReplay DSL for generated tests
qcs_replay/dsl.py            RepositoryResolver, FormReplay, ReplayBackend, ReplayLogger
qcs_replay/failure_bundle.py FailureBundle + BundleWriter; per-step failure diagnostics
generator/                   Deterministic generation of pages, flows, and pytest
generator/naming.py          sanitize_ref(), AliasResolver, NameResult; business naming layer
generator/alias_catalog.py   AliasCatalog, FormAliasFile, ElementAlias; catalog loader + validator
generator/script_validator.py ScriptViolation, validate_generated_script/dir; readability gate
config/aliases/              Alias catalog directory tree: <domain>/<form_ref>.json
config/aliases/order_management/  Order management form aliases
config/aliases/purchasing/   Purchasing form aliases
config/aliases/common/       Shared dialog aliases
qcs_center/                  Early FastAPI control-plane prototype
qcs_agent/                   Early Windows worker prototype
repo/                        Local repository data: repo.db, YAML fallback, flows
recordings/                  Per-run recordings and diagnostics
generated_tests/             Generated pytest suites
pages/                       Generated page objects
flows_gen/                   Generated flows
reports/                     Pytest reports
```

## Repository Model

- Use `qcs_repo.store` as the object repository API.
- Local catalog data lives in `repo/repo.db`.
- YAML under `repo/` remains the review/export/fallback format for forms, elements, flows, fingerprints, and patches.
- Normal recording stores only actioned elements.
- Repeated recordings should update matching actioned elements idempotently rather than duplicate them.

Important identity terms:

- `form_id`: stable screen or dialog id.
- `semantic_ref`: stable user-facing element reference scoped to a form.
- `element_uid`: deterministic internal identity from Java/Web metadata.
- `locator_candidates`: ranked deterministic locator strategies.

## Generated Script Contract

Generated tests should be readable business scripts using `qcs_replay.script.OracleReplay`. Each action uses the **FormReplay DSL**:

- `oracle_replay.form('form_ref')` — returns a `FormReplay` handle for the named form or page. Assign to a variable and reuse it for all steps on that form.
- `var.set_text('element_ref', 'value')` — type a value into a field.
- `var.click('element_ref')` — click a control.
- `var.press_key('key')` — send a key to the form.
- `var.assert_text('element_ref', 'expected')` / `var.assert_value(...)` — assert output.
- `oracle_replay.open_form(url=...)` — navigate to the Oracle Forms JNLP URL.
- `oracle_replay.login(url=..., user_env=..., password_env=...)` — EBS login.

`form_ref` is the repository form id (e.g. `java_find_orders`). `element_ref` is the element id within that form. Both are semantic repository names, not raw Java-agent paths, Playwright selectors, or form descriptors.

**Do not use:**
- `oracle_replay.click(ref)` / `oracle_replay.set_text(ref, val)` — flat-ref methods; removed.
- `.textbox('Label').set('value')` — old JavaFormReplay chain; removed from `form()`.
- Raw `page.locator(...)` calls or Java descriptor dictionaries in the step section.

Example generated script (business-readable, after alias resolution):

```python
from qcs_replay.script import OracleReplay


def test_update_po_number(oracle_replay: OracleReplay):
    oracle_replay.login(url="https://.../AppsLocalLogin.jsp")
    oracle_replay.open_form(url="https://.../RF.jsp?...")

    order_type_lov = oracle_replay.form("order_type_lov")
    order_type_lov.set_text("order_type", "CREDIT_ICO_TDS")
    order_type_lov.click("find")

    confirm_dialog = oracle_replay.form("confirm_dialog")
    confirm_dialog.click("yes")

    order_summary = oracle_replay.form("order_summary")
    order_summary.click("open")

    purchase_order = oracle_replay.form("purchase_order")
    purchase_order.set_text("po_number", "KRISHAN001")
```

Technical Java Forms IDs (`java_*`, `_alt_*`, `_mnemonic_*`, `toolbar\d`) must not appear in generated `form_ref` or `element_ref` arguments. The generator applies the alias catalog (`config/aliases/`) first, then the deterministic sanitiser as fallback. Low-confidence sanitised names are flagged with `# alias_review: was '...'` comments and reported by `qcs gen validate`.

Do not hand-edit generated tests, generated page objects, or generated flows for lasting fixes. Change `generator/`, `qcs_replay.script`, or repository metadata instead.

## Runtime Rules

- Use Playwright for browser/OAF pages.
- Use `java-agent/` through `qcs_java_agent` for Oracle Forms extraction and replay.
- Do not use screenshot clicking for normal Java Forms execution.
- After EBS login, call `oracle_form_open` immediately. Do not navigate responsibilities or menus first.
- If `oracle_form_open` launches Forms or downloads JNLP, call `java_form_launch` immediately before Java Forms interaction.
- AI sees screenshots during recording; Java DOM stays local.
- Normal replay must not call an LLM.
- Never put credentials, Azure keys, or other secrets in prompts, logs, DB rows, recordings, generated tests, docs, or reports.

## Latest Validated State

- Phase 4 complete: `form_ref` + `element_ref` are the canonical manifest step fields. `target_ref` removed everywhere.
- Business naming layer complete: `generator/naming.py` provides `sanitize_ref()` + `AliasResolver`. Generator strips `java_*`, `_alt_*`, `_mnemonic_*`, `toolbar\d` technical noise and resolves names through `config/aliases/`.
- Alias catalog complete: `generator/alias_catalog.py` + `config/aliases/<domain>/<form_ref>.json`. CLI: `qcs aliases validate` / `qcs aliases report`. All rec_013 forms covered across three domains.
- Failure bundle diagnostics: `qcs_replay/failure_bundle.py` — `FailureBundle` + `BundleWriter` write structured JSON + screenshots + Java snapshots on step failure. `OracleReplay` wires `BundleWriter` automatically when `artifact_dir` is provided.
- Business-readability gate: `generator/script_validator.py` — `validate_generated_script()` and `validate_generated_dir()` detect `java_*` refs, `toolbar\d`, `_alt_*`, `_mnemonic_*`, `page.locator()`, and raw Java descriptor dicts. CLI: `qcs gen validate <dir>`.
- `qcs gen` is now a subcommand group: `qcs gen run <recording_path> <test_name>` and `qcs gen validate <dir>`.
- `generated_tests/rec_013_replay` regenerated with business names: `order_type_lov`, `confirm_dialog`, `find_orders`, `order_summary`, `purchase_order`, `po_date_lov`. Press-key steps are form-scoped.
- `qcs_repo.schema` + `qcs_repo.store`: `RepoEntry` dataclass, `validate_entry`, `RepoValidationError`, `validate_repo`, `qcs repo validate` CLI.
- `RepositoryResolver.resolve` raises `ReplayRefNotFoundError` on miss; no silent fallback.
- `python -m pytest tests/ -q` passes (183 tests).

## Known Gap To Fix Next

Recorder semantic disambiguation still needs work. In `rec_013`, the PO step element was recorded as `po_received`; the intended target was `PO#`. Improve coordinate-to-element label scoring so fresh recordings emit the correct business field. This also feeds alias catalog quality — better recorder output reduces `alias_review` items in generated scripts.

## Development Priorities

1. Improve recorder semantic disambiguation for fields like `PO#`.
2. Harden assertion generation so generated tests never contain undefined refs or variables.
3. Grow the alias catalog as new Oracle EBS flows are recorded — one JSON file per business form.
4. Expand `qcs_center` and `qcs_agent` from record-job prototype to record/generate/replay/artifact jobs.
5. Add Azure Test Plans integration around manifests, pytest/JUnit output, and run summaries.

## MVP Validation Checklist

1. `qcs record` completes and writes `recordings/<run_id>/recording.jsonl`.
2. Normalized manifest exists and validates (`recordings/<run_id>/recording.manifest.json`).
3. Diagnostics are created under `recordings/<run_id>/diagnostics/`.
4. The recording includes deterministic login, immediate `oracle_form_open`, Java Forms launch/readiness, and Java Forms actions.
5. Repository updates include only actioned elements.
6. `qcs record` prints the generated script path.
7. Generated pytest imports cleanly and uses `OracleReplay`.
8. Replay passes without AI in the normal path — confirmed by `test_replay_dsl_module_does_not_import_ai_modules` and no flat-ref methods remaining in `OracleReplay`.
9. No secrets appear in recordings, diagnostics, generated tests, reports, or docs.
10. Generated scripts expose only business-readable `form_ref` and `element_ref` names. No `java_*` prefixes, `_alt_*` shortcuts, `_mnemonic_*` patterns, `toolbar\d` IDs, Playwright selectors, or Java descriptor dicts appear in the step section.
11. `qcs gen validate generated_tests\<suite>` exits 0 (business-readability gate passes).
12. `qcs aliases validate` exits 0 (alias catalog has no errors).

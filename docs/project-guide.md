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
  -> actioned-element repository updates through qcs_repo.store
  -> automatic deterministic generation
  -> generated_tests/<run_id>/test_<run_id>.py
  -> pytest replay without AI
```

`qcs gen` still exists for manual regeneration after reviewed repository edits. A normal `qcs record` run generates replay code immediately and prints the script path.

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
python -m qcs gen recordings\rec_014 rec_014 --out generated_tests\rec_014
```

Useful validation:

```powershell
python -m compileall qcs oracle_ai_agent qcs_java_agent qcs_repo qcs_replay generator
python test_local.py
python -m qcs record --help
python -m qcs gen recordings\rec_013 rec_013_replay --out generated_tests\rec_013_replay
python -m pytest generated_tests\rec_013_replay -q -s
```

For Java-agent changes:

```powershell
& 'C:\apache-maven-3.9.15\bin\mvn.cmd' -f java-agent\pom.xml -DskipTests package
```

## Code Map

```text
qcs/                    CLI: record, gen, pages, flows, center, agent
oracle_ai_agent/        AI recorder orchestration and screenshot-only recording
oracle_ai_agent/cu_system_prompt.txt
                        Stable computer-use system prompt
java-agent/             Java Attach API agent loaded into Oracle Forms JVM
qcs_java_agent/         Python client for Java agent commands, readiness, snapshots
qcs_repo/               Repository storage, identity, naming, fingerprints
qcs_replay/             Deterministic replay runtime and failure-only healing
qcs_replay/script.py    User-facing OracleReplay DSL for generated tests
generator/              Deterministic generation of pages, flows, and pytest
qcs_center/             Early FastAPI control-plane prototype
qcs_agent/              Early Windows worker prototype
repo/                   Local repository data: repo.db, YAML fallback, flows
recordings/             Per-run recordings and diagnostics
generated_tests/        Generated pytest suites
pages/                  Generated page objects
flows_gen/              Generated flows
reports/                Pytest reports
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

Generated tests should be readable, UFT-like business scripts using `qcs_replay.script.OracleReplay`. Each Oracle Forms action line should use `oracle.form("Form Name").textbox("Textbox Name").set("value")` or the equivalent `button`, `menu`, `toolbar`, or `press_key` call. Technical Java refs, form ids, and object repository wiring belong outside the visible step section:

The form name for each action comes from the repository form that owns the resolved control descriptor. Recorded target ids are treated as hints because they can be too granular or transient.

```python
from qcs_replay.script import OracleReplay


def test_update_po_number(oracle: OracleReplay):
    oracle.login(url="https://.../AppsLocalLogin.jsp")
    oracle.open_form(url="https://.../RF.jsp?...")

    oracle.form("Order Type List Of Values").textbox("Order Type").set("CREDIT_ICO_TDS")
    oracle.form("Order Type List Of Values").button("Find").click()
    oracle.form("Summary Tab Page Order Number").button("Open").click()
    oracle.form("PO #").textbox("PO#").set("KRISHAN001")
```

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

- `generated_tests/rec_013_replay` was regenerated to use `OracleReplay` readable script style.
- `python -m pytest generated_tests\rec_013_replay -q -s` passed end to end.
- `python test_local.py` passed `62/62`.
- Java agent was rebuilt successfully with Maven after attach-thread focus fixes.
- `python -m qcs record --help` shows automatic generation options such as `--test-name` and `--out`.

## Known Gap To Fix Next

Recorder semantic disambiguation still needs work. In `rec_013`, replay passed technically, but the PO step was recorded as `PO Received`; the intended target was `PO#`. Improve coordinate-to-element and label scoring so fresh recordings emit the correct business field.

## Development Priorities

1. Improve recorder semantic disambiguation for fields like `PO#`.
2. Add a normalized `test_manifest` between `recording.jsonl` and generated pytest.
3. Wrap generated steps with explicit deterministic failure and healing boundaries.
4. Harden assertion generation so generated tests never contain undefined refs or variables.
5. Expand `qcs_center` and `qcs_agent` from record-job prototype to record/generate/replay/artifact jobs.
6. Add Azure Test Plans integration around manifests, pytest/JUnit output, and run summaries.

## MVP Validation Checklist

1. `qcs record` completes and writes `recordings/<run_id>/recording.jsonl`.
2. Diagnostics are created under `recordings/<run_id>/diagnostics/`.
3. The recording includes deterministic login, immediate `oracle_form_open`, Java Forms launch/readiness, and Java Forms actions.
4. Repository updates include only actioned elements.
5. `qcs record` prints the generated script path.
6. Generated pytest imports cleanly and uses `OracleReplay`.
7. Replay passes without AI in the normal path.
8. No secrets appear in recordings, diagnostics, generated tests, reports, or docs.

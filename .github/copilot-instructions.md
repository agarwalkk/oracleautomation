# QCS Oracle Automation Agent Instructions

For the full human/team guide, read `docs/project-guide.md`. Keep this file focused on rules that should apply to Copilot every time.

## Mission
Build an application that turns natural-language Oracle EBS R12 test instructions into deterministic, repeatable pytest automation for mixed OAF/web and Java Forms UI.

## Non-Negotiables
- AI is allowed during recording and failure healing only. Normal replay must be AI-free.
- Use Playwright for browser and OAF pages.
- Use the local Java Forms agent in `java-agent/` through `qcs_java_agent` for Oracle Forms extraction and replay. Do not use screenshot clicking for normal execution.
- After Oracle EBS login is successful, call `oracle_form_open` immediately — it handles responsibility selection and function navigation internally. Do not navigate to menus or responsibilities first. If `oracle_form_open` results in a JNLP download or Forms launch, call `java_form_launch` immediately before any Java Forms interaction.
- Keep pytest as the supported execution framework for generated tests.
- Treat `qcs_repo.store` as the object repository API. Local catalog data is in `repo/repo.db`; YAML under `repo/` remains the review/export/fallback format for forms, elements, flows, fingerprints, and patches.
- Never auto-merge AI-recorded or self-healed locator changes. Produce patches or PRs for review.
- Never put EBS credentials, Azure keys, or other secrets in prompts, logs, DB rows, generated tests, recordings, or docs.

## Current Architecture Facts
- `qcs/__main__.py` owns CLI commands. `qcs record` records and then runs deterministic generation immediately.
- `oracle_ai_agent` owns the AI recording loop.
- Recording is screenshot-only from the AI perspective: AI gets screenshots and returns actions; Java DOM stays local and is used only for coordinate mapping and deterministic descriptors.
- Normal recording stores only actioned elements in the repository.
- `qcs record` runs deterministic code generation immediately after recording and prints the generated replay script path.
- Playwright MCP runs through `NpxStdioTransport`; no Playwright HTTP daemon is required by default.
- Java Forms agent tools run through the local Java Attach API command protocol in `qcs_java_agent`.
- The old standalone `oracle_mcp_server` was removed. Do not reintroduce it unless there is a strong architectural reason.
- `generator` converts recordings into readable pytest suites deterministically; generated tests use `qcs_replay.script.OracleReplay`.
- `qcs_replay` performs deterministic runtime actions and owns failure-only self-healing.
- `qcs_center` and `qcs_agent` are early distributed execution prototypes that should evolve into the management/control plane and Windows worker fleet.

## Recorder And Repository Rules
- Recording follows deterministic login, immediate `oracle_form_open`, immediate `java_form_launch` when Forms starts, then screenshot-only AI-guided step execution.
- After each successful Java Forms action, capture active form state with the local Java Forms agent and match the interacted target locally against the structured DOM inventory.
- Store elements by surface and form/page. Do not mix elements from different active forms, dialogs, or web pages.
- Repository updates from recording or healing must be reviewable additions or patches. Avoid destructive overwrites.
- Recording should fail fast when a typed value is not accepted, a LOV returns no match, the wrong form opens, or a required field/action cannot be verified.
- Unexpected popups are not automatically test failures. Known popups should become deterministic handlers; unknown popups should be captured and stop with a clear failure reason.

## Replay Rules
- Generated pytest and manifests should refer to semantic names, not raw Java-agent paths or browser selectors.
- Generated scripts should be UFT-like: each Forms window/dialog/page is a named replay object, and controls are invoked from that object. Do not hang every step off the initially opened form object.
- Each generated Forms action line should use the chained format `oracle.form('Form Name').textbox('Textbox Name').set('value')` or the equivalent `button/menu/toolbar/press_key` call. Do not emit temporary form variables or visible object-repository parameters for normal action lines.
- The form name in each generated action must come from the repository form that owns the resolved control descriptor. Do not blindly use the transient `form_id` captured in `recording.jsonl` if repo resolution maps the control to a different form.
- Generated scripts should launch the initial Oracle Forms URL with `oracle.open_form(url=...)`; do not pass instruction/search text such as a form name into the visible launch call.
- Replay must resolve semantic names through `qcs_repo` descriptors using `qcs_java_agent` or Playwright deterministic resolvers.
- Healing may propose repository patches, alternate descriptors, or popup handling, but must not silently merge them.

## Product Direction
- Add a normalized test manifest between `recording.jsonl` and generated pytest. Use it for codegen, UI display, Azure Test Plans sync, and run analytics.
- Generated pytest steps must resolve structured descriptors through `qcs_java_agent`/Playwright, perform deterministic actions, verify outcomes, and call `HealingEngine.run_with_healing` only after deterministic failure.
- Improve recorder semantic disambiguation so fields like `PO#` are mapped to the intended business field rather than nearby labels.
- Azure Test Plans integration should be an adapter around manifests, pytest/JUnit output, and run summaries. Do not replace pytest with a custom runner.
- Build the management UI over `qcs_center`, not inside generated tests.

## Development Rules
- Prefer existing package boundaries: recorder in `oracle_ai_agent`, repository logic in `qcs_repo`, code generation in `generator`, replay in `qcs_replay`, orchestration in `qcs_center` and `qcs_agent`.
- Do not edit generated tests, page objects, or flow files by hand for lasting changes. Update the generator or repository source instead.
- Preserve local CLI workflows while adding remote center/agent behavior.
- When touching generator, replay, healing, center, or agent behavior, add or update tests where practical.
- Validate generated tests by importing/running pytest and checking that no undefined variables are emitted.
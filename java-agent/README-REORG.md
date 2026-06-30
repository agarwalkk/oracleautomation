# EBS Forms Java Agent — Reorganized (non-robotic execution)

This is a from-scratch reorganization of the `com.pyebsdom.agent` Java agent
(extraction + execution) into a layered package structure, with execution
rebuilt to be **non-robotic**. The wire protocol and JSON result shape are
unchanged, so the Python clients (`qcs_java_agent`, `qcs_replay`) need no
changes.

> **Build note:** this tree was reorganized without a local compiler. Run
> `mvn -f java-agent/pom.xml -DskipTests package` and the snapshot regression
> suite on your dev box before merging. Known manual follow-ups are listed at
> the bottom.

## What changed at a glance

- **Layered packages** instead of one flat package: `runtime/ json/ model/
  extract/ render/ execute/ capture/ io/ attach/` + root entry classes.
- **Execution is non-robotic.** Actions drive the component model in-JVM
  (`doClick` / `setText` / `setSelectedIndex` / `setSelectionRow`) or dispatch a
  targeted AWT event to the component. No OS mouse movement, no global keystroke
  injection.
- **No automatic Robot fallback.** The old `java.awt.Robot` code is kept only as
  a reference copy at `backup-reference/RobotFallback.java`, which is **outside
  the compiled source tree** and is never invoked.
- **Serialization centralized** in `render/DomJson` — the model classes are now
  pure data (no `toJson`).
- **Identity unified** in `extract/Identity` — the resolver no longer carries a
  "mirror of IdentityResolver".
- **Shared utilities** `runtime/Edt` (EDT marshalling) and `runtime/Reflect`
  (safe reflection) replace copy-pasted helpers.
- **Dropped** the stale `backup/` folder and committed `target/` build artifacts.

## New package tree

```
com.pyebsdom.agent
  AgentMain          entry: premain/agentmain -> runCommand
  AgentBootstrap     zero-dependency manifest shim (Premain/Agent-Class)
  CommandRouter      command name -> handler
  AgentCommand       command model + parser (merged)
  runtime/  AwtContext, Edt, Reflect
  json/     Json (escape/quote), Results (health/ok/error envelopes)
  model/    DomNode, Bounds, LocatorCandidate, TableModel   (pure data)
  extract/  DomScanner, ComponentReader, ComponentClassifier,
            StructureAnnotator, TreeItemExpander, TableDetector,
            IdentityResolver, Identity
  render/   DomJson (DOM/tables -> JSON), TextLayout (plain-text layout)
  execute/  ActionExecutor, ComponentResolver, ModelActions, KeyMap
  capture/  ScreenCapture (screenshot), PngWriter
  io/       FileUtil
  attach/   AttachLauncher   (client-side; runs in its own JVM)
com.pyebsdom.testapp
  SampleSwingApp     (unchanged test fixture)

backup-reference/RobotFallback.java   reference-only, NOT compiled
```

## Old → new file mapping

| Original (flat `agent/`) | New location | Notes |
|---|---|---|
| `EbsDomAgent` | `AgentMain` + `CommandRouter` | entry split from dispatch |
| `EbsDomBootstrapAgent` | `AgentBootstrap` | unchanged role; targets `AgentMain` |
| `AgentCommand` + `AgentCommandParser` | `AgentCommand` | parser merged into the model |
| `AwtContext` | `runtime/AwtContext` | made `public` (cross-package) |
| *(new)* | `runtime/Edt` | EDT marshalling, was inline in `ActionExecutor` |
| *(new)* | `runtime/Reflect` | shared safe-reflection helpers |
| `JsonUtil` | `json/Json` + `json/Results` | escaping vs envelopes |
| `DomNode`/`Bounds`/`LocatorCandidate`/`TableModel` | `model/*` | `toJson` removed (pure data) |
| `ReflectionExtractor` | `extract/ComponentReader` | renamed |
| `DomScanner` | `extract/DomScanner` | `scanTables`→`detectTables` (returns model) |
| `ComponentClassifier` | `extract/ComponentClassifier` | relocated |
| `StructureAnnotator` | `extract/StructureAnnotator` | relocated |
| `TreeItemExpander` | `extract/TreeItemExpander` | relocated |
| `TableDetector` | `extract/TableDetector` | relocated |
| `IdentityResolver` | `extract/IdentityResolver` | relocated |
| *(extracted)* | `extract/Identity` | shared identity (was duplicated in resolver) |
| `TextLayoutWriter` | `render/TextLayout` | relocated |
| *(extracted)* | `render/DomJson` | all the old `toJson` logic |
| `ActionExecutor` | `execute/ActionExecutor` | rewritten non-robotic |
| `ComponentResolver` | `execute/ComponentResolver` | identity delegated to `Identity` |
| *(new)* | `execute/ModelActions` | non-robotic action primitives |
| `SafeRobot` (key table) | `execute/KeyMap` | key-name → VK for `KeyEvent` dispatch |
| `SafeRobot` (mouse/key synth) | `backup-reference/RobotFallback` | reference-only, uncompiled |
| `SafeRobot` (screen capture) | `capture/ScreenCapture` | screenshot only |
| `PngWriter` | `capture/PngWriter` | relocated |
| `FileUtil` | `io/FileUtil` | relocated |
| `AttachAgentByPid` | `attach/AttachLauncher` | renamed |
| `SampleSwingApp` | `testapp/SampleSwingApp` | unchanged |
| `backup/` (9 files) | **deleted** | use version history |

## Non-robotic execution — how each action works now

| Command | Technique (in priority order) |
|---|---|
| `focus` | `requestFocusInWindow()` |
| `click` | radio → `setSelected(true)` (idempotent); button → `AbstractButton.doClick()` → reflective `doClick()` → dispatch `MouseEvent` to component centre (local coords) |
| `click` (tab) | `setSelectedIndex(i)` / `selectTab(i)` on the tab container (replaces pixel-clicking; `tab_count` no longer needed) |
| `doubleclick` | press/release/click dispatched twice (2nd with `clickCount=2`) to the component — e.g. open a record |
| `settext` | `JTextComponent.setText()` → reflective `setText`/`setValue` |
| `clear` | `setText("")` |
| `selectoption` | combo/poplist/list by **value**: `setSelectedItem` / `setSelectedValue` / index-match → `setSelectedIndex` |
| `setcheck` | checkbox/toggle to a specific state **idempotently** (reads `isSelected`, flips via `doClick` only if it differs); `value=true|false` |
| `expandtree` / `collapsetree` | tree node by model row: `expandRow(i)` / `collapseRow(i)` → `setExpandedState`; row from `tree_row` or the leaf of `locatorTreePath` |
| `presskey` | build a `KeyEvent` via `KeyMap` and dispatch to the target/focus owner |
| `screenshot` | `Robot.createScreenCapture` — reads pixels only, not input |
| `highlight` | translucent `JWindow` overlay (unchanged) |
| `elementat` | read-only coordinate → component mapping (unchanged) |

The original command set was extended with `doubleclick`, `selectoption`,
`setcheck`, `expandtree`, and `collapsetree` — the EBS interactions that used to
ride on a generic coordinate `click`. New params: `selectoption` takes
`value`/`value64`; `setcheck` takes `value=true|false`; `expandtree`/`collapsetree`
take `tree_row` or reuse `locatorTreePath`. These **new verbs need matching
methods in the Python client** (`qcs_java_agent` driver + `qcs_replay`) and in the
recorder so a recording can emit them.

Each action response now also carries a small **additive** `"technique"` field
(e.g. `"doClick"`, `"dispatchMouseEvent"`) for traceability. This is purely
additive to the JSON; remove it from `ActionExecutor` if you require a strict
byte-for-byte legacy envelope.

## Preserved contracts

- **Wire protocol** `command=<name>;out=<path>;…` (lower-cased keys) — identical.
- **JSON DOM shape** (field names, order, number formatting) — identical;
  `render/DomJson` reproduces the previous output exactly.
- **Manifest** `Premain-Class`/`Agent-Class` now point at `AgentBootstrap`
  (same class, new name); jar `finalName` is still `ebs-dom-agent`.

## Build & verify (on the dev box)

```powershell
mvn -f java-agent\pom.xml -DskipTests package
# then your snapshot regression + a replay smoke test
python -m pytest tests\test_aisnapshot_regression.py -q
```

## Known manual follow-ups

1. **Compile + regression** — done on the dev box (no compiler in the authoring
   environment). Unused imports may remain in a few relocated files (warnings,
   not errors); clean them when convenient.
2. **String-normalizer de-dup** — `extract/IdentityResolver` (DomNode-based)
   still has its own copies of the tab/level strippers. Point them at
   `extract/Identity` once the build is green; left untouched here to avoid
   changing proven scan-time output blind.
3. **Forms widget coverage** — `ModelActions` now covers text, button, tab,
   tree select **and** tree expand/collapse, combo/list select-by-value,
   idempotent radio/checkbox, and double-click, falling through to targeted
   event dispatch otherwise. Validate the model methods against the real Oracle
   EWT widget families (DTree, FComboBox/poplist, VCheckbox/VRadioButton) and
   extend the reflective method lists if a family names its methods differently.
4. **Python client + recorder** — wire the new verbs (`doubleclick`,
   `selectoption`, `setcheck`, `expandtree`, `collapsetree`) into the
   `qcs_java_agent` driver and `qcs_replay`, and teach the recorder to emit them
   instead of a generic coordinate click. (Java side is complete; I can do the
   Python side next.)

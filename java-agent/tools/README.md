# Java-agent-only DOM extractor

Capture the Oracle Forms DOM directly from the live JVM — no Python qcs stack,
no recorder, no AI. Just the agent jar attaching to the running Forms process.

## Prerequisites

1. **Build the jar** (once):
   ```
   mvn -f java-agent\pom.xml -DskipTests package
   ```
   produces `java-agent\target\ebs-dom-agent.jar`.
2. **A JDK** (not a JRE) — the Attach API needs `tools.jar` (Java 8) or the
   `jdk.attach` module (Java 9+). Set `JAVA_HOME` to it, or pass `-JavaHome`.
3. **Architecture must match** the Forms JVM. The EBS Forms client is often a
   **32-bit** JVM (`jp2launcher.exe`) — attach with a 32-bit JDK, or the attach
   fails with an architecture-mismatch error.
4. **Same OS user** as the Forms process (Attach API requirement). If the Forms
   client runs elevated, run the dumper from an elevated shell too.
5. The Forms screen **open and settled** (no spinner) on the screen you want.

## Run it

PowerShell (recommended on Windows):
```powershell
cd java-agent\tools
.\dump-dom.ps1                       # auto-detect the Forms JVM
.\dump-dom.ps1 -TargetPid 13728 -Screenshot
.\dump-dom.ps1 -Match oracle.forms   # if auto-detect picks the wrong JVM
```

Python (cross-platform):
```bash
python dump_dom.py
python dump_dom.py --pid 13728 --shot
```

Finding the PID manually: Task Manager → Details → the `javaw.exe` /
`jp2launcher.exe` hosting Forms; pass it as `-TargetPid` / `--pid`.

## Output

A timestamped folder `dom-dumps\<yyyyMMdd_HHmmss>\` containing:

| File | Command | What it is |
|---|---|---|
| `health.json` | `health` | agent loaded OK (sanity) |
| `scan.json` | `scan` | visible component tree, **schema-2.0** (the main artifact) |
| `raw.json` | `raw` | full tree incl. invisible nodes (shows what `scan` filters) |
| `layout.txt` | `layout` | human-readable tree (easiest first read) |
| `tables.json` | `tables` | detected grids/tables |
| `screenshot.png` | `screenshot` | optional, for visual correlation |

## What to capture for the hardening review

Pick **one rich screen** that exercises the widget families, so the review
covers them in a single pass — ideally a screen with:

- a **multi-record grid / table** (rows + columns),
- a **navigator tree** (expandable nodes),
- **tabs / tab pages**,
- a field with an **LOV** (the `...` / Ctrl+L list of values),
- a **checkbox** and a **radio group**,
- a **poplist / combo**.

The "Find Orders/Quotes" or an Order Organizer screen is a good candidate.

## Sending it back

Zip the `dom-dumps\<timestamp>` folder and upload it here. At minimum I need
`scan.json` + `layout.txt`; `raw.json` and `tables.json` make the review
sharper (they show what's filtered and how grids are detected). I'll review the
fresh schema-2.0 output for **missing aspects** (state, hints, structure) and
**redundancy** (duplicate/never-used fields) and propose the hardening edits.

# dump_scan.py — attach to live Oracle Forms JVM and write output files:
#   scan_dump.json      — raw Java agent DOM (project root, quick debug)
#   snapshot_output.txt — AI-friendly action snapshot (project root, quick debug)
#   tests/testdata/aisnapshot/<timestamp>/  — archived copy for test fixtures:
#       java_scan_dump.json, ai_snapshot.txt, screenshot.png
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

import config
from qcs_java_agent import JavaAgentDriver
from qcs_java_agent.snapshot import (
    build_action_context,
    _oracle_forms_active_frame,
)

driver = JavaAgentDriver.attach(contains=config.JAVA_AGENT_PROCESS_MATCH)
scan = driver.scan()

# --- Project-root quick-debug copies (unchanged) ---
with open("scan_dump.json", "w", encoding="utf-8") as f:
    json.dump(scan, f, indent=2, default=str)
print("wrote scan_dump.json")

snapshot_text, id_map = build_action_context(scan)
with open("snapshot_output.txt", "w", encoding="utf-8") as f:
    f.write(snapshot_text)
print(f"wrote snapshot_output.txt  ({len(id_map)} elements)")

# --- Archived test-data copy (timestamped folder) ---
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
archive_dir = Path("tests/testdata/aisnapshot/new") / timestamp
archive_dir.mkdir(parents=True, exist_ok=True)

archive_scan = archive_dir / "java_scan_dump.json"
with open(archive_scan, "w", encoding="utf-8") as f:
    json.dump(scan, f, indent=2, default=str)
print(f"wrote {archive_scan}")

archive_snapshot = archive_dir / "ai_snapshot.txt"
with open(archive_snapshot, "w", encoding="utf-8") as f:
    f.write(snapshot_text)
print(f"wrote {archive_snapshot}")

# Screenshot: target the parent form window (ExtendedFrame), not the full
# desktop.  When a popup is active, screenshot the parent form behind it.
archive_screenshot = archive_dir / "screenshot.png"
frame = _oracle_forms_active_frame(scan)
if frame and str(frame.get("simpleClassName") or "") in ("FWindow", "ChoiceBox"):
    # Popup is active — skip parent frame logic (not available in HEAD)
    pass

# Bring the Oracle Forms window to the foreground so the screenshot
# captures the form, not whatever is covering it (e.g. VS Code).
subprocess.run(
    ["powershell", "-c",
     f"(New-Object -ComObject WScript.Shell).AppActivate({driver.pid})"],
    timeout=5, capture_output=True,
)
time.sleep(0.5)  # wait for window to repaint

descriptor = {"path": frame["path"]} if frame and frame.get("path") else None
result = driver.screenshot(str(archive_screenshot.resolve()), descriptor=descriptor)
mode = result.get("captureMode", "unknown")
print(f"wrote {archive_screenshot}  (captureMode={mode})")
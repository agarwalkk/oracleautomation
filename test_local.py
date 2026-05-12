"""
Local smoke test — no Oracle EBS or Azure OpenAI required.

Tests:
  1. All project modules import cleanly
  2. RecorderSession creates run_dir + writes recording.jsonl
    3. TOOL_SCHEMAS has exactly 14 entries
  4. dispatch('session_start') works in-process
    5. qcs_repo stable identity and scoped ref resolution
    6. qcs pages works against an empty repository
    7. [Optional] Playwright MCP connection (only if npx server is running)
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
PASS = "\u2713"
FAIL = "\u2717"
results: list[tuple[str, bool, str]] = []

def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    icon = PASS if ok else FAIL
    print(f"  {icon}  {name}" + (f"  [{detail}]" if detail else ""))

# ---------------------------------------------------------------------------
# 1. Imports
# ---------------------------------------------------------------------------
print("\n[1] Imports")
try:
    import config
    check("config", True)
except Exception as e:
    check("config", False, str(e))

try:
    from qcs_repo import store, snapshot, fingerprint, naming, identity
    check("qcs_repo.*", True)
except Exception as e:
    check("qcs_repo.*", False, str(e))
    sys.exit(1)

try:
    from oracle_ai_agent.tools import RecorderSession, dispatch, TOOL_SCHEMAS
    check("oracle_ai_agent.tools", True)
except Exception as e:
    check("oracle_ai_agent.tools", False, str(e))
    sys.exit(1)  # nothing else will work without this

try:
    from oracle_ai_agent import run_agent   # noqa: F401  (don't call it)
    check("oracle_ai_agent.__init__", True)
except Exception as e:
    check("oracle_ai_agent.__init__", False, str(e))

try:
    from qcs_java_agent import JavaAgentDriver  # noqa: F401
    check("qcs_java_agent.JavaAgentDriver accessible", True)
except Exception as e:
    check("qcs_java_agent.JavaAgentDriver accessible", False, str(e))

# ---------------------------------------------------------------------------
# 2. TOOL_SCHEMAS count
# ---------------------------------------------------------------------------
print("\n[2] TOOL_SCHEMAS")
n = len(TOOL_SCHEMAS)
check(f"schema count == 14  (got {n})", n == 14)
for s in TOOL_SCHEMAS:
    check(f"  schema '{s['function']['name']}'", "function" in s and "name" in s["function"])

# ---------------------------------------------------------------------------
# 3. RecorderSession
# ---------------------------------------------------------------------------
print("\n[3] RecorderSession")
tmp = Path(tempfile.mkdtemp())
import config as _cfg
orig_rec = _cfg.RECORDINGS_DIR
_cfg.RECORDINGS_DIR = tmp          # redirect so we don't pollute repo

try:
    sess = RecorderSession("smoke_001", auto_name=True)
    check("RecorderSession.__init__", True, f"run_dir={sess.run_dir}")
    check("run_dir created", sess.run_dir.is_dir())
    check("screenshots dir created", (sess.run_dir / "screenshots").is_dir())
    check("diagnostics dir created", (sess.run_dir / "diagnostics").is_dir())
    check("auto_name=True", sess.auto_name is True)
    check("surface default", sess.surface == "unknown")

    sess.log_action("test_op", detail="hello")
    sess.log_diagnostic("test_event", detail="debug")
    jl = sess.run_dir / "recording.jsonl"
    dl = sess.run_dir / "diagnostics" / "events.jsonl"
    check("recording.jsonl created", jl.exists())
    check("diagnostics events.jsonl created", dl.exists())
    row = json.loads(jl.read_text(encoding="utf-8").strip())
    check("log_action row.op", row["op"] == "test_op")
    check("log_action row.run_id", row["run_id"] == "smoke_001")
    check("log_action row.detail", row.get("detail") == "hello")
    diag_row = json.loads(dl.read_text(encoding="utf-8").strip())
    check("log_diagnostic row.event", diag_row["event"] == "test_event")
    check("log_diagnostic row.detail", diag_row.get("detail") == "debug")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
    _cfg.RECORDINGS_DIR = orig_rec

# ---------------------------------------------------------------------------
# 4. dispatch("session_start")  — no Oracle, just registers surface
# ---------------------------------------------------------------------------
print("\n[4] dispatch('session_start')")
async def test_dispatch():
    tmp2 = Path(tempfile.mkdtemp())
    _cfg.RECORDINGS_DIR = tmp2
    try:
        sess2 = RecorderSession("smoke_002")
        result = await dispatch(sess2, "session_start", {
            "run_id": "smoke_002",
        })
        ok = isinstance(result, str) and "smoke_002" in result
        check("dispatch session_start returns str with run_id", ok, result[:80] if result else "empty")
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)
        _cfg.RECORDINGS_DIR = orig_rec

asyncio.run(test_dispatch())

# ---------------------------------------------------------------------------
# 5. qcs_repo identity/cache behavior
# ---------------------------------------------------------------------------
print("\n[5] qcs_repo identity/cache behavior")
repo_tmp = Path(tempfile.mkdtemp())
try:
    form_id = "java_find_orders"
    store.save_form({"id": form_id, "surface": "java", "title": "Find Orders"}, repo_tmp)
    first_capture = [
        {
            "elementid": "e1",
            "role": "TextField",
            "name": "Order Type",
            "text": "",
            "xpath": "JFrame[0]/JTextField[0]",
            "x": 10,
            "y": 20,
            "width": 120,
            "height": 24,
            "java": {
                "path": "JFrame[0]/JTextField[0]",
                "accessibleName": "Order Type",
                "locators": [{"strategy": "accessibleName", "value": "Order Type"}],
            },
        },
        {
            "elementid": "e2",
            "role": "Button",
            "name": "Find",
            "text": "Find",
            "xpath": "JFrame[0]/JButton[0]",
            "x": 150,
            "y": 20,
            "width": 70,
            "height": 24,
            "java": {
                "path": "JFrame[0]/JButton[0]",
                "accessibleName": "Find",
                "locators": [{"strategy": "accessibleName", "value": "Find"}],
            },
        },
    ]
    store.save_form_capture(form_id, first_capture, repo_dir=repo_tmp)
    elements = store.load_elements(form_id, repo_tmp)
    order_type = store.find_element_by_ref(form_id, "order_type", repo_tmp)
    check("repo capture creates semantic refs", order_type is not None, str([e.get("semantic_ref") for e in elements]))
    check("repo scoped ref resolves", store.resolve_element_ref(f"{form_id}.order_type", repo_dir=repo_tmp) is not None)
    check("repo public ref is form.element", store.element_public_ref(form_id, order_type or {}) == f"{form_id}.order_type")

    from qcs_java_agent.snapshot import locator_params as _locator_params
    params = _locator_params(order_type or {})
    check("locator params use candidates", params.get("locatorAccessibleName") == "Order Type", str(params))

    second_capture = [{
        **first_capture[0],
        "elementid": "e9",
        "xpath": "JFrame[0]/JPanel[1]/JTextField[0]",
        "java": {
            **first_capture[0]["java"],
            "path": "JFrame[0]/JPanel[1]/JTextField[0]",
        },
    }]
    store.save_form_capture(form_id, second_capture, repo_dir=repo_tmp)
    refreshed = store.load_elements(form_id, repo_tmp)
    order_refs = [el for el in refreshed if el.get("semantic_ref") == "order_type"]
    stale_find = store.find_element_by_ref(form_id, "find", repo_tmp, include_disabled=True)
    check("repo capture dedupes changed eXX refs", len(order_refs) == 1, str(order_refs))
    check("repo capture preserves stale missing elements", (stale_find or {}).get("capture_status") == "stale", str(stale_find))

    action_element = {
        "elementid": "e36",
        "friendly_name": "order_type",
        "surface": "java",
        "role": "Field",
        "name": "Order TypeList of Values",
        "xpath": "JFrame[0]/VTextField[3]",
        "x": 503,
        "y": 172,
        "width": 153,
        "height": 24,
        "bounds": {"x": 503, "y": 172, "width": 153, "height": 24},
        "java": {"path": "JFrame[0]/VTextField[3]", "accessibleName": "Order TypeList of Values"},
    }
    store.upsert_actioned_element(form_id, action_element, repo_tmp, source="test:first")
    store.upsert_actioned_element(form_id, {**action_element, "x": 504}, repo_tmp, source="test:second")
    action_matches = [el for el in store.load_elements(form_id, repo_tmp) if el.get("elementid") == "e36"]
    check("action upsert is idempotent by elementid", len(action_matches) == 1, str(action_matches))

    from qcs_java_agent.snapshot import actioned_element_at, java_nodes_to_repo_elements
    scan = {
        "windows": [{
            "id": 1,
            "path": "JFrame[0]",
            "parentPath": None,
            "depth": 0,
            "index": 0,
            "siblingCount": 1,
            "semanticType": "Window",
            "displayName": "Find Orders",
            "screenBounds": {"x": 100, "y": 100, "width": 800, "height": 600},
            "bounds": {"x": 0, "y": 0, "width": 800, "height": 600},
            "visible": True,
            "showing": True,
            "enabled": True,
            "children": [{
                "id": 2,
                "path": "JFrame[0]/VTextField[0]",
                "parentPath": "JFrame[0]",
                "depth": 1,
                "index": 0,
                "siblingCount": 1,
                "semanticType": "Field",
                "displayName": "Order Type",
                "screenBounds": {"x": 503, "y": 172, "width": 153, "height": 24},
                "bounds": {"x": 403, "y": 72, "width": 153, "height": 24},
                "visible": True,
                "showing": True,
                "enabled": True,
                "focusable": True,
                "children": [],
            }, {
                "id": 3,
                "path": "JFrame[0]/VTextField[1]",
                "parentPath": "JFrame[0]",
                "depth": 2,
                "index": 1,
                "siblingCount": 2,
                "semanticType": "Field",
                "displayName": "VTextField560",
                "screenBounds": {"x": 530, "y": 174, "width": 20, "height": 12},
                "bounds": {"x": 430, "y": 74, "width": 20, "height": 12},
                "visible": True,
                "showing": True,
                "enabled": True,
                "focusable": True,
                "children": [],
            }],
        }],
    }
    mapped_elements = java_nodes_to_repo_elements(scan)
    check("java scan keeps screen x/y", mapped_elements[1]["x"] == 503 and mapped_elements[1]["y"] == 172, str(mapped_elements[1].get("bounds")))
    hit = actioned_element_at(scan, 544, 174)
    check("actioned_element_at uses screenBounds x/y", (hit or {}).get("name") == "Order Type", str(hit))
finally:
    shutil.rmtree(repo_tmp, ignore_errors=True)

# ---------------------------------------------------------------------------
# 6. qcs pages command
# ---------------------------------------------------------------------------
print("\n[6] qcs pages command")
py = sys.executable
out = subprocess.run(
    [py, "-m", "qcs", "pages"],
    capture_output=True, text=True, timeout=10,
    cwd=Path(__file__).parent,
)
pages_ok = "page objects regenerated" in out.stdout.lower()
check("'qcs pages' handles empty repo", pages_ok, repr(out.stdout[:120]))
check("'qcs pages' exits 0", out.returncode == 0)

# ---------------------------------------------------------------------------
# 7. Playwright MCP (optional — only if port 8931 is reachable)
# ---------------------------------------------------------------------------
print("\n[7] Playwright MCP connection (optional)")
import socket
def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False

if not _port_open("127.0.0.1", 8931):
    print("   (skipped — Playwright MCP not running on :8931)")
    print("   To test: run  npx @playwright/mcp@latest --port 8931  in another terminal")
else:
    async def test_pw():
        from fastmcp import Client
        from fastmcp.client.transports import StreamableHttpTransport
        import httpx
        transport = StreamableHttpTransport("http://127.0.0.1:8931/mcp")
        try:
            async with Client(transport) as pw:
                tools = await pw.list_tools()
            check("Playwright MCP connected", True, f"{len(tools)} tools")
            check("browser_navigate in tools", any(t.name == "browser_navigate" for t in tools))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                print("   (403 Forbidden — newer @playwright/mcp requires --allowed-origins)")
                print("   Fix: npx @playwright/mcp@latest --port 8931 --allowed-origins '*'")
                print("   Or:  npx @playwright/mcp@latest --port 8931 --transport sse")
                print("   Skipping Playwright MCP checks.")
            else:
                raise
    asyncio.run(test_pw())

# ---------------------------------------------------------------------------
# 8. qcs_center imports + in-memory DB
# ---------------------------------------------------------------------------
print("\n[8] qcs_center")

import qcs_center.models as _cm
check("qcs_center.models imports", True)

import qcs_center.db as _cdb
check("qcs_center.db imports", True)

# Spin up an in-memory DB and exercise basic CRUD
async def test_center_db():
    import tempfile, os
    tmp = tempfile.mktemp(suffix=".db")
    try:
        await _cdb.init_db(tmp)

        # create a job
        job = await _cdb.job_create("run_smoke", "Click OK", auto_name=True)
        check("center db: job created", job["status"] == "queued", job["status"])
        check("center db: job has id",  bool(job["id"]))

        # dequeue it
        claimed = await _cdb.job_dequeue()
        check("center db: dequeue returns job", claimed is not None)
        assert claimed is not None
        check("center db: dequeued status=claimed", claimed["status"] == "claimed", claimed["status"])

        # finish it
        await _cdb.job_finish(claimed["id"], 0, "stdout", "stderr", '{"op":"done"}')
        done = await _cdb.job_get(claimed["id"])
        assert done is not None
        check("center db: finished status=done", done["status"] == "done", done["status"])
        check("center db: recording_jsonl stored", '"op":"done"' in (done["recording_jsonl"] or ""))

        # second dequeue → None (queue empty)
        nojob = await _cdb.job_dequeue()
        check("center db: empty dequeue returns None", nojob is None)

        # agent heartbeat + list
        await _cdb.agent_heartbeat("agent-1", "WIN-PC", "oracle")
        agents = await _cdb.agent_list()
        check("center db: agent registered", any(a["name"] == "agent-1" for a in agents))

        await _cdb.close_db()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

asyncio.run(test_center_db())

from qcs_center.app import app as _center_app
check("qcs_center.app (FastAPI) imports", True)

# ---------------------------------------------------------------------------
# 9. qcs_agent imports + executor helpers
# ---------------------------------------------------------------------------
print("\n[9] qcs_agent")

import qcs_agent.executor as _ae
check("qcs_agent.executor imports", True)
check("_find_python returns a path", bool(_ae._find_python()))

import qcs_agent.loop as _al
check("qcs_agent.loop imports", True)

import qcs_agent.main as _am
check("qcs_agent.main imports", True)

# ---------------------------------------------------------------------------
# 10. qcs center / agent CLI subcommands
# ---------------------------------------------------------------------------
print("\n[10] qcs center / agent --help")

py = sys.executable
out_c = subprocess.run(
    [py, "-m", "qcs", "center", "--help"],
    capture_output=True, text=True, timeout=10,
    cwd=Path(__file__).parent,
)
check("'qcs center --help' exits 0", out_c.returncode == 0, out_c.stderr[:120])

out_a = subprocess.run(
    [py, "-m", "qcs", "agent", "--help"],
    capture_output=True, text=True, timeout=10,
    cwd=Path(__file__).parent,
)
check("'qcs agent --help' exits 0", out_a.returncode == 0, out_a.stderr[:120])

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "="*55)
passed = sum(1 for _, ok, _ in results if ok)
total  = len(results)
failed = [n for n, ok, _ in results if not ok]
print(f"  {passed}/{total} passed")
if failed:
    print(f"  FAILED: {', '.join(failed)}")
    sys.exit(1)
else:
    print("  All checks passed.")

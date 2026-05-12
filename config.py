"""
Central configuration — all tunables in one place.
Override any value via environment variable of the same name.
A .env file in the project root is loaded automatically if present.
"""
from __future__ import annotations
import os
from pathlib import Path

# ── Load .env if present ───────────────────────────────────────────────────
def _load_dotenv(path: Path) -> None:
    """Minimal .env loader — no extra dependencies required."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Remove surrounding quotes if present
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        # Never overwrite values already set in the real environment
        if key and key not in os.environ:
            os.environ[key] = value

_load_dotenv(Path(__file__).parent / ".env")

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT_DIR        = Path(__file__).parent
REPO_DIR        = Path(os.environ.get("QCS_REPO_DIR",        ROOT_DIR / "repo"))
REPO_DB_PATH    = Path(os.environ.get("QCS_REPO_DB_PATH",    REPO_DIR / "repo.db"))
RECORDINGS_DIR  = Path(os.environ.get("QCS_RECORDINGS_DIR",  ROOT_DIR / "recordings"))
PAGES_DIR       = Path(os.environ.get("QCS_PAGES_DIR",       ROOT_DIR / "pages"))
FLOWS_GEN_DIR   = Path(os.environ.get("QCS_FLOWS_GEN_DIR",   ROOT_DIR / "flows_gen"))
TESTS_DIR       = Path(os.environ.get("QCS_TESTS_DIR",       ROOT_DIR / "generated_tests"))
REPORTS_DIR     = Path(os.environ.get("QCS_REPORTS_DIR",     ROOT_DIR / "reports"))

# ── Oracle EBS connection ───────────────────────────────────────────────────
EBS_URL      = os.environ.get("EBS_URL", "")
EBS_USER     = os.environ.get("EBS_USER", "")
EBS_PASSWORD = os.environ.get("EBS_PASSWORD", "")

# ── Java Forms agent ────────────────────────────────────────────────────────
# The local Java agent is built from java-agent/target/ebs-dom-agent.jar and
# attached to the Oracle Forms JVM with the Java Attach API.
JAVA_AGENT_DIR = Path(os.environ.get("QCS_JAVA_AGENT_DIR", ROOT_DIR / "java-agent"))
JAVA_AGENT_JAR = Path(
    os.environ.get("QCS_JAVA_AGENT_JAR", JAVA_AGENT_DIR / "target" / "ebs-dom-agent.jar")
)
JAVA_AGENT_JAVA_EXE = os.environ.get("QCS_JAVA_AGENT_JAVA_EXE", "java")
JAVA_AGENT_PROCESS_MATCH = os.environ.get("QCS_JAVA_AGENT_PROCESS_MATCH", "javaws")

# ── Forms timing ───────────────────────────────────────────────────────────
# Poll interval (ms) when waiting for a Forms window to stabilise
FORMS_POLL_MS       = int(os.environ.get("QCS_FORMS_POLL_MS",    "250"))
# Max wait (ms) for Forms to stabilise before giving up and using fixed delay
FORMS_SETTLE_MS     = int(os.environ.get("QCS_FORMS_SETTLE_MS",  "5000"))
# Quiet period (ms) before treating an active Java Forms frame as fully idle
FORMS_IDLE_MS       = int(os.environ.get("QCS_FORMS_IDLE_MS",    "2000"))
# How long to wait (ms) after an action before verifying its effect
POST_ACTION_VERIFY_MS = int(os.environ.get("QCS_POST_ACTION_VERIFY_MS", "1500"))

# ── Locator resolver ────────────────────────────────────────────────────────
LOCATOR_TIMEOUT_S   = int(os.environ.get("QCS_LOCATOR_TIMEOUT_S", "10"))

# ── Recording ───────────────────────────────────────────────────────────────
PLACEHOLDER_PREFIX  = os.environ.get("QCS_PLACEHOLDER_PREFIX", "DATA_")
MAX_SNAPSHOT_CHARS  = int(os.environ.get("QCS_MAX_SNAPSHOT_CHARS", "30000"))

# ── Azure OpenAI (recorder + Tier-1 healer) ────────────────────────────────
AZURE_OPENAI_API_KEY  = os.environ.get("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")
AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")

# ── Healing ────────────────────────────────────────────────────────────────
# QCS_HEALING: "on" | "off" | "tier1-only"
HEALING_MODE         = os.environ.get("QCS_HEALING", "on")
HEALING_MAX_PER_STEP = int(os.environ.get("QCS_HEALING_MAX_PER_STEP", "2"))
HEALING_MAX_PER_TEST = int(os.environ.get("QCS_HEALING_MAX_PER_TEST", "5"))
HEALING_TIMEOUT_S    = int(os.environ.get("QCS_HEALING_TIMEOUT_S",    "60"))

# ── Computer-use provider ──────────────────────────────────────────────────
# QCS_CU_PROVIDER: "openai" | "anthropic" | "omniparser"
CU_PROVIDER = os.environ.get("QCS_CU_PROVIDER", "openai")
# QCS_CU_MODEL should be set to a computer-use capable deployment on your Azure OpenAI resource.
# For play mode the Responses API is used; the model must support computer_use_preview tool.
# Example: computer-use-preview, gpt-4o, or the deployment name in your Azure resource.
CU_MODEL       = os.environ.get("QCS_CU_MODEL",       "gpt-5.4-mini")
# Responses API requires a newer preview version than the chat completions API
CU_API_VERSION = os.environ.get("QCS_CU_API_VERSION", "2025-04-01-preview")

# ── MCP server endpoints ────────────────────────────────────────────────────
PLAYWRIGHT_MCP_URL  = os.environ.get("QCS_PLAYWRIGHT_MCP_URL", "http://127.0.0.1:8931/mcp")

# ── Distributed center (run once on the control machine) ───────────────────
QCS_CENTER_HOST    = os.environ.get("QCS_CENTER_HOST",    "0.0.0.0")
QCS_CENTER_PORT    = int(os.environ.get("QCS_CENTER_PORT", "8080"))
# Shared Bearer token — set this to a strong random secret before starting.
QCS_CENTER_API_KEY = os.environ.get("QCS_CENTER_API_KEY", "")
QCS_CENTER_DB_PATH = os.environ.get("QCS_CENTER_DB_PATH", str(ROOT_DIR / "center.db"))

# ── Distributed agent (run on each Oracle EBS Windows machine) ─────────────
QCS_CENTER_URL  = os.environ.get("QCS_CENTER_URL",  "http://localhost:8080")
QCS_AGENT_TOKEN = os.environ.get("QCS_AGENT_TOKEN", "")   # same as QCS_CENTER_API_KEY
QCS_AGENT_NAME  = os.environ.get("QCS_AGENT_NAME",  "")   # unique per machine
QCS_AGENT_TAGS  = os.environ.get("QCS_AGENT_TAGS",  "")   # e.g. "oracle,win10"
QCS_HEARTBEAT_S = int(os.environ.get("QCS_HEARTBEAT_S", "20"))
# Python executable used by the agent to invoke 'qcs record'.
# Defaults to .venv/Scripts/python.exe relative to the project root.
QCS_PYTHON_EXE  = os.environ.get(
    "QCS_PYTHON_EXE",
    str(ROOT_DIR / ".venv" / "Scripts" / "python.exe"),
)

# ── Telemetry ───────────────────────────────────────────────────────────────
APPINSIGHTS_CONNECTION_STRING = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "")

# run_record.ps1 — load .env and start the QCS recorder
#
# Usage:
#   .\run_record.ps1                    # uses QCS_RUN_ID from .env
#   .\run_record.ps1 -RunId run_002     # override run ID
#   .\run_record.ps1 -RunId run_002 -Instructions path\to\other.txt
#
param(
    [string]$RunId        = "",
    [string]$Instructions = "instructions.txt",
    [string]$EnvFile      = ".env"
)

# ── Load .env ──────────────────────────────────────────────────────────────────
if (-not (Test-Path $EnvFile)) {
    Write-Error "Environment file '$EnvFile' not found. Copy .env and fill in your values."
    exit 1
}

Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#")) {
        if ($line -match '^([^=]+)=(.*)$') {
            $key   = $Matches[1].Trim()
            $value = $Matches[2].Trim()
            [System.Environment]::SetEnvironmentVariable($key, $value, 'Process')
        }
    }
}

# ── Resolve run ID ─────────────────────────────────────────────────────────────
if (-not $RunId) {
    $RunId = [System.Environment]::GetEnvironmentVariable("QCS_RUN_ID", "Process")
}
if (-not $RunId) {
    $RunId = "run_" + (Get-Date -Format "yyyyMMdd_HHmmss")
}

# ── Validate required vars ─────────────────────────────────────────────────────
$missing = @()
foreach ($v in @("AZURE_OPENAI_ENDPOINT","AZURE_OPENAI_API_KEY","EBS_USER","EBS_PASSWORD")) {
    $val = [System.Environment]::GetEnvironmentVariable($v, "Process")
    if (-not $val -or $val -like "*YOUR_*") { $missing += $v }
}
if ($missing.Count -gt 0) {
    Write-Error "Missing / placeholder values in .env: $($missing -join ', ')"
    Write-Error "Edit .env and fill in real values before running."
    exit 1
}

# ── Playwright MCP — stdio mode (no HTTP server needed) ────────────────────────
# The recorder uses NpxStdioTransport by default, which launches @playwright/mcp
# as a child process over stdio. No port binding, no CORS issues.
# Set QCS_PLAYWRIGHT_MCP_USE_STDIO=0 in .env to revert to HTTP mode.
$use_stdio = [System.Environment]::GetEnvironmentVariable("QCS_PLAYWRIGHT_MCP_USE_STDIO", "Process")
if (-not $use_stdio) { $use_stdio = "1" }

if ($use_stdio -eq "1") {
    Write-Host "[OK] Playwright MCP will run via stdio (no separate server needed)"
} else {
    Write-Host "[INFO] Playwright MCP HTTP mode -- make sure 'npx @playwright/mcp@latest --port 8931 --allowed-origins *' is running"
}

# ── Run the recorder ───────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Starting QCS recorder"
Write-Host "  Run ID      : $RunId"
Write-Host "  Instructions: $Instructions"
Write-Host "  Deployment  : $($env:AZURE_OPENAI_DEPLOYMENT)"
Write-Host ""

$python = ".\.venv\Scripts\python.exe"

& $python -m qcs record `
    --run-id $RunId `
    --auto-name `
    $Instructions

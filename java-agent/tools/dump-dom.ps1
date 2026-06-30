<#
.SYNOPSIS
  Dump the Oracle Forms DOM using the Java agent ONLY (no qcs Python stack).

.DESCRIPTION
  Attaches ebs-dom-agent.jar to a running Oracle Forms JVM via the Attach API
  and writes the extraction outputs (scan / raw / layout / tables, plus an
  optional screenshot) to a timestamped folder so they can be reviewed.

  This is a thin wrapper around:
    java [attach flags] -cp <jar>[;tools.jar] com.pyebsdom.agent.attach.AttachLauncher \
         <pid> <jar> "command=<cmd>;out=<file>"

.PARAMETER TargetPid
  PID of the Oracle Forms JVM (the javaw.exe / jp2launcher.exe process).
  If omitted, the script auto-detects it via `jps` using -Match.

.PARAMETER Jar
  Path to ebs-dom-agent.jar. Defaults to ..\target\ebs-dom-agent.jar relative
  to this script.

.PARAMETER JavaHome
  A JDK home (NOT a JRE - needs the Attach API). MUST match the target JVM's
  architecture (a 32-bit Forms JVM needs a 32-bit JDK to attach). Defaults to
  $env:JAVA_HOME.

.PARAMETER OutDir
  Output folder. Defaults to .\dom-dumps\<yyyyMMdd_HHmmss>.

.PARAMETER Match
  Substring used to pick the target JVM from `jps -l -v` when auto-detecting.
  Defaults to "forms". Try "frmweb", "oracle.forms", or "javaws" if needed.

.PARAMETER Screenshot
  Also capture a full-screen PNG next to the DOM dump.

.EXAMPLE
  # Auto-detect the Forms JVM and dump everything
  .\dump-dom.ps1

.EXAMPLE
  # Explicit PID (from Task Manager) + screenshot
  .\dump-dom.ps1 -TargetPid 13728 -Screenshot
#>
[CmdletBinding()]
param(
    [int]$TargetPid = 0,
    [string]$Jar = "",
    [string]$JavaHome = $env:JAVA_HOME,
    [string]$OutDir = "",
    [string]$Match = "javaws|forms|frm",
    [switch]$Screenshot
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# -- Resolve the agent jar ----------------------------------------------------
if (-not $Jar) { $Jar = Join-Path $scriptDir "..\target\ebs-dom-agent.jar" }
$Jar = (Resolve-Path $Jar -ErrorAction Stop).Path
Write-Host "[dump-dom] agent jar : $Jar"

# -- Resolve the JDK ----------------------------------------------------------
if (-not $JavaHome) { throw "JAVA_HOME is not set. Pass -JavaHome <jdk> (a JDK, not a JRE)." }
$java = Join-Path $JavaHome "bin\java.exe"
$jps  = Join-Path $JavaHome "bin\jps.exe"
$toolsJar = Join-Path $JavaHome "lib\tools.jar"
if (-not (Test-Path $java)) { throw "java.exe not found under JAVA_HOME: $java" }
Write-Host "[dump-dom] jdk       : $JavaHome"

# -- Detect Java major version -> choose attach flags / classpath -------------
$oldEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$verRaw = (& $java -version 2>&1) -join " "
$ErrorActionPreference = $oldEAP
$major = 8
if ($verRaw -match 'version "(\d+)(?:\.(\d+))?') {
    $major = if ($Matches[1] -eq "1") { [int]$Matches[2] } else { [int]$Matches[1] }
}
if ($major -ge 9) {
    $cp = $Jar
    $attachFlags = @("--add-modules", "jdk.attach")
} else {
    if (-not (Test-Path $toolsJar)) { throw "Java 8 attach needs tools.jar: $toolsJar (use a JDK, not a JRE)." }
    $cp = "$Jar;$toolsJar"
    $attachFlags = @()
}
Write-Host "[dump-dom] java major : $major"

# -- Auto-detect the Forms JVM PID if not given -------------------------------
if ($TargetPid -le 0) {
    if (-not (Test-Path $jps)) { throw "jps not found; pass -TargetPid explicitly." }
    $lines = & $jps -l -v 2>$null | Where-Object { $_ -match $Match -and $_ -notmatch "AttachLauncher" -and $_ -notmatch "sun.tools.jps" }
    $pids = @($lines | ForEach-Object { ($_ -split "\s+")[0] } | Where-Object { $_ -match '^\d+$' })
    if ($pids.Count -eq 1) {
        $TargetPid = [int]$pids[0]
        Write-Host "[dump-dom] auto PID   : $TargetPid  ($($lines))"
    } elseif ($pids.Count -gt 1) {
        Write-Host "[dump-dom] Multiple JVMs matched '$Match' - re-run with -TargetPid <pid>:"
        $lines | ForEach-Object { Write-Host "    $_" }
        throw "Ambiguous target; specify -TargetPid."
    } else {
        Write-Host "[dump-dom] No JVM matched '$Match'. All JVMs:"
        & $jps -l -v 2>$null | ForEach-Object { Write-Host "    $_" }
        throw "Could not auto-detect the Forms JVM; specify -TargetPid."
    }
}

# -- Output folder ------------------------------------------------------------
if (-not $OutDir) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutDir = Join-Path $scriptDir "dom-dumps\$stamp"
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$OutDir = (Resolve-Path $OutDir).Path
Write-Host "[dump-dom] out dir    : $OutDir`n"

function Invoke-AgentCommand([string]$cmd, [string]$outFile, [string]$extra = "") {
    $args = "command=$cmd;out=$outFile"
    if ($extra) { $args += ";$extra" }
    Write-Host "[dump-dom] -> $cmd"
    $oldEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $java @attachFlags -cp $cp "com.pyebsdom.agent.attach.AttachLauncher" $TargetPid $Jar $args 2>&1 |
        ForEach-Object { Write-Host "      $_" }
    $ErrorActionPreference = $oldEAP
    if (Test-Path $outFile) {
        $kb = [math]::Round((Get-Item $outFile).Length / 1KB, 1)
        Write-Host "      wrote $([System.IO.Path]::GetFileName($outFile))  (${kb} KB)"
    } else {
        Write-Warning "      $cmd produced no output file"
    }
}

# -- Run the extraction commands ----------------------------------------------
Invoke-AgentCommand "health" (Join-Path $OutDir "health.json")
Invoke-AgentCommand "scan"   (Join-Path $OutDir "scan.json")
Invoke-AgentCommand "raw"    (Join-Path $OutDir "raw.json")
Invoke-AgentCommand "layout" (Join-Path $OutDir "layout.txt")
Invoke-AgentCommand "tables" (Join-Path $OutDir "tables.json")
if ($Screenshot) {
    $png = Join-Path $OutDir "screenshot.png"
    Invoke-AgentCommand "screenshot" (Join-Path $OutDir "screenshot.result.json") "screenshotout=$png"
}

Write-Host "`n[dump-dom] DONE. Review/share the folder:`n    $OutDir"
Write-Host "[dump-dom] For a first look, open layout.txt (human-readable) and scan.json (schema-2.0)."

#!/usr/bin/env pwsh
# scripts/smoke.ps1 - pre-deploy smoke: the 10-run deliverable discriminator.
#
# Drives fork_cli against a live Fork instance ten times with a deliverable
# (tool-calling) prompt and prints a per-run summary + a single verdict line.
# This is the tripwire for the reasoning-field 400 class of bug: a deliverable
# turn that errors produces an empty answer (answer_chars=0), and the tool path
# is exactly what breaks - so we require both a full success rate AND that the
# tool path actually fired on several runs.
#
# VERDICT: PASS iff all runs succeed (answer_chars > 0) AND >= 3 runs contain a
#          tool_call/tool_result pair. Otherwise FAIL with a nonzero exit code.
#
# Credentials come from the environment ONLY - never hardcode a key here:
#   $env:FORK_API_KEY = "<master key>"          # preferred
#   -- or -- $env:FORK_EMAIL + $env:FORK_PASSWORD
#   -- or -- $env:FORK_TOKEN
# Target defaults to prod (fork_cli's own default); override with $env:FORK_BASE_URL.
#
# Usage:
#   $env:FORK_API_KEY="..."; ./scripts/smoke.ps1
#   ./scripts/smoke.ps1 -Project dar_al_arkan_master -Runs 10
[CmdletBinding()]
param(
    [string]$Project = $(if ($env:FORK_PROJECT) { $env:FORK_PROJECT } else { "dar_al_arkan_master" }),
    [string]$Base    = $(if ($env:FORK_BASE_URL) { $env:FORK_BASE_URL } else { "" }),
    [int]$Runs       = 10,
    [string]$Message = "generate a commissioning checklist for the MV substation"
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"   # Windows console chokes on symbols (Ohm etc.) in checklists

# --- credential guard: env only, never hardcoded ---
$haveKey   = [bool]$env:FORK_API_KEY
$haveLogin = [bool]$env:FORK_EMAIL -and [bool]$env:FORK_PASSWORD
$haveTok   = [bool]$env:FORK_TOKEN
if (-not ($haveKey -or $haveLogin -or $haveTok)) {
    Write-Error "No credentials in env. Set FORK_API_KEY (or FORK_EMAIL + FORK_PASSWORD, or FORK_TOKEN) before running smoke."
    exit 2
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$cli = Join-Path $scriptDir "fork_cli.py"
if (-not (Test-Path $cli)) { Write-Error "fork_cli.py not found next to smoke.ps1 ($cli)"; exit 2 }

$baseArgs = @()
if ($Base) { $baseArgs += @("--base", $Base) }

$successes = 0
$toolRuns  = 0

Write-Host "Pre-deploy smoke: $Runs runs, project=$Project" -ForegroundColor Cyan
Write-Host ("message: {0}" -f $Message)
Write-Host ("=" * 72)

for ($i = 1; $i -le $Runs; $i++) {
    $out = & python $cli @baseArgs chat $Message --project $Project --events 2>&1 | Out-String

    $m = [regex]::Match($out, "total=([\d.]+)s\s+first_token=([\d.\-]+s?|-)\s+events=(\d+)\s+answer_chars=(\d+)")
    $total = if ($m.Success) { [double]$m.Groups[1].Value } else { $null }
    $ftok  = if ($m.Success) { $m.Groups[2].Value } else { $null }
    $chars = if ($m.Success) { [int]$m.Groups[4].Value } else { 0 }

    $hasTool = ($out -match "TOOL_CALL") -and ($out -match "TOOL_RESULT")
    $ok = $m.Success -and ($chars -gt 0)

    if ($ok)      { $successes++ }
    if ($hasTool) { $toolRuns++ }

    $status   = if ($ok) { "OK  " } else { "FAIL" }
    $toolFlag = if ($hasTool) { "tool=Y" } else { "tool=n" }
    $totalTxt = if ($total) { "{0,6:N2}" -f $total } else { "     ?" }
    $ftokTxt  = if ($ftok)  { "{0,7}" -f $ftok }     else { "      ?" }
    Write-Host ("run{0,2}  {1}  {2}  total={3}s  first_token={4}  answer_chars={5}" -f `
        $i, $status, $toolFlag, $totalTxt, $ftokTxt, $chars)
}

Write-Host ("=" * 72)
$pass = ($successes -eq $Runs) -and ($toolRuns -ge 3)
if ($pass) {
    Write-Host ("VERDICT: PASS  ({0}/{1} succeeded, {2} runs with tool_call/tool_result)" -f $successes, $Runs, $toolRuns) -ForegroundColor Green
    exit 0
} else {
    Write-Host ("VERDICT: FAIL  ({0}/{1} succeeded, {2} runs with tool_call/tool_result; need {1}/{1} and >=3 tool)" -f $successes, $Runs, $toolRuns) -ForegroundColor Red
    exit 1
}

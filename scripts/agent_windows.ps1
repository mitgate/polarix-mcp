# Polarix desktop agent — run INSIDE the Windows guest (VM) that Polarix will control.
#
#   1. Copy this repository (at least the polarix/ folder and this script) into the VM.
#   2. Open PowerShell as the user that owns the desktop session (not as a service:
#      UI Automation needs an interactive session).
#   3. .\scripts\agent_windows.ps1 -Token "um-segredo"
#
# On the host, point Polarix at the guest:
#   POLARIX_DESKTOP_DRIVER=remote
#   POLARIX_DESKTOP_AGENT_URL=http://<ip-da-vm>:8020
#   POLARIX_AGENT_TOKEN=um-segredo
#
# The agent is standard-library HTTP; the guest needs only Python 3.11+, pywinauto,
# pynput (macro recording) and pillow (screenshots). No Playwright, no MCP SDK.

param(
    [string]$BindHost = "0.0.0.0",
    [int]$Port = 8020,
    [string]$Token = $env:POLARIX_AGENT_TOKEN,
    [ValidateSet("uia", "win32")][string]$Backend = "uia",
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location (Join-Path $here "..")

if (-not (Test-Path ".venv")) {
    Write-Host "[polarix-agent] creating .venv"
    py -3.11 -m venv .venv
}
$python = Join-Path (Get-Location) ".venv\Scripts\python.exe"

if (-not $SkipInstall) {
    & $python -m pip install --quiet --upgrade pip
    & $python -m pip install --quiet pywinauto pynput pillow
}

# Let the host reach the agent (idempotent; ignored when the rule exists or we lack rights)
try {
    New-NetFirewallRule -DisplayName "Polarix Agent $Port" -Direction Inbound `
        -LocalPort $Port -Protocol TCP -Action Allow -ErrorAction Stop | Out-Null
} catch { }

$env:POLARIX_DESKTOP_DRIVER = "pywinauto"
$env:POLARIX_DESKTOP_BACKEND = $Backend
Write-Host "[polarix-agent] http://${BindHost}:$Port backend=$Backend token=$([bool]$Token)"
& $python -m polarix.desktop.agent --host $BindHost --port $Port --token "$Token" --backend $Backend

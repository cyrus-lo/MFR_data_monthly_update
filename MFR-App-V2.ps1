<#
MFR Monthly Pipeline v2 UI helper

Usage:
  .\MFR-App-V2.ps1 Start
  .\MFR-App-V2.ps1 Stop
  .\MFR-App-V2.ps1 Status
#>

param(
    [ValidateSet("Start", "Stop", "Status")]
    [string]$Action = "Status"
)

$ErrorActionPreference = "Stop"
$AppPort = 8502
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppFile = Join-Path $Root "web_app_v2\app.py"
$LogFile = Join-Path $Root "data\mfr_app_v2.log"
$ErrorLogFile = Join-Path $Root "data\mfr_app_v2_error.log"

function Get-MfrV2AppProcess {
    Get-NetTCPConnection -LocalPort $AppPort -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty OwningProcess
}

switch ($Action) {
    "Status" {
        $processId = Get-MfrV2AppProcess
        if ($processId) {
            Write-Host "The MFR v2 UI is running at http://localhost:$AppPort (process $processId)." -ForegroundColor Green
        }
        else {
            Write-Host "The MFR v2 UI is not running." -ForegroundColor Yellow
        }
    }
    "Start" {
        $processId = Get-MfrV2AppProcess
        if ($processId) {
            Write-Host "The MFR v2 UI is already running at http://localhost:$AppPort (process $processId)." -ForegroundColor Yellow
            break
        }
        if (-not (Test-Path $AppFile)) {
            throw "Cannot find the v2 app at $AppFile."
        }
        $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
        $pythonCommand = if (Test-Path $venvPython) { $venvPython } else { "python" }
        New-Item -ItemType Directory -Path (Split-Path $LogFile) -Force | Out-Null
        Start-Process -FilePath $pythonCommand `
            -ArgumentList "-m", "streamlit", "run", $AppFile, "--server.port", $AppPort `
            -WorkingDirectory $Root `
            -RedirectStandardOutput $LogFile `
            -RedirectStandardError $ErrorLogFile `
            -WindowStyle Hidden
        Write-Host "Starting the MFR v2 UI. Open http://localhost:$AppPort in a browser." -ForegroundColor Green
        Write-Host "Start .\MFR-Worker.ps1 Start separately to execute queued pipeline work." -ForegroundColor Cyan
    }
    "Stop" {
        $processId = Get-MfrV2AppProcess
        if (-not $processId) {
            Write-Host "The MFR v2 UI is already stopped." -ForegroundColor Yellow
            break
        }
        Stop-Process -Id $processId -Force
        Write-Host "The MFR v2 UI has been stopped. The shared worker, if started, continues running." -ForegroundColor Green
    }
}
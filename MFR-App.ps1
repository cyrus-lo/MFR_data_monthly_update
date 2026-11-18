<#
MFR Monthly Pipeline app helper

Run this script from PowerShell while in this folder:
  .\MFR-App.ps1 Start
  .\MFR-App.ps1 Stop
  .\MFR-App.ps1 Status

If PowerShell blocks the script, run this once for the current window:
  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#>

param(
    [ValidateSet("Start", "Stop", "Status")]
    [string]$Action = "Status"
)

$ErrorActionPreference = "Stop"
$AppPort = 8501
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppFile = Join-Path $Root "web_app\app.py"
$LogFile = Join-Path $Root "data\mfr_app_v1.log"
$ErrorLogFile = Join-Path $Root "data\mfr_app_v1_error.log"

function Get-MfrAppProcess {
    Get-NetTCPConnection -LocalPort $AppPort -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty OwningProcess
}

switch ($Action) {
    "Status" {
        $processId = Get-MfrAppProcess
        if ($processId) {
            Write-Host "The MFR v1 UI is running at http://localhost:$AppPort (process $processId)." -ForegroundColor Green
        }
        else {
            Write-Host "The MFR v1 UI is not running." -ForegroundColor Yellow
        }
    }

    "Start" {
        $processId = Get-MfrAppProcess
        if ($processId) {
            Write-Host "The MFR v1 UI is already running at http://localhost:$AppPort (process $processId)." -ForegroundColor Yellow
            break
        }

        if (-not (Test-Path $AppFile)) {
            throw "Cannot find the app at $AppFile. Run this script from the project folder."
        }

        $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
        if (Test-Path $venvPython) {
            $pythonCommand = $venvPython
        }
        else {
            $pythonCommand = "python"
        }

        New-Item -ItemType Directory -Path (Split-Path $LogFile) -Force | Out-Null
        Start-Process -FilePath $pythonCommand `
            -ArgumentList "-m", "streamlit", "run", $AppFile, "--server.port", $AppPort `
            -WorkingDirectory $Root `
            -RedirectStandardOutput $LogFile `
            -RedirectStandardError $ErrorLogFile `
            -WindowStyle Hidden

        Write-Host "Starting the MFR v1 UI. Open http://localhost:$AppPort in a browser." -ForegroundColor Green
        Write-Host "Start .\MFR-Worker.ps1 Start separately to execute queued pipeline work." -ForegroundColor Cyan
        Write-Host "Use .\MFR-App.ps1 Stop when you are finished."
    }

    "Stop" {
        $processId = Get-MfrAppProcess
        if (-not $processId) {
            Write-Host "The MFR app is already stopped." -ForegroundColor Yellow
            break
        }

        Stop-Process -Id $processId -Force
        Write-Host "The MFR v1 UI has been stopped. The shared worker, if started, continues running." -ForegroundColor Green
    }
}
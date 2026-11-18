<#
Shared MFR pipeline worker helper. Run exactly one worker for both dashboard versions.

Usage:
  .\MFR-Worker.ps1 Start
  .\MFR-Worker.ps1 Stop
  .\MFR-Worker.ps1 Status
#>

param(
    [ValidateSet("Start", "Stop", "Status")]
    [string]$Action = "Status"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $Root "data\mfr_worker.pid"
$LogFile = Join-Path $Root "data\mfr_worker.log"
$ErrorLogFile = Join-Path $Root "data\mfr_worker_error.log"

function Get-MfrWorkerProcess {
    if (-not (Test-Path $PidFile)) {
        return $null
    }
    $savedProcessId = Get-Content $PidFile -Raw | ForEach-Object { $_.Trim() }
    if (-not $savedProcessId) {
        Remove-Item $PidFile -Force
        return $null
    }
    $process = Get-Process -Id $savedProcessId -ErrorAction SilentlyContinue
    if (-not $process) {
        Remove-Item $PidFile -Force
        return $null
    }
    return $process
}

switch ($Action) {
    "Status" {
        $process = Get-MfrWorkerProcess
        if ($process) {
            Write-Host "The shared MFR worker is running (process $($process.Id))." -ForegroundColor Green
        }
        else {
            Write-Host "The shared MFR worker is not running." -ForegroundColor Yellow
        }
    }
    "Start" {
        $process = Get-MfrWorkerProcess
        if ($process) {
            Write-Host "The shared MFR worker is already running (process $($process.Id))." -ForegroundColor Yellow
            break
        }
        $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
        $pythonCommand = if (Test-Path $venvPython) { $venvPython } else { "python" }
        New-Item -ItemType Directory -Path (Split-Path $LogFile) -Force | Out-Null
        Set-Content -Path $LogFile -Value "" -NoNewline
        Set-Content -Path $ErrorLogFile -Value "" -NoNewline
        $process = Start-Process -FilePath $pythonCommand `
            -ArgumentList "-m", "worker.worker_main" `
            -WorkingDirectory $Root `
            -RedirectStandardOutput $LogFile `
            -RedirectStandardError $ErrorLogFile `
            -WindowStyle Hidden `
            -PassThru
        Set-Content -Path $PidFile -Value $process.Id -NoNewline
        if ($process.WaitForExit(500)) {
            Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
            throw "The shared MFR worker exited during startup. Review $ErrorLogFile for details."
        }
        Write-Host "Started the shared MFR worker (process $($process.Id))." -ForegroundColor Green
    }
    "Stop" {
        $process = Get-MfrWorkerProcess
        if (-not $process) {
            Write-Host "The shared MFR worker is already stopped." -ForegroundColor Yellow
            break
        }
        Stop-Process -Id $process.Id -Force
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        Write-Host "The shared MFR worker has been stopped." -ForegroundColor Green
    }
}
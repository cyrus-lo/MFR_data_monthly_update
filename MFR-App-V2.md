# Start MFR Dashboard v2

Open PowerShell in the project folder. If script execution is blocked, allow scripts for the current PowerShell session, then start the dashboard:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\MFR-App-V2.ps1 Start
```

Open <http://localhost:8502> in your browser. Check or stop the dashboard with:

```powershell
.\MFR-App-V2.ps1 Status
.\MFR-App-V2.ps1 Stop
```

The execution-policy setting above applies only to the current PowerShell session. When **Check submission** or **Run All** is queued, the dashboard attempts to start the shared worker if it is stopped. It retries with `-ExecutionPolicy Bypass` only when PowerShell explicitly blocks the launcher, and only for that launch process; no persistent policy is changed. You can still start the worker manually with:

```powershell
.\MFR-Worker.ps1 Start
```

<#
Step 5 — Risk DB Sync Template

This template documents the future manual Risk DB sync. It intentionally refuses
execution until all TBC settings are replaced with approved dev-environment values.
#>

$ErrorActionPreference = "Stop"

$SshUser = "<TBC_SSH_USER>"
$SshHost = "<TBC_SSH_HOST>"
$SshKeyPath = "<TBC_SSH_KEY_PATH_OR_REMOVE_OPTION>"
$RemoteBackendDirectory = "<TBC_REMOTE_BACKEND_DIRECTORY>"
$DockerContainer = "<TBC_DOCKER_CONTAINER>"
$DockerRiskWorkbookPath = "<TBC_DOCKER_RISK_WORKBOOK_PATH>"
$ExpectedStatus = "<TBC_POST_SYNC_STATUS_CRITERION>"
$RiskInputDirectory = Join-Path $PSScriptRoot "..\mfr_risk_check\MFR_vs_Risk_calculation\Risk_calculation_files"

$values = @(
    $SshUser,
    $SshHost,
    $SshKeyPath,
    $RemoteBackendDirectory,
    $DockerContainer,
    $DockerRiskWorkbookPath,
    $ExpectedStatus
)
if ($values | Where-Object { $_ -match "<TBC_" }) {
    Write-Error "Step 5 is a template. Replace all <TBC_...> values before executing it."
    exit 2
}

<#
Future approved sequence — enable only after dev-environment values are confirmed:

ssh -i $SshKeyPath "$SshUser@$SshHost" "cd $RemoteBackendDirectory; curl -sS -X POST 'http://localhost:8000/api/update-db' | jq"
ssh -i $SshKeyPath "$SshUser@$SshHost" "docker cp $DockerContainer`:$DockerRiskWorkbookPath /approved/temporary/location"
Copy-Item "/approved/temporary/location/<exported-risk-workbook>.xlsx" $RiskInputDirectory

Verify the agreed post-sync status criterion: $ExpectedStatus
#>

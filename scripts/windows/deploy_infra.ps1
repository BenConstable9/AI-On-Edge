#!/usr/bin/env pwsh
# Creates the Azure resources and registers the Jetson as an IoT Edge device,
# then writes a .env file the build and deploy steps read.
#
# Usage: .\deploy_infra.ps1 [-ResourceGroup <name>] [-DeviceSuffix <suffix>]
# baseName and location come from infra/src/main.bicepparam

[CmdletBinding()]
param(
    [Parameter()]
    [string]$ResourceGroup = "ai-on-edge-rg",

    [Parameter()]
    [string]$DeviceSuffix = "orin",

    [Parameter()]
    [ValidateSet("sas", "selfSigned", "certificateAuthority", "shared_private_key")]
    [string]$AuthType = "shared_private_key"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $ProjectRoot
Write-Host "Project root: $ProjectRoot"

$existing = az group show --name $ResourceGroup 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "Resource group '$ResourceGroup' not found. Create it first with: az group create --name $ResourceGroup --location uksouth"
}

$BicepParamFile = Join-Path $ProjectRoot "infra\src\main.bicepparam"
Write-Host "Deploying resources from $BicepParamFile"

# The hub takes no shared access keys, so whoever runs this gets a data role on it.
if ((az account show --query user.type -o tsv) -eq "servicePrincipal") {
    $DeployerType = "ServicePrincipal"
    $DeployerId = az ad sp show --id (az account show --query user.name -o tsv) --query id -o tsv
} else {
    $DeployerType = "User"
    $DeployerId = az ad signed-in-user show --query id -o tsv
}
if (-not $DeployerId) { throw "Could not read the signed-in principal's object id" }

$deploymentOutput = az deployment group create `
    --resource-group $ResourceGroup `
    --parameters $BicepParamFile `
    --parameters deployerPrincipalId=$DeployerId deployerPrincipalType=$DeployerType `
    --query properties.outputs -o json | ConvertFrom-Json

if ($LASTEXITCODE -ne 0) {
    throw "Bicep deployment failed"
}

$IotHubName = $deploymentOutput.iotHubName.value
$AcrName = $deploymentOutput.acrName.value
$AcrLoginServer = $deploymentOutput.acrLoginServer.value

Write-Host ""
Write-Host "Deployed:"
Write-Host "  IoT Hub:          $IotHubName"
Write-Host "  Container Reg:    $AcrLoginServer"
Write-Host ""

$BaseName = $IotHubName -replace '^iot-', ''
$DeviceId = "$BaseName-$DeviceSuffix"

# A new role assignment can take a few minutes to reach the hub.
Write-Host "Waiting for the IoT Hub data role"
for ($i = 0; $i -lt 40; $i++) {
    az iot hub device-identity list --hub-name $IotHubName --auth-type login --top 1 -o none 2>$null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 15
}
if ($LASTEXITCODE -ne 0) { throw "The IoT Hub data role did not take effect within 10 minutes" }

$deviceExists = az iot hub device-identity show --hub-name $IotHubName --device-id $DeviceId --auth-type login 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Device '$DeviceId' already registered."
} else {
    Write-Host "Registering edge device '$DeviceId'"
    az iot hub device-identity create `
        --hub-name $IotHubName `
        --device-id $DeviceId `
        --edge-enabled true `
        --auth-method $AuthType `
        --auth-type login
    if ($LASTEXITCODE -ne 0) { throw "Device registration failed" }
}

# This is what goes into /etc/aziot/config.toml on the Jetson.
$DeviceConnStr = az iot hub device-identity connection-string show `
    --hub-name $IotHubName --device-id $DeviceId --auth-type login --query connectionString -o tsv
if (-not $DeviceConnStr) { throw "Could not read the device connection string" }

$SubscriptionId = az account show --query id -o tsv

# IoT Edge pulls with an Entra service principal holding only AcrPull on this
# registry. The ACR admin account is disabled: it is registry-wide, it can push
# and delete, and it cannot be scoped. A managed identity would avoid the secret
# altogether, but the device has no IMDS endpoint to get a token from.
$SpName = "$BaseName-acr-pull"
Write-Host "Ensuring service principal '$SpName'"

$AcrId = az acr show --name $AcrName --resource-group $ResourceGroup --query id -o tsv
$AppId = az ad app list --display-name $SpName --query "[0].appId" -o tsv 2>$null

if (-not $AppId) {
    $AppId = az ad app create --display-name $SpName --query appId -o tsv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the app registration" }
}

$SpObjectId = az ad sp list --filter "appId eq '$AppId'" --query "[0].id" -o tsv 2>$null
if (-not $SpObjectId) {
    $SpObjectId = az ad sp create --id $AppId --query id -o tsv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the service principal" }
}

$HasRole = az role assignment list --assignee $AppId --scope $AcrId --query "[?roleDefinitionName=='AcrPull'] | length(@)" -o tsv
if ($HasRole -eq "0") {
    az role assignment create --assignee-object-id $SpObjectId `
        --assignee-principal-type ServicePrincipal `
        --role AcrPull --scope $AcrId -o none
    if ($LASTEXITCODE -ne 0) { throw "Could not assign AcrPull" }
}

# Tenant policy caps password lifetime, commonly at 30 days, so this secret is
# short-lived by design. Re-run this script to roll it before it expires.
$CredentialEnd = (Get-Date).AddDays(29).ToString('yyyy-MM-dd')
$SpCredential = az ad app credential reset --id $AppId `
    --display-name 'iotedge-acrpull' --end-date $CredentialEnd --only-show-errors | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Could not create the registry credential" }

$AcrUsername = $SpCredential.appId
$AcrPassword = $SpCredential.password
Write-Host "  AcrPull service principal ready, secret expires $CredentialEnd"

$TenantId = az account show --query tenantId -o tsv

$EnvFile = Join-Path $ProjectRoot ".env"
$EnvContent = @"
RESOURCE_GROUP=$ResourceGroup
SUBSCRIPTION_ID=$SubscriptionId
AZURE_TENANT_ID=$TenantId

IOT_HUB_NAME=$IotHubName
IOT_DEVICE_ID=$DeviceId
# Paste into /etc/aziot/config.toml on the Jetson
IOT_DEVICE_CONNECTION_STRING=$DeviceConnStr

ACR_NAME=$AcrName
CONTAINER_REGISTRY_SERVER=$AcrLoginServer
CONTAINER_REGISTRY_USERNAME=$AcrUsername
CONTAINER_REGISTRY_PASSWORD=$AcrPassword
"@

Set-Content -Path $EnvFile -Value $EnvContent -NoNewline

Write-Host ""
Write-Host ".env written. It contains secrets and is git-ignored — keep it that way."
Write-Host ""
Write-Host "Next: build and push the module images"
Write-Host "  .\scripts\windows\build_modules.ps1"

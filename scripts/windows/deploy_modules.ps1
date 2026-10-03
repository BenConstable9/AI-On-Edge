#!/usr/bin/env pwsh
# Fills the placeholders in deployment.template.json from .env and applies the
# result to the IoT Edge device.
#
# Usage: .\deploy_modules.ps1 [-DeviceId <device>] [-HubName <hub>]

[CmdletBinding()]
param(
    [Parameter()]
    [string]$DeviceId,

    [Parameter()]
    [string]$HubName
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $ProjectRoot

$EnvFile = Join-Path $ProjectRoot ".env"
if (-not (Test-Path $EnvFile)) {
    throw ".env not found. Run scripts\windows\deploy_infra.ps1 first."
}

$Settings = @{}
Get-Content $EnvFile | Where-Object { $_ -match '^\s*[^#].*=' } | ForEach-Object {
    $key, $value = $_ -split '=', 2
    $Settings[$key.Trim()] = $value.Trim()
}

if (-not $DeviceId) { $DeviceId = $Settings['IOT_DEVICE_ID'] }
if (-not $HubName) { $HubName = $Settings['IOT_HUB_NAME'] }
if (-not $DeviceId -or -not $HubName) {
    throw "IOT_DEVICE_ID and IOT_HUB_NAME must be in .env or passed as parameters"
}

# The same tags build_modules.ps1 gives: the last commit that changed each folder.
$Images = [ordered]@{
    VLLM_TAG   = @("vllm", "modules/vllm")
    CAMERA_TAG = @("camera-processor", "modules/camera_processor")
}
foreach ($key in $Images.Keys) {
    $image, $folder = $Images[$key]
    $tag = "git-" + (git log -1 --format=%H -- $folder ":(exclude)$folder/*.md").Substring(0, 10)
    az acr repository show --name $Settings['ACR_NAME'] --image "${image}:${tag}" -o none 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "${image}:${tag} is not in the registry. Run build_modules.ps1 first."
    }
    $Settings[$key] = $tag
    Write-Host "${image}: $tag"
}

$Template = Join-Path $ProjectRoot "deployment\deployment.template.json"
$Generated = Join-Path $ProjectRoot "deployment\deployment.generated.json"

Write-Host "Rendering $Template"

$content = Get-Content $Template -Raw

# Longest keys first so a key that is a prefix of another, such as
# $CONTAINER_REGISTRY, cannot replace part of the longer one.
foreach ($key in ($Settings.Keys | Sort-Object -Property Length -Descending)) {
    $value = $Settings[$key] -replace '\\', '\\\\' -replace '"', '\"'
    $content = $content.Replace("`$$key", $value)
}

$unresolved = [regex]::Matches($content, '\$[A-Z_]{3,}') | ForEach-Object { $_.Value } | Sort-Object -Unique
if ($unresolved) {
    throw "Unresolved placeholders in deployment template: $($unresolved -join ', ')"
}

# createOptions is JSON encoded inside JSON, so a malformed one parses fine as
# part of the manifest and only fails at set-modules. Parse it here instead.
try {
    $doc = $content | ConvertFrom-Json
} catch {
    throw "Rendered manifest is not valid JSON: $_"
}

$agent = $doc.modulesContent.'$edgeAgent'.'properties.desired'
foreach ($group in 'systemModules', 'modules') {
    foreach ($module in $agent.$group.PSObject.Properties) {
        $options = $module.Value.settings.createOptions
        if ($options) {
            try {
                $null = $options | ConvertFrom-Json
            } catch {
                throw "Module '$($module.Name)': createOptions is not valid JSON - $_"
            }
        }
    }
}

$moduleNames = $agent.modules.PSObject.Properties.Name
Write-Host "Manifest validated: $($moduleNames -join ', ')"

Set-Content -Path $Generated -Value $content -NoNewline
Write-Host "Wrote $Generated"

Write-Host "Applying deployment to device '$DeviceId' on hub '$HubName'"
az iot edge set-modules `
    --hub-name $HubName `
    --device-id $DeviceId `
    --content $Generated `
    --auth-type login

if ($LASTEXITCODE -ne 0) {
    throw "Deployment failed"
}

Write-Host ""
Write-Host "Deployment applied."
Write-Host ""
Write-Host "On first start the vllm module downloads 4.4 GB of weights. This takes"
Write-Host "several minutes. Detection and counting start before vLLM is ready."
Write-Host ""
Write-Host "Watch progress on the device with:"
Write-Host "  sudo iotedge list"
Write-Host "  sudo iotedge logs cameraprocessor -f"
Write-Host "  sudo iotedge logs vllm -f"
Write-Host ""
Write-Host "Preview on the local network: http://<device-ip>:8090/stream"

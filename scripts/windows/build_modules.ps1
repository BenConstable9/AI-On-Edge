#!/usr/bin/env pwsh
# Builds the vllm and camera-processor images for arm64 with ACR Tasks.
#
# ACR Tasks build arm64 images under emulation on amd64 hosts. That is enough:
# the images compile nothing, and the one stage that runs PyTorch runs on amd64.
#
# Each image is tagged with the last commit that changed its own folder. A
# change to one module then leaves the other's tag, and its running container,
# alone: a vLLM restart costs minutes. An image already in the registry is not
# built again unless -Force is given.
#
# Usage: .\build_modules.ps1 [-Module <vllm|camera-processor|all>] [-Force]

[CmdletBinding()]
param(
    [Parameter()]
    [ValidateSet("vllm", "camera-processor", "all")]
    [string]$Module = "all",

    [Parameter()]
    [switch]$Force
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

$AcrName = $Settings['ACR_NAME']
if (-not $AcrName) { throw "ACR_NAME missing from .env" }

Write-Host "Registry: $AcrName"

function Build-Module {
    param([string]$Name, [string]$Context)

    # The tag names a commit, so the folder must match it. A README is not in
    # the image, so a change to one must not rebuild or restart the module.
    $Paths = @($Context, ":(exclude)$Context/*.md")
    if (git status --porcelain -- @Paths) {
        throw "$Context has uncommitted changes. Commit them first."
    }
    $Tag = "git-" + (git log -1 --format=%H -- @Paths).Substring(0, 10)

    if (-not $Force) {
        az acr repository show --name $AcrName --image "${Name}:${Tag}" -o none 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "${Name}:${Tag} is already in the registry. Skipped."
            return
        }
    }

    # ACR uploads the working copy, not the commit, and a CRLF line ending in a
    # shell script or Dockerfile breaks the container on the device.
    $crlf = Get-ChildItem $Context -Recurse -File -Include *.sh, Dockerfile, *.Dockerfile |
        Where-Object { (Get-Content $_.FullName -Raw) -match "`r`n" }
    if ($crlf) {
        throw "CRLF line endings in $($crlf.FullName -join ', '). Re-check them out with git."
    }

    Write-Host ""
    Write-Host "Building $Name from $Context"

    # az resolves --file against the working directory rather than the build
    # context, so run from inside the context and pass '.' as the context.
    Push-Location $Context
    try {
        # ACR Tasks default to one hour. The first build pulls an 8.5 GB base.
        # camera-processor has a task file: its detector export must run on amd64.
        if (Test-Path "acr-task.yaml") {
            az acr run --registry $AcrName --platform linux/amd64 --timeout 7200 `
                --set "tag=$Tag" --file acr-task.yaml .
        } else {
            az acr build --registry $AcrName --image "${Name}:${Tag}" `
                --platform linux/arm64 --timeout 7200 --file Dockerfile .
        }
        if ($LASTEXITCODE -ne 0) {
            throw "$Name build failed. The task log above holds the cause."
        }
    }
    finally {
        Pop-Location
    }
    Write-Host "$Name pushed as ${AcrName}.azurecr.io/${Name}:${Tag}"
}

if ($Module -in @("vllm", "all")) {
    Build-Module -Name "vllm" -Context (Join-Path $ProjectRoot "modules\vllm")
}

if ($Module -in @("camera-processor", "all")) {
    Build-Module -Name "camera-processor" -Context (Join-Path $ProjectRoot "modules\camera_processor")
}

Write-Host ""
Write-Host "Build complete. Next: deploy to the device"
Write-Host "  .\scripts\windows\deploy_modules.ps1"

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$ProjectSerialPort,

    [Parameter(Mandatory)]
    [string]$IdfPath,

    [Parameter(Mandatory)]
    [string]$ModelPath,

    [Parameter(Mandatory)]
    [string]$ManifestPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$resolvedModel = (Resolve-Path -LiteralPath $ModelPath).Path
$resolvedManifest = (Resolve-Path -LiteralPath $ManifestPath).Path
$model = Get-Item -LiteralPath $resolvedModel
$manifest = Get-Content -LiteralPath $resolvedManifest -Raw -Encoding utf8 |
    ConvertFrom-Json

if ($manifest.target -ne "esp32c3" -or
    $manifest.engine -ne "WakeNet9s" -or
    $manifest.wake_phrase -ne "主人主人") {
    throw "Manifest does not describe the exact ESP32-C3 WakeNet9s '主人主人' model."
}
if ($manifest.model_status -ne "ready" -or
    [string]::IsNullOrWhiteSpace([string]$manifest.sha256) -or
    [string]::IsNullOrWhiteSpace([string]$manifest.license)) {
    throw "Model Gate 0 is closed. Set status=ready, sha256, and license only after receiving the official custom model."
}
if ($model.Length -le 0 -or
    $model.Length -gt [int64]$manifest.model_partition_max_bytes) {
    throw "Model image size $($model.Length) is outside the manifest limit."
}
$actualHash = (Get-FileHash -LiteralPath $resolvedModel -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -cne ([string]$manifest.sha256).ToLowerInvariant()) {
    throw "WakeNet model SHA-256 does not match the reviewed manifest."
}

$parttool = Join-Path $IdfPath "components\partition_table\parttool.py"
if (-not (Test-Path -LiteralPath $parttool)) {
    throw "ESP-IDF parttool was not found: $parttool"
}

Write-Warning "This command writes only the 1 MiB 'model' partition on $ProjectSerialPort."
Write-Warning "Disconnect all body electrodes, chargers, and grounded bench instruments first."
$confirmation = Read-Host "Type I CONFIRM NO ELECTRODES to continue"
if ($confirmation -cne "I CONFIRM NO ELECTRODES") {
    throw "WakeNet model provisioning cancelled; safety confirmation did not match."
}

& python $parttool `
    --port $ProjectSerialPort `
    write_partition `
    --partition-name model `
    --input $resolvedModel
if ($LASTEXITCODE -ne 0) {
    throw "WakeNet model partition write failed with exit code $LASTEXITCODE."
}

Write-Output "Exact WakeNet model partition written and SHA-256 verified."

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("build", "size", "flash")]
    [string]$Action = "build",

    [string]$Port,

    [ValidateRange(115200, 921600)]
    [int]$Baud = 460800
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$FirmwareDir = Join-Path $Root "experiments\inmp441_ble_capture\firmware"
$BuildDir = Join-Path $FirmwareDir "build"
$Sdkconfig = Join-Path $FirmwareDir "sdkconfig"
$LocalConfig = Join-Path $Root "config\local.ps1"

if (-not (Test-Path -LiteralPath $FirmwareDir)) {
    throw "INMP441 test firmware directory is missing: $FirmwareDir"
}

if ([string]::IsNullOrWhiteSpace($Port) -and (Test-Path -LiteralPath $LocalConfig)) {
    . $LocalConfig
    if (-not [string]::IsNullOrWhiteSpace($ProjectSerialPort)) {
        $Port = $ProjectSerialPort
    }
    if ($null -ne $ProjectFlashBaud) {
        $Baud = $ProjectFlashBaud
    }
}

if ($null -eq (Get-Command idf.py -ErrorAction SilentlyContinue)) {
    $IdfProfile = "C:\Espressif\tools\Microsoft.v6.0.2.PowerShell_profile.ps1"
    if (-not (Test-Path -LiteralPath $IdfProfile)) {
        throw "ESP-IDF PowerShell profile is missing: $IdfProfile"
    }
    . $IdfProfile
}

$IdfArguments = @(
    "-C", $FirmwareDir,
    "-B", $BuildDir,
    "-DIDF_TARGET=esp32c3",
    "-DSDKCONFIG=$Sdkconfig"
)

switch ($Action) {
    "build" {
        & idf.py @IdfArguments build
    }
    "size" {
        & idf.py @IdfArguments size
    }
    "flash" {
        if ([string]::IsNullOrWhiteSpace($Port)) {
            throw "Flash requires -Port COMxx or an ignored config/local.ps1."
        }
        Write-Warning "Flashing temporary INMP441-only firmware to $Port. No body electrodes may be connected."
        & idf.py @IdfArguments -p $Port -b $Baud flash
    }
}

if ($LASTEXITCODE -ne 0) {
    throw "idf.py $Action failed with exit code $LASTEXITCODE"
}

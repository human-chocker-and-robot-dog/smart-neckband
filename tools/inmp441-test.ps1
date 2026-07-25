[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("build", "size", "flash")]
    [string]$Action = "build",

    [string]$Port,

    [ValidateRange(115200, 921600)]
    [int]$Baud = 460800,

    [ValidateRange(1, 64)]
    [int]$BuildJobs = 4
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

if ([string]::IsNullOrWhiteSpace($env:IDF_PATH) -or
    [string]::IsNullOrWhiteSpace($env:IDF_PYTHON_ENV_PATH)) {
    $IdfProfile = "C:\Espressif\tools\Microsoft.v6.0.2.PowerShell_profile.ps1"
    if (-not (Test-Path -LiteralPath $IdfProfile)) {
        throw "ESP-IDF PowerShell profile is missing: $IdfProfile"
    }
    . $IdfProfile
}

function Invoke-Idf {
    param([Parameter(Mandatory)][string[]]$Arguments)

    $IdfPython = Join-Path $env:IDF_PYTHON_ENV_PATH "Scripts\python.exe"
    $IdfScript = Join-Path $env:IDF_PATH "tools\idf.py"
    if ((Test-Path -LiteralPath $IdfPython) -and
        (Test-Path -LiteralPath $IdfScript)) {
        & $IdfPython $IdfScript @Arguments
    }
    else {
        $IdfCommand = Get-Command idf.py -ErrorAction SilentlyContinue
        if ($null -eq $IdfCommand) {
            throw "ESP-IDF Python environment is unavailable."
        }
        & idf.py @Arguments
    }

    if ($LASTEXITCODE -ne 0) {
        throw "idf.py failed with exit code $LASTEXITCODE"
    }
}

function Get-NinjaPath {
    $NinjaCommand = Get-Command ninja.exe -ErrorAction SilentlyContinue
    if ($null -ne $NinjaCommand) {
        return $NinjaCommand.Source
    }

    $Ninja = Get-ChildItem -LiteralPath "C:\Espressif\tools\ninja" `
        -Recurse -Filter "ninja.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    if ($null -eq $Ninja) {
        throw "ninja.exe is unavailable."
    }
    return $Ninja.FullName
}

function Invoke-Ninja {
    param([ValidateSet("build", "size")][string]$RequestedAction)

    if (-not (Test-Path -LiteralPath (Join-Path $BuildDir "build.ninja"))) {
        Invoke-Idf -Arguments ($IdfArguments + "reconfigure")
    }

    $NinjaArguments = @("-C", $BuildDir, "-j", "$BuildJobs")
    if ($RequestedAction -eq "size") {
        $NinjaArguments += "size"
    }
    & (Get-NinjaPath) @NinjaArguments
    if ($LASTEXITCODE -ne 0) {
        throw "ninja $RequestedAction failed with exit code $LASTEXITCODE"
    }
}

$IdfArguments = @(
    "-C", $FirmwareDir,
    "-B", $BuildDir,
    "-DIDF_TARGET=esp32c3",
    "-DSDKCONFIG=$Sdkconfig"
)

switch ($Action) {
    "build" {
        Invoke-Ninja -RequestedAction "build"
    }
    "size" {
        Invoke-Ninja -RequestedAction "size"
    }
    "flash" {
        if ([string]::IsNullOrWhiteSpace($Port)) {
            throw "Flash requires -Port COMxx or an ignored config/local.ps1."
        }
        Write-Warning "Flashing temporary INMP441-only firmware to $Port. No body electrodes may be connected."
        Invoke-Ninja -RequestedAction "build"
        Invoke-Idf -Arguments ($IdfArguments + @("-p", $Port, "-b", "$Baud", "flash"))
    }
}

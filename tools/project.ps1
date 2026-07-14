[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet(
        "doctor",
        "set-target",
        "reconfigure",
        "menuconfig",
        "build",
        "size",
        "flash",
        "monitor",
        "flash-monitor",
        "erase-flash",
        "fullclean",
        "pc-setup",
        "pc-test"
    )]
    [string]$Action = "build"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$FirmwareDir = Join-Path $Root "firmware"
$PcDir = Join-Path $Root "pc_app"
$LocalConfig = Join-Path $Root "config\local.ps1"

if (-not (Test-Path -LiteralPath $LocalConfig)) {
    throw "Missing config/local.ps1. Copy config/local.example.ps1 first."
}

. $LocalConfig

function Invoke-Idf {
    param([Parameter(Mandatory)][string[]]$Arguments)

    $idf = Get-Command idf.py -ErrorAction SilentlyContinue
    if ($null -ne $idf) {
        & idf.py -C $FirmwareDir @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "idf.py failed with exit code $LASTEXITCODE"
        }
        return
    }

    $eim = Get-Command eim -ErrorAction SilentlyContinue
    if ($null -eq $eim) {
        throw "Neither idf.py nor eim is available in PATH."
    }

    $escapedFirmwareDir = $FirmwareDir.Replace('"', '\"')
    $commandParts = @("idf.py", "-C", ('"{0}"' -f $escapedFirmwareDir)) + $Arguments
    $commandLine = $commandParts -join " "
    & eim run $commandLine
    if ($LASTEXITCODE -ne 0) {
        throw "eim run failed with exit code $LASTEXITCODE"
    }
}

switch ($Action) {
    "doctor" {
        & (Join-Path $PSScriptRoot "doctor.ps1")
    }
    "set-target" {
        Invoke-Idf -Arguments @("set-target", "esp32")
    }
    "reconfigure" {
        Invoke-Idf -Arguments @("reconfigure")
    }
    "menuconfig" {
        Invoke-Idf -Arguments @("menuconfig")
    }
    "build" {
        Invoke-Idf -Arguments @("build")
    }
    "size" {
        Invoke-Idf -Arguments @("size")
    }
    "flash" {
        Invoke-Idf -Arguments @("-p", $ProjectSerialPort, "-b", "$ProjectFlashBaud", "flash")
    }
    "monitor" {
        Invoke-Idf -Arguments @("-p", $ProjectSerialPort, "monitor")
    }
    "flash-monitor" {
        Invoke-Idf -Arguments @("-p", $ProjectSerialPort, "-b", "$ProjectFlashBaud", "flash", "monitor")
    }
    "erase-flash" {
        Invoke-Idf -Arguments @("-p", $ProjectSerialPort, "erase-flash")
    }
    "fullclean" {
        Invoke-Idf -Arguments @("fullclean")
    }
    "pc-setup" {
        if (-not (Test-Path -LiteralPath $PcDir)) {
            throw "PC application directory does not exist: $PcDir"
        }
        Push-Location $PcDir
        try {
            if (-not (Test-Path -LiteralPath ".venv")) {
                & py -3.12 -m venv .venv
            }
            & .\.venv\Scripts\python.exe -m pip install --upgrade pip
            & .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
        }
        finally {
            Pop-Location
        }
    }
    "pc-test" {
        if (-not (Test-Path -LiteralPath (Join-Path $PcDir ".venv\Scripts\python.exe"))) {
            throw "PC virtual environment missing. Run .\tools\project.ps1 pc-setup first."
        }
        Push-Location $PcDir
        try {
            & .\.venv\Scripts\python.exe -m pytest
            if ($LASTEXITCODE -ne 0) {
                throw "pytest failed with exit code $LASTEXITCODE"
            }
        }
        finally {
            Pop-Location
        }
    }
}

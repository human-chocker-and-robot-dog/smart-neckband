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

function Invoke-Native {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$Description
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE"
    }
}

function Test-PythonModule {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$BaseArguments = @(),
        [Parameter(Mandatory)][string]$ModuleName
    )

    & $FilePath @BaseArguments -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('$ModuleName') else 1)" *> $null
    return $LASTEXITCODE -eq 0
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
            $venvPython = Join-Path $PcDir ".venv\Scripts\python.exe"
            $venvNeedsRepair = -not (Test-Path -LiteralPath $venvPython)
            if (-not $venvNeedsRepair) {
                $venvNeedsRepair = -not (Test-PythonModule -FilePath $venvPython -ModuleName "pip")
            }

            if ($venvNeedsRepair) {
                $venvArgs = @("-3.12", "-m", "venv")
                if (Test-Path -LiteralPath ".venv") {
                    $venvArgs += "--clear"
                }
                $venvArgs += ".venv"
                Invoke-Native -FilePath "py" -Arguments $venvArgs -Description "Python virtual environment creation"
            }

            Invoke-Native -FilePath $venvPython -Arguments @("-m", "pip", "install", "pytest>=8") -Description "pytest installation"
        }
        finally {
            Pop-Location
        }
    }
    "pc-test" {
        $venvPython = Join-Path $PcDir ".venv\Scripts\python.exe"
        $pythonPath = "py"
        $pythonArgs = @("-3.12")

        if ((Test-Path -LiteralPath $venvPython) -and
            (Test-PythonModule -FilePath $venvPython -ModuleName "pytest")) {
            $pythonPath = $venvPython
            $pythonArgs = @()
        }

        $srcPath = Join-Path $PcDir "src"
        $oldPythonPath = $env:PYTHONPATH
        if ([string]::IsNullOrWhiteSpace($oldPythonPath)) {
            $env:PYTHONPATH = $srcPath
        } else {
            $env:PYTHONPATH = "$srcPath;$oldPythonPath"
        }

        Push-Location $PcDir
        try {
            Invoke-Native -FilePath $pythonPath -Arguments ($pythonArgs + @("-m", "pytest")) -Description "pytest"
        }
        finally {
            $env:PYTHONPATH = $oldPythonPath
            Pop-Location
        }
    }
}

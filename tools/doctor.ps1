[CmdletBinding()]
param(
    [ValidateSet("esp32c3")]
    [string]$Target
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$LocalConfig = Join-Path $Root "config\local.ps1"

if (-not (Test-Path -LiteralPath $LocalConfig)) {
    throw "Missing config/local.ps1. Copy config/local.example.ps1 first."
}

. $LocalConfig

if ([string]::IsNullOrWhiteSpace($Target)) {
    $Target = $ExpectedTarget
}
if ($Target -ne "esp32c3") {
    throw "Unsupported target '$Target'. Expected esp32c3."
}

function Assert-Command {
    param([Parameter(Mandatory)][string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "Required command not found: $Name"
    }
    return $command.Source
}

Write-Host "Repository: $Root"
Write-Host "PowerShell: $($PSVersionTable.PSVersion)"
Write-Host "Git: $(Assert-Command -Name git)"
Write-Host "Python launcher: $(Assert-Command -Name py)"

$idfPython = $null
$idfScript = $null
if (-not [string]::IsNullOrWhiteSpace($env:IDF_PATH) -and
    -not [string]::IsNullOrWhiteSpace($env:IDF_PYTHON_ENV_PATH)) {
    $candidatePython = Join-Path $env:IDF_PYTHON_ENV_PATH "Scripts\python.exe"
    $candidateScript = Join-Path $env:IDF_PATH "tools\idf.py"
    if ((Test-Path -LiteralPath $candidatePython) -and
        (Test-Path -LiteralPath $candidateScript)) {
        $idfPython = $candidatePython
        $idfScript = $candidateScript
    }
}

$idf = Get-Command idf.py -ErrorAction SilentlyContinue
$eim = Get-Command eim -ErrorAction SilentlyContinue

if ($null -eq $idfPython -and $null -eq $idf -and $null -eq $eim) {
    throw "Neither idf.py nor eim is available in PATH. Open an ESP-IDF PowerShell or install EIM."
}

if ($null -ne $idfPython) {
    $idfVersion = (& $idfPython $idfScript --version | Out-String).Trim()
} elseif ($null -ne $idf) {
    $idfVersion = (& idf.py --version | Out-String).Trim()
} else {
    $idfVersion = (& eim run "idf.py --version" | Out-String).Trim()
}

Write-Host "ESP-IDF: $idfVersion"
if ($idfVersion -notmatch [regex]::Escape($ExpectedIdfVersion)) {
    Write-Warning "Expected ESP-IDF $ExpectedIdfVersion but found: $idfVersion"
}

Write-Host "Configured serial port: $ProjectSerialPort"
Write-Host "Configured default target: $ExpectedTarget"
Write-Host "Selected target: $Target"
Write-Host "Selected build directory: firmware/build-$Target"
Write-Host "Selected sdkconfig: firmware/sdkconfig.$Target"
Write-Host "Expected flash size: $ExpectedFlashSize"
Write-Host "Doctor check completed."

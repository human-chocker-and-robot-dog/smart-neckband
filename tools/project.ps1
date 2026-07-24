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
        "voice-provision",
        "voice-model-provision",
        "pc-setup",
        "pc-gui",
        "pc-test"
    )]
    [string]$Action = "build",

    [ValidateSet("esp32", "esp32c3")]
    [string]$Target,

    [switch]$Voice,

    [string]$WakeNetModelPath
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

if ([string]::IsNullOrWhiteSpace($Target)) {
    $Target = $ExpectedTarget
}
if ($Target -notin @("esp32", "esp32c3")) {
    throw "Unsupported target '$Target'. Expected esp32 or esp32c3."
}
if ($Voice -and $Target -ne "esp32c3") {
    throw "-Voice is supported only with -Target esp32c3."
}
if ($Action -in @("voice-provision", "voice-model-provision") -and -not $Voice) {
    throw "$Action requires -Voice."
}
if ($Action -eq "voice-model-provision" -and
    [string]::IsNullOrWhiteSpace($WakeNetModelPath)) {
    throw "voice-model-provision requires -WakeNetModelPath."
}

$BuildFlavor = if ($Voice) { "$Target-voice" } else { $Target }
$BuildDir = Join-Path $FirmwareDir "build-$BuildFlavor"
$SdkconfigPath = Join-Path $FirmwareDir "sdkconfig.$BuildFlavor"
$TargetArguments = @(
    "-B", $BuildDir,
    "-DIDF_TARGET=$Target",
    "-DSDKCONFIG=$SdkconfigPath"
)
if ($Voice) {
    $voiceDefaults = @(
        "sdkconfig.defaults",
        "sdkconfig.defaults.esp32c3",
        "sdkconfig.defaults.voice"
    ) -join ";"
    $TargetArguments += @(
        "-DSMART_NECKBAND_VOICE=ON",
        "-DSDKCONFIG_DEFAULTS=$voiceDefaults"
    )
}

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
    try {
        & eim run $commandLine
        $lastExit = Get-Variable -Name LASTEXITCODE -ValueOnly -ErrorAction SilentlyContinue
        if (($null -ne $lastExit) -and ($lastExit -eq 0)) {
            return
        }
    }
    catch {
        Write-Warning "eim run failed: $($_.Exception.Message)"
    }

    if (Invoke-IdfLocalBuildFallback -Arguments $Arguments) {
        return
    }

    $fallbackExit = Get-Variable -Name LASTEXITCODE -ValueOnly -ErrorAction SilentlyContinue
    $message = "eim run failed"
    if ($null -ne $fallbackExit) {
        $message = "$message with exit code $fallbackExit"
    }
    throw $message
}

function Invoke-TargetIdf {
    param([Parameter(Mandatory)][string[]]$Arguments)

    Invoke-Idf -Arguments ($TargetArguments + $Arguments)
}

function Add-PathPrefix {
    param([Parameter(Mandatory)][string]$PathPrefix)

    if ((Test-Path -LiteralPath $PathPrefix) -and
        -not (($env:PATH -split ';') -contains $PathPrefix)) {
        $env:PATH = "$PathPrefix;$env:PATH"
    }
}

function Initialize-LocalIdfToolEnvironment {
    $idfRoot = Join-Path "C:\Espressif" $ExpectedIdfVersion
    $idfPath = Join-Path $idfRoot "esp-idf"
    if (-not (Test-Path -LiteralPath $idfPath)) {
        throw "ESP-IDF path not found: $idfPath"
    }
    $env:IDF_PATH = $idfPath

    $ccache = Get-ChildItem -LiteralPath "C:\Espressif\tools\ccache" -Recurse -Filter "ccache.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    if ($null -ne $ccache) {
        Add-PathPrefix -PathPrefix (Split-Path -Parent $ccache.FullName)
    }

    $compilerRoot = if ($Target -eq "esp32c3") {
        "C:\Espressif\tools\riscv32-esp-elf"
    } else {
        "C:\Espressif\tools\xtensa-esp-elf"
    }
    $compilerName = if ($Target -eq "esp32c3") {
        "riscv32-esp-elf-gcc.exe"
    } else {
        "xtensa-esp32-elf-gcc.exe"
    }
    $targetGcc = Get-ChildItem -LiteralPath $compilerRoot -Recurse -Filter $compilerName -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    if ($null -ne $targetGcc) {
        Add-PathPrefix -PathPrefix (Split-Path -Parent $targetGcc.FullName)
    }

    $ninja = Get-ChildItem -LiteralPath "C:\Espressif\tools\ninja" -Recurse -Filter "ninja.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    if ($null -eq $ninja) {
        throw "ninja.exe not found under C:\Espressif\tools\ninja"
    }
    Add-PathPrefix -PathPrefix (Split-Path -Parent $ninja.FullName)

    $romElfName = if ($Target -eq "esp32c3") {
        "esp32c3_rev0_rom.elf"
    } else {
        "esp32_rev0_rom.elf"
    }
    $romElf = Get-ChildItem -LiteralPath "C:\Espressif\tools\esp-rom-elfs" -Recurse -Filter $romElfName -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    if ($null -ne $romElf) {
        $env:ESP_ROM_ELF_DIR = Split-Path -Parent $romElf.FullName
    }

    return $ninja.FullName
}

function Invoke-IdfLocalBuildFallback {
    param([Parameter(Mandatory)][string[]]$Arguments)

    if ($Arguments.Count -eq 0) {
        return $false
    }

    $requestedAction = $Arguments[$Arguments.Count - 1]
    if ($requestedAction -notin @("build", "size")) {
        return $false
    }

    $buildNinja = Join-Path $BuildDir "build.ninja"
    if (-not (Test-Path -LiteralPath $buildNinja)) {
        return $false
    }

    Write-Warning "Falling back to local Ninja for idf.py $requestedAction ($Target)."
    $ninja = Initialize-LocalIdfToolEnvironment
    $ninjaArgs = @("-C", $BuildDir)
    if ($requestedAction -eq "size") {
        $ninjaArgs += "size"
    }

    Invoke-Native -FilePath $ninja -Arguments $ninjaArgs -Description "ninja $requestedAction ($Target)"
    return $true
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
        & (Join-Path $PSScriptRoot "doctor.ps1") -Target $Target
    }
    "set-target" {
        Invoke-TargetIdf -Arguments @("set-target", $Target)
    }
    "reconfigure" {
        Invoke-TargetIdf -Arguments @("reconfigure")
    }
    "menuconfig" {
        Invoke-TargetIdf -Arguments @("menuconfig")
    }
    "build" {
        Invoke-TargetIdf -Arguments @("build")
    }
    "size" {
        Invoke-TargetIdf -Arguments @("size")
    }
    "flash" {
        Invoke-TargetIdf -Arguments @("-p", $ProjectSerialPort, "-b", "$ProjectFlashBaud", "flash")
    }
    "monitor" {
        Invoke-TargetIdf -Arguments @("-p", $ProjectSerialPort, "monitor")
    }
    "flash-monitor" {
        Invoke-TargetIdf -Arguments @("-p", $ProjectSerialPort, "-b", "$ProjectFlashBaud", "flash", "monitor")
    }
    "erase-flash" {
        Invoke-TargetIdf -Arguments @("-p", $ProjectSerialPort, "erase-flash")
    }
    "fullclean" {
        Invoke-TargetIdf -Arguments @("fullclean")
    }
    "voice-provision" {
        $provisionScript = Join-Path $PSScriptRoot "voice-provision.ps1"
        if (-not (Test-Path -LiteralPath $provisionScript)) {
            throw "Missing voice provisioning script: $provisionScript"
        }
        & $provisionScript `
            -ProjectSerialPort $ProjectSerialPort `
            -IdfPath $env:IDF_PATH
    }
    "voice-model-provision" {
        $modelProvisionScript = Join-Path $PSScriptRoot "voice-model-provision.ps1"
        if (-not (Test-Path -LiteralPath $modelProvisionScript)) {
            throw "Missing WakeNet model provisioning script: $modelProvisionScript"
        }
        & $modelProvisionScript `
            -ProjectSerialPort $ProjectSerialPort `
            -IdfPath $env:IDF_PATH `
            -ModelPath $WakeNetModelPath `
            -ManifestPath (Join-Path $FirmwareDir "models\wakenet_manifest.json")
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

            Invoke-Native -FilePath $venvPython -Arguments @(
                "-m", "pip", "install", "-e", ".[dev,gui,serial,health]"
            ) -Description "PC application and BLE GUI dependency installation"
        }
        finally {
            Pop-Location
        }
    }
    "pc-gui" {
        $venvPython = Join-Path $PcDir ".venv\Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $venvPython)) {
            throw "PC virtual environment is missing. Run '.\tools\project.ps1 pc-setup' first."
        }
        foreach ($moduleName in @("bleak", "PySide6")) {
            if (-not (Test-PythonModule -FilePath $venvPython -ModuleName $moduleName)) {
                throw "PC module '$moduleName' is missing from .venv. Run '.\tools\project.ps1 pc-setup' first."
            }
        }
        Push-Location $PcDir
        try {
            Invoke-Native -FilePath $venvPython -Arguments @(
                "-m", "smart_neckband"
            ) -Description "PC GUI"
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
            $pytestBaseTemp = Join-Path $PcDir ("pytest-cache-files-{0}" -f [guid]::NewGuid().ToString("N"))
            Invoke-Native -FilePath $pythonPath -Arguments (
                $pythonArgs + @("-m", "pytest", "--basetemp", $pytestBaseTemp)
            ) -Description "pytest"
        }
        finally {
            $env:PYTHONPATH = $oldPythonPath
            Pop-Location
        }
    }
}

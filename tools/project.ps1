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
        "pc-gui",
        "pc-mic",
        "pc-health-mcp",
        "pc-health-mcp-http",
        "pc-health-status",
        "pc-health-soak",
        "pc-test"
    )]
    [string]$Action = "build",

    [ValidateSet("esp32c3")]
    [string]$Target,

    [switch]$SensorsOnly,

    [ValidateRange(1, 64)]
    [int]$BuildJobs = 4,

    [ValidateRange(1, 1440)]
    [int]$HealthSoakMinutes = 5,

    [string]$HealthSoakDbPath
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
if ($Target -ne "esp32c3") {
    throw "Unsupported target '$Target'. Expected esp32c3."
}
$BuildFlavor = if ($SensorsOnly) { "$Target-sensors-only" } else { "$Target-unified" }
$BuildDirFlavor = if ($SensorsOnly) { "c3-sensors-only" } else { "c3-unified" }
$BuildDir = Join-Path $FirmwareDir "build-$BuildDirFlavor"
$SdkconfigPath = Join-Path $FirmwareDir "sdkconfig.$BuildFlavor"
$TargetArguments = @(
    "-B", $BuildDir,
    "-DIDF_TARGET=$Target",
    "-DSDKCONFIG=$SdkconfigPath",
    ("-DSMART_NECKBAND_MIC={0}" -f $(if ($SensorsOnly) { "OFF" } else { "ON" }))
)

function Invoke-Idf {
    param([Parameter(Mandatory)][string[]]$Arguments)

    if (-not [string]::IsNullOrWhiteSpace($env:IDF_PATH) -and
        -not [string]::IsNullOrWhiteSpace($env:IDF_PYTHON_ENV_PATH)) {
        $idfPython = Join-Path $env:IDF_PYTHON_ENV_PATH "Scripts\python.exe"
        $idfScript = Join-Path $env:IDF_PATH "tools\idf.py"
        if ((Test-Path -LiteralPath $idfPython) -and
            (Test-Path -LiteralPath $idfScript)) {
            Add-PathPrefix -PathPrefix (Split-Path -Parent $idfPython)
            $ninjaPath = Initialize-LocalIdfToolEnvironment
            $cCompiler = Get-Command riscv32-esp-elf-gcc.exe -ErrorAction Stop
            $cxxCompiler = Get-Command riscv32-esp-elf-g++.exe -ErrorAction Stop
            $env:CC = $cCompiler.Source
            $env:CXX = $cxxCompiler.Source
            $env:ASM = $cCompiler.Source
            $Arguments = @(
                "-DCMAKE_MAKE_PROGRAM=$ninjaPath",
                "-DCMAKE_C_COMPILER=$($cCompiler.Source)",
                "-DCMAKE_CXX_COMPILER=$($cxxCompiler.Source)",
                "-DCMAKE_ASM_COMPILER=$($cCompiler.Source)"
            ) + $Arguments
            & $idfPython $idfScript -C $FirmwareDir @Arguments
            if ($LASTEXITCODE -ne 0) {
                throw "idf.py failed with exit code $LASTEXITCODE"
            }
            return
        }
    }

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

function Invoke-TargetNinja {
    param([ValidateSet("build", "size")][string]$RequestedAction)

    $buildNinja = Join-Path $BuildDir "build.ninja"
    if (-not (Test-Path -LiteralPath $buildNinja)) {
        Invoke-TargetIdf -Arguments @("reconfigure")
    }

    $ninja = Initialize-LocalIdfToolEnvironment
    $ninjaArguments = @("-C", $BuildDir, "-j", "$BuildJobs")
    if ($RequestedAction -eq "size") {
        $ninjaArguments += "size"
    }
    Invoke-Native `
        -FilePath $ninja `
        -Arguments $ninjaArguments `
        -Description "ninja $RequestedAction ($Target, $BuildJobs jobs)"
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
    $env:ESP_IDF_VERSION = $ExpectedIdfVersion.TrimStart("v")

    $ccache = Get-ChildItem -LiteralPath "C:\Espressif\tools\ccache" -Recurse -Filter "ccache.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    if ($null -ne $ccache) {
        Add-PathPrefix -PathPrefix (Split-Path -Parent $ccache.FullName)
    }

    $compilerRoot = "C:\Espressif\tools\riscv32-esp-elf"
    $compilerName = "riscv32-esp-elf-gcc.exe"
    $targetGcc = Get-ChildItem -LiteralPath $compilerRoot -Recurse -Filter $compilerName -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    if ($null -ne $targetGcc) {
        Add-PathPrefix -PathPrefix (Split-Path -Parent $targetGcc.FullName)
    }

    $cmake = Get-ChildItem -LiteralPath "C:\Espressif\tools\cmake" -Recurse -Filter "cmake.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    if ($null -eq $cmake) {
        throw "cmake.exe not found under C:\Espressif\tools\cmake"
    }
    Add-PathPrefix -PathPrefix (Split-Path -Parent $cmake.FullName)

    $ninja = Get-ChildItem -LiteralPath "C:\Espressif\tools\ninja" -Recurse -Filter "ninja.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    if ($null -eq $ninja) {
        throw "ninja.exe not found under C:\Espressif\tools\ninja"
    }
    Add-PathPrefix -PathPrefix (Split-Path -Parent $ninja.FullName)

    $romElfName = "esp32c3_rev0_rom.elf"
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

function Invoke-PcPython {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$Description
    )

    $srcPath = Join-Path $PcDir "src"
    $oldPythonPath = $env:PYTHONPATH
    try {
        if ([string]::IsNullOrWhiteSpace($oldPythonPath)) {
            $env:PYTHONPATH = $srcPath
        }
        else {
            $env:PYTHONPATH = "$srcPath;$oldPythonPath"
        }
        Invoke-Native -FilePath $FilePath -Arguments $Arguments -Description $Description
    }
    finally {
        $env:PYTHONPATH = $oldPythonPath
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

function Get-PcVenvPythonCommand {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $py) {
        try {
            & py -3.12 -c "import sys" *> $null
            if ($LASTEXITCODE -eq 0) {
                return @{
                    FilePath = "py"
                    Arguments = @("-3.12", "-m", "venv")
                }
            }
        }
        catch {
        }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $python) {
        & $python.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" *> $null
        if ($LASTEXITCODE -eq 0) {
            return @{
                FilePath = $python.Source
                Arguments = @("-m", "venv")
            }
        }
    }

    throw "Python >= 3.11 is required for the PC app, but neither 'py -3.12' nor 'python' is usable."
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
        Invoke-TargetNinja -RequestedAction "build"
    }
    "size" {
        Invoke-TargetNinja -RequestedAction "size"
    }
    "flash" {
        Invoke-TargetNinja -RequestedAction "build"
        Invoke-TargetIdf -Arguments @("-p", $ProjectSerialPort, "-b", "$ProjectFlashBaud", "flash")
    }
    "monitor" {
        Invoke-TargetIdf -Arguments @("-p", $ProjectSerialPort, "monitor")
    }
    "flash-monitor" {
        Invoke-TargetNinja -RequestedAction "build"
        Invoke-TargetIdf -Arguments @("-p", $ProjectSerialPort, "-b", "$ProjectFlashBaud", "flash", "monitor")
    }
    "erase-flash" {
        Invoke-TargetIdf -Arguments @("-p", $ProjectSerialPort, "erase-flash")
    }
    "fullclean" {
        Invoke-TargetIdf -Arguments @("fullclean")
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
                try {
                    $venvNeedsRepair = -not (Test-PythonModule -FilePath $venvPython -ModuleName "pip")
                }
                catch {
                    $venvNeedsRepair = $true
                }
            }

            if ($venvNeedsRepair) {
                $venvCommand = Get-PcVenvPythonCommand
                $venvArgs = @($venvCommand.Arguments)
                if (Test-Path -LiteralPath ".venv") {
                    $venvArgs += "--clear"
                }
                $venvArgs += ".venv"
                Invoke-Native -FilePath $venvCommand.FilePath -Arguments $venvArgs -Description "Python virtual environment creation"
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
            Invoke-PcPython -FilePath $venvPython -Arguments @(
                "-m", "smart_neckband"
            ) -Description "PC GUI"
        }
        finally {
            Pop-Location
        }
    }
    "pc-mic" {
        $venvPython = Join-Path $PcDir ".venv\Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $venvPython)) {
            throw "PC virtual environment is missing. Run '.\tools\project.ps1 pc-setup' first."
        }
        foreach ($moduleName in @("bleak", "numpy", "pyqtgraph", "PySide6", "websocket")) {
            if (-not (Test-PythonModule -FilePath $venvPython -ModuleName $moduleName)) {
                throw "PC module '$moduleName' is missing from .venv. Run '.\tools\project.ps1 pc-setup' first."
            }
        }
        Push-Location $PcDir
        try {
            Invoke-PcPython -FilePath $venvPython -Arguments @(
                "-m", "smart_neckband.mic_capture_gui"
            ) -Description "INMP441 BLE capture GUI"
        }
        finally {
            Pop-Location
        }
    }
    "pc-health-mcp" {
        $venvPython = Join-Path $PcDir ".venv\Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $venvPython)) {
            throw "PC virtual environment is missing. Run '.\tools\project.ps1 pc-setup' first."
        }
        if ([string]::IsNullOrWhiteSpace($env:SMART_COLLAR_WEARER_ID)) {
            throw "SMART_COLLAR_WEARER_ID must be set before starting Health MCP."
        }
        Push-Location $PcDir
        try {
            Invoke-PcPython -FilePath $venvPython -Arguments @(
                "-m", "smart_neckband.health_mcp", "--transport", "stdio"
            ) -Description "Health MCP stdio"
        }
        finally {
            Pop-Location
        }
    }
    "pc-health-mcp-http" {
        $venvPython = Join-Path $PcDir ".venv\Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $venvPython)) {
            throw "PC virtual environment is missing. Run '.\tools\project.ps1 pc-setup' first."
        }
        if ([string]::IsNullOrWhiteSpace($env:SMART_COLLAR_WEARER_ID)) {
            throw "SMART_COLLAR_WEARER_ID must be set before starting Health MCP."
        }
        if ([string]::IsNullOrWhiteSpace($env:SMART_COLLAR_HEALTH_MCP_BEARER_TOKEN)) {
            throw "SMART_COLLAR_HEALTH_MCP_BEARER_TOKEN must be set for Streamable HTTP."
        }
        $mcpHost = if ([string]::IsNullOrWhiteSpace($env:SMART_COLLAR_HEALTH_MCP_HOST)) {
            "0.0.0.0"
        }
        else {
            $env:SMART_COLLAR_HEALTH_MCP_HOST
        }
        $mcpPort = if ([string]::IsNullOrWhiteSpace($env:SMART_COLLAR_HEALTH_MCP_PORT)) {
            "8765"
        }
        else {
            $env:SMART_COLLAR_HEALTH_MCP_PORT
        }
        $mcpPath = if ([string]::IsNullOrWhiteSpace($env:SMART_COLLAR_HEALTH_MCP_PATH)) {
            "/mcp"
        }
        else {
            $env:SMART_COLLAR_HEALTH_MCP_PATH
        }
        $allowedHosts = @(
            $env:SMART_COLLAR_HEALTH_MCP_ALLOWED_HOSTS -split "," |
                ForEach-Object { $_.Trim() } |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
        )
        if ($allowedHosts.Count -eq 0) {
            throw "SMART_COLLAR_HEALTH_MCP_ALLOWED_HOSTS must list the Windows host:port used by the RDK."
        }
        $arguments = @(
            "-m", "smart_neckband.health_mcp",
            "--transport", "streamable-http",
            "--host", $mcpHost,
            "--port", $mcpPort,
            "--path", $mcpPath
        )
        foreach ($allowedHost in $allowedHosts) {
            $arguments += @("--allowed-host", $allowedHost)
        }
        Push-Location $PcDir
        try {
            Invoke-PcPython -FilePath $venvPython -Arguments $arguments -Description "Health MCP Streamable HTTP"
        }
        finally {
            Pop-Location
        }
    }
    "pc-health-status" {
        $venvPython = Join-Path $PcDir ".venv\Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $venvPython)) {
            throw "PC virtual environment is missing. Run '.\tools\project.ps1 pc-setup' first."
        }
        if ([string]::IsNullOrWhiteSpace($env:SMART_COLLAR_WEARER_ID)) {
            throw "SMART_COLLAR_WEARER_ID must be set before reading Health status."
        }
        Push-Location $PcDir
        try {
            $statusArguments = @("-m", "smart_neckband.health_admin")
            if (-not [string]::IsNullOrWhiteSpace($env:SMART_COLLAR_HEALTH_DB_PATH)) {
                $statusArguments += @("--db", $env:SMART_COLLAR_HEALTH_DB_PATH)
            }
            $statusArguments += @(
                "status",
                "--wearer-id", $env:SMART_COLLAR_WEARER_ID
            )
            Invoke-PcPython -FilePath $venvPython -Arguments $statusArguments -Description "Health status"
        }
        finally {
            Pop-Location
        }
    }
    "pc-health-soak" {
        $venvPython = Join-Path $PcDir ".venv\Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $venvPython)) {
            throw "PC virtual environment is missing. Run '.\tools\project.ps1 pc-setup' first."
        }
        $healthDir = Join-Path $Root "data\health"
        New-Item -ItemType Directory -Force -Path $healthDir | Out-Null
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $dbPath = if ([string]::IsNullOrWhiteSpace($HealthSoakDbPath)) {
            Join-Path $healthDir "soak-$stamp.sqlite3"
        }
        else {
            $HealthSoakDbPath
        }
        if (-not [System.IO.Path]::IsPathRooted($dbPath)) {
            $dbPath = Join-Path $Root $dbPath
        }
        $resultPath = "$dbPath.result.json"
        Push-Location $PcDir
        try {
            Invoke-PcPython -FilePath $venvPython -Arguments @(
                "-m", "smart_neckband.health_soak",
                "--db", $dbPath,
                "--duration-s", ($HealthSoakMinutes * 60),
                "--interval-s", "0.5",
                "--result-path", $resultPath
            ) -Description "Health MCP synthetic soak"
        }
        finally {
            Pop-Location
        }
        Write-Host "Health soak database: $dbPath"
        Write-Host "Health soak result: $resultPath"
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

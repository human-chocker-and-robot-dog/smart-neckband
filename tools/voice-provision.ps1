[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$ProjectSerialPort,

    [Parameter(Mandatory)]
    [string]$IdfPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Read-SecretText {
    param([Parameter(Mandatory)][string]$Prompt)

    $secure = Read-Host $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function ConvertTo-NvsCsvField {
    param([Parameter(Mandatory)][string]$Value)

    if ($Value.Contains("`r") -or $Value.Contains("`n")) {
        throw "NVS string values cannot contain newlines."
    }
    return '"' + $Value.Replace('"', '""') + '"'
}

if (-not (Test-Path -LiteralPath $IdfPath)) {
    throw "ESP-IDF path does not exist: $IdfPath"
}

Write-Warning "This command writes only the 'voicecfg' NVS partition on $ProjectSerialPort."
Write-Warning "Plain NVS is not hardware-secure unless Flash Encryption is enabled."
Write-Warning "Disconnect all body electrodes, chargers, and grounded bench instruments first."
$confirmation = Read-Host "Type I CONFIRM NO ELECTRODES to continue"
if ($confirmation -cne "I CONFIRM NO ELECTRODES") {
    throw "Voice provisioning cancelled; safety confirmation did not match."
}

$wifiSsid = Read-Host "Wi-Fi SSID"
$wifiPassword = Read-SecretText "Wi-Fi password"
$authMode = (Read-Host "Volcengine auth mode (api_key or legacy)").Trim().ToLowerInvariant()
if ($authMode -notin @("api_key", "legacy")) {
    throw "Auth mode must be exactly 'api_key' or 'legacy'."
}
$appId = ""
$apiKey = ""
$accessToken = ""
if ($authMode -eq "api_key") {
    $apiKey = Read-SecretText "Volcengine API key"
}
else {
    $appId = Read-Host "Volcengine AppID"
    $accessToken = Read-SecretText "Volcengine Access Key"
}
$resourceId = Read-Host "Volcengine Resource ID"

$tempRoot = Join-Path ([IO.Path]::GetTempPath()) (
    "smart-neckband-voicecfg-" + [guid]::NewGuid().ToString("N")
)
$resolvedTempParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$resolvedTempRoot = [IO.Path]::GetFullPath($tempRoot)
if (-not $resolvedTempRoot.StartsWith($resolvedTempParent, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing unexpected temporary path: $resolvedTempRoot"
}

$csvPath = Join-Path $tempRoot "voicecfg.csv"
$binPath = Join-Path $tempRoot "voicecfg.bin"
$generator = Join-Path $IdfPath "components\nvs_flash\nvs_partition_generator\nvs_partition_gen.py"
$parttool = Join-Path $IdfPath "components\partition_table\parttool.py"

try {
    [IO.Directory]::CreateDirectory($tempRoot) | Out-Null
    $rows = @(
        "key,type,encoding,value",
        "voicecfg,namespace,,",
        "schema_ver,data,u32,1",
        "wifi_ssid,data,string,$(ConvertTo-NvsCsvField $wifiSsid)",
        "wifi_pass,data,string,$(ConvertTo-NvsCsvField $wifiPassword)",
        "auth_mode,data,string,$authMode",
        "app_id,data,string,$(ConvertTo-NvsCsvField $appId)",
        "api_key,data,string,$(ConvertTo-NvsCsvField $apiKey)",
        "access_token,data,string,$(ConvertTo-NvsCsvField $accessToken)",
        "resource_id,data,string,$(ConvertTo-NvsCsvField $resourceId)"
    )
    [IO.File]::WriteAllLines($csvPath, $rows, [Text.UTF8Encoding]::new($false))

    & python $generator generate $csvPath $binPath 0x6000 --version 2
    if ($LASTEXITCODE -ne 0) {
        throw "NVS partition generation failed with exit code $LASTEXITCODE."
    }
    & python $parttool `
        --port $ProjectSerialPort `
        write_partition `
        --partition-name voicecfg `
        --input $binPath
    if ($LASTEXITCODE -ne 0) {
        throw "voicecfg partition write failed with exit code $LASTEXITCODE."
    }
    Write-Output "voicecfg was written successfully; no application or model partition was changed."
}
finally {
    $wifiPassword = $null
    $apiKey = $null
    $accessToken = $null
    if (Test-Path -LiteralPath $resolvedTempRoot) {
        Remove-Item -LiteralPath $resolvedTempRoot -Recurse -Force
    }
}

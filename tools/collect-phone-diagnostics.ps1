[CmdletBinding()]
param(
    [string]$Serial,
    [string]$AdbPath,
    [string]$OutputDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
if (-not $AdbPath) { $AdbPath = Join-Path $repositoryRoot '.local-tools/android-sdk/platform-tools/adb.exe' }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repositoryRoot 'data/diagnostics' }
$AdbPath = (Resolve-Path -LiteralPath $AdbPath).Path

if (-not $Serial) {
    $deviceLines = & $AdbPath devices
    if ($LASTEXITCODE -ne 0) { throw 'ADB device enumeration failed.' }
    $devices = @($deviceLines | Where-Object { $_ -match '^\S+\s+device$' } | ForEach-Object { ($_ -split '\s+')[0] })
    if ($devices.Count -ne 1) { throw 'Connect one authorized device or specify -Serial.' }
    $Serial = $devices[0]
}

$remoteFile = 'files/diagnostics/latest.jsonl'
$null = & $AdbPath -s $Serial shell run-as com.smartneckband.companion ls $remoteFile
if ($LASTEXITCODE -ne 0) { throw 'No saved capture. Tap Settings > 捕获诊断 first (debug APK required).' }
$null = New-Item -ItemType Directory -Path $OutputDirectory -Force
$target = Join-Path $OutputDirectory ('phone-{0}.jsonl' -f (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
$temporary = "$target.tmp"
# JSONL is UTF-8 text. Do not echo sensor payloads to the terminal or use logcat.
$previousEncoding = [Console]::OutputEncoding
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    & $AdbPath -s $Serial exec-out run-as com.smartneckband.companion cat $remoteFile |
        Set-Content -LiteralPath $temporary -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw 'Capture read failed; temporary output was not promoted.' }
    $header = Get-Content -LiteralPath $temporary -TotalCount 1 | ConvertFrom-Json
    if ($header.type -ne 'meta' -or $header.schema -ne 1) { throw 'Invalid capture header.' }
    Move-Item -LiteralPath $temporary -Destination $target
} finally {
    [Console]::OutputEncoding = $previousEncoding
}
Write-Output $target

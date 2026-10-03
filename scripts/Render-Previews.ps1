# SPDX-License-Identifier: GPL-3.0-or-later

[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path $env:LOCALAPPDATA "AI-Mini-Monitor\previews")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$Executable = Join-Path $ProjectRoot "dist\AI-Mini-Monitor\AI-Mini-Monitor-CLI.exe"

if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw "Packaged CLI is missing. Run scripts\Build.ps1 first: $Executable"
}

$destination = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $destination -Force | Out-Null
& $Executable --render-previews $destination
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$count = @(Get-ChildItem -LiteralPath $destination -Filter "*.png" -File).Count
Write-Host "Preview PNG files: $count"
Write-Host "Output: $destination"

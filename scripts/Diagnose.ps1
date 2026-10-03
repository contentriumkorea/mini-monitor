# SPDX-License-Identifier: GPL-3.0-or-later

[CmdletBinding()]
param(
    [string]$OutputPath = "-"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$Executable = Join-Path $ProjectRoot "dist\AI-Mini-Monitor\AI-Mini-Monitor-CLI.exe"

if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw "Packaged CLI is missing. Run scripts\Build.ps1 first: $Executable"
}

$destination = $OutputPath
if ($destination -ne "-") {
    $destination = [IO.Path]::GetFullPath($destination)
    $parent = Split-Path -Parent $destination
    if ($parent) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
}

# The application's diagnose path enumerates ports and sensors but never opens
# the serial display and never modifies HKCU startup state.
& $Executable --diagnose $destination
exit $LASTEXITCODE

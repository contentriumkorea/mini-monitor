# SPDX-License-Identifier: GPL-3.0-or-later

[CmdletBinding()]
param(
    [switch]$Cli,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$AppArguments
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$name = if ($Cli) { "Mini-Monitor-CLI.exe" } else { "Mini-Monitor.exe" }
$Executable = Join-Path $ProjectRoot "dist\Mini-Monitor\$name"

if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw "Packaged executable is missing. Run scripts\Build.ps1 first: $Executable"
}

& $Executable @AppArguments
if ($null -ne $LASTEXITCODE) {
    exit $LASTEXITCODE
}

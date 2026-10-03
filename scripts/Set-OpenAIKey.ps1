# SPDX-License-Identifier: GPL-3.0-or-later

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$Executable = Join-Path $ProjectRoot "dist\Mini-Monitor\Mini-Monitor-CLI.exe"

if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw "Packaged CLI is missing. Run scripts\Build.ps1 first: $Executable"
}

# The key is requested by getpass inside the application.  It is never passed
# through a command-line argument, environment variable, or PowerShell value.
& $Executable --set-openai-key
exit $LASTEXITCODE

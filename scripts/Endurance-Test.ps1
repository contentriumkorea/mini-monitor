# SPDX-License-Identifier: GPL-3.0-or-later

[CmdletBinding()]
param(
    [ValidateRange(0.001, 31536000.0)]
    [double]$DurationSeconds = 1800.0,

    [string]$Output = 'diagnostics\endurance-30min.json',

    [string]$Config,

    [string]$Port,

    [switch]$ConfirmDeviceOutput
)

$ErrorActionPreference = 'Stop'

if (-not $ConfirmDeviceOutput) {
    throw 'Refusing physical display output. Re-run with -ConfirmDeviceOutput only for the verified USB35INCHIPSV2 display.'
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}
$outputPath = if ([IO.Path]::IsPathRooted($Output)) { $Output } else { Join-Path $projectRoot $Output }
$arguments = @(
    '-m', 'ai_mini_monitor.endurance',
    '--duration', $DurationSeconds.ToString([Globalization.CultureInfo]::InvariantCulture),
    '--output', $outputPath,
    '--confirm-device-output'
)
if (-not [string]::IsNullOrWhiteSpace($Config)) { $arguments += @('--config', $Config) }
if (-not [string]::IsNullOrWhiteSpace($Port)) { $arguments += @('--port', $Port) }

$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $projectRoot 'src'
    & $python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Endurance test failed with exit code $LASTEXITCODE. Inspect: $outputPath"
    }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
}

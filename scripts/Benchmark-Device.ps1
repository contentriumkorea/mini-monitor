# SPDX-License-Identifier: GPL-3.0-or-later
# One five-size calibration is followed by partial-patch-only endurance; the
# Python module retains the identity gate and final dashboard restoration.

[CmdletBinding()]
param(
    [ValidateRange(0.001, 31536000.0)]
    [double]$DurationSeconds = 1800.0,

    [string]$Output = 'diagnostics\device-benchmark.json',

    [string]$Port,

    [switch]$ConfirmDeviceOutput
)

$ErrorActionPreference = 'Stop'

if (-not $ConfirmDeviceOutput) {
    throw 'Refusing physical display output. Re-run with -ConfirmDeviceOutput after verifying the connected USB35INCHIPSV2 display.'
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $python = $venvPython
}
else {
    $python = (Get-Command python -ErrorAction Stop).Source
}

if ([IO.Path]::IsPathRooted($Output)) {
    $outputPath = $Output
}
else {
    $outputPath = Join-Path $projectRoot $Output
}

$arguments = @(
    '-m'
    'ai_mini_monitor.benchmark'
    '--duration'
    $DurationSeconds.ToString([Globalization.CultureInfo]::InvariantCulture)
    '--output'
    $outputPath
    '--confirm-device-output'
)
if (-not [string]::IsNullOrWhiteSpace($Port)) {
    $arguments += @('--port', $Port)
}

$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $projectRoot 'src'
    & $python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Device benchmark failed with exit code $LASTEXITCODE. Inspect: $outputPath"
    }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
}

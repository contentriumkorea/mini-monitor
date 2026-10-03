# SPDX-License-Identifier: GPL-3.0-or-later

[CmdletBinding()]
param(
    [switch]$SkipTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Spec = Join-Path $ProjectRoot "AI-Mini-Monitor.spec"
$WorkRoot = [IO.Path]::GetFullPath((Join-Path $ProjectRoot "build\pyinstaller"))
$DistRoot = [IO.Path]::GetFullPath((Join-Path $ProjectRoot "dist"))
$ArtifactRoot = [IO.Path]::GetFullPath((Join-Path $DistRoot "AI-Mini-Monitor"))

function Assert-ProjectChildPath {
    param([Parameter(Mandatory = $true)][string]$Candidate)
    $rootWithSeparator = $ProjectRoot.TrimEnd('\') + '\'
    $fullCandidate = [IO.Path]::GetFullPath($Candidate)
    if (-not $fullCandidate.StartsWith($rootWithSeparator, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing path outside project root: $fullCandidate"
    }
}

function Remove-BuildPath {
    param([Parameter(Mandatory = $true)][string]$Candidate)
    Assert-ProjectChildPath -Candidate $Candidate
    if (Test-Path -LiteralPath $Candidate) {
        Remove-Item -LiteralPath $Candidate -Recurse -Force
    }
}

function Get-BuildInputFingerprint {
    $candidates = @()
    foreach ($directory in @(
        "src",
        "tests",
        "scripts",
        "assets",
        "previews",
        "third_party",
        "docs",
        "LICENSES"
    )) {
        $path = Join-Path $ProjectRoot $directory
        if (Test-Path -LiteralPath $path -PathType Container) {
            $candidates += Get-ChildItem -LiteralPath $path -File -Recurse |
                Where-Object {
                    $_.Extension -ne ".pyc" -and
                    $_.FullName -notmatch "[\\/]__pycache__[\\/]" -and
                    $_.FullName -notmatch "[\\/][^\\/]+\.egg-info[\\/]"
                }
        }
    }
    foreach ($filename in @(
        "AI-Mini-Monitor.spec",
        "pyproject.toml",
        "requirements.lock",
        "config.example.json",
        "LICENSE",
        "LICENSE.txt",
        "COPYING",
        "NOTICE",
        "THIRD_PARTY_NOTICES.md",
        "THIRD_PARTY_NOTICES.txt",
        "THIRD_PARTY_COMPONENTS.json",
        "README_KO.md",
        "README.md",
        "DEVICE_BENCHMARK.md",
        "TEST_RESULTS.md",
        "SOURCE-OFFER.md",
        "scripts\Build.ps1"
    )) {
        $path = Join-Path $ProjectRoot $filename
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $candidates += Get-Item -LiteralPath $path
        }
    }

    # PowerShell's Sort-Object is culture-aware, while the packaging test runs
    # in Python.  Use an explicit ordinal path order so both implementations
    # produce the same fingerprint on every Windows locale.
    $byRelativePath = [Collections.Generic.Dictionary[string, string]]::new(
        [StringComparer]::Ordinal
    )
    foreach ($candidate in $candidates) {
        $relative = $candidate.FullName.Substring($ProjectRoot.Length + 1).Replace('\', '/')
        $byRelativePath[$relative] = $candidate.FullName
    }
    [string[]]$relativePaths = @($byRelativePath.Keys)
    [Array]::Sort($relativePaths, [StringComparer]::Ordinal)
    $lines = @(
        foreach ($relative in $relativePaths) {
            $hash = (Get-FileHash -LiteralPath $byRelativePath[$relative] -Algorithm SHA256).Hash.ToLowerInvariant()
            "$hash  $relative"
        }
    )
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes(($lines -join "`n"))
        return -join ($sha256.ComputeHash($bytes) | ForEach-Object { $_.ToString("x2") })
    }
    finally {
        $sha256.Dispose()
    }
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Pinned virtual environment is missing: $Python"
}
if (-not (Test-Path -LiteralPath $Spec -PathType Leaf)) {
    throw "PyInstaller spec is missing: $Spec"
}

$env:PYTHONHASHSEED = "0"
$env:PYTHONUTF8 = "1"
$env:SOURCE_DATE_EPOCH = "1704067200"
$runtime = & $Python -c "import platform, sys, PyInstaller; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}|{platform.architecture()[0]}|{PyInstaller.__version__}')"
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect the packaging runtime."
}
$expectedRuntime = "3.13.3|64bit|6.22.0"
if ($runtime.Trim() -ne $expectedRuntime) {
    throw "Packaging runtime drift: expected $expectedRuntime, got $($runtime.Trim())"
}

Push-Location $ProjectRoot
try {
    & $Python -m pip check
    if ($LASTEXITCODE -ne 0) {
        throw "The pinned virtual environment failed pip check."
    }
    if (-not $SkipTests) {
        & $Python -m pytest `
            "tests\test_packaging_config.py" `
            "tests\test_licenses.py" `
            "tests\test_environment_integrity.py" `
            -q
        if ($LASTEXITCODE -ne 0) {
            throw "Packaging configuration tests failed."
        }
    }

    $inputFingerprint = Get-BuildInputFingerprint
    Remove-BuildPath -Candidate $WorkRoot
    Remove-BuildPath -Candidate $ArtifactRoot
    New-Item -ItemType Directory -Path $WorkRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $DistRoot -Force | Out-Null

    & $Python -m PyInstaller `
        --clean `
        --noconfirm `
        --log-level WARN `
        --distpath $DistRoot `
        --workpath $WorkRoot `
        $Spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed with exit code $LASTEXITCODE."
    }
    if ((Get-BuildInputFingerprint) -ne $inputFingerprint) {
        Remove-BuildPath -Candidate $ArtifactRoot
        throw "Build inputs changed during PyInstaller analysis. Rerun after source changes settle."
    }

    $DesktopExe = Join-Path $ArtifactRoot "AI-Mini-Monitor.exe"
    $CliExe = Join-Path $ArtifactRoot "AI-Mini-Monitor-CLI.exe"
    foreach ($required in @($DesktopExe, $CliExe)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "Required executable is missing: $required"
        }
    }

    # Keep human-facing notices and the configuration example discoverable
    # beside the executables, in addition to PyInstaller's internal copy.
    foreach ($filename in @(
        "LICENSE",
        "LICENSE.txt",
        "COPYING",
        "NOTICE",
        "THIRD_PARTY_NOTICES.md",
        "THIRD_PARTY_NOTICES.txt",
        "THIRD_PARTY_COMPONENTS.json",
        "README_KO.md",
        "README.md",
        "DEVICE_BENCHMARK.md",
        "TEST_RESULTS.md",
        "SOURCE-OFFER.md",
        "config.example.json",
        "requirements.lock"
    )) {
        $source = Join-Path $ProjectRoot $filename
        if (Test-Path -LiteralPath $source -PathType Leaf) {
            Copy-Item -LiteralPath $source -Destination $ArtifactRoot -Force
        }
    }
    foreach ($directory in @("docs", "LICENSES", "previews")) {
        $source = Join-Path $ProjectRoot $directory
        if (Test-Path -LiteralPath $source -PathType Container) {
            $destination = Join-Path $ArtifactRoot $directory
            Remove-BuildPath -Candidate $destination
            Copy-Item -LiteralPath $source -Destination $destination -Recurse -Force
        }
    }

    # GPL corresponding source and the exact build/test inputs are shipped in
    # a discoverable folder beside the executables.
    $sourceBundle = Join-Path $ArtifactRoot "source"
    Remove-BuildPath -Candidate $sourceBundle
    New-Item -ItemType Directory -Path $sourceBundle -Force | Out-Null
    foreach ($directory in @(
        "src",
        "tests",
        "scripts",
        "assets",
        "previews",
        "third_party",
        "LICENSES",
        "docs"
    )) {
        $source = Join-Path $ProjectRoot $directory
        if (Test-Path -LiteralPath $source -PathType Container) {
            Copy-Item -LiteralPath $source -Destination $sourceBundle -Recurse -Force
        }
    }
    # Build/test caches are not corresponding source. Keep the shipped source
    # tree readable and deterministic even when the local checkout was tested.
    @(
        Get-ChildItem -LiteralPath $sourceBundle -Directory -Recurse -Force |
            Where-Object {
                $_.Name -eq "__pycache__" -or $_.Name -like "*.egg-info"
            } |
            Sort-Object FullName -Descending
    ) | ForEach-Object {
        Remove-BuildPath -Candidate $_.FullName
    }
    @(
        Get-ChildItem -LiteralPath $sourceBundle -File -Recurse -Force |
            Where-Object { $_.Extension -eq ".pyc" }
    ) | ForEach-Object {
        Remove-BuildPath -Candidate $_.FullName
    }
    foreach ($filename in @(
        "AI-Mini-Monitor.spec",
        "pyproject.toml",
        "requirements.lock",
        "config.example.json",
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "THIRD_PARTY_COMPONENTS.json",
        "README_KO.md",
        "DEVICE_BENCHMARK.md",
        "TEST_RESULTS.md",
        "SOURCE-OFFER.md"
    )) {
        $source = Join-Path $ProjectRoot $filename
        if (Test-Path -LiteralPath $source -PathType Leaf) {
            Copy-Item -LiteralPath $source -Destination $sourceBundle -Force
        }
    }

    if ((Get-BuildInputFingerprint) -ne $inputFingerprint) {
        Remove-BuildPath -Candidate $ArtifactRoot
        throw "Build inputs changed while assembling distribution documents. Rerun the build."
    }

    $buildInfoPath = Join-Path $ArtifactRoot "BUILD-INFO.json"
    $buildInfo = [ordered]@{
        schema = 1
        input_fingerprint = $inputFingerprint
        python = "3.13.3"
        architecture = "64bit"
        pyinstaller = "6.22.0"
        source_date_epoch = $env:SOURCE_DATE_EPOCH
        layout = "onedir"
    }
    $buildInfoJson = ($buildInfo | ConvertTo-Json) + "`n"
    [IO.File]::WriteAllText(
        $buildInfoPath,
        $buildInfoJson,
        [Text.UTF8Encoding]::new($false)
    )

    $hashManifest = Join-Path $ArtifactRoot "SHA256SUMS.txt"
    $manifestLines = @(
        Get-ChildItem -LiteralPath $ArtifactRoot -File -Recurse |
            Where-Object { $_.FullName -ne $hashManifest } |
            Sort-Object FullName |
            ForEach-Object {
                $relative = $_.FullName.Substring($ArtifactRoot.Length + 1).Replace('\', '/')
                $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
                "$hash  $relative"
            }
    )
    [IO.File]::WriteAllLines(
        $hashManifest,
        $manifestLines,
        [Text.UTF8Encoding]::new($false)
    )

    # Validate the artifact that was just assembled. The pre-build run can
    # legitimately skip a stale dist tree, so release acceptance must happen
    # only after BUILD-INFO and SHA256SUMS exist for this exact snapshot.
    if (-not $SkipTests) {
        & $Python -m pytest `
            "tests\test_packaging_config.py" `
            "tests\test_licenses.py" `
            -q
        if ($LASTEXITCODE -ne 0) {
            Remove-BuildPath -Candidate $ArtifactRoot
            throw "Fresh packaging artifact validation failed."
        }
    }
    if ((Get-BuildInputFingerprint) -ne $inputFingerprint) {
        Remove-BuildPath -Candidate $ArtifactRoot
        throw "Build inputs changed during final artifact validation. Rerun the build."
    }

    $totalBytes = (
        Get-ChildItem -LiteralPath $ArtifactRoot -File -Recurse |
            Measure-Object -Property Length -Sum
    ).Sum
    Write-Host "Built: $ArtifactRoot"
    Write-Host "Files: $($manifestLines.Count + 1)"
    Write-Host "Bytes: $totalBytes"
    Write-Host "Desktop SHA256: $((Get-FileHash -LiteralPath $DesktopExe -Algorithm SHA256).Hash.ToLowerInvariant())"
    Write-Host "CLI SHA256: $((Get-FileHash -LiteralPath $CliExe -Algorithm SHA256).Hash.ToLowerInvariant())"
}
finally {
    Pop-Location
}

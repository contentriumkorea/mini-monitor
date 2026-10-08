# SPDX-License-Identifier: GPL-3.0-or-later

[CmdletBinding()]
param(
    [string]$ArchivePath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$pinPath = Join-Path $projectRoot "third_party\codex-app-server\RUNTIME.json"
$pin = Get-Content -LiteralPath $pinPath -Raw -Encoding UTF8 | ConvertFrom-Json
$target = [IO.Path]::GetFullPath((Join-Path $projectRoot $pin.runtime_path))
$allowedTarget = [IO.Path]::GetFullPath((Join-Path $projectRoot "third_party\codex-app-server\codex-app-server.exe"))
if (-not $target.Equals($allowedTarget, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Codex runtime pin names an unexpected target."
}
if ($pin.archive_url -ne "https://github.com/openai/codex/releases/download/rust-v0.161.0/codex-app-server-x86_64-pc-windows-msvc.exe.zip") {
    throw "Codex runtime pin names an unexpected source."
}

function Assert-PinnedFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][long]$Size,
        [Parameter(Mandatory = $true)][string]$Sha256
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    $item = Get-Item -LiteralPath $Path
    if ($item.Length -ne $Size) {
        return $false
    }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.Equals(
        $Sha256, [StringComparison]::OrdinalIgnoreCase
    )
}

if (Test-Path -LiteralPath $target) {
    if (-not (Assert-PinnedFile -Path $target -Size $pin.runtime_size -Sha256 $pin.runtime_sha256)) {
        throw "Refusing mismatched existing Codex runtime: $target"
    }
    Write-Output "Pinned Codex runtime verified."
    return
}

$stagingParent = [IO.Path]::GetFullPath((Join-Path $projectRoot "build\codex-runtime-fetch"))
$staging = Join-Path $stagingParent ([Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $staging -Force | Out-Null
try {
    $archive = Join-Path $staging "codex-app-server.zip"
    if ($ArchivePath) {
        Copy-Item -LiteralPath $ArchivePath -Destination $archive
    }
    else {
        Invoke-WebRequest -Uri $pin.archive_url -OutFile $archive
    }
    if (-not (Assert-PinnedFile -Path $archive -Size $pin.archive_size -Sha256 $pin.archive_sha256)) {
        throw "Downloaded Codex runtime archive failed pinned SHA256/size validation."
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($archive)
    try {
        if ($zip.Entries.Count -ne 1 -or $zip.Entries[0].FullName -cne $pin.archive_member) {
            throw "Codex runtime archive has an unexpected member list."
        }
        $extracted = Join-Path $staging "codex-app-server.exe"
        [IO.Compression.ZipFileExtensions]::ExtractToFile($zip.Entries[0], $extracted)
    }
    finally {
        $zip.Dispose()
    }
    if (-not (Assert-PinnedFile -Path $extracted -Size $pin.runtime_size -Sha256 $pin.runtime_sha256)) {
        throw "Extracted Codex runtime failed pinned SHA256/size validation."
    }
    if (Test-Path -LiteralPath $target) {
        throw "Codex runtime appeared during fetch; refusing to overwrite it."
    }
    Move-Item -LiteralPath $extracted -Destination $target
    if (-not (Assert-PinnedFile -Path $target -Size $pin.runtime_size -Sha256 $pin.runtime_sha256)) {
        throw "Installed Codex runtime failed final SHA256/size validation."
    }
    Write-Output "Pinned Codex runtime fetched and verified."
}
finally {
    $expectedPrefix = $stagingParent.TrimEnd('\') + '\'
    $resolvedStaging = [IO.Path]::GetFullPath($staging)
    if (-not $resolvedStaging.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing cleanup outside runtime staging directory."
    }
    Remove-Item -LiteralPath $resolvedStaging -Recurse -Force
}

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$sourceRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot '..\35inchENG')).TrimEnd('\')
$expected = '67cdaed7898055fd2fcdebd3559b4692756802a870402326d7a45e3f88e12562'

if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container)) {
    throw "Original directory not found: $sourceRoot"
}

$items = @(Get-ChildItem -LiteralPath $sourceRoot -File -Recurse -Force | ForEach-Object {
    [pscustomobject]@{
        RelativePath = $_.FullName.Substring($sourceRoot.Length + 1).Replace('\', '/')
        Size = [int64]$_.Length
        SHA256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
})

$comparer = [System.Collections.Generic.Comparer[object]]::Create(
    [System.Comparison[object]]{
        param($first, $second)
        [StringComparer]::Ordinal.Compare([string]$first.RelativePath, [string]$second.RelativePath)
    }
)
[Array]::Sort($items, $comparer)
$manifest = [string]::Concat(($items | ForEach-Object {
    "{0}`t{1}`t{2}`n" -f $_.RelativePath, $_.Size, $_.SHA256
}))
$utf8 = [Text.UTF8Encoding]::new($false)
$sha = [Security.Cryptography.SHA256]::Create()
try {
    $actual = ([BitConverter]::ToString($sha.ComputeHash($utf8.GetBytes($manifest)))).Replace('-', '').ToLowerInvariant()
}
finally {
    $sha.Dispose()
}

$result = [pscustomobject]@{
    Source = $sourceRoot
    FileCount = $items.Count
    TotalBytes = ($items | Measure-Object -Property Size -Sum).Sum
    ExpectedSHA256 = $expected
    ActualSHA256 = $actual
    Unchanged = ($actual -eq $expected)
}
$result | Format-List
if (-not $result.Unchanged) {
    throw 'Original 35inchENG baseline mismatch.'
}


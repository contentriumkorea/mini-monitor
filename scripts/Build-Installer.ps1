# SPDX-License-Identifier: GPL-3.0-or-later
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw '.NET Framework C# compiler is required.' }
$output = Join-Path $root 'build/installer'
$source = Join-Path $root 'installer/Setup.cs'
$client = (Resolve-Path -LiteralPath 'installer/ReleaseClient.cs').Path
$dependency = Get-Content installer/dependencies.json -Raw | ConvertFrom-Json
$deps = Join-Path $root 'build/installer-deps'
New-Item -ItemType Directory -Force $deps | Out-Null
$package = Join-Path $deps 'chaos.nupkg'
if (-not (Test-Path -LiteralPath $package)) { Invoke-WebRequest $dependency.url -OutFile $package }
if ((Get-FileHash -LiteralPath $package).Hash -ne $dependency.sha256) { throw 'Verifier dependency hash mismatch' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead($package)
try {
    [IO.Compression.ZipFileExtensions]::ExtractToFile($archive.GetEntry($dependency.assembly), (Join-Path $deps 'Chaos.NaCl.dll'), $true)
    [IO.Compression.ZipFileExtensions]::ExtractToFile($archive.GetEntry($dependency.license), (Join-Path $deps 'LICENSE.md'), $true)
} finally { $archive.Dispose() }
New-Item -ItemType Directory -Force -Path $output | Out-Null
$common = @('/nologo','/target:winexe','/platform:x64','/optimize+','/utf8output','/reference:System.Windows.Forms.dll','/reference:System.Drawing.dll','/reference:System.IO.Compression.dll','/reference:System.IO.Compression.FileSystem.dll','/reference:Microsoft.CSharp.dll','/win32manifest:installer/setup.manifest','/win32icon:assets/app-icon.ico')
$common += @('/reference:System.Web.Extensions.dll',"/reference:$deps/Chaos.NaCl.dll", "/resource:$deps/Chaos.NaCl.dll,Chaos.NaCl.dll", "/resource:$deps/LICENSE.md,Chaos.NaCl-LICENSE.md")
& $compiler @common "/out:$output/Uninstall.exe" $source $client
if ($LASTEXITCODE -ne 0) { throw 'Uninstaller compilation failed.' }
& $compiler @common "/out:$output/Mini-Monitor-Setup.exe" "/resource:$output/Uninstall.exe,uninstaller.exe" $source $client
if ($LASTEXITCODE -ne 0) { throw 'Setup compilation failed.' }
if ((Get-Item -LiteralPath "$output/Mini-Monitor-Setup.exe").Length -gt 1MB) { throw 'Online installer exceeds 1 MiB size budget.' }
Get-FileHash -LiteralPath "$output/Mini-Monitor-Setup.exe" -Algorithm SHA256


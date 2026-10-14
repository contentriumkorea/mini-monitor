# SPDX-License-Identifier: GPL-3.0-or-later
param([string]$Payload = 'build/release-v0.2.10/Mini-Monitor.zip')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw '.NET Framework C# compiler is required.' }
if ((Get-FileHash -LiteralPath $Payload -Algorithm SHA256).Hash -ne 'FA8B81EC80244E20AE7946AD4FE3F28BD49B5557299FB04E72030BEB1140F129') { throw 'Expected the verified 0.2.10 payload.' }
$output = Join-Path $root 'build/installer'
$source = Join-Path $root 'installer/Setup.cs'
$Payload = (Resolve-Path -LiteralPath $Payload).Path
New-Item -ItemType Directory -Force -Path $output | Out-Null
$common = @('/nologo','/target:winexe','/platform:x64','/optimize+','/utf8output','/reference:System.Windows.Forms.dll','/reference:System.Drawing.dll','/reference:System.IO.Compression.dll','/reference:System.IO.Compression.FileSystem.dll','/reference:Microsoft.CSharp.dll','/win32manifest:installer/setup.manifest','/win32icon:assets/app-icon.ico')
& $compiler @common "/out:$output/Uninstall.exe" $source
if ($LASTEXITCODE -ne 0) { throw 'Uninstaller compilation failed.' }
& $compiler @common "/out:$output/Mini-Monitor-Setup.exe" "/resource:$Payload,payload.zip" "/resource:$output/Uninstall.exe,uninstaller.exe" $source
if ($LASTEXITCODE -ne 0) { throw 'Setup compilation failed.' }
Get-FileHash -LiteralPath "$output/Mini-Monitor-Setup.exe" -Algorithm SHA256

param([switch]$Live)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repo
$compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$setup = (Resolve-Path build/installer/Mini-Monitor-Setup.exe).Path
$dependency = (Resolve-Path build/installer-deps/Chaos.NaCl.dll).Path
$source = (Resolve-Path installer/ReleaseClientTests.cs).Path
$test = Join-Path $repo 'build/installer/ReleaseClientTests.exe'
$stub = Join-Path $repo 'build/installer/FixtureCli.exe'
& $compiler /nologo /target:exe /platform:x64 "/out:$stub" (Resolve-Path installer/FixtureCli.cs).Path
if ($LASTEXITCODE -ne 0) { throw 'Fixture CLI compile failed' }
Copy-Item -LiteralPath $dependency -Destination (Join-Path $repo 'build/installer/Chaos.NaCl.dll') -Force
& $compiler /nologo /target:exe /platform:x64 /reference:System.IO.Compression.dll "/reference:$setup" "/reference:$dependency" "/out:$test" $source
if ($LASTEXITCODE -ne 0) { throw 'Client tests compile failed' }
$qa = Join-Path $repo 'build/online-installer-qa'
New-Item -ItemType Directory -Force $qa | Out-Null
if ($Live) { & $test $setup $qa --live } else { & $test $setup $qa }
if ($LASTEXITCODE -ne 0) { throw 'Client tests failed' }

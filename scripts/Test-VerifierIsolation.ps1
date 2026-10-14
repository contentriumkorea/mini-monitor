$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repo
$qa = Join-Path $repo ('build/verifier-isolation/' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force $qa | Out-Null
$csc = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
& $csc /nologo /target:library "/out:$qa/Chaos.NaCl.dll" (Resolve-Path installer/UntrustedVerifierFixture.cs).Path
if ($LASTEXITCODE -ne 0) { throw 'Untrusted fixture compilation failed' }
& $csc /nologo /target:exe "/out:$qa/VerifierProbe.exe" (Resolve-Path installer/VerifierProbe.cs).Path
if ($LASTEXITCODE -ne 0) { throw 'Verifier probe compilation failed' }
Copy-Item -LiteralPath build/installer/Mini-Monitor-Setup.exe -Destination $qa
& (Join-Path $qa 'VerifierProbe.exe') (Join-Path $qa 'Mini-Monitor-Setup.exe')
if ($LASTEXITCODE -ne 0) { throw 'External dependency probing bypasses embedded verifier' }


# SPDX-License-Identifier: GPL-3.0-or-later
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repo
$reg = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\MiniMonitor'
if (Test-Path -LiteralPath $reg) { throw 'An installer-managed installation already exists. Use an isolated Windows account for QA.' }
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
function Get-StartupValue {
    $values = Get-ItemProperty -LiteralPath $runKey -ErrorAction SilentlyContinue
    if ($null -ne $values) { $entry = $values.PSObject.Properties['AI Mini Monitor']; if ($null -ne $entry) { return $entry.Value } }
    return $null
}
$runBefore = Get-StartupValue
$desktopShortcut = Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'Mini Monitor.lnk'
if (Test-Path -LiteralPath $desktopShortcut) { throw 'Existing desktop shortcut: use an isolated Windows account for QA.' }
$config = Join-Path $env:LOCALAPPDATA 'AI-Mini-Monitor/config.json'
$configHash = if (Test-Path -LiteralPath $config) { (Get-FileHash -LiteralPath $config).Hash } else { '' }
$existingProcesses = @(Get-Process -Name Mini-Monitor -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
$qa = Join-Path $repo ('build/installer-qa/' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $qa | Out-Null
$target = Join-Path $qa 'Mini-Monitor'
$group = 'Mini Monitor QA ' + [Guid]::NewGuid().ToString('N')
$setup = Join-Path $qa 'Mini-Monitor-Setup.exe'
Copy-Item -LiteralPath (Join-Path $repo 'build/installer/Mini-Monitor-Setup.exe') -Destination $setup
function Invoke-Setup([int]$Expected) {
    $process = Start-Process -FilePath $setup -ArgumentList @('--quiet',('"--dir=' + $target + '"'),('"--group=' + $group + '"')) -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne $Expected) { throw "Setup returned $($process.ExitCode), expected $Expected. QA: $qa" }
}
Invoke-Setup 0
if ((Get-ItemPropertyValue -LiteralPath $reg -Name InstallLocation) -ne $target) { throw 'Wrong installed registration' }
$shortcut = Join-Path ([Environment]::GetFolderPath('Programs')) "$group/Mini Monitor.lnk"
$shell = New-Object -ComObject WScript.Shell
if ($shell.CreateShortcut($shortcut).TargetPath -ne (Join-Path $target 'Mini-Monitor.exe')) { throw 'Wrong shortcut target' }
if (-not (Test-Path -LiteralPath $desktopShortcut)) { throw 'Missing default desktop shortcut' }
if ($shell.CreateShortcut($desktopShortcut).TargetPath -ne (Join-Path $target 'Mini-Monitor.exe')) { throw 'Wrong desktop shortcut target' }
Write-Output 'PASS real installer / HKCU app removal registration / Start menu target'
$env:PYTHONPATH = Join-Path $repo 'src'
& .venv/Scripts/python.exe -c 'import sys; from pathlib import Path; from ai_mini_monitor.updater import _install_inventory; print("PASS updater-compatible inventory:", len(_install_inventory(Path(sys.argv[1]))))' $target
if ($LASTEXITCODE -ne 0) { throw 'Updater inventory failed' }
& (Join-Path $target 'Mini-Monitor-CLI.exe') --version
if ($LASTEXITCODE -ne 0) { throw 'Installed executable failed' }
$smoke = Start-Process -FilePath (Join-Path $target 'Mini-Monitor.exe') -ArgumentList @('--no-serial','--desktop-smoke','5','--config',('"' + (Join-Path $qa 'smoke-config.json') + '"')) -WindowStyle Hidden -PassThru
if (-not $smoke.WaitForExit(60000)) { throw 'Installed desktop smoke timed out' }
if ($smoke.ExitCode -ne 0) { throw 'Installed desktop smoke failed' }
Write-Output 'PASS installed desktop startup and clean exit (no serial output)'
$extra = Join-Path $target 'user-file.txt'
New-Item -ItemType File -Path $extra | Out-Null
Invoke-Setup 1
if (-not (Test-Path -LiteralPath $extra)) { throw 'User file was removed' }
Remove-Item -LiteralPath $extra
Write-Output 'PASS unknown user file blocks overwrite'
$buildInfo = Join-Path $target 'BUILD-INFO.json'
$originalBytes = [IO.File]::ReadAllBytes($buildInfo)
$compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$testSource = Join-Path $repo 'installer/InstallerTests.cs'
& $compiler /nologo /target:exe /platform:x64 /reference:System.Windows.Forms.dll /reference:System.Drawing.dll "/reference:$setup" "/out:$repo/build/installer/InstallerTests.exe" $testSource
if ($LASTEXITCODE -ne 0) { throw 'QA compile failed' }
& "$repo/build/installer/InstallerTests.exe" --defaults
if ($LASTEXITCODE -ne 0) { throw 'Installer default settings failed' }
try {
    & "$repo/build/installer/InstallerTests.exe" $target $group
    if ($LASTEXITCODE -ne 0) { throw 'Installer safety tests failed' }
} finally { [IO.File]::WriteAllBytes($buildInfo, $originalBytes) }
Invoke-Setup 0
Write-Output 'PASS same-path reinstall'
# Copy the same small removal executable to the QA directory, as the UI launcher does.
$remove = Join-Path $qa 'Uninstall-test.exe'
Copy-Item -LiteralPath "$target-setup/Uninstall.exe" -Destination $remove
$process = Start-Process -FilePath $remove -ArgumentList @('--uninstall','--worker','--quiet') -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode -ne 0) { throw 'Uninstall failed' }
if ((Test-Path -LiteralPath $target) -or (Test-Path -LiteralPath $reg) -or (Test-Path -LiteralPath $shortcut) -or (Test-Path -LiteralPath "$target-setup")) { throw 'Uninstall left a registered component' }
if (Test-Path -LiteralPath $desktopShortcut) { throw 'Uninstall left desktop shortcut' }
if ((Get-StartupValue) -ne $runBefore) { throw 'User startup value changed' }
$afterHash = if (Test-Path -LiteralPath $config) { (Get-FileHash -LiteralPath $config).Hash } else { '' }
if ($afterHash -ne $configHash) { throw 'User configuration changed' }
foreach ($id in $existingProcesses) { if (-not (Get-Process -Id $id -ErrorAction SilentlyContinue)) { throw 'Existing user app was stopped' } }
Write-Output 'PASS uninstall / user settings / startup registration / running user app preserved'
Write-Output "QA artifacts: $qa"


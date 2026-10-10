# SPDX-License-Identifier: GPL-3.0-or-later
# Only existing Mini Monitor links / opted-in startup registrations are changed.
$ErrorActionPreference = 'Stop'

function Repair-MonitorShortcut($Shell, [string]$Path, [string]$NewExe, [string[]]$OldExecutables) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked shortcut is not a regular file.' }
    $link = $Shell.CreateShortcut($Path)
    if ($OldExecutables -notcontains $link.TargetPath) { return }
    # Keep arguments, including a user's custom configuration path.
    $link.TargetPath = $NewExe
    $link.WorkingDirectory = [IO.Path]::GetDirectoryName($NewExe)
    $link.IconLocation = $NewExe + ',0'
    $link.Description = 'Mini Monitor'
    $link.Save()
    if ($Shell.CreateShortcut($Path).TargetPath -ne $NewExe) { throw 'Shortcut repair did not persist.' }
}

function Repair-MonitorRunCommand([string]$Command, [string]$NewExe, [string[]]$OldExecutables) {
    if (-not $Command) { return $null }
    if ($Command -match '^"([^"]+)"(.*)$') { $exe = $Matches[1]; $tail = $Matches[2] }
    elseif ($Command -match '^(\S+)(.*)$') { $exe = $Matches[1]; $tail = $Matches[2] }
    else { return $null }
    if ($OldExecutables -notcontains $exe -and $exe -ne $NewExe) { return $null }
    # Normalize the historical explicit DEFAULT config, not custom configs.
    $defaultConfig = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'AI-Mini-Monitor\config.json'
    if ($tail.Trim() -eq ('--config ' + $defaultConfig + ' --minimized') -or
        $tail.Trim() -eq ('--config "' + $defaultConfig + '" --minimized')) { $tail = ' --minimized' }
    return '"' + $NewExe + '"' + $tail
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        $inputRecord = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:MINI_MONITOR_REPAIR_LINKS)) | ConvertFrom-Json
        $newExe = Join-Path $inputRecord.install_root 'Mini-Monitor.exe'
        if (-not (Test-Path -LiteralPath $newExe -PathType Leaf)) { throw 'Current program is missing.' }
        $oldExecutables = @(
            foreach ($retired in $inputRecord.retired_roots) {
                foreach ($name in @('Mini-Monitor.exe', 'AI-Mini-Monitor.exe', 'Mini-Monitor-CLI.exe', 'AI-Mini-Monitor-CLI.exe')) {
                    Join-Path $retired $name
                }
            }
        )
        $shell = New-Object -ComObject WScript.Shell
        foreach ($folder in @([Environment]::GetFolderPath('DesktopDirectory'), [Environment]::GetFolderPath('Programs'))) {
            if (-not $folder -or -not (Test-Path -LiteralPath $folder -PathType Container)) { continue }
            foreach ($name in @('Mini Monitor.lnk', 'Mini-Monitor.lnk', 'AI Mini Monitor.lnk', 'AI-Mini-Monitor.lnk')) {
                Repair-MonitorShortcut $shell (Join-Path $folder $name) $newExe $oldExecutables
            }
        }
        $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
        $command = Get-ItemPropertyValue -LiteralPath $runKey -Name 'AI Mini Monitor' -ErrorAction SilentlyContinue
        $replacement = Repair-MonitorRunCommand $command $newExe $oldExecutables
        if ($replacement -and $replacement -ne $command) {
            Set-ItemProperty -LiteralPath $runKey -Name 'AI Mini Monitor' -Value $replacement
            if ((Get-ItemPropertyValue -LiteralPath $runKey -Name 'AI Mini Monitor') -ne $replacement) { throw 'Startup repair did not persist.' }
        }
        exit 0
    } catch {
        # No popup or destructive fallback. The caller retains the old target.
        Write-Error $_ -ErrorAction Continue
        exit 1
    }
}

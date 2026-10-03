# SPDX-License-Identifier: GPL-3.0-or-later
# Runs outside the onedir tree. All paths originate from a hash-pinned journal.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$JournalPath,
    [Parameter(Mandatory = $true)][string]$JournalSha256,
    [Parameter(Mandatory = $true)][string]$HelperSha256,
    [Parameter(Mandatory = $true)][int]$ParentPid
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Resolve-FullPath([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { throw 'Empty update path.' }
    return [IO.Path]::GetFullPath($Value)
}

function Assert-DirectChild([string]$Child, [string]$Parent) {
    $fullChild = Resolve-FullPath $Child
    $fullParent = Resolve-FullPath $Parent
    if (-not [string]::Equals([IO.Path]::GetDirectoryName($fullChild), $fullParent, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unexpected update path parent: $fullChild"
    }
}

function Assert-Regular([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Update path is a reparse point: $Path"
    }
}

function Get-Sha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
    } finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Assert-Relative([string]$Relative) {
    if (-not $Relative -or $Relative.Contains('\') -or $Relative.StartsWith('/') -or $Relative.Contains(':')) {
        throw "Unsafe relative update path: $Relative"
    }
    foreach ($piece in $Relative.Split('/')) {
        if (-not $piece -or $piece -eq '.' -or $piece -eq '..' -or $piece.EndsWith('.') -or $piece.EndsWith(' ') -or
            $piece -match '[<>:"|?*~]' -or $piece -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$') {
            throw "Unsafe update path component: $piece"
        }
    }
}

function Get-ExpectedMap($Rows, [bool]$StripRoot) {
    $map = [Collections.Generic.Dictionary[string,string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($row in $Rows) {
        if ($row.Count -ne 2) { throw 'Malformed update file record.' }
        [string]$relative = $row[0]
        [string]$digest = $row[1]
        if ($StripRoot) {
            if (-not $relative.StartsWith('Mini-Monitor/', [StringComparison]::Ordinal)) { throw 'Unexpected archive root.' }
            $relative = $relative.Substring('Mini-Monitor/'.Length)
        }
        Assert-Relative $relative
        if ($digest -cnotmatch '^[0-9a-f]{64}$' -or $map.ContainsKey($relative)) { throw 'Duplicate file alias or invalid hash.' }
        $map.Add($relative, $digest)
    }
    return $map
}

function Assert-Tree([string]$Root, $Expected, [bool]$ExcludeInventory) {
    Assert-Regular $Root
    $actual = [Collections.Generic.Dictionary[string,string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($item in Get-ChildItem -LiteralPath $Root -Force -Recurse) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Reparse point in update tree: $($item.FullName)" }
        if ($item.PSIsContainer) { continue }
        [string]$relative = $item.FullName.Substring($Root.TrimEnd('\').Length + 1).Replace('\','/')
        Assert-Relative $relative
        if ($ExcludeInventory -and $relative -eq 'SHA256SUMS.txt') { continue }
        if ($actual.ContainsKey($relative)) { throw 'Duplicate Windows alias in update tree.' }
        $actual.Add($relative, (Get-Sha256 $item.FullName))
    }
    if ($actual.Count -ne $Expected.Count) { throw 'Unknown or missing files in update tree.' }
    foreach ($entry in $Expected.GetEnumerator()) {
        if (-not $actual.ContainsKey($entry.Key) -or $actual[$entry.Key] -cne $entry.Value) {
            throw "Changed update file: $($entry.Key)"
        }
    }
}

function Save-Journal($Record, [string]$Path, [string]$State) {
    $Record.state = $State
    $temporary = $Path + '.tmp'
    $previous = $Path + '.previous'
    $json = $Record | ConvertTo-Json -Depth 8 -Compress
    [IO.File]::WriteAllText($temporary, $json, [Text.UTF8Encoding]::new($false))
    [IO.File]::Replace($temporary, $Path, $previous)
    [IO.File]::Delete($previous)
}

function Show-UpdateNotice([string]$Detail, [string]$BackupPath) {
    # A modal WScript popup auto-closes after 15 seconds. Never block recovery
    # or change an installed tree merely because desktop notification fails.
    if ($env:PYTEST_CURRENT_TEST -and $env:MINI_MONITOR_UPDATE_TEST_NO_NOTICE -eq '1') { return }
    try {
        $message = "Mini Monitor update needs attention.`r`n$Detail"
        if ($BackupPath) {
            $message += "`r`nPrevious program backup: $BackupPath"
            $message += "`r`nClose Mini Monitor before manually restoring this folder, or download the official release."
        }
        $shell = New-Object -ComObject WScript.Shell
        [void]$shell.Popup($message, 15, 'Mini Monitor update', 48)
    } catch { }
}

$journal = Resolve-FullPath $JournalPath
$stageDir = [IO.Path]::GetDirectoryName($journal)
Assert-DirectChild $journal $stageDir
Assert-DirectChild $PSCommandPath $stageDir
Assert-Regular $stageDir
Assert-Regular $PSCommandPath
if ((Get-Sha256 $PSCommandPath) -cne $HelperSha256) {
    throw 'Update helper hash changed.'
}
if ((Get-Sha256 $journal) -cne $JournalSha256) {
    throw 'Update journal hash changed.'
}
$record = Get-Content -LiteralPath $journal -Raw -Encoding UTF8 | ConvertFrom-Json
if ($record.schema_version -ne 1 -or $record.state -ne 'prepared') { throw 'Update journal state is not prepared.' }
$install = Resolve-FullPath $record.install_root
$staged = Resolve-FullPath $record.staged_root
$backup = Resolve-FullPath $record.backup_root
$ack = Resolve-FullPath $record.ack_path
$installParent = [IO.Path]::GetDirectoryName($install)
if ([IO.Path]::GetFileName($install) -cne 'Mini-Monitor') { throw 'Unexpected install name.' }
Assert-DirectChild $staged $stageDir
Assert-DirectChild $stageDir $installParent
Assert-DirectChild $backup $installParent
Assert-DirectChild $ack $stageDir
if (-not [IO.Path]::GetFileName($backup).StartsWith('.mini-monitor-backup-', [StringComparison]::Ordinal)) {
    throw 'Unexpected backup name.'
}
if (Test-Path -LiteralPath $backup) { throw 'Backup target already exists.' }
if ($record.nonce -cnotmatch '^[0-9a-f]{32}$') { throw 'Invalid update acknowledgement nonce.' }
Assert-Regular $installParent
$oldMap = Get-ExpectedMap $record.old_files $false
$newMap = Get-ExpectedMap $record.new_files $true
if ([string]$record.old_inventory_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
    (Get-Sha256 (Join-Path $install 'SHA256SUMS.txt')) -cne [string]$record.old_inventory_sha256) {
    throw 'Installed file inventory changed.'
}
Assert-Tree $install $oldMap $true
Assert-Tree $staged $newMap $false

$ackTimeout = 60
if ($record.PSObject.Properties.Name -contains 'ack_timeout_seconds') {
    $ackTimeout = [int]$record.ack_timeout_seconds
    if ($ackTimeout -lt 1 -or $ackTimeout -gt 60) { throw 'Invalid acknowledgement timeout.' }
}
$deadline = [DateTime]::UtcNow.AddSeconds(60)
while ($ParentPid -gt 0 -and (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue)) {
    if ([DateTime]::UtcNow -ge $deadline) {
        Save-Journal $record $journal 'parent_exit_timeout'
        Show-UpdateNotice 'The previous app did not exit. No files were replaced.' ''
        exit 2
    }
    Start-Sleep -Milliseconds 250
}
# Recheck after the old process exits; no unexpected file is copied or destroyed.
if ((Get-Sha256 (Join-Path $install 'SHA256SUMS.txt')) -cne [string]$record.old_inventory_sha256) {
    throw 'Installed file inventory changed before swap.'
}
Assert-Tree $install $oldMap $true
Assert-Tree $staged $newMap $false

try {
    Move-Item -LiteralPath $install -Destination $backup -ErrorAction Stop
    Save-Journal $record $journal 'old_backed_up'
    Move-Item -LiteralPath $staged -Destination $install -ErrorAction Stop
    Save-Journal $record $journal 'new_installed'
} catch {
    if (-not (Test-Path -LiteralPath $install) -and (Test-Path -LiteralPath $backup)) {
        Move-Item -LiteralPath $backup -Destination $install -ErrorAction Stop
    }
    Save-Journal $record $journal 'swap_failed'
    Show-UpdateNotice 'The update could not be swapped. Check the update journal and backup folder.' $backup
    throw
}

$program = Join-Path $install 'Mini-Monitor.exe'
Assert-Regular $program
$psi = [Diagnostics.ProcessStartInfo]::new()
$psi.FileName = $program
$psi.WorkingDirectory = $install
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $false
$restart = @($record.restart_args)
$safeArgs = [Collections.Generic.List[string]]::new()
for ($index = 0; $index -lt $restart.Count; $index++) {
    [string]$flag = $restart[$index]
    if ($flag -eq '--minimized' -or $flag -eq '--no-serial') {
        $safeArgs.Add($flag)
    } elseif (($flag -eq '--desktop-smoke' -or $flag -eq '--headless-run') -and $index + 1 -lt $restart.Count) {
        [double]$duration = 0
        if ([string]$restart[$index + 1] -cnotmatch '^[0-9]+(?:\.[0-9]+)?$' -or
            -not [double]::TryParse([string]$restart[$index + 1], [ref]$duration) -or
            $duration -le 0 -or $duration -gt 3600) { throw 'Unsafe restart duration.' }
        $safeArgs.Add($flag)
        $safeArgs.Add([string]$restart[$index + 1])
        $index++
    } else {
        throw 'Unsupported restart option.'
    }
}
$psi.Arguments = [string]::Join(' ', $safeArgs)
$psi.EnvironmentVariables['MINI_MONITOR_UPDATE_ACK_PATH'] = $ack
$psi.EnvironmentVariables['MINI_MONITOR_UPDATE_ACK_NONCE'] = [string]$record.nonce
if ($record.config_path) {
    $config = Resolve-FullPath $record.config_path
    if ($config.StartsWith($install.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Custom config moved inside the install tree.'
    }
    $psi.EnvironmentVariables['MINI_MONITOR_UPDATE_CONFIG_PATH'] = $config
}
try {
    $newProcess = [Diagnostics.Process]::Start($psi)
} catch {
    $newProcess = $null
}
if ($null -eq $newProcess) {
    $failed = Join-Path $installParent ('.mini-monitor-failed-' + [guid]::NewGuid().ToString('N'))
    Move-Item -LiteralPath $install -Destination $failed -ErrorAction Stop
    Move-Item -LiteralPath $backup -Destination $install -ErrorAction Stop
    Save-Journal $record $journal 'rolled_back_start_failed'
    Show-UpdateNotice 'The replacement app could not start. The previous program was restored.' ''
    exit 3
}

$deadline = [DateTime]::UtcNow.AddSeconds($ackTimeout)
$healthy = $false
while ([DateTime]::UtcNow -lt $deadline) {
    if (Test-Path -LiteralPath $ack -PathType Leaf) {
        try {
            $acknowledgement = Get-Content -LiteralPath $ack -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($acknowledgement.ready -eq $true -and [string]$acknowledgement.nonce -ceq [string]$record.nonce -and
                [string]$acknowledgement.version -ceq [string]$record.version) {
                $healthy = $true
                break
            }
        } catch { }
    }
    $newProcess.Refresh()
    if ($newProcess.HasExited) { break }
    Start-Sleep -Milliseconds 250
}
if ($healthy) {
    Save-Journal $record $journal 'healthy_backup_retained'
    exit 0
}
$newProcess.Refresh()
if (-not $newProcess.HasExited) {
    try { [void]$newProcess.CloseMainWindow() } catch { }
    try { [void]$newProcess.WaitForExit(5000) } catch { }
    $newProcess.Refresh()
}
if (-not $newProcess.HasExited) {
    Save-Journal $record $journal 'needs_manual_recovery_alive'
    Show-UpdateNotice 'The replacement app did not confirm a healthy start. It is still running, so no files were deleted or rolled back.' $backup
    exit 4
}
$failed = Join-Path $installParent ('.mini-monitor-failed-' + [guid]::NewGuid().ToString('N'))
Move-Item -LiteralPath $install -Destination $failed -ErrorAction Stop
Move-Item -LiteralPath $backup -Destination $install -ErrorAction Stop
Save-Journal $record $journal 'rolled_back_no_ack'
Show-UpdateNotice 'The replacement app exited without confirming a healthy start. The previous program was restored.' ''
exit 5

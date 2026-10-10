"""Real Windows shortcuts in an isolated folder; never touch user's shortcuts."""

import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/Repair-UpdateLinks.ps1"


@pytest.mark.skipif(os.name != "nt", reason="Windows shell shortcuts")
def test_owned_retired_shortcuts_are_repaired_and_unrelated_shortcuts_unchanged(tmp_path):
    assert SCRIPT.is_file(), "automatic cleanup must fix links before recycling their target"
    # Base64 input avoids command-string substitution of user-controlled paths.
    import base64
    import json
    fixture = base64.b64encode(json.dumps({"root": str(tmp_path), "script": str(SCRIPT)}).encode()).decode()
    code = r"""
    $fixture = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:MONITOR_LINK_FIXTURE)) | ConvertFrom-Json
    . $fixture.script
    $new = Join-Path $fixture.root 'Mini-Monitor.exe'
    $old = Join-Path $fixture.root 'retired\Mini-Monitor.exe'
    $unrelated = Join-Path $fixture.root 'Other.exe'
    $shell = New-Object -ComObject WScript.Shell
    foreach ($name in @('owned', 'other')) {
        $link = $shell.CreateShortcut((Join-Path $fixture.root ($name + '.lnk')))
        $link.TargetPath = $(if ($name -eq 'owned') {$old} else {$unrelated})
        $link.Arguments = '--config "C:\User Settings\custom.json"'
        $link.Save()
    }
    $owned = Join-Path $fixture.root 'owned.lnk'
    $other = Join-Path $fixture.root 'other.lnk'
    $before = (Get-FileHash -LiteralPath $other).Hash
    Repair-MonitorShortcut $shell $owned $new @($old)
    Repair-MonitorShortcut $shell $other $new @($old)
    $link = $shell.CreateShortcut($owned)
    if ($link.TargetPath -ne $new -or $link.WorkingDirectory -ne $fixture.root -or
        $link.Arguments -ne '--config "C:\User Settings\custom.json"' -or
        $link.IconLocation -ne ($new + ',0') -or (Get-FileHash -LiteralPath $other).Hash -ne $before) {exit 2}
    $fixed = Repair-MonitorRunCommand ('"' + $old + '" --minimized') $new @($old)
    if ($fixed -ne ('"' + $new + '" --minimized')) {exit 3}
    if ($null -ne (Repair-MonitorRunCommand $null $new @($old))) {exit 4}
    if ($null -ne (Repair-MonitorRunCommand ('"' + $unrelated + '" --minimized') $new @($old))) {exit 5}
    $custom = Repair-MonitorRunCommand ('"' + $old + '" --config "C:\User Settings\custom.json" --minimized') $new @($old)
    if ($custom -ne ('"' + $new + '" --config "C:\User Settings\custom.json" --minimized')) {exit 6}
    "PASS"
    """
    encoded = base64.b64encode(code.encode("utf-16le")).decode()
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    environment = dict(os.environ)
    from ai_mini_monitor.security.windows_system import pin_powershell_modules
    pin_powershell_modules(environment, powershell.parent / "Modules")
    environment["MONITOR_LINK_FIXTURE"] = fixture
    result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                             "-EncodedCommand", encoded], capture_output=True, text=True, timeout=20,
                            env=environment,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0 and "PASS" in result.stdout, result.stderr

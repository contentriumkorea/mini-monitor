// SPDX-License-Identifier: GPL-3.0-or-later
// Online, per-user setup. App files retain the release's signed inventory.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("Mini Monitor Setup")]
[assembly: AssemblyProduct("Mini Monitor")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]
[assembly: System.Runtime.Versioning.TargetFramework(".NETFramework,Version=v4.8")]

namespace MiniMonitorInstaller {
public static class Engine {
    public const string Key = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\MiniMonitor";
    public const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    public const string RunName = "AI Mini Monitor";
    public static string DefaultRoot { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Mini-Monitor"); } }
    public static string Exe(string root) { return Path.Combine(root, "Mini-Monitor.exe"); }
    static string Hash(Stream stream) { using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant(); }
    static string HashFile(string path) { using (var stream = File.OpenRead(path)) return Hash(stream); }
    public static void NoLinks(string path) {
        for (var p = new DirectoryInfo(Path.GetFullPath(path)); p != null; p = p.Parent)
            if (p.Exists && (p.Attributes & FileAttributes.ReparsePoint) != 0) throw new IOException("연결된 폴더에는 설치할 수 없습니다: " + p.FullName);
    }
    public static string ValidateRoot(string root) {
        var full = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar);
        if (!String.Equals(Path.GetFileName(full), "Mini-Monitor", StringComparison.OrdinalIgnoreCase) || full.StartsWith(@"\\"))
            throw new IOException("설치 폴더 이름은 Mini-Monitor여야 하며 로컬 드라이브를 사용해야 합니다.");
        NoLinks(full); NoLinks(full + "-setup");
        return full;
    }
    static IEnumerable<string> Files(string root) {
        foreach (var item in Directory.GetFileSystemEntries(root)) {
            if ((File.GetAttributes(item) & FileAttributes.ReparsePoint) != 0) throw new IOException("연결 파일이 포함되어 있습니다.");
            if (Directory.Exists(item)) { foreach (var child in Files(item)) yield return child; }
            else yield return item;
        }
    }
    public static void VerifyTree(string root) {
        NoLinks(root);
        var sums = Path.Combine(root, "SHA256SUMS.txt");
        if (!File.Exists(sums) || (File.GetAttributes(sums) & FileAttributes.ReparsePoint) != 0) throw new IOException("기존 폴더의 설치 내역을 확인할 수 없습니다. 새 설치 폴더를 선택하세요.");
        var expected = new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase);
        foreach (var line in File.ReadAllLines(sums)) {
            var match = Regex.Match(line, @"^([0-9a-f]{64})  (.+)$");
            if (!match.Success) throw new IOException("잘못된 파일 검사 목록입니다.");
            string relative = match.Groups[2].Value.Replace('/', '\\');
            if (Path.IsPathRooted(relative) || relative.Split('\\').Any(p => p == ".." || p == "." || p.Contains(':')) || expected.ContainsKey(relative)) throw new IOException("잘못된 파일 경로입니다.");
            expected.Add(relative, match.Groups[1].Value);
        }
        var actual = Files(root).Where(p => !String.Equals(p, sums, StringComparison.OrdinalIgnoreCase)).ToArray();
        if (actual.Length != expected.Count || !File.Exists(Exe(root))) throw new IOException("설치 폴더에 추가되거나 빠진 파일이 있습니다. 사용자 파일을 보존하기 위해 작업을 중단합니다.");
        foreach (string path in actual) {
            string relative = path.Substring(root.Length + 1), hash;
            if (!expected.TryGetValue(relative, out hash) || HashFile(path) != hash) throw new IOException("변경된 파일을 덮어쓰거나 삭제하지 않습니다: " + relative);
        }
    }
    static void EnsureClosed(string root) {
        foreach (var name in new[] { "Mini-Monitor", "Mini-Monitor-CLI", "codex-app-server" }) {
            foreach (var p in Process.GetProcessesByName(name)) using (p) {
                string path;
                try { path = p.MainModule.FileName; } catch (InvalidOperationException) { continue; } catch (System.ComponentModel.Win32Exception) { throw new IOException("실행 중인 Mini Monitor 상태를 확인할 수 없습니다. 앱을 종료한 뒤 다시 시도하세요."); }
                if (path.StartsWith(root + "\\", StringComparison.OrdinalIgnoreCase)) throw new IOException("Mini Monitor를 종료한 뒤 다시 시도하세요. 작업표시줄 아이콘 메뉴 또는 설정창의 프로그램 종료를 사용하세요.");
            }
        }
    }
    static string ShortcutPath(string group) {
        if (String.IsNullOrWhiteSpace(group) || group.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 || group == "." || group == "..") throw new IOException("잘못된 시작 메뉴 이름입니다.");
        return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), group, "Mini Monitor.lnk");
    }
    static void Shortcut(string path, string exe) {
        NoLinks(Path.GetDirectoryName(path)); Directory.CreateDirectory(Path.GetDirectoryName(path));
        dynamic shell = Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell"));
        try {
            dynamic link = shell.CreateShortcut(path);
            try {
                if (File.Exists(path) && !String.Equals((string)link.TargetPath, exe, StringComparison.OrdinalIgnoreCase)) throw new IOException("다른 프로그램의 바로가기를 덮어쓰지 않습니다: " + path);
                link.TargetPath = exe; link.WorkingDirectory = Path.GetDirectoryName(exe); link.IconLocation = exe + ",0"; link.Description = "Mini Monitor"; link.Save();
            }
            finally { Marshal.FinalReleaseComObject(link); }
        } finally { Marshal.FinalReleaseComObject(shell); }
    }
    static void RemoveShortcut(string path, string exe) {
        if (String.IsNullOrEmpty(path) || !File.Exists(path)) return;
        if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0) throw new IOException("연결된 바로가기는 삭제하지 않습니다.");
        dynamic shell = Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell"));
        try {
            dynamic link = shell.CreateShortcut(path); string target;
            try { target = link.TargetPath; } finally { Marshal.FinalReleaseComObject(link); }
            if (String.Equals(target, exe, StringComparison.OrdinalIgnoreCase)) File.Delete(path);
        } finally { Marshal.FinalReleaseComObject(shell); }
    }
    static void WriteResource(string name, string path) {
        using (var input = Assembly.GetExecutingAssembly().GetManifestResourceStream(name)) {
            if (input == null) throw new IOException("설치 리소스가 없습니다: " + name);
            using (var output = new FileStream(path, FileMode.CreateNew)) input.CopyTo(output);
        }
    }
    sealed class RegistrationBackup {
        readonly Dictionary<string, byte[]> files = new Dictionary<string, byte[]>();
        readonly Dictionary<string, object> values = new Dictionary<string, object>();
        readonly Dictionary<string, RegistryValueKind> kinds = new Dictionary<string, RegistryValueKind>();
        readonly bool existed;
        public RegistrationBackup(IEnumerable<string> paths) {
            foreach (string path in paths.Where(p => !String.IsNullOrEmpty(p)).Distinct()) {
                NoLinks(Path.GetDirectoryName(path));
                if (File.Exists(path) && (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0) throw new IOException("연결된 설치 관리 파일입니다.");
                files.Add(path, File.Exists(path) ? File.ReadAllBytes(path) : null);
            }
            using (var key = Registry.CurrentUser.OpenSubKey(Key)) {
                existed = key != null;
                if (key != null) foreach (var name in key.GetValueNames()) { values[name] = key.GetValue(name); kinds[name] = key.GetValueKind(name); }
            }
        }
        public void Restore() {
            foreach (var pair in files) {
                if (pair.Value == null) { if (File.Exists(pair.Key)) File.Delete(pair.Key); }
                else File.WriteAllBytes(pair.Key, pair.Value);
            }
            Registry.CurrentUser.DeleteSubKeyTree(Key, false);
            if (existed) using (var key = Registry.CurrentUser.CreateSubKey(Key)) foreach (var pair in values) key.SetValue(pair.Key, pair.Value, kinds[pair.Key]);
        }
    }
    static FileStream UpdateLock(string root) {
        string parent = Path.GetDirectoryName(ValidateRoot(root));
        Directory.CreateDirectory(parent);
        string path = Path.Combine(parent, ".mini-monitor-update.lock");
        if (File.Exists(path) && (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0) throw new IOException("연결된 업데이트 잠금 파일입니다.");
        try { return new FileStream(path, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None); }
        catch (IOException) { throw new IOException("업데이트 또는 설치가 진행 중입니다. 완료된 뒤 다시 시도하세요."); }
    }
    public static void Install(string destination, string group, bool desktop, Action<string> progress) {
        Install(destination, group, desktop, progress, CancellationToken.None);
    }
    public static void Install(string destination, string group, bool desktop, Action<string> progress, CancellationToken cancel) {
        Install(destination, group, desktop, progress, cancel, new ReleaseClient());
    }
    public static void Install(string destination, string group, bool desktop, Action<string> progress, CancellationToken cancel, ReleaseClient client) {
        using (var operationLock = UpdateLock(destination)) InstallCore(destination, group, desktop, progress, cancel, client);
    }
    static void InstallCore(string destination, string group, bool desktop, Action<string> progress, CancellationToken cancel, ReleaseClient client) {
        string root = ValidateRoot(destination), parent = Path.GetDirectoryName(root), metadata = root + "-setup";
        using (var old = Registry.CurrentUser.OpenSubKey(Key)) {
            if (old != null && !String.Equals(old.GetValue("InstallLocation") as string, root, StringComparison.OrdinalIgnoreCase)) throw new IOException("이미 등록된 설치가 있습니다. 기존 설치 경로를 사용하세요.");
        }
        string shortcut = ShortcutPath(group);
        string installedVersion = null;
        if (Directory.Exists(root)) {
            EnsureClosed(root); VerifyTree(root);
            string project = File.ReadAllText(Path.Combine(root, "source", "pyproject.toml"));
            var m = Regex.Match(project, "(?m)^version = \"([0-9.]+)\"");
            if (!m.Success || !ReleaseClient.StableVersion(m.Groups[1].Value)) throw new IOException("기존 버전을 확인할 수 없습니다.");
            installedVersion = m.Groups[1].Value;
        }
        cancel.ThrowIfCancellationRequested(); progress("최신 정식 버전 확인 중…");
        var release = client.Latest(cancel);
        if (installedVersion != null && new System.Version(installedVersion) > new System.Version(release.Version)) throw new IOException("이미 더 최신 버전이 설치되어 있습니다. 이전 버전으로 변경하지 않습니다.");
        // Fail before changing any existing program when setup metadata is unfamiliar.
        if (Directory.Exists(metadata) && (!File.Exists(Path.Combine(metadata, "owner.txt")) || File.ReadAllText(Path.Combine(metadata, "owner.txt")) != "Mini Monitor Setup\n" + root)) throw new IOException("설치 관리 폴더가 이미 사용 중입니다.");
        string desktopLink = desktop ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "Mini Monitor.lnk") : "";
        var registration = new RegistrationBackup(new[] { shortcut, desktopLink, Path.Combine(metadata, "Uninstall.exe"), Path.Combine(metadata, "owner.txt") });
        Directory.CreateDirectory(parent);
        string stage = Path.Combine(parent, ".mini-monitor-setup-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(stage);
        string payload = Path.Combine(stage, "Mini-Monitor"), backup = null;
        bool placed = false;
        try {
            progress((installedVersion == release.Version ? "최신 버전 다시 설치: " : "설치할 버전: ") + release.Version);
            client.DownloadAndExtract(release, stage, cancel, progress);
            progress("전체 파일 검증 중…"); VerifyTree(payload);
            WriteResource("uninstaller.exe", Path.Combine(stage, "Uninstall.exe"));
            cancel.ThrowIfCancellationRequested();
            // Cancellation stops here: the short replacement transaction must finish or roll back.
            progress("프로그램 교체 중… 잠시 기다려 주세요.");
            // Check again immediately before renaming; no forced process termination.
            EnsureClosed(root); NoLinks(root); NoLinks(metadata);
            if (Directory.Exists(root)) {
                VerifyTree(root);
                backup = Path.Combine(parent, ".mini-monitor-before-setup-" + Guid.NewGuid().ToString("N"));
                Directory.Move(root, backup);
            }
            Directory.Move(payload, root); placed = true;
            CheckHealth(root, release.Version);
            progress("시작 메뉴와 앱 제거 항목 등록 중…");
            Directory.CreateDirectory(metadata);
            File.Copy(Path.Combine(stage, "Uninstall.exe"), Path.Combine(metadata, "Uninstall.exe"), true);
            File.WriteAllText(Path.Combine(metadata, "owner.txt"), "Mini Monitor Setup\n" + root, Encoding.UTF8);
            Shortcut(shortcut, Exe(root));
            if (desktop) Shortcut(desktopLink, Exe(root));
            using (var key = Registry.CurrentUser.CreateSubKey(Key)) {
                key.SetValue("DisplayName", "Mini Monitor"); key.SetValue("DisplayVersion", release.Version);
                key.SetValue("Publisher", "Mini Monitor contributors"); key.SetValue("InstallLocation", root);
                key.SetValue("DisplayIcon", Exe(root) + ",0");
                key.SetValue("UninstallString", "\"" + Path.Combine(metadata, "Uninstall.exe") + "\" --uninstall");
                key.SetValue("NoModify", 1, RegistryValueKind.DWord); key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                key.SetValue("StartShortcut", shortcut);
                if (desktop) key.SetValue("DesktopShortcut", desktopLink);
            }
            // Preserve every preference and sign-in file. Keep the previous program
            // as a recoverable backup; never sweep arbitrary legacy directories.
            progress(release.Version + " 설치 완료" + (backup == null ? "" : " · 이전 프로그램 백업 보존"));
        } catch {
            if (placed && Directory.Exists(root)) {
                // Retain a failed candidate for diagnostics instead of recursive deletion.
                Directory.Move(root, Path.Combine(parent, ".mini-monitor-setup-failed-" + Guid.NewGuid().ToString("N")));
            }
            if (backup != null && Directory.Exists(backup) && !Directory.Exists(root)) Directory.Move(backup, root);
            if (placed) registration.Restore();
            throw;
        } finally {
            // Only our newly created scratch tree; validated paths and no reparse traversal.
            if (Directory.Exists(stage)) { foreach (var f in Files(stage).ToArray()) File.Delete(f); RemoveEmpty(stage); }
        }
    }
    static void CheckHealth(string root, string version) {
        var start = new ProcessStartInfo(Path.Combine(root, "Mini-Monitor-CLI.exe"), "--version") {
            UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = root,
            RedirectStandardOutput = true, RedirectStandardError = true
        };
        using (var process = Process.Start(start)) {
            // Drain both pipes concurrently, avoiding a deadlock if a failed executable logs errors.
            var output = process.StandardOutput.ReadToEndAsync(); var error = process.StandardError.ReadToEndAsync();
            if (!process.WaitForExit(30000)) {
                try { process.Kill(); } catch (InvalidOperationException) { }
                // Kill is asynchronous. Never start rollback while this child is still alive.
                process.WaitForExit();
                throw new IOException("새 프로그램 실행 확인 시간이 초과되어 이전 버전을 복구합니다.");
            }
            if (process.ExitCode != 0 || output.Result.Trim() != "Mini Monitor " + version) throw new IOException("새 프로그램 실행 확인에 실패하여 이전 버전을 복구합니다.");
        }
    }
    static void RemoveEmpty(string root) {
        foreach (var dir in Directory.GetDirectories(root)) RemoveEmpty(dir);
        if (!Directory.EnumerateFileSystemEntries(root).Any()) Directory.Delete(root);
    }
    public static string RegisteredRoot() { using (var key = Registry.CurrentUser.OpenSubKey(Key)) return key == null ? null : key.GetValue("InstallLocation") as string; }
    public static void Uninstall(Action<string> progress) {
        string root = RegisteredRoot(); if (root == null) throw new IOException("등록된 설치가 없습니다.");
        using (var operationLock = UpdateLock(root)) UninstallCore(progress);
    }
    static void UninstallCore(Action<string> progress) {
        string root = RegisteredRoot(); if (root == null) throw new IOException("등록된 설치가 없습니다.");
        root = ValidateRoot(root); string metadata = root + "-setup";
        EnsureClosed(root); VerifyTree(root);
        string owner = Path.Combine(metadata, "owner.txt");
        if (!File.Exists(owner) || Files(metadata).Count() != 2 || File.ReadAllText(owner) != "Mini Monitor Setup\n" + root) throw new IOException("설치 관리 정보를 확인할 수 없습니다.");
        string start, desktop;
        using (var key = Registry.CurrentUser.OpenSubKey(Key)) { start = key.GetValue("StartShortcut") as string; desktop = key.GetValue("DesktopShortcut") as string; }
        progress("프로그램 제거 중… 로그인과 설정은 보존됩니다.");
        string retired = root + "-removing-" + Guid.NewGuid().ToString("N");
        Directory.Move(root, retired);
        try {
            RemoveShortcut(start, Exe(root)); RemoveShortcut(desktop, Exe(root));
            using (var run = Registry.CurrentUser.OpenSubKey(RunKey, true)) {
                string value = run == null ? null : run.GetValue(RunName) as string;
                if (value != null && (value.Equals("\"" + Exe(root) + "\"", StringComparison.OrdinalIgnoreCase) || value.StartsWith("\"" + Exe(root) + "\" ", StringComparison.OrdinalIgnoreCase))) run.DeleteValue(RunName);
            }
            Registry.CurrentUser.DeleteSubKeyTree(Key);
        } catch { if (!Directory.Exists(root)) Directory.Move(retired, root); throw; }
        foreach (var f in Files(retired).ToArray()) File.Delete(f); RemoveEmpty(retired);
        File.Delete(Path.Combine(metadata, "Uninstall.exe")); File.Delete(owner);
        if (!Directory.EnumerateFileSystemEntries(metadata).Any()) Directory.Delete(metadata);
        if (!String.IsNullOrEmpty(start)) { var dir = Path.GetDirectoryName(start); if (Directory.Exists(dir) && !Directory.EnumerateFileSystemEntries(dir).Any()) Directory.Delete(dir); }
        progress("제거 완료 · 로그인과 설정은 보존되었습니다.");
    }
}

class Wizard : Form {
    readonly TextBox path = new TextBox(); readonly Button action = new Button(); readonly Button cancel = new Button();
    readonly Button browse = new Button(); readonly CheckBox desktop = new CheckBox(); readonly Label status = new Label(); readonly ProgressBar progress = new ProgressBar();
    bool busy, finished; readonly bool uninstall; CancellationTokenSource cancellation;
    public Wizard(bool removing) {
        uninstall = removing; Text = removing ? "Mini Monitor 제거" : "Mini Monitor 설치";
        ClientSize = new Size(600, 330); MinimumSize = Size; MaximumSize = Size; StartPosition = FormStartPosition.CenterScreen; MaximizeBox = false;
        AutoScaleMode = AutoScaleMode.Dpi; Font = new Font("맑은 고딕", 10); BackColor = Color.FromArgb(24,24,24); ForeColor = Color.WhiteSmoke;
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        var title = new Label { Text = "Mini Monitor", Font = new Font("맑은 고딕", 23, FontStyle.Bold), Left = 26, Top = 22, Width = 545, Height = 48 };
        var info = new Label { Text = removing ? "프로그램만 제거합니다. 로그인과 설정은 보존됩니다." : "Windows 64비트 · 온라인 설치\n실행할 때 최신 버전을 받아 설치하거나 업데이트합니다.", Left = 28, Top = 83, Width = 540, Height = 52 };
        path.SetBounds(28,150,445,28); path.Text = Engine.RegisteredRoot() ?? Engine.DefaultRoot; path.ReadOnly = removing;
        browse.Text = "변경"; browse.SetBounds(485,148,85,32); browse.Enabled = !removing && Engine.RegisteredRoot() == null;
        browse.Click += delegate { using (var picker = new FolderBrowserDialog { Description = "Mini-Monitor 폴더를 만들 상위 폴더를 선택하세요." }) if (picker.ShowDialog(this) == DialogResult.OK) path.Text = Path.Combine(picker.SelectedPath, "Mini-Monitor"); };
        desktop.Text = "바탕화면 바로가기 만들기"; desktop.SetBounds(28,190,400,28); desktop.Visible = !removing;
        status.SetBounds(28,230,542,26); status.Text = removing ? "실행 중인 Mini Monitor를 먼저 종료하세요." : "현재 사용자 계정에 설치합니다. 자동 실행 설정은 유지됩니다.";
        progress.SetBounds(28,266,300,6); progress.Visible = false;
        action.Text = removing ? "제거" : "설치 / 갱신"; action.SetBounds(350,278,112,34);
        cancel.Text = "취소"; cancel.SetBounds(472,278,98,34); cancel.Click += delegate { if (busy) { cancellation.Cancel(); cancel.Enabled = false; } else Close(); };
        action.Click += async delegate {
            if (finished) {
                if (!uninstall) Process.Start(new ProcessStartInfo(Engine.Exe(path.Text)) { UseShellExecute = true, WorkingDirectory = path.Text });
                Close(); return;
            }
            busy = true; cancellation = new CancellationTokenSource(); action.Enabled = browse.Enabled = path.Enabled = desktop.Enabled = false; cancel.Enabled = !uninstall;
            progress.Visible = true; progress.Style = ProgressBarStyle.Marquee;
            string target = path.Text; bool makeDesktop = desktop.Checked;
            try {
                await Task.Run(() => { using (var gate = Program.AcquireLock()) { if (uninstall) Engine.Uninstall(Report); else Engine.Install(target, "Mini Monitor", makeDesktop, Report, cancellation.Token); } });
                finished = true; action.Text = uninstall ? "닫기" : "실행"; cancel.Text = "닫기";
            } catch (OperationCanceledException) { status.Text = "취소했습니다. 기존 프로그램은 변경하지 않았습니다."; }
            catch (Exception ex) { status.Text = "작업을 완료하지 못했습니다. 다시 시도할 수 있습니다."; MessageBox.Show(this, ex.Message, Text, MessageBoxButtons.OK, MessageBoxIcon.Warning); }
            finally { busy = false; cancellation.Dispose(); action.Enabled = cancel.Enabled = true; path.Enabled = !uninstall && !finished; desktop.Enabled = !finished; progress.Visible = false; }
        };
        Controls.AddRange(new Control[] { title,info,path,browse,desktop,status,progress,action,cancel });
        FormClosing += (s,e) => { if (busy) { e.Cancel = true; if (cancel.Enabled) cancellation.Cancel(); } };
    }
    void Report(string value) { BeginInvoke((Action)(() => { status.Text = value; if (value.StartsWith("프로그램 교체")) cancel.Enabled = false; })); }
}

public static class Program {
    sealed class LockHandle : IDisposable { Mutex handle; public LockHandle(Mutex m) { handle = m; } public void Dispose() { handle.ReleaseMutex(); handle.Dispose(); } }
    public static IDisposable AcquireLock() { var m = new Mutex(false, @"Local\MiniMonitor-Setup-0B36579F"); bool acquired; try { acquired = m.WaitOne(0); } catch (AbandonedMutexException) { acquired = true; } if (!acquired) { m.Dispose(); throw new IOException("다른 설치 작업이 진행 중입니다."); } return new LockHandle(m); }
    [STAThread] public static int Main(string[] args) {
        AppContext.SetSwitch("Switch.System.IO.UseLegacyPathHandling", false);
        AppContext.SetSwitch("Switch.System.IO.BlockLongPaths", false);
        bool quiet = args.Contains("--quiet"), uninstall = args.Contains("--uninstall");
        try {
            if (uninstall && !args.Contains("--worker")) {
                string temp = Path.Combine(Path.GetTempPath(), "MiniMonitor-Uninstall-" + Guid.NewGuid().ToString("N") + ".exe");
                File.Copy(Application.ExecutablePath, temp);
                Process.Start(new ProcessStartInfo(temp, "--uninstall --worker" + (quiet ? " --quiet" : "")) { UseShellExecute = false });
                return 0;
            }
            if (quiet) {
                using (var gate = AcquireLock()) {
                    if (uninstall) Engine.Uninstall(s => { });
                    else Engine.Install(args.First(a => a.StartsWith("--dir=")).Substring(6), args.Where(a => a.StartsWith("--group=")).Select(a => a.Substring(8)).FirstOrDefault() ?? "Mini Monitor", false, s => { });
                }
            } else { Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false); Application.Run(new Wizard(uninstall)); }
            return 0;
        } catch (Exception ex) {
            if (!quiet) MessageBox.Show(ex.Message, "Mini Monitor 설치", MessageBoxButtons.OK, MessageBoxIcon.Error);
            else Console.Error.WriteLine(ex.ToString());
            return 1;
        }
    }
}
}


// SPDX-License-Identifier: GPL-3.0-or-later
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;

namespace MiniMonitorInstaller {
public interface IReleaseTransport {
    void Fetch(string url, Stream output, long limit, CancellationToken cancel, Action<long> progress);
}
public sealed class GitHubTransport : IReleaseTransport {
    public static void ValidateUrl(string value) {
        Uri uri;
        if (!Uri.TryCreate(value, UriKind.Absolute, out uri) || uri.Scheme != "https" || !uri.IsDefaultPort || uri.UserInfo != "" || uri.Fragment != "" ||
            !new[] { "api.github.com", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com" }.Contains(uri.Host))
            throw new IOException("허용되지 않은 다운로드 주소입니다.");
    }
    public void Fetch(string url, Stream output, long limit, CancellationToken cancel, Action<long> progress) {
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
        for (int redirect = 0; redirect < 6; redirect++) {
            cancel.ThrowIfCancellationRequested(); ValidateUrl(url);
            var request = (HttpWebRequest)WebRequest.Create(url);
            request.UserAgent = "Mini-Monitor-Setup/1.0"; request.AllowAutoRedirect = false;
            request.Timeout = 30000; request.ReadWriteTimeout = 30000;
            using (cancel.Register(request.Abort)) {
                try {
                    using (var response = (HttpWebResponse)request.GetResponse()) {
                        int status = (int)response.StatusCode;
                        if (status == 301 || status == 302 || status == 303 || status == 307 || status == 308) {
                            url = new Uri(new Uri(url), response.Headers["Location"]).AbsoluteUri; continue;
                        }
                        if (status != 200 || response.ContentLength > limit) throw new IOException("다운로드 응답 또는 파일 크기가 올바르지 않습니다.");
                        using (var input = response.GetResponseStream()) {
                            var buffer = new byte[65536]; long total = 0; int count;
                            while ((count = input.Read(buffer, 0, buffer.Length)) != 0) {
                                cancel.ThrowIfCancellationRequested(); total += count;
                                if (total > limit) throw new IOException("다운로드 크기 제한을 초과했습니다.");
                                output.Write(buffer, 0, count); progress(total);
                            }
                            if (response.ContentLength >= 0 && total != response.ContentLength) throw new IOException("다운로드가 중단되었습니다. 다시 시도하세요.");
                        }
                        return;
                    }
                } catch (WebException ex) {
                    cancel.ThrowIfCancellationRequested();
                    throw new IOException("GitHub에 연결하지 못했습니다. 인터넷 연결 또는 요청 제한을 확인하고 다시 시도하세요.", ex);
                }
            }
        }
        throw new IOException("다운로드 이동 횟수를 초과했습니다.");
    }
}
public sealed class ReleaseInfo {
    public string Version { get; internal set; }
    public string ArchiveUrl { get; internal set; }
    public string ArchiveHash { get; internal set; }
    public long ArchiveSize { get; internal set; }
    internal Dictionary<string, string> Files;
}
public sealed class ReleaseClient {
    public const string Repository = "https://github.com/contentriumkorea/mini-monitor";
    public const string LatestUrl = "https://api.github.com/repos/contentriumkorea/mini-monitor/releases/latest";
    const long MaxArchive = 1073741824, MaxUnpacked = 3221225472;
    readonly IReleaseTransport transport; readonly byte[] publicKey;
    static readonly Func<byte[],byte[],byte[],bool> VerifySignature = LoadEmbeddedVerifier();
    static Func<byte[],byte[],byte[],bool> LoadEmbeddedVerifier() {
        // Byte-load the pinned resource explicitly. Normal assembly probing must
        // never select a weak-named lookalike DLL next to a downloaded Setup.exe.
        using (var input = typeof(ReleaseClient).Assembly.GetManifestResourceStream("Chaos.NaCl.dll")) {
            if (input == null) throw new IOException("내장 서명 검증기가 없습니다.");
            using (var bytes = new MemoryStream()) {
                input.CopyTo(bytes);
                var assembly = Assembly.Load(bytes.ToArray());
                var method = assembly.GetType("Chaos.NaCl.Ed25519", true).GetMethod("Verify", new[] {typeof(byte[]),typeof(byte[]),typeof(byte[])});
                return (Func<byte[],byte[],byte[],bool>)Delegate.CreateDelegate(typeof(Func<byte[],byte[],byte[],bool>), method);
            }
        }
    }
    public ReleaseClient() : this(new GitHubTransport(), Convert.FromBase64String("03H3C4UrykHZq8ivGMHVV0QwOZyhuHKfZYkwo48RH0U=")) { }
    public ReleaseClient(IReleaseTransport transport, byte[] publicKey) { this.transport = transport; this.publicKey = (byte[])publicKey.Clone(); }
    static IOException Invalid() { return new IOException("릴리스 서명 또는 파일 목록이 올바르지 않습니다."); }
    static object Json(byte[] bytes) {
        return new JavaScriptSerializer { MaxJsonLength = 1048576, RecursionLimit = 32 }.DeserializeObject(new UTF8Encoding(false, true).GetString(bytes));
    }
    static string Quote(string value) {
        var s = new StringBuilder("\"");
        foreach (char c in value) {
            switch(c) {
                case '"': s.Append("\\\""); break; case '\\': s.Append("\\\\"); break;
                case '\b': s.Append("\\b"); break; case '\f': s.Append("\\f"); break;
                case '\n': s.Append("\\n"); break; case '\r': s.Append("\\r"); break; case '\t': s.Append("\\t"); break;
                default: if (c < 32) s.Append("\\u" + ((int)c).ToString("x4")); else s.Append(c); break;
            }
        }
        return s.Append('"').ToString();
    }
    static string Canonical(object value) {
        if (value is string) return Quote((string)value);
        var map = value as Dictionary<string,object>;
        if (map != null) return "{" + String.Join(",", map.OrderBy(p => p.Key, StringComparer.Ordinal).Select(p => Quote(p.Key) + ":" + Canonical(p.Value))) + "}";
        var list = value as object[];
        if (list != null) return "[" + String.Join(",", list.Select(Canonical)) + "]";
        if (value is int || value is long) return Convert.ToString(value, CultureInfo.InvariantCulture);
        if (value is bool) return (bool)value ? "true" : "false";
        if (value == null) return "null";
        throw Invalid();
    }
    static long Integer(object value) { if (!(value is int) && !(value is long)) throw Invalid(); return Convert.ToInt64(value); }
    public static bool StableVersion(string version) {
        System.Version parsed;
        return version != null && Regex.IsMatch(version, @"\A(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\z") && System.Version.TryParse(version, out parsed);
    }
    static void SafePath(string path) {
        if (path == null || !path.StartsWith("Mini-Monitor/", StringComparison.Ordinal) || path.Length > 24000 || path != path.Normalize(NormalizationForm.FormC)) throw Invalid();
        foreach (string part in path.Split('/')) {
            if (part.Length == 0 || part == "." || part == ".." || part.EndsWith(".") || part.EndsWith(" ") || part.Any(c => c < 32 || "\\:*?\"<>|~".Contains(c)) ||
                Regex.IsMatch(part, @"\A(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|\z)", RegexOptions.IgnoreCase)) throw Invalid();
        }
    }
    public ReleaseInfo Verify(byte[] raw, byte[] signature) {
        try {
            if (raw.Length == 0 || raw.Length > 1048576 || signature.Length != 64 || !VerifySignature(signature, raw, publicKey)) throw Invalid();
            var obj = Json(raw) as Dictionary<string,object>;
            if (obj == null || obj.Count != 7 || !new[] { "schema_version", "version", "channel", "archive_url", "archive_sha256", "archive_size", "files" }.All(obj.ContainsKey)) throw Invalid();
            if (!raw.SequenceEqual(new UTF8Encoding(false, true).GetBytes(Canonical(obj)))) throw Invalid();
            var info = new ReleaseInfo { Version = obj["version"] as string, ArchiveUrl = obj["archive_url"] as string, ArchiveHash = obj["archive_sha256"] as string, ArchiveSize = Integer(obj["archive_size"]), Files = new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase) };
            if (Integer(obj["schema_version"]) != 1 || (obj["channel"] as string) != "stable" || !StableVersion(info.Version) || info.ArchiveUrl != Repository + "/releases/download/v" + info.Version + "/Mini-Monitor.zip" || info.ArchiveHash == null || !Regex.IsMatch(info.ArchiveHash, @"\A[0-9a-f]{64}\z") || info.ArchiveSize <= 0 || info.ArchiveSize > MaxArchive) throw Invalid();
            var files = obj["files"] as object[];
            if (files == null || files.Length == 0 || files.Length > 20000) throw Invalid();
            string previous = null;
            foreach (var item in files) {
                var record = item as object[];
                if (record == null || record.Length != 2) throw Invalid();
                var path = record[0] as string; var hash = record[1] as string; SafePath(path);
                if (hash == null || !Regex.IsMatch(hash, @"\A[0-9a-f]{64}\z") || (previous != null && String.CompareOrdinal(previous, path) >= 0) || info.Files.ContainsKey(path)) throw Invalid();
                info.Files.Add(path, hash); previous = path;
            }
            if (!info.Files.ContainsKey("Mini-Monitor/Mini-Monitor.exe") || !info.Files.ContainsKey("Mini-Monitor/SHA256SUMS.txt")) throw Invalid();
            return info;
        } catch (IOException) { throw; } catch (Exception ex) { throw new IOException("릴리스 검증에 실패했습니다.", ex); }
    }
    byte[] FetchSmall(string url, int limit, CancellationToken cancel) {
        using (var stream = new MemoryStream()) { transport.Fetch(url, stream, limit, cancel, n => {}); if (stream.Length > limit) throw Invalid(); return stream.ToArray(); }
    }
    public ReleaseInfo Latest(CancellationToken cancel) {
        try {
            var release = Json(FetchSmall(LatestUrl, 1048576, cancel)) as Dictionary<string,object>;
            if (release == null || !release.ContainsKey("tag_name") || !release.ContainsKey("draft") || !release.ContainsKey("prerelease") || !Object.Equals(release["draft"], false) || !Object.Equals(release["prerelease"], false)) throw Invalid();
            string tag = release["tag_name"] as string;
            if (tag == null || !tag.StartsWith("v") || !StableVersion(tag.Substring(1))) throw Invalid();
            string prefix = Repository + "/releases/download/" + tag + "/";
            var info = Verify(FetchSmall(prefix + "update-v2-manifest.json", 1048576, cancel), FetchSmall(prefix + "update-v2-manifest.sig", 64, cancel));
            if (tag != "v" + info.Version) throw Invalid();
            return info;
        } catch (OperationCanceledException) { throw; } catch (IOException) { throw; } catch (Exception ex) { throw new IOException("최신 릴리스 정보를 확인할 수 없습니다.", ex); }
    }
    static string Hash(Stream input) { using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(input)).Replace("-", "").ToLowerInvariant(); }
    public void DownloadAndExtract(ReleaseInfo release, string stage, CancellationToken cancel, Action<string> progress) {
        string zipPath = Path.Combine(stage, "download.zip");
        using (var output = new FileStream(zipPath, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None)) {
            int last = -1;
            transport.Fetch(release.ArchiveUrl, output, release.ArchiveSize, cancel, count => { int pct = (int)(count * 100 / release.ArchiveSize); if (pct != last) { last = pct; progress("프로그램 다운로드 " + pct + "%"); } });
            cancel.ThrowIfCancellationRequested(); output.Position = 0;
            if (output.Length != release.ArchiveSize || Hash(output) != release.ArchiveHash) throw Invalid();
            output.Position = 0;
            using (var archive = new ZipArchive(output, ZipArchiveMode.Read, true)) {
                var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase); long total = 0;
                foreach (var entry in archive.Entries) {
                    cancel.ThrowIfCancellationRequested(); SafePath(entry.FullName);
                    string expected;
                    if (!release.Files.TryGetValue(entry.FullName, out expected) || !seen.Add(entry.FullName) || ((entry.ExternalAttributes >> 16) & 0xF000) == 0xA000) throw Invalid();
                    total = checked(total + entry.Length); if (total > MaxUnpacked) throw Invalid();
                    string destination = Path.GetFullPath(Path.Combine(stage, entry.FullName.Replace('/', '\\')));
                    if (!destination.StartsWith(Path.GetFullPath(stage) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) throw Invalid();
                    Engine.NoLinks(Path.GetDirectoryName(destination)); Directory.CreateDirectory(Path.GetDirectoryName(destination));
                    using (var input = entry.Open()) using (var file = new FileStream(destination, FileMode.CreateNew, FileAccess.ReadWrite)) {
                        var buffer = new byte[65536]; int n; long actual = 0;
                        while ((n = input.Read(buffer, 0, buffer.Length)) > 0) { cancel.ThrowIfCancellationRequested(); actual += n; if (actual > entry.Length) throw Invalid(); file.Write(buffer, 0, n); }
                        file.Position = 0; if (actual != entry.Length || Hash(file) != expected) throw Invalid();
                    }
                }
                if (seen.Count != release.Files.Count) throw Invalid();
            }
        }
    }
}
}


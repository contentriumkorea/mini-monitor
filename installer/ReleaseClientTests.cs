// SPDX-License-Identifier: GPL-3.0-or-later
using System;
using System.Reflection;
using System.IO;
using System.IO.Compression;
using System.Text;
using System.Linq;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Threading;
using MiniMonitorInstaller;
class ReleaseClientTests {
    sealed class Transport : IReleaseTransport {
        public Dictionary<string,byte[]> Responses = new Dictionary<string,byte[]>();
        public void Fetch(string url, Stream output, long limit, CancellationToken token, Action<long> progress) {
            token.ThrowIfCancellationRequested(); byte[] value = Responses[url];
            if (value.LongLength > limit) throw new IOException("too large");
            output.Write(value, 0, value.Length); progress(value.Length);
        }
    }
    static byte[] Bytes(string value) { return Encoding.UTF8.GetBytes(value); }
    static string Hash(byte[] value) { using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(value)).Replace("-", "").ToLowerInvariant(); }
    static readonly byte[] Seed = Enumerable.Range(1,32).Select(n => (byte)n).ToArray();
    static byte[] Sign(byte[] value) { return Chaos.NaCl.Ed25519.Sign(value, Chaos.NaCl.Ed25519.ExpandedPrivateKeyFromSeed(Seed)); }
    static int checks;
    static void Assert(bool ok, string name) { if (!ok) throw new Exception("FAIL " + name); checks++; Console.WriteLine("PASS " + name); }
    static void Reject(Action action, string name) { try { action(); } catch (IOException) { Assert(true, name); return; } throw new Exception("FAIL accepted " + name); }
    static void Health(string root, string version) {
        try { typeof(Engine).GetMethod("CheckHealth", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, new object[] {root, version}); }
        catch(TargetInvocationException ex) { throw ex.InnerException; }
    }
    static Tuple<byte[],byte[]> Fixture(string version, bool badHealth = false) {
        byte[] cli = File.ReadAllBytes(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"FixtureCli.exe"));
        var files = new SortedDictionary<string,byte[]>(StringComparer.Ordinal) {
            {"Mini-Monitor/Mini-Monitor.exe", cli}, {"Mini-Monitor/Mini-Monitor-CLI.exe", cli},
            {"Mini-Monitor/source/pyproject.toml", Bytes("version = \"" + (badHealth ? "0.0.0" : version) + "\"\n")}
        };
        files.Add("Mini-Monitor/SHA256SUMS.txt", Bytes(String.Join("\n", files.Select(p => Hash(p.Value) + "  " + p.Key.Substring("Mini-Monitor/".Length))) + "\n"));
        byte[] zip;
        using (var memory = new MemoryStream()) { using (var archive = new ZipArchive(memory, ZipArchiveMode.Create, true)) foreach (var pair in files) using(var target = archive.CreateEntry(pair.Key).Open()) target.Write(pair.Value, 0, pair.Value.Length); zip = memory.ToArray(); }
        string records = String.Join(",", files.Select(p => "[\"" + p.Key + "\",\"" + Hash(p.Value) + "\"]"));
        var raw = Bytes("{\"archive_sha256\":\"" + Hash(zip) + "\",\"archive_size\":" + zip.Length + ",\"archive_url\":\"" + ReleaseClient.Repository + "/releases/download/v" + version + "/Mini-Monitor.zip\",\"channel\":\"stable\",\"files\":[" + records + "],\"schema_version\":1,\"version\":\"" + version + "\"}");
        return Tuple.Create(raw, zip);
    }
    static int Main(string[] args) {
        AppContext.SetSwitch("Switch.System.IO.UseLegacyPathHandling", false);
        AppContext.SetSwitch("Switch.System.IO.BlockLongPaths", false);
        var assembly = Assembly.LoadFrom(args[0]);
        if (assembly.GetType("MiniMonitorInstaller.ReleaseClient") == null) {
            Console.Error.WriteLine("FAIL: installer cannot resolve and verify a latest release; ReleaseClient missing");
            return 1;
        }
        try {
            string healthRoot = Path.Combine(args[1],"health-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(Path.Combine(healthRoot,"source"));
            File.Copy(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"FixtureCli.exe"), Path.Combine(healthRoot,"Mini-Monitor-CLI.exe"));
            File.WriteAllText(Path.Combine(healthRoot,"source","pyproject.toml"),"version = \"10.2.10\"");
            Reject(() => Health(healthRoot,"0.2.10"),"exact version required, suffix is not enough");
            File.WriteAllText(Path.Combine(healthRoot,"wait.txt"),"test");
            Reject(() => Health(healthRoot,"10.2.10"),"real CLI timeout reported");
            Assert(!System.Diagnostics.Process.GetProcessesByName("Mini-Monitor-CLI").Any(p => { using(p) { try { return p.MainModule.FileName.StartsWith(healthRoot,StringComparison.OrdinalIgnoreCase); } catch(InvalidOperationException) { return false; } } }),"timed-out child exited before rollback");
            Directory.Move(healthRoot,healthRoot+"-recovered");
            if (Engine.RegisteredRoot() != null) throw new Exception("Use isolated account: installer registration exists");
            var transport = new Transport();
            var client = new ReleaseClient(transport, Chaos.NaCl.Ed25519.PublicKeyFromSeed(Seed));
            string installRoot = Path.Combine(args[1], "install-" + Guid.NewGuid().ToString("N"), "Mini-Monitor");
            string group = "Mini Monitor Fixture " + Guid.NewGuid().ToString("N");
            string binaryHash = Hash(File.ReadAllBytes(args[0]));
            foreach (string version in new[] {"1.2.3", "1.2.4"}) {
                var fixture = Fixture(version); string prefix = ReleaseClient.Repository + "/releases/download/v" + version + "/";
                transport.Responses[ReleaseClient.LatestUrl] = Bytes("{\"tag_name\":\"v" + version + "\",\"draft\":false,\"prerelease\":false}");
                transport.Responses[prefix + "update-v2-manifest.json"] = fixture.Item1;
                transport.Responses[prefix + "update-v2-manifest.sig"] = Sign(fixture.Item1);
                transport.Responses[prefix + "Mini-Monitor.zip"] = fixture.Item2;
                var info = client.Latest(CancellationToken.None);
                Assert(info.Version == version, "same binary resolves " + version);
                string stage = Path.Combine(args[1], "fixture-" + version + "-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(stage);
                client.DownloadAndExtract(info, stage, CancellationToken.None, s => {});
                Assert(File.ReadAllText(Path.Combine(stage, "Mini-Monitor", "source", "pyproject.toml")).Contains(version), "signed extracted payload " + version);
                Engine.Install(installRoot, group, false, s => {}, CancellationToken.None, client);
                Assert(File.ReadAllText(Path.Combine(installRoot,"source","pyproject.toml")).Contains(version), "same setup installs " + version);
                foreach (string extra in new[] { "Mini-Monitor/unexpected.txt", "Mini-Monitor/MINI-MONITOR.EXE", "Mini-Monitor/../escape.txt" }) {
                    byte[] altered;
                    using (var memory = new MemoryStream()) {
                        memory.Write(fixture.Item2,0,fixture.Item2.Length); memory.Position=0;
                        using(var archive = new ZipArchive(memory,ZipArchiveMode.Update,true)) using(var added=archive.CreateEntry(extra).Open()) added.WriteByte(1);
                        altered=memory.ToArray();
                    }
                    byte[] updated = Bytes(Encoding.UTF8.GetString(fixture.Item1).Replace(Hash(fixture.Item2),Hash(altered)).Replace("\"archive_size\":"+fixture.Item2.Length,"\"archive_size\":"+altered.Length));
                    var alteredInfo=client.Verify(updated,Sign(updated)); transport.Responses[prefix+"Mini-Monitor.zip"]=altered;
                    string badStage=Path.Combine(args[1],Guid.NewGuid().ToString("N")); Directory.CreateDirectory(badStage);
                    Reject(()=>client.DownloadAndExtract(alteredInfo,badStage,CancellationToken.None,s=>{}),"reject ZIP extra/alias/escape " + extra);
                }
                transport.Responses[prefix+"Mini-Monitor.zip"]=fixture.Item2;
                using(var during = new CancellationTokenSource()) {
                    try { Engine.Install(installRoot,group,false,s=>{if(s.StartsWith("프로그램 다운로드")) during.Cancel();},during.Token,client); throw new Exception("mid-download cancel ignored"); }
                    catch(OperationCanceledException) { Assert(File.ReadAllText(Path.Combine(installRoot,"source","pyproject.toml")).Contains(version),"cancel during download preserves installed files"); }
                }
                var badSignature = Sign(fixture.Item1); badSignature[0] ^= 1;
                Reject(() => client.Verify(fixture.Item1, badSignature), "signature tampering");
                foreach (var replace in new[] {
                    new[] {ReleaseClient.Repository, "https://evil.example/mini-monitor"},
                    new[] {"Mini-Monitor/Mini-Monitor.exe", "Mini-Monitor/../escape.exe"},
                    new[] {"Mini-Monitor/Mini-Monitor.exe", "Mini-Monitor/CON.exe"},
                    new[] {"\"schema_version\":1", "\"schema_version\":1,\"schema_version\":1"},
                    new[] {"\"channel\":\"stable\"", "\"channel\":\"beta\""},
                    new[] {"\"archive_size\":" + fixture.Item2.Length, "\"archive_size\":1073741825"}
                }) { var bad = Bytes(Encoding.UTF8.GetString(fixture.Item1).Replace(replace[0],replace[1])); Reject(() => client.Verify(bad, Sign(bad)), "signed invalid field " + replace[1]); }
                transport.Responses[prefix + "Mini-Monitor.zip"] = fixture.Item2.Take(fixture.Item2.Length - 1).ToArray();
                string broken = Path.Combine(args[1], Guid.NewGuid().ToString("N")); Directory.CreateDirectory(broken);
                Reject(() => client.DownloadAndExtract(info, broken, CancellationToken.None, s => {}), "truncated download");
                Assert(!Directory.Exists(Path.Combine(broken,"Mini-Monitor")), "truncation extracts nothing");
            }
            string installedHash = Hash(File.ReadAllBytes(Path.Combine(installRoot,"source","pyproject.toml")));
            using (var cancel = new CancellationTokenSource()) {
                cancel.Cancel();
                try { Engine.Install(installRoot,group,false,s => {},cancel.Token,client); throw new Exception("installation cancellation ignored"); }
                catch(OperationCanceledException) { Assert(Hash(File.ReadAllBytes(Path.Combine(installRoot,"source","pyproject.toml"))) == installedHash,"cancel preserves installation"); }
            }
            foreach (var version in new[] {"1.2.3","1.2.5"}) {
                var bad = Fixture(version, version == "1.2.5"); string prefix = ReleaseClient.Repository + "/releases/download/v" + version + "/";
                transport.Responses[ReleaseClient.LatestUrl] = Bytes("{\"tag_name\":\"v" + version + "\",\"draft\":false,\"prerelease\":false}");
                transport.Responses[prefix+"update-v2-manifest.json"] = bad.Item1; transport.Responses[prefix+"update-v2-manifest.sig"] = Sign(bad.Item1); transport.Responses[prefix+"Mini-Monitor.zip"] = bad.Item2;
                Reject(() => Engine.Install(installRoot,group,false,s=>{},CancellationToken.None,client), version == "1.2.3" ? "downgrade refused" : "health failure rolls back");
                Assert(Hash(File.ReadAllBytes(Path.Combine(installRoot,"source","pyproject.toml"))) == installedHash,"prior files retained");
            }
            Engine.Uninstall(s=>{});
            Assert(!Directory.Exists(installRoot) && Engine.RegisteredRoot() == null,"fixture removal and registration cleanup");
            Assert(Hash(File.ReadAllBytes(args[0])) == binaryHash,"two installed versions used identical setup binary");
            foreach (var url in new[] {"http://github.com/a", "https://evil.example/a", "https://github.com.evil.example/a", "https://user@github.com/a", "https://github.com:444/a"}) Reject(() => GitHubTransport.ValidateUrl(url), "disallowed URL " + url);
            using (var cancel = new CancellationTokenSource()) { cancel.Cancel(); try { client.Latest(cancel.Token); throw new Exception("cancel ignored"); } catch(OperationCanceledException) { Assert(true,"cancel before request"); } }
            if (args.Length > 2) {
                var live = new ReleaseClient(); var release = live.Latest(CancellationToken.None);
                Assert(ReleaseClient.StableVersion(release.Version), "public signed latest " + release.Version);
            }
            Console.WriteLine(checks + " checks passed"); return 0;
        } catch (Exception ex) { Console.Error.WriteLine(ex); return 1; }
    }
}


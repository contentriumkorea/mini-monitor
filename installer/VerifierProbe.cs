// Test only: an adjacent lookalike verifier must never be trusted.
using System;
using System.Reflection;
using System.Text;
class VerifierProbe {
    static int Main(string[] args) {
        var assembly = Assembly.LoadFrom(args[0]);
        var type = assembly.GetType("MiniMonitorInstaller.ReleaseClient");
        var client = Activator.CreateInstance(type);
        string zeros = new string('0',64);
        string raw = "{\"archive_sha256\":\"" + zeros + "\",\"archive_size\":1,\"archive_url\":\"https://github.com/contentriumkorea/mini-monitor/releases/download/v1.2.3/Mini-Monitor.zip\",\"channel\":\"stable\",\"files\":[[\"Mini-Monitor/Mini-Monitor.exe\",\"" + zeros + "\"],[\"Mini-Monitor/SHA256SUMS.txt\",\"" + zeros + "\"]],\"schema_version\":1,\"version\":\"1.2.3\"}";
        try { type.GetMethod("Verify").Invoke(client,new object[] {Encoding.UTF8.GetBytes(raw),new byte[64]}); }
        catch(TargetInvocationException ex) {
            if (ex.InnerException is System.IO.IOException) { Console.WriteLine("PASS adjacent verifier cannot accept unsigned manifest"); return 0; }
            throw;
        }
        Console.Error.WriteLine("FAIL external verifier bypassed embedded signature verification"); return 1;
    }
}

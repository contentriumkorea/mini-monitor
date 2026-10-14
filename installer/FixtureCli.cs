// Test-only executable; never embedded in the shipped installer.
using System;
using System.IO;
using System.Text.RegularExpressions;
class FixtureCli {
    static int Main() {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        Console.WriteLine("Mini Monitor " + Regex.Match(File.ReadAllText(Path.Combine(root,"source","pyproject.toml")), "version = \"([^\"]+)\"").Groups[1].Value);
        return 0;
    }
}

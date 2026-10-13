// SPDX-License-Identifier: GPL-3.0-or-later
using System;
using System.IO;
using System.Linq;
using System.Windows.Forms;
using System.Drawing;
using MiniMonitorInstaller;
class InstallerTests {
    static void ExpectFailure(Action action, string name) {
        try { action(); } catch (IOException) { Console.WriteLine("PASS " + name); return; }
        throw new Exception("Expected failure: " + name);
    }
    [STAThread] static int Main(string[] args) {
        AppContext.SetSwitch("Switch.System.IO.UseLegacyPathHandling", false);
        AppContext.SetSwitch("Switch.System.IO.BlockLongPaths", false);
        try {
            if (args[0] == "--render") {
                Application.EnableVisualStyles();
                var type = typeof(Engine).Assembly.GetType("MiniMonitorInstaller.Wizard");
                using (var form = (Form)Activator.CreateInstance(type, new object[] { false })) {
                    form.Show(); Application.DoEvents();
                    using (var image = new Bitmap(form.Width, form.Height)) { form.DrawToBitmap(image, new Rectangle(0, 0, form.Width, form.Height)); image.Save(args[1]); }
                    form.Close();
                }
                return 0;
            }
            string root = args[0], group = args[1];
            Engine.VerifyTree(root); Console.WriteLine("PASS full installed inventory");
            ExpectFailure(() => Engine.ValidateRoot(Path.GetPathRoot(root)), "drive root rejected");
            ExpectFailure(() => Engine.ValidateRoot(Path.GetDirectoryName(root)), "non-product directory rejected");
            byte[] original = File.ReadAllBytes(Path.Combine(root, "BUILD-INFO.json"));
            File.AppendAllText(Path.Combine(root, "BUILD-INFO.json"), " ");
            ExpectFailure(() => Engine.Install(root, group, false, s => {}), "modified installation preserved");
            File.WriteAllBytes(Path.Combine(root, "BUILD-INFO.json"), original);
            using (var locked = new FileStream(Path.Combine(Path.GetDirectoryName(root), ".mini-monitor-update.lock"), FileMode.Open, FileAccess.ReadWrite, FileShare.None))
                ExpectFailure(() => Engine.Install(root, group, false, s => {}), "active updater blocks setup");
            ExpectFailure(() => Engine.Install(root, group, false, s => { if (s.StartsWith("시작 메뉴")) throw new IOException("Injected registration failure"); }), "failed install rolls back");
            Engine.VerifyTree(root);
            if (Engine.RegisteredRoot() != root) throw new Exception("Registration not restored");
            Console.WriteLine("PASS rolled-back files and registration valid");
            return 0;
        } catch (Exception ex) { Console.Error.WriteLine(ex); return 1; }
    }
}

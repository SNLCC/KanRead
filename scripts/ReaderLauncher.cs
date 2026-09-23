using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

// Original launcher code. Uses the installed Windows runtime and browser.
//
// 关键行为：只有当 8765 端口上**没有**本应用在跑时才启动后台。
// 这带来一个很难自查的坑：更新程序后重新双击本 exe，旧的后台进程仍在运行，
// 浏览器读到的是磁盘上的新前端，接口却还是旧代码——用户看到"我重开了程序却没生效"。
// 因此这里会比较接口版本：旧版本先结束掉再启动新的。
class KanReadLauncher {
    const string Url = "http://127.0.0.1:8765/";
    // 与 app/version.py 的 API_VERSION 保持一致（tests/test_ui_contracts.py 会断言）。
    const int ExpectedApiVersion = 46;
    // /api/health 里的应用标识。**改名时要把旧标识追加到这里**：用户的端口上可能正跑着
    // 上一代的后台，认不出来就会被当成别人的程序，于是新后台不启动、旧后台也换不掉
    // （"重开了却没生效"）。与 app/single_instance.py 的 LEGACY_MARKERS 是同一份清单，
    // 两边必须同步。
    static readonly string[] AppMarkers = new [] {"kanread"};

    static string Health() {
        try {
            var request = (HttpWebRequest)WebRequest.Create(Url + "api/health");
            request.Proxy = null;
            request.Timeout = 1000;
            using (var response = request.GetResponse())
            using (var reader = new StreamReader(response.GetResponseStream()))
                return reader.ReadToEnd();
        } catch { return null; }
    }

    // 端口上是不是本应用；是本应用时给出它的接口版本（没有该字段=旧版本，记 0）。
    // 必须区分"什么都没有"和"旧版本后台没有版本字段"，否则永远换不掉旧后台。
    static bool Running(out int version) {
        string body = Health();
        version = 0;
        if (body == null) return false;
        bool ours = false;
        foreach (string marker in AppMarkers) if (body.Contains(marker)) ours = true;
        if (!ours) return false;
        var match = Regex.Match(body, "\"api_version\"\\s*:\\s*(\\d+)");
        if (match.Success) version = int.Parse(match.Groups[1].Value);
        return true;
    }

    // 后台是否带着"停止期限"（关掉窗口后它会在几秒内自行退出，启动宽限期间也算）。
    // 用户"关掉窗口立刻又双击图标"时会撞上这个窗口期：不能直接复用，否则新窗口刚打开
    // 后台就退了；直接等它退出又要多等十几秒。所以先发一次心跳把期限推后（续命），
    // 实在续不上（已经在收尾）才等它退干净。
    static bool Closing() {
        string body = Health();
        return body != null && body.Contains("\"closing\":true");
    }

    static void Ping() {
        try {
            var request = (HttpWebRequest)WebRequest.Create(Url + "api/session/ping");
            request.Proxy = null;
            request.Timeout = 2000;
            request.Method = "POST";
            request.ContentType = "application/json";
            byte[] body = System.Text.Encoding.UTF8.GetBytes("{}");
            request.ContentLength = body.Length;
            using (var stream = request.GetRequestStream()) stream.Write(body, 0, body.Length);
            using (var response = request.GetResponse()) { }
        } catch { }
    }

    static void ReviveIfClosing() {
        if (!Closing()) return;
        Ping();
        if (Closing()) WaitUntilStopped();   // 已经在收尾：等它退干净，随后由调用方起新的
    }

    static void WaitUntilStopped() {
        for (int i = 0; i < 60; i++) {
            int version;
            if (!Running(out version)) return;
            Thread.Sleep(250);
        }
    }

    static int RunningApiVersion() {
        int version;
        Running(out version);
        return version;
    }

    static int ListenerPid() {
        try {
            var info = new ProcessStartInfo("netstat", "-ano -p tcp") {
                UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true };
            using (var process = Process.Start(info)) {
                string output = process.StandardOutput.ReadToEnd();
                process.WaitForExit(10000);
                foreach (string line in output.Split('\n')) {
                    string[] parts = line.Split(new [] {' '}, StringSplitOptions.RemoveEmptyEntries);
                    if (parts.Length < 4 || parts[0].ToUpper() != "TCP") continue;
                    if (!parts[1].EndsWith(":8765") || parts[3].ToUpper() != "LISTENING") continue;
                    int pid;
                    if (int.TryParse(parts[parts.Length - 1], out pid)) return pid;
                }
            }
        } catch { }
        return 0;
    }

    static bool IsOurPython(int pid) {
        try {
            using (var process = Process.GetProcessById(pid)) {
                string name = process.ProcessName.ToLower();
                if (!name.StartsWith("python")) return false;
                try {
                    string path = process.MainModule.FileName;
                    string root = AppDomain.CurrentDomain.BaseDirectory;
                    return path.StartsWith(root, StringComparison.OrdinalIgnoreCase);
                } catch { return true; }   // 拿不到路径时，端口上确认是本应用即可
            }
        } catch { return false; }
    }

    static void StopStaleServer() {
        int pid = ListenerPid();
        if (pid == 0 || !IsOurPython(pid)) return;
        try {
            using (var process = Process.GetProcessById(pid)) {
                process.Kill();
                process.WaitForExit(5000);
            }
        } catch { }
        for (int i = 0; i < 20; i++) {
            int version;
            if (!Running(out version)) break;
            Thread.Sleep(250);
        }
    }

    // 打开阅读窗口用的浏览器参数。
    //
    // 这里**故意**保持最小：`--app=<url>`（应用窗口，无地址栏）。
    //
    // 历史（三条路都被否掉，别再走）：① 用 --disable-features=msWebOOUI,msPdfOOUI 关掉浏览器
    // 自带的「选择文本时显示迷你菜单」——那需要配独立浏览器配置目录（Edge/Chromium 按
    // user-data-dir 单例运行，否则已有进程会忽略新参数），而全新配置会让浏览器弹出首次运行的
    // 登录/同步提示；② 写用户的浏览器策略——用户认为"本应用不该去改浏览器设置"，已删除。
    // 现行做法：本应用不碰浏览器配置，只把自己跟随选区的操作条向右让开一段（见
    // static/experience.js 的 SELECTION_TOOLBAR_OFFSET）；浏览器那个菜单由用户自己关。
    static string BrowserArguments() {
        return "--app=" + Url;
    }

    // 上一版为了让浏览器的特性开关生效，曾给浏览器单开过一个配置目录
    // （data\browser-profile）。那个做法会让浏览器弹出首次运行的登录/同步提示，已经取消；
    // 这里顺手清理遗留目录：只删本应用自己创建的那一个，被占用（旧窗口还开着）时忽略，
    // 下次启动再试；任何失败都不影响启动。
    static void CleanupLegacyBrowserProfile(string root) {
        try {
            string legacy = Path.Combine(root, "data", "browser-profile");
            if (Directory.Exists(legacy)) Directory.Delete(legacy, true);
        } catch { }
    }

    static Process StartServer(string root) {
        string python = Path.Combine(root, @".venv\Scripts\pythonw.exe");
        if (!File.Exists(python)) throw new Exception("运行环境尚未安装。请先按 README 完成首次安装，再双击本程序。");
        var process = Process.Start(new ProcessStartInfo(python, "-m app.desktop_server") {
            WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden
        });
        for (int i = 0; i < 60; i++) {
            int version;
            if (Running(out version) && version >= ExpectedApiVersion) return process;
            if (process.HasExited) break;
            Thread.Sleep(500);
        }
        int current;
        if (Running(out current) && current >= ExpectedApiVersion) return process;
        throw new Exception("启动失败，请检查 8765 端口是否被占用，或查看 data/application.log。");
    }

    [STAThread]
    static void Main() {
        try {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            using (var mutex = new Mutex(false, "Local\\KanReadLauncher")) {
                if (!mutex.WaitOne(0)) return;
                try {
                    int version;
                    bool ours = Running(out version);
                    // 旧版本的后台不会被"重新打开程序"替换掉（下面这个分支就是为此存在的）：
                    // 没有 api_version 字段的旧后台版本记 0，同样要走这里。
                    if (ours && version < ExpectedApiVersion) {
                        StopStaleServer();
                        ours = Running(out version);
                    }
                    // 带着停止期限的后台（刚关掉窗口）：先续命，续不上再等它退干净。
                    if (ours) {
                        ReviveIfClosing();
                        ours = Running(out version);
                    }
                    if (!ours || version < ExpectedApiVersion) StartServer(root);
                    CleanupLegacyBrowserProfile(root);
                    string browser = null;
                    foreach (string folder in new [] {Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData)}) {
                        string candidate = Path.Combine(folder, @"Microsoft\Edge\Application\msedge.exe");
                        if (File.Exists(candidate)) { browser = candidate; break; }
                    }
                    if (browser != null) Process.Start(new ProcessStartInfo(browser, BrowserArguments()) {UseShellExecute = true});
                    else Process.Start(new ProcessStartInfo(Url) {UseShellExecute = true});
                } finally { mutex.ReleaseMutex(); }
            }
        } catch (Exception error) { MessageBox.Show(error.Message, "勘读 · KanRead", MessageBoxButtons.OK, MessageBoxIcon.Error); }
    }
}

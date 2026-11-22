using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using System.Text.RegularExpressions;

namespace HopOn
{
    /// <summary>
    /// HopOn 桌面壳：静默托管 engine/ 便携 Python 引擎，WebView2 加载本地控制台。
    ///
    /// 生命周期契约：
    ///   - 双击/X 按钮       → 窗口藏进托盘，引擎与守护进程照常运行（壳进程活着 = Job 活着）
    ///   - 托盘「彻底退出」  → 收尸引擎（Job close 连带 daemon），不留任何幽灵进程
    ///   - 壳被强杀/崩溃     → Windows Job Object 内核级收尸，引擎同样活不过壳
    ///   - 引擎已在跑        → 直接复用，绝不重复拉起（Job 只收本壳拉起的进程）
    /// </summary>
    public class MainForm : Form
    {
        private static readonly string Root = AppDomain.CurrentDomain.BaseDirectory;
        private static readonly string EnginePython = Path.Combine(Root, "engine", "python", "pythonw.exe");
        private static readonly string EngineApp = Path.Combine(Root, "engine", "app.py");
        private static readonly string ShellLog = Path.Combine(Root, "logs", "shell.log");

        //: 虚拟主机名：页面与静态资源由 WebView2 直接从 engine\ui 读盘，
        //: 不经过 Python 的本地 HTTP 服务。/api/* 由本进程代转到引擎端口。
        private const string UiHost = "hopon.local";

        private readonly bool _startMinimized;
        private readonly int? _forcedPort;   // --port N：优先在该端口复用/拉起引擎（默认 8765→8899 探测）
        private Process _engine;          // 本壳拉起的引擎；复用现有实例时为 null（退出时不收尸）
        private IntPtr _job = IntPtr.Zero; // 引擎（连带它派生的 daemon）都挂在这个 Job 上
        private string _url = "http://127.0.0.1:8765/";
        private int _port = 8765;          // 本轮实际使用的控制台端口（托盘「立即巡检」用）
        private NotifyIcon _tray;
        private WebView2 _web;
        private bool _reallyExit;
        private bool _trayHintShown;
        private int _apiHits;             // 已被拦截代转的 /api/* 次数（只用于首条日志）
        private Icon _iconNormal;          // 基座图标（品牌原样，不碰观感）
        private Icon _iconPending;         // 基座 + 右下琥珀角标（微软 overlay 位）
        private bool _trayPending;         // 角标当前状态（翻转才换图，防闪烁）
        private System.Windows.Forms.Timer _trayTimer; // 托盘摘要 30s 轮询
        /// <summary>explorer 重建任务栏的广播消息（值每次会话变化，禁止硬编码）。</summary>
        private readonly uint _taskbarCreatedMsg = RegisterWindowMessage("TaskbarCreated");

        public MainForm(bool startMinimized, int? forcedPort)
        {
            _startMinimized = startMinimized;
            _forcedPort = forcedPort;

            Text = "HopOn · 日常巡查";
            // 尺寸口径必须与脚本版 app.py 一致：980×800（逻辑像素），最小 820×620。
            // 980 落在页面 1000px 断点下方，走单列紧凑布局（右侧「凭证健康／设备伪装」
            // 栏折到页尾，往下滚即可见）。两个入口尺寸不一致时，同一份 UI 会呈现成
            // 两套信息架构。
            // PerMonitorV2 下 WinForms 的 Size 单位是物理像素，必须手动乘 DPI 系数
            // （与 pywebview 的 Size(w*scale, h*scale) 同口径）。不乘的话 125% 缩放时
            // 窗口只有 784 逻辑宽，比脚本版整整窄 20%。
            AutoScaleMode = AutoScaleMode.None;
            float dpiScale;
            using (Graphics screenG = Graphics.FromHwnd(IntPtr.Zero))
                dpiScale = screenG.DpiX > 0 ? screenG.DpiX / 96f : 1f;
            const int baseW = 980, baseH = 800, baseMinW = 820, baseMinH = 620;
            int wantW = (int)Math.Round(baseW * dpiScale);
            int wantH = (int)Math.Round(baseH * dpiScale);
            int minW = (int)Math.Round(baseMinW * dpiScale);
            int minH = (int)Math.Round(baseMinH * dpiScale);
            Rectangle wa = Screen.PrimaryScreen != null
                ? Screen.PrimaryScreen.WorkingArea
                : new Rectangle(0, 0, wantW, wantH);
            Size = new Size(Math.Max(minW, Math.Min(wantW, wa.Width - 40)),
                            Math.Max(minH, Math.Min(wantH, wa.Height - 40)));
            MinimumSize = new Size(minW, minH);
            StartPosition = FormStartPosition.CenterScreen;
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { /* 图标缺省兜底 */ }

            bool dark = IsWindowsDark();
            BackColor = dark ? Color.FromArgb(26, 26, 26) : Color.FromArgb(244, 246, 249);

            _web = new WebView2
            {
                Dock = DockStyle.Fill,
                DefaultBackgroundColor = BackColor,   // 防首帧白闪
            };
            Controls.Add(_web);
            _web.CoreWebView2InitializationCompleted += OnWebInitDone;

            _tray = new NotifyIcon
            {
                Icon = Icon,
                Text = "HopOn · 日常巡查",
                Visible = true,
            };
            var menu = new ContextMenuStrip();
            menu.Items.Add("打开控制台", null, (s, e) => ShowMain());
            menu.Items.Add("立即巡检", null, (s, e) => RunManualCheck());
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("彻底退出", null, (s, e) => ReallyExit());
            _tray.ContextMenuStrip = menu;
            // 托盘数据面（2026-11-11 第一队列 #2）：语义在引擎侧 /api/tray_summary
            // 算好，壳只渲染。待办用右下琥珀角标——微软规范：状态用 overlay 角标、
            // 基座不碰红黄绿；角标只在状态翻转时换图，绝不闪烁。
            _iconNormal = _tray.Icon;
            try { _iconPending = MakePendingIcon(_iconNormal); }
            catch { _iconPending = _iconNormal; }
            _trayTimer = new System.Windows.Forms.Timer { Interval = 30000 };
            _trayTimer.Tick += (s, e) => RefreshTraySummary();
            _trayTimer.Start();
            _tray.DoubleClick += (s, e) => ShowMain();

            Load += async (s, e) => await BootAsync();
            FormClosing += OnFormClosing;
        }

        // ------------------------------------------------------------ 启动

        private async Task BootAsync()
        {
            try
            {
                if (_startMinimized) Hide(); else Show();
                Log("壳启动 pid=" + Process.GetCurrentProcess().Id + (_startMinimized ? "（--tray 自启）" : ""));

                // 上次更新覆盖中断 → 本壳退出、更新器续 apply、拉起新壳
                if (TryResumeStagedUpdate()) return;

                int port = PickPortWithOverride();
                if (port < 0)
                {
                    MessageBox.Show("8765 / 8899 / 8900-8909 全部不可用，无法启动。");
                    _reallyExit = true;
                    Close();
                    return;
                }
                _url = "http://127.0.0.1:" + port + "/";
                _port = port;

                if (IsHopOnAlive(port))
                {
                    _engine = null;
                    Log("端口 " + port + " 检测到 HopOn 实例 → 直接复用，不重复拉起");
                }
                else
                {
                    Log("端口 " + port + " 无 HopOn 实例 → 静默拉起引擎（pythonw，无黑框）");
                    StartEngine(port);
                    bool ready = await Task.Run(() => WaitEngineReady(port, 20000));
                    if (!ready)
                    {
                        MessageBox.Show("引擎 20 秒内未就绪，详见 logs\\engine.log。");
                        _reallyExit = true;
                        Close();
                        return;
                    }
                    Log("引擎就绪 pid=" + (_engine == null ? "?" : _engine.Id.ToString()) + "，端口 " + port);
                }

                // 冷启动兜底：daemon 唯一入口是页面按钮（发行版无 start.bat）；fire-and-forget 防 6 秒白屏。
                _ = Task.Run(() => EnsureDaemon(port));
                RefreshTraySummary();   // 托盘摘要首拉（引擎未就绪时静默失败，30s 后定时器补拉）

                Directory.CreateDirectory(Path.Combine(Root, "logs", "webview2"));
                var env = await CoreWebView2Environment.CreateAsync(
                    userDataFolder: Path.Combine(Root, "logs", "webview2"));
                await _web.EnsureCoreWebView2Async(env);

                // UI 走虚拟主机映射：index.html / app.js / style.css 由 WebView2 直接读 engine\ui，
                // 页面不再依赖 Python 的本地 HTTP 服务——引擎还没起完也能先把界面画出来，
                // 不占端口、不受系统代理影响，顺手把 /favicon.ico 的 404 也免了。
                // 前端 fetch('/api/...') 与图标 /static/... 由本进程拦截代转，JS 一行不改。
                if (TryMapUiFolder())
                {
                    _web.CoreWebView2.Navigate("https://" + UiHost + "/index.html");
                    Log("壳加载 https://" + UiHost + "/index.html（engine\\ui 页面接管，不走本地 HTTP）");
                }
                else
                {
                    _web.CoreWebView2.Navigate(_url);
                    Log("壳加载 " + _url + "（页面接管不可用，回退本地 HTTP）");
                }
            }
            catch (Exception ex)
            {
                Log("启动异常：" + ex);
                MessageBox.Show("启动失败：" + ex.Message);
            }
        }

        /// <summary>
        /// 挂上 https://hopon.local/* 的拦截器：页面、样式、脚本、图标、接口全部由本进程应答。
        /// 不使用 SetVirtualHostNameToFolderMapping —— 实测映射到文件夹的主机不会触发
        /// WebResourceRequested，/api/* 就拦不住（页面会拿到 404 HTML，表现为 Failed to fetch）。
        /// 这里按官方静态服务样例的路线：不注册虚拟主机，整站自己兜。
        /// 失败（目录缺失、SDK 不支持）时返回 false，调用方回退到本地 HTTP。
        /// </summary>
        private bool TryMapUiFolder()
        {
            string uiDir = Path.Combine(Root, "engine", "ui");
            if (!Directory.Exists(uiDir))
            {
                Log("engine\\ui 不存在，跳过页面接管");
                return false;
            }
            try
            {
                _web.CoreWebView2.AddWebResourceRequestedFilter(
                    "https://" + UiHost + "/*", CoreWebView2WebResourceContext.All);
                _web.CoreWebView2.WebResourceRequested += OnWebResourceRequested;
                _web.CoreWebView2.NavigationCompleted += OnPageLoaded;
                _web.CoreWebView2.WebMessageReceived += OnWebMessage;
                return true;
            }
            catch (Exception ex)
            {
                Log("页面接管失败：" + ex.Message);
                return false;
            }
        }

        /// <summary>页面加载完成后做一次 DOM 自检，结果写进 logs\shell.log，供构建门禁断言。</summary>
        private async void OnPageLoaded(object sender, CoreWebView2NavigationCompletedEventArgs e)
        {
            Log("页面加载 " + (e.IsSuccess ? "成功" : "失败：" + e.WebErrorStatus));
            if (!e.IsSuccess) return;
            try
            {
                string n = await _web.CoreWebView2.ExecuteScriptAsync("document.querySelectorAll('.tabpanel').length");
                Log("DOM 自检 tabpanel=" + n);
                // 探针：在页面里真发一次 fetch('/api/env')，验证 /api/* 确实被壳拦截代转，
                // 而不是打到了虚拟主机的静态目录（那样会拿到 404 HTML）。
                // 注意 ExecuteScriptAsync 不等待 Promise（只会返回 {}），所以结果用 web message 回传。
                await _web.CoreWebView2.ExecuteScriptAsync(
                    "fetch('/api/env').then(r => r.text().then(t =>" +
                    " window.chrome.webview.postMessage('rlprobe:' + r.status + '/LEN' + t.length)))" +
                    ".catch(e => window.chrome.webview.postMessage('rlprobe:ERR:' + e));");
            }
            catch (Exception ex)
            {
                Log("DOM 自检异常：" + ex.Message);
            }
        }

        private void OnWebMessage(object sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            string msg;
            try { msg = e.TryGetWebMessageAsString(); }
            catch { return; }
            if (msg == null) return;
            if (msg.StartsWith("rlprobe:", StringComparison.Ordinal))
                Log("API 探针 " + msg.Substring("rlprobe:".Length));
        }

        private void OnWebResourceRequested(object sender, CoreWebView2WebResourceRequestedEventArgs e)
        {
            string uri = e.Request.Uri;
            int at = uri.IndexOf("/api/", StringComparison.OrdinalIgnoreCase);
            if (at >= 0)
            {
                if (Interlocked.Increment(ref _apiHits) == 1)
                    Log("首个 API 拦截：" + uri.Substring(at));
                ForwardToEngine(uri.Substring(at), e.Request.Method, e);
                return;
            }
            at = uri.IndexOf("/static/", StringComparison.OrdinalIgnoreCase);
            if (at >= 0)
            {
                ServeLocalStatic(uri.Substring(at), e);
                return;
            }
            ServeUiAsset(uri, e);
        }

        /// <summary>页面本体：index.html / app.js / style.css 等，全部从 engine\ui 读盘。</summary>
        private void ServeUiAsset(string uri, CoreWebView2WebResourceRequestedEventArgs e)
        {
            string path;
            try { path = new Uri(uri).AbsolutePath; }
            catch { path = "/index.html"; }
            if (string.IsNullOrEmpty(path) || path == "/") path = "/index.html";

            // 浏览器总会自发请求 /favicon.ico；这里给 204，免得控制台里挂一条 404 噪音
            if (string.Equals(path, "/favicon.ico", StringComparison.OrdinalIgnoreCase))
            {
                e.Response = _web.CoreWebView2.Environment.CreateWebResourceResponse(
                    new MemoryStream(new byte[0]), 204, "No Content", "Cache-Control: no-store\r\n");
                return;
            }

            string baseDir = Path.GetFullPath(Path.Combine(Root, "engine", "ui"));
            string full = Path.GetFullPath(Path.Combine(baseDir, path.TrimStart('/').Replace('/', Path.DirectorySeparatorChar)));
            if (!full.StartsWith(baseDir, StringComparison.OrdinalIgnoreCase) || !File.Exists(full))
            {
                e.Response = _web.CoreWebView2.Environment.CreateWebResourceResponse(
                    new MemoryStream(Encoding.UTF8.GetBytes("not found")), 404, "Not Found",
                    "Content-Type: text/plain; charset=utf-8\r\n");
                return;
            }
            try
            {
                byte[] body;
                string ctype = GuessContentType(Path.GetExtension(full));

                // index.html 是模板：__STYLE__ / __APP_JS__ 两个占位符要内联成 style.css 与 app.js，
                // 与 login_ui.py 的 _assemble_page() 保持同一口径（那边是 HTTP 路径在用）。
                // 不做这一步，页面会静默丢掉全部 JS（表现为所有 tab 都停在「读取中…」）。
                if (string.Equals(Path.GetFileName(full), "index.html", StringComparison.OrdinalIgnoreCase))
                {
                    string html = File.ReadAllText(full, Encoding.UTF8);
                    string cssPath = Path.Combine(baseDir, "style.css");
                    string jsPath = Path.Combine(baseDir, "app.js");
                    if (!File.Exists(cssPath) || !File.Exists(jsPath))
                        throw new FileNotFoundException("ui\\style.css 或 ui\\app.js 缺失");
                    html = html.Replace("__STYLE__", File.ReadAllText(cssPath, Encoding.UTF8))
                               .Replace("__APP_JS__", File.ReadAllText(jsPath, Encoding.UTF8));
                    body = Encoding.UTF8.GetBytes(html);
                    ctype = "text/html; charset=utf-8";
                }
                else
                {
                    body = File.ReadAllBytes(full);
                }

                e.Response = _web.CoreWebView2.Environment.CreateWebResourceResponse(
                    new MemoryStream(body), 200, "OK",
                    "Content-Type: " + ctype + "\r\nCache-Control: no-store\r\n");
            }
            catch (Exception ex)
            {
                Log("页面资源读取失败 " + path + "：" + ex.Message);
                e.Response = _web.CoreWebView2.Environment.CreateWebResourceResponse(
                    new MemoryStream(Encoding.UTF8.GetBytes("error")), 500, "Internal Error",
                    "Content-Type: text/plain; charset=utf-8\r\n");
            }
        }

        /// <summary>/static/… 直接读 engine\static（图标等），不经 Python。</summary>
        private void ServeLocalStatic(string pathAndQuery, CoreWebView2WebResourceRequestedEventArgs e)
        {
            string rel = pathAndQuery.Substring("/static/".Length);
            int q = rel.IndexOf('?');
            if (q >= 0) rel = rel.Substring(0, q);
            string baseDir = Path.GetFullPath(Path.Combine(Root, "engine", "static"));
            string full = Path.GetFullPath(Path.Combine(baseDir, rel.Replace('/', Path.DirectorySeparatorChar)));
            if (!full.StartsWith(baseDir, StringComparison.OrdinalIgnoreCase) || !File.Exists(full))
            {
                e.Response = _web.CoreWebView2.Environment.CreateWebResourceResponse(
                    new MemoryStream(Encoding.UTF8.GetBytes("not found")), 404, "Not Found",
                    "Content-Type: text/plain; charset=utf-8\r\n");
                return;
            }
            try
            {
                byte[] body = File.ReadAllBytes(full);
                e.Response = _web.CoreWebView2.Environment.CreateWebResourceResponse(
                    new MemoryStream(body), 200, "OK",
                    "Content-Type: " + GuessContentType(Path.GetExtension(full)) + "\r\nCache-Control: no-store\r\n");
            }
            catch (Exception ex)
            {
                Log("静态资源读取失败 " + rel + "：" + ex.Message);
                e.Response = _web.CoreWebView2.Environment.CreateWebResourceResponse(
                    new MemoryStream(Encoding.UTF8.GetBytes("error")), 500, "Internal Error",
                    "Content-Type: text/plain; charset=utf-8\r\n");
            }
        }

        private static string GuessContentType(string ext)
        {
            switch ((ext ?? "").ToLowerInvariant())
            {
                case ".png": return "image/png";
                case ".jpg":
                case ".jpeg": return "image/jpeg";
                case ".gif": return "image/gif";
                case ".webp": return "image/webp";
                case ".svg": return "image/svg+xml";
                case ".ico": return "image/x-icon";
                case ".html":
                case ".htm": return "text/html; charset=utf-8";
                case ".js":
                case ".mjs": return "text/javascript; charset=utf-8";
                case ".css": return "text/css; charset=utf-8";
                case ".json": return "application/json; charset=utf-8";
                case ".woff2": return "font/woff2";
                case ".txt": return "text/plain; charset=utf-8";
                default: return "application/octet-stream";
            }
        }

        /// <summary>
        /// 把页面发往 https://hopon.local/api/* 的请求代转到引擎端口。
        /// 必须显式 req.Proxy = null：系统代理会拦截 loopback，返回空响应。
        /// </summary>
        private void ForwardToEngine(string pathAndQuery, string method, CoreWebView2WebResourceRequestedEventArgs e)
        {
            CoreWebView2Deferral deferral = e.GetDeferral();

            byte[] reqBody = null;
            string reqType = null;
            try
            {
                if (e.Request.Content != null)
                {
                    using (var ms = new MemoryStream())
                    {
                        e.Request.Content.CopyTo(ms);
                        reqBody = ms.ToArray();
                    }
                    if (reqBody.Length > 0) reqType = e.Request.Headers.GetHeader("Content-Type");
                }
            }
            catch (Exception ex)
            {
                Log("读取请求体失败：" + ex.Message);
            }

            int port = _port;
            Task.Run(() =>
            {
                int code = 502;
                string reason = "Bad Gateway";
                byte[] body = Encoding.UTF8.GetBytes("{\"ok\":false,\"message\":\"引擎不可达\"}");
                string ctype = "application/json; charset=utf-8";
                // 连接类失败重试 2 次（2026-11-22 朋友机「点了没反应」取证：
                // 引擎巡检期响应瞬时变慢，转发单次放弃加重感知）；
                // 引擎已应答（任何 HTTP 码）不重试。
                for (int attempt = 1; attempt <= 3; attempt++)
                {
                    code = 502;
                    reason = "Bad Gateway";
                    body = Encoding.UTF8.GetBytes("{\"ok\":false,\"message\":\"引擎不可达\"}");
                    ctype = "application/json; charset=utf-8";
                    bool answered = false;
                    try
                    {
                        var req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + port + pathAndQuery);
                        req.Method = method;
                        req.Proxy = null;
                        req.Timeout = 30000;
                        req.ReadWriteTimeout = 30000;
                        req.KeepAlive = false;
                        req.Accept = "application/json";
                        if (reqBody != null && reqBody.Length > 0)
                        {
                            req.ContentType = string.IsNullOrEmpty(reqType) ? "application/json" : reqType;
                            req.ContentLength = reqBody.Length;
                            using (var rs = req.GetRequestStream()) rs.Write(reqBody, 0, reqBody.Length);
                        }
                        using (var resp = (HttpWebResponse)req.GetResponse())
                        {
                            code = (int)resp.StatusCode;
                            reason = resp.StatusDescription;
                            if (!string.IsNullOrEmpty(resp.ContentType)) ctype = resp.ContentType;
                            using (var rs = resp.GetResponseStream())
                            using (var ms = new MemoryStream())
                            {
                                if (rs != null) rs.CopyTo(ms);
                                body = ms.ToArray();
                            }
                        }
                        answered = true;
                    }
                    catch (WebException wex)
                    {
                        var hr = wex.Response as HttpWebResponse;
                        if (hr != null)
                        {
                            code = (int)hr.StatusCode;
                            reason = hr.StatusDescription;
                            if (!string.IsNullOrEmpty(hr.ContentType)) ctype = hr.ContentType;
                            using (var rs = hr.GetResponseStream())
                            using (var ms = new MemoryStream())
                            {
                                if (rs != null) rs.CopyTo(ms);
                                body = ms.ToArray();
                            }
                            hr.Dispose();
                            answered = true;   // 引擎已应答（HTTP 错误码）→ 不重试
                        }
                        else
                        {
                            Log("转发 " + pathAndQuery + " 失败（第 " + attempt + "/3 次）：" + wex.Message);
                        }
                    }
                    catch (Exception ex)
                    {
                        Log("转发 " + pathAndQuery + " 异常（第 " + attempt + "/3 次）：" + ex.Message);
                    }
                    if (answered || attempt >= 3) break;
                    Thread.Sleep(400 * attempt);
                }

                try
                {
                    _web.BeginInvoke(new Action(() =>
                    {
                        try
                        {
                            e.Response = _web.CoreWebView2.Environment.CreateWebResourceResponse(
                                new MemoryStream(body), code, reason,
                                "Content-Type: " + ctype + "\r\nCache-Control: no-store\r\n");
                        }
                        catch (Exception ex)
                        {
                            Log("构造响应失败：" + ex.Message);
                        }
                        finally
                        {
                            deferral.Complete();
                        }
                    }));
                }
                catch (Exception ex)
                {
                    Log("回投失败：" + ex.Message);
                    deferral.Complete();
                }
            });
        }

        private int PickPort()
        {
            // ① 已有 HopOn 实例的端口直接复用（8765 优先，8899 次之）
            foreach (int p in new[] { 8765, 8899 })
                if (IsHopOnAlive(p)) return p;
            // ② 全空闲 → 新引擎挑一个（8765 优先，被占就往后走）
            foreach (int p in new[] { 8765, 8899, 8900, 8901, 8902, 8903, 8904, 8905, 8906, 8907, 8908, 8909 })
                if (TcpFree(p)) return p;
            return -1;
        }

        private int PickPortWithOverride()
        {
            if (_forcedPort.HasValue)
            {
                int p = _forcedPort.Value;
                if (IsHopOnAlive(p)) return p;              // 该端口已有 HopOn → 复用
                for (int q = p; q < p + 12; q++)                // 空闲或被非 HopOn 占用 → 从它开始向后找
                    if (TcpFree(q)) return q;
                return -1;
            }
            return PickPort();
        }

        private void StartEngine(int port)
        {
            var psi = new ProcessStartInfo
            {
                FileName = EnginePython,
                Arguments = "\"" + EngineApp + "\" --port " + port,
                WorkingDirectory = Path.GetDirectoryName(EngineApp),
                UseShellExecute = false,
                CreateNoWindow = true,          // pythonw 本就无窗，双保险防任何黑框
            };
            _engine = Process.Start(psi);
            AttachJobObject(_engine);           // 内核级收尸：壳死 = 引擎死
            // 引擎守护（2026-11-22 朋友机实证：引擎中途死亡后壳不管，页面持续
            // 「无法连接本地」直到人工重启——业界 supervisor 模式：引擎意外
            // 退出自动拉起，连续失败退避停止防崩溃循环）
            _engine.EnableRaisingEvents = true;
            _engine.Exited += Engine_Exited;
        }

        private int _engineRestartFails = 0;

        /// <summary>.update\pending.json 是否在场（引擎 install 时写的
        /// 「有更新待应用」事实源）。</summary>
        private bool UpdatePending()
        {
            try { return File.Exists(Path.Combine(Root, ".update", "pending.json")); }
            catch { return false; }
        }

        /// <summary>HopOnUpdater.exe 是否正在运行（按可执行路径精确匹配，
        /// 不按进程名——防别的目录同名 exe 误判；访问失败跳过该进程）。
        /// pending 只说明「有更新待应用」，Updater 在跑才说明「正被接管」：
        /// 2026-11-22 自审修正——staged 后用户未点安装时引擎意外死亡，
        /// 若只看 pending 壳会放弃拉起而 Updater 永远不来 → 全家躺尸。</summary>
        private static bool UpdaterRunning()
        {
            try
            {
                string self = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "HopOnUpdater.exe");
                foreach (Process p in Process.GetProcessesByName("HopOnUpdater"))
                {
                    try
                    {
                        string path = null;
                        try { path = p.MainModule.FileName; } catch { }
                        if (!string.IsNullOrEmpty(path) &&
                            string.Equals(path, self, StringComparison.OrdinalIgnoreCase))
                            return true;
                    }
                    finally { try { p.Dispose(); } catch { } }
                }
            }
            catch { }
            return false;
        }

        /// <summary>staging 是否已含可执行新版（与 HopOnUpdater 同判据）：
        /// staging\HopOn.exe 或 staging\<唯一子目录>\HopOn.exe。</summary>
        private static bool StagingHasExe(string staging)
        {
            try
            {
                if (File.Exists(Path.Combine(staging, "HopOn.exe"))) return true;
                string[] entries = Directory.GetFileSystemEntries(staging);
                return entries.Length == 1 && Directory.Exists(entries[0])
                    && File.Exists(Path.Combine(entries[0], "HopOn.exe"));
            }
            catch { return false; }
        }

        /// <summary>壳启动续 apply（2026-11-22 staged 改造；Firefox「重启时
        /// 继续应用已暂存更新」同款）：pending 在场且 staging 完整 = 上次
        /// 覆盖中断。清 pending → spawn HopOnUpdater（--pid=本壳，等本壳
        /// 退出后纯覆盖）→ 本壳退出。Updater 成功后拉起新壳。staging/
        /// 更新器不就绪则清 pending 正常启动——续跑绝不堵启动。</summary>
        private bool TryResumeStagedUpdate()
        {
            try
            {
                string pending = Path.Combine(Root, ".update", "pending.json");
                if (!File.Exists(pending)) return false;
                if (UpdaterRunning())
                    return false;   // 另一实例的更新器正在接管，本壳不抢
                string staging = Path.Combine(Root, ".update", "staging");
                string updater = Path.Combine(Root, "HopOnUpdater.exe");
                if (!StagingHasExe(staging) || !File.Exists(updater))
                {
                    Log("发现 pending.json 但 staging/更新器不就绪 → 清 pending 正常启动");
                    try { File.Delete(pending); } catch { }
                    return false;
                }
                Log("检测到未完成的暂存更新 → 本壳退出，由更新器继续应用");
                try { File.Delete(pending); } catch { }   // 先清：防本壳异常退出后下次重复续跑
                Process.Start(new ProcessStartInfo(updater)
                {
                    Arguments = "--pid " + Process.GetCurrentProcess().Id +
                                " --root \"" + Root.TrimEnd(Path.DirectorySeparatorChar) + "\"" +
                                " --staged --restart",
                    UseShellExecute = false,
                    CreateNoWindow = true,
                });
                _reallyExit = true;
                Close();
                return true;
            }
            catch (Exception ex)
            {
                Log("续 apply 检查异常（忽略，正常启动）：" + ex.Message);
                return false;
            }
        }

        /// <summary>引擎意外退出（崩溃/被外部杀）→ 自动重启拉起。
        /// 主动退出（ReallyExit 的 Kill）走 _reallyExit 不触发重启；
        /// 连续 3 次重启失败停止自动拉起，防「起不来→退出→再起」死循环；
        /// 端口已有 HopOn 实例在服务时不重复拉起。</summary>
        private void Engine_Exited(object sender, EventArgs e)
        {
            if (_reallyExit) return;
            if (UpdatePending() && UpdaterRunning())
            {
                // 2026-11-22 staged 改造：pending+Updater 活着 = 更新链正在
                // 接管（引擎 install 自杀 → Updater 覆盖 → 拉起新版）。此时
                // 拉旧引擎会与覆盖竞态，随后被 Updater 清场强杀——脏日志+白拉起。
                // 2026-11-23 跨链推演 D8：**本壳必须退出**——rename 化后覆盖
                // 运行中的 HopOn.exe 让位成功，不再触发 KillProcessesUnder，
                // 旧壳若不退就会与 StartShell 拉起的新壳并存（双窗双托盘，
                // 黑幕页面永不消失）。业界标准（Firefox/VS Code/微信）：apply
                // 发生在应用完全退出之后——壳退出，Updater 独占 root，装完
                // StartShell 拉新版窗口回来。Updter 失败时回滚已还原旧版，
                // 用户手动启动即可（与旧链装失败一致）。
                Log("引擎退出且更新待应用 + 更新器在跑 → 本壳退出让位（apply 需独占 root）");
                _reallyExit = true;
                try
                {
                    // Process.Exited 在线程池线程触发——Close 是 UI 操作，必须
                    // 封送回 UI 线程（逐行审 D9：直接调用跨线程崩溃）
                    BeginInvoke((Action)(() => Close()));
                }
                catch (ObjectDisposedException) { }   // 壳正在关（D13：句柄失效不炸事件线程）
                catch (InvalidOperationException) { } // 句柄未创建等极端时序
                return;
            }
            // pending 在场但更新器不在跑（staged 从未触发安装，或上次接管
            // 已崩）→ 正常拉起引擎：staged 态落盘仍在，用户可继续点安装。
            var dead = sender as Process;
            string exitCode = "";
            try { exitCode = "，退出码=" + dead.ExitCode; } catch { }
            Log("引擎意外退出（pid=" + (dead != null ? dead.Id.ToString() : "?") + exitCode + "）→ 自动重启");
            // 退出码语义（死因分类）：0=正常；0x40010004 等=被 TerminateProcess
            // 族外部终结（安全软件/人工/脚本）；0xC0000005 等=真崩溃。
            try
            {
                if (IsHopOnAlive(_port)) return;   // 端口已有 HopOn 实例在服务 → 无需拉起
                StartEngine(_port);
                bool ready = Task.Run(() => WaitEngineReady(_port, 20000))
                                  .GetAwaiter().GetResult();
                if (!ready)
                {
                    _engineRestartFails++;
                    Log("引擎自动重启后 20 秒未就绪（第 " + _engineRestartFails + " 次，详见 engine.log）");
                    if (_engineRestartFails >= 3)
                        Log("连续 3 次重启失败，停止自动拉起（请查看 logs\\engine.log）");
                    return;
                }
                _engineRestartFails = 0;
                Log("引擎已自动重启并就绪，页面连接自动恢复");
            }
            catch (Exception ex)
            {
                _engineRestartFails++;
                Log("引擎自动重启异常（第 " + _engineRestartFails + " 次）：" + ex.Message);
            }
        }

        private static bool WaitEngineReady(int port, int timeoutMs)
        {
            DateTime deadline = DateTime.UtcNow.AddMilliseconds(timeoutMs);
            while (DateTime.UtcNow < deadline)
            {
                if (IsHopOnAlive(port)) return true;
                Thread.Sleep(250);
            }
            return false;
        }

        // ------------------------------------------------------------ WebView2 沉浸化

        private void OnWebInitDone(object sender, CoreWebView2InitializationCompletedEventArgs e)
        {
            if (!e.IsSuccess) { Log("WebView2 初始化失败：" + e.InitializationException?.Message); return; }
            var s = _web.CoreWebView2.Settings;
            s.AreDefaultContextMenusEnabled = false;      // 屏蔽网页右键菜单
            s.AreDevToolsEnabled = false;
            s.IsStatusBarEnabled = false;
            s.AreBrowserAcceleratorKeysEnabled = false;   // 屏蔽 Ctrl+F / F12 等浏览器手感
            s.IsZoomControlEnabled = false;
            s.IsBuiltInErrorPageEnabled = true;
            // 隐藏滚动条（页面深浅色由用户自己选，见 UI 侧栏底部的三态按钮）
            _web.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(
                "(function(){var apply=function(){var st=document.createElement('style');" +
                "st.textContent='::-webkit-scrollbar{width:0;height:0}::-webkit-scrollbar-thumb{background:transparent}';" +
                // 注意：这里原本还有一句 removeItem('rl-theme')，用来保证页面主题
                // 与壳同口径。现已改为允许用户在侧栏底部手动切换深浅配色（auto/dark/light，
                // 偏好存在 localStorage 里），所以不再清理，否则每次启动都会抹掉用户的选择。
                "document.documentElement.appendChild(st);};" +
                // 文档创建时 documentElement 可能还不存在，直接 appendChild 会抛 null 引用
                "if(document.documentElement){apply()}else{document.addEventListener('DOMContentLoaded',apply)}" +
                "})();");
            _web.CoreWebView2.NewWindowRequested += (s2, e2) =>
            {
                // 外部链接（检查更新弹窗的 GitHub/Gitee 渠道按钮、window.open 等）
                // 交给系统默认浏览器打开——2026-11-15 根修：原 Handled=true 把新窗
                // 请求静默吞掉，v2.6.1 用户点「检查更新」toast 说「打开下载页」却
                // 什么都不发生；v2.6.2 弹窗的渠道按钮（target=_blank）同样中招。
                e2.Handled = true;
                try
                {
                    if (!string.IsNullOrEmpty(e2.Uri))
                    {
                        System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(e2.Uri) { UseShellExecute = true });
                        Log("外部链接已交系统浏览器：" + e2.Uri);
                    }
                }
                catch (Exception ex) { Log("外部链接打开失败：" + ex.Message); }
            };
            Log("WebView2 就绪：右键菜单/DevTools/加速键已关，滚动条已隐藏");
        }

        private static bool IsWindowsDark()
        {
            try
            {
                using (var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(
                    @"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"))
                {
                    object v = key == null ? null : key.GetValue("AppsUseLightTheme");
                    if (v is int) return ((int)v) == 0;
                }
            }
            catch { }
            return false;
        }

        // ------------------------------------------------------------ 探测

        private static bool IsHopOnAlive(int port)
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + port + "/api/env");
                req.Timeout = 800;
                req.ReadWriteTimeout = 800;
                req.Proxy = null;
                using (var resp = (HttpWebResponse)req.GetResponse())
                using (var sr = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                {
                    return sr.ReadToEnd().Contains("channels");   // HopOn 特征字段
                }
            }
            catch { return false; }
        }

        private static string HttpJson(int port, string path, string method, string body, int timeoutMs)
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + port + path);
                req.Method = method;
                req.Proxy = null;
                req.Timeout = timeoutMs;
                req.ReadWriteTimeout = timeoutMs;
                req.ServicePoint.Expect100Continue = false;
                if (method == "POST")
                {
                    byte[] bytes = Encoding.UTF8.GetBytes(body ?? "{}");
                    req.ContentType = "application/json; charset=utf-8";
                    req.ContentLength = bytes.Length;
                    using (var s = req.GetRequestStream()) s.Write(bytes, 0, bytes.Length);
                }
                using (var resp = (HttpWebResponse)req.GetResponse())
                using (var sr = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                    return sr.ReadToEnd();
            }
            catch { return ""; }
        }

        /// <summary>守护进程是否在跑：daemon 对象（"daemon" 后 400 字符窗口）内匹配 running:true。</summary>
        private static bool DaemonRunning(int port)
        {
            string body = HttpJson(port, "/api/schedule", "GET", null, 1500);
            int i = body.IndexOf("\"daemon\"");
            return i >= 0 && Regex.IsMatch(body.Substring(i, Math.Min(400, body.Length - i)),
                                            "\"running\"\\s*:\\s*true");
        }

        /// <summary>关机前落盘：POST /api/daemon/quiet-run，阻塞到 daemon 跑完静默检查或超时。
        /// 引擎/守护不在时端点秒回 ok:false，不拖慢退出。</summary>
        private void QuietRunBeforeExit(int timeoutMs)
        {
            try
            {
                string resp = HttpJson(_port, "/api/daemon/quiet-run", "POST", "{}", timeoutMs);
                Log("关机前落盘：" + (string.IsNullOrEmpty(resp) ? "超时未完成（收尸照常）" : resp));
            }
            catch (Exception ex) { Log("关机前落盘异常（收尸照常）：" + ex.Message); }
        }

        private void EnsureDaemon(int port)
        {
            if (DaemonRunning(port))
            {
                Log("守护进程已在运行，跳过拉起");
                return;
            }
            string resp = HttpJson(port, "/api/daemon/start", "POST", "{}", 10000);
            var ok = Regex.IsMatch(resp, "\"ok\"\\s*:\\s*true");
            // start_daemon() 内部自带最长 6 秒就绪等待：POST 超时返回空串时
            // daemon 可能其实已经起来了 —— 必须把 8 秒观察窗跑满，再统一判定。
            if (!ok && resp == "")
                Log("启动请求无响应（可能仍在启动），继续观察 8 秒");
            DateTime deadline = DateTime.UtcNow.AddSeconds(8);
            while (DateTime.UtcNow < deadline && !DaemonRunning(port))
                Thread.Sleep(500);
            if (DaemonRunning(port))
            {
                var m = Regex.Match(resp, "\"pid\"\\s*:\\s*(\\d+)");
                Log("守护进程已拉起 pid=" + (m.Success ? m.Groups[1].Value : "?"));
                return;
            }
            Log("守护进程拉起失败：" + (resp == "" ? "(无响应)" : resp));
            if (IsHandleCreated && !IsDisposed)
                BeginInvoke((Action)(() => _tray.ShowBalloonTip(3000, "HopOn",
                    "定时巡检未能启动，请到「定时任务」页手动启动", ToolTipIcon.Warning)));
        }

        private static bool TcpFree(int port)
        {
            try
            {
                var l = new TcpListener(IPAddress.Loopback, port);
                l.Start(); l.Stop();
                return true;
            }
            catch { return false; }
        }

        /// <summary>拉托盘摘要并刷新菜单与角标。网络失败静默（保留上次内容）。</summary>
        private void RefreshTraySummary()
        {
            if (!IsHandleCreated || IsDisposed) return;
            Task.Run(() =>
            {
                string resp = HttpJson(_port, "/api/tray_summary", "GET", null, 2500);
                if (string.IsNullOrEmpty(resp)) return;
                string headline = Regex.Match(resp, "\"headline\"\\s*:\\s*\"([^\"]*)\"").Groups[1].Value;
                bool pending = Regex.IsMatch(resp, "\"pending\"\\s*:\\s*true");
                string text = "";
                var m = Regex.Match(resp, "\"text\"\\s*:\\s*\"([^\"]*)\"");
                if (m.Success) text = m.Groups[1].Value.Replace("\\n", "\n");
                string manual = "";
                var mm = Regex.Match(resp, "\"manual\"\\s*:\\s*\"([^\"]*)\"");
                if (mm.Success) manual = mm.Groups[1].Value.Replace("\\n", "\n");
                if (IsHandleCreated && !IsDisposed)
                    BeginInvoke((Action)(() => ApplyTraySummary(headline, pending, text, manual)));
            });
        }

        /// <summary>把摘要渲染进托盘：信息行置顶（只读），手动打卡可勾行，
        /// 操作区按微软规范排序；默认命令加粗；不适用的信息直接不渲染。</summary>
        private void ApplyTraySummary(string headline, bool pending, string text, string manual)
        {
            try
            {
                var menu = _tray.ContextMenuStrip;
                if (menu.Visible) return;   // 用户正看着就别拆，30s 后的 tick 再刷新
                menu.Items.Clear();
                menu.Items.Add(new ToolStripMenuItem(
                    "HopOn · " + (string.IsNullOrEmpty(headline) ? "读取中" : headline))
                { Enabled = false });
                if (!string.IsNullOrEmpty(text))
                    foreach (var line in text.Split('\n'))
                        if (line.Length > 0)
                            menu.Items.Add(new ToolStripMenuItem(line) { Enabled = false });
                // 手动打卡可勾行（2026-11-12）：「☐ 名称」点一下=今日完成
                if (!string.IsNullOrEmpty(manual))
                    foreach (var line in manual.Split('\n'))
                    {
                        var parts = line.Split('|');
                        if (parts.Length < 3 || parts[0].Length == 0) continue;
                        bool done = parts[2] == "1";
                        var mi = new ToolStripMenuItem((done ? "✓ " : "☐ ") + parts[1])
                        { Enabled = !done };
                        if (!done)
                        {
                            var mid = parts[0];
                            mi.Click += (s, e) => ToggleManualDone(mid);
                        }
                        menu.Items.Add(mi);
                    }
                menu.Items.Add(new ToolStripSeparator());
                var open = new ToolStripMenuItem("打开控制台", null, (s, e) => ShowMain());
                open.Font = new Font(open.Font, FontStyle.Bold);   // 默认命令加粗（微软规范）
                menu.Items.Add(open);
                menu.Items.Add("立即巡检", null, (s, e) => RunManualCheck());
                menu.Items.Add("导出诊断包", null, (s, e) => ExportDiagnostics());
                menu.Items.Add(new ToolStripSeparator());
                menu.Items.Add("彻底退出", null, (s, e) => ReallyExit());
                if (pending != _trayPending)
                {
                    _trayPending = pending;
                    _tray.Icon = pending ? _iconPending : _iconNormal;
                }
                try { _tray.Text = "HopOn · " + (string.IsNullOrEmpty(headline) ? "日常巡查" : headline); }
                catch { }
            }
            catch (Exception ex) { Log("托盘菜单刷新失败：" + ex.Message); }
        }

        /// <summary>基座图标右下角加琥珀圆点（overlay 位），不换基座不碰品牌色。</summary>
        private static Icon MakePendingIcon(Icon baseIcon)
        {
            using (var src = baseIcon.ToBitmap())
            {
                int size = Math.Max(src.Width, 32);
                var bmp = new Bitmap(size, size);
                using (var g = Graphics.FromImage(bmp))
                {
                    g.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.HighQualityBicubic;
                    g.DrawImage(src, 0, 0, size, size);
                    int d = Math.Max(8, size / 3);
                    using (var br = new SolidBrush(Color.FromArgb(245, 158, 11)))
                        g.FillEllipse(br, size - d - 1, size - d - 1, d, d);
                }
                return Icon.FromHandle(bmp.GetHicon());
            }
        }

        /// <summary>托盘「导出诊断包」：GET /api/diag_export（脱敏+fail-closed 在引擎侧），
        /// 完成后气泡报落盘路径。导出耗时秒级，先给一条「正在收集」。</summary>
        private void ExportDiagnostics()
        {
            if (!IsHandleCreated || IsDisposed) return;
            _tray.ShowBalloonTip(2000, "HopOn", "正在收集诊断包…", ToolTipIcon.Info);
            Task.Run(() =>
            {
                string resp = HttpJson(_port, "/api/diag_export", "GET", null, 60000);
                bool ok = Regex.IsMatch(resp, "\"ok\"\\s*:\\s*true");
                string msg = "诊断包导出失败，请打开控制台查看";
                if (ok)
                {
                    var m = Regex.Match(resp, "\"path\"\\s*:\\s*\"([^\"]*)\"");
                    if (m.Success)
                        msg = "诊断包已导出：" + m.Groups[1].Value.Replace("\\\\", "\\");
                }
                else
                {
                    var m = Regex.Match(resp, "\"message\"\\s*:\\s*\"([^\"]*)\"");
                    if (m.Success) msg = m.Groups[1].Value;
                }
                if (IsHandleCreated && !IsDisposed)
                    BeginInvoke((Action)(() => _tray.ShowBalloonTip(5000, "HopOn", msg,
                        ok ? ToolTipIcon.Info : ToolTipIcon.Warning)));
            });
        }

        /// <summary>托盘手动打卡勾选：POST toggle_done 后立即重拉摘要。</summary>
        private void ToggleManualDone(string id)
        {
            Task.Run(() =>
            {
                HttpJson(_port, "/api/custom_dailies", "POST",
                         "{\"action\":\"toggle_done\",\"id\":\"" + id + "\"}", 8000);
                RefreshTraySummary();
            });
        }

        /// <summary>托盘「立即巡检」：经 /api/daemon/run-now 让 daemon 跑一轮，不阻塞 UI。</summary>
        private void RunManualCheck()
        {
            if (!IsHandleCreated || IsDisposed) return;   // D0 守卫：壳已不在就不发请求
            Task.Run(() =>
            {
                string resp = HttpJson(_port, "/api/daemon/run-now", "POST", "{}", 5000);
                bool ok = Regex.IsMatch(resp, "\"ok\"\\s*:\\s*true");
                if (IsHandleCreated && !IsDisposed)
                    BeginInvoke((Action)(() => _tray.ShowBalloonTip(3000, "HopOn",
                        ok ? "已开始巡检" : "巡检未能启动，请打开控制台查看",
                        ok ? ToolTipIcon.Info : ToolTipIcon.Warning)));
            });
        }

        // ------------------------------------------------------------ 生命周期

        private void ShowMain()
        {
            Show();
            WindowState = FormWindowState.Normal;
            Activate();
        }

        private void OnFormClosing(object sender, FormClosingEventArgs e)
        {
            Log("FormClosing 触发：reason=" + e.CloseReason + " cancel前=" + e.Cancel);
            // 点 X / 最小化：藏进托盘（Job 活着 = 引擎活着），只有「彻底退出」才真退
            if (_reallyExit || e.CloseReason == CloseReason.ApplicationExitCall
                || e.CloseReason == CloseReason.WindowsShutDown)
            {
                // 系统关机：宽限极短，尽力抢一轮静默落盘（失败不拦关机）
                if (e.CloseReason == CloseReason.WindowsShutDown)
                    QuietRunBeforeExit(4000);
                return;
            }
            e.Cancel = true;
            Hide();
            Log("已取消关闭并隐藏到托盘");
            if (!_trayHintShown)
            {
                _tray.ShowBalloonTip(2000, "HopOn", "已最小化到托盘，日常巡查继续运行。", ToolTipIcon.Info);
                _trayHintShown = true;
            }
        }

        private void ReallyExit()
        {
            _reallyExit = true;
            // 关机前落盘（2026-11-06）：让 daemon 跑一轮静默检查（写历史、
            // 不推送），把「今天到底做没做完」冻进历史——22:00 最后一轮之后
            // 才打完的游戏不再被记成漏做。引擎没在跑时这一步秒回，不拖慢退出。
            QuietRunBeforeExit(25000);
            Log("壳彻底退出：开始收尸引擎");
            try
            {
                if (_engine != null && !_engine.HasExited)
                {
                    _engine.Kill();
                    _engine.WaitForExit(3000);
                    Log("引擎进程已结束 pid=" + _engine.Id);
                }
            }
            catch (Exception ex) { Log("收尸异常：" + ex.Message); }
            finally
            {
                if (_job != IntPtr.Zero) { CloseHandle(_job); _job = IntPtr.Zero; }  // Job close 连带 daemon
            }
            _tray.Visible = false;
            try { _trayTimer.Stop(); } catch { }
            Application.Exit();
        }

        protected override void OnShown(EventArgs e)
        {
            base.OnShown(e);
            if (_startMinimized) Hide();
        }

        /// <summary>explorer 重启广播（TaskbarCreated，值每次会话变化必须
        /// RegisterWindowMessage 取）：托盘图标随任务栏重建全部丢失——
        /// Visible 双掷重挂 + 重放当前状态图标（2026-11-11 #6·③）。</summary>
        protected override void WndProc(ref Message m)
        {
            if (_taskbarCreatedMsg != 0 && m.Msg == (int)_taskbarCreatedMsg)
            {
                try
                {
                    Log("收到 TaskbarCreated（explorer 重启），重挂托盘图标");
                    _tray.Visible = false;
                    _tray.Visible = true;
                    _tray.Icon = _trayPending ? _iconPending : _iconNormal;
                    RefreshTraySummary();   // 菜单数据顺带补一轮
                }
                catch (Exception ex) { Log("重挂托盘失败：" + ex.Message); }
            }
            base.WndProc(ref m);
        }

        // ------------------------------------------------------------ Job Object（内核级收尸）

        private void AttachJobObject(Process child)
        {
            try
            {
                _job = CreateJobObject(IntPtr.Zero, null);
                var info = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
                info.BasicLimitInformation.LimitFlags = 0x2800;   // KILL_ON_JOB_CLOSE | BREAKAWAY_OK（允许 HopOnUpdater 脱离 Job，2026-11-16）
                int len = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
                IntPtr ptr = Marshal.AllocHGlobal(len);
                try
                {
                    Marshal.StructureToPtr(info, ptr, false);
                    SetInformationJobObject(_job, 9, ptr, len);   // JobObjectExtendedLimitInformation
                }
                finally { Marshal.FreeHGlobal(ptr); }
                AssignProcessToJobObject(_job, child.Handle);
                Log("引擎已挂入 Job Object（KILL_ON_JOB_CLOSE）：壳异常退出也会被内核收尸");
            }
            catch (Exception ex) { Log("挂 Job 失败（不影响运行，仅失去兜底收尸）：" + ex.Message); }
        }

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern uint RegisterWindowMessage(string lpString);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr CreateJobObject(IntPtr a, string lpName);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(IntPtr hJob, int infoType, IntPtr lpInfo, int cbInfo);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr hObject);

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_BASIC_LIMIT_INFORMATION
        {
            public long PerProcessUserTimeLimit;
            public long PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize;
            public UIntPtr MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass;
            public uint SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct IO_COUNTERS
        {
            public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount;
            public ulong ReadTransferCount, WriteTransferCount, OtherTransferCount;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        {
            public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
            public IO_COUNTERS IoInfo;
            public UIntPtr ProcessMemoryLimit;
            public UIntPtr JobMemoryLimit;
            public UIntPtr PeakProcessMemoryUsed;
            public UIntPtr PeakJobMemoryUsed;
        }

        // ------------------------------------------------------------ 杂项

        private static void Log(string msg)
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(ShellLog));
                File.AppendAllText(ShellLog,
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "  " + msg + Environment.NewLine);
            }
            catch { }
        }
    }
}

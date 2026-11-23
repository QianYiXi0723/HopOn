using System;
using System.Threading;
using System.Windows.Forms;

namespace HopOn
{
    internal static class Program
    {
        /// <summary>应用入口。--tray：开机自启用，启动即藏进托盘不弹窗。
        /// --allow-multi：仅构建门禁用，允许与用户正在运行的壳并存（跳过单实例互斥）。
        /// --relaunched：由 HopOnUpdater 在更新完成后拉起（2026-11-23）——更新时
        ///   旧壳可能尚未完全释放单实例互斥，此模式会等旧壳退出后再接管，
        ///   避免「更新成功但应用打不开」。</summary>
        [STAThread]
        private static void Main(string[] args)
        {
            bool allowMulti = args != null && Array.IndexOf(args, "--allow-multi") >= 0;
            bool relaunched = args != null && Array.IndexOf(args, "--relaunched") >= 0;

            // 互斥名（测试可用 HOPON_SHELL_MUTEX 覆盖，沙箱 E2E 与用户实例隔离）
            string mutexName = Environment.GetEnvironmentVariable("HOPON_SHELL_MUTEX");
            if (string.IsNullOrEmpty(mutexName)) mutexName = "HopOn.Shell.Mutex";
            if (allowMulti)
                mutexName = mutexName + ".Probe." + System.Diagnostics.Process.GetCurrentProcess().Id;

            bool createdNew;
            var mutex = new Mutex(true, mutexName, out createdNew);
            if (!createdNew && relaunched)
            {
                // 2026-11-23 根修：更新拉起的新壳等旧壳释放互斥（最多 60 秒）。
                // 旧壳在 D8 路径下会主动退出，通常 1~3 秒内释放。
                for (int i = 0; i < 60 && !createdNew; i++)
                {
                    Thread.Sleep(1000);
                    mutex.Dispose();
                    mutex = new Mutex(true, mutexName, out createdNew);
                }
            }
            using (mutex)
            {
                if (!createdNew)
                {
                    // 不再静默：写日志说明为什么这次启动放弃（此前退出码 0 + 零日志，
                    // 现场无法区分「互斥顶回」和「启动崩溃」）
                    Log("已有实例持有互斥（" + mutexName + "），本次启动放弃"
                        + "（--relaunched=" + relaunched + "）");
                    return;
                }
                bool startMinimized = args != null && Array.IndexOf(args, "--tray") >= 0;
                int? forcedPort = null;
                if (args != null)
                    for (int i = 0; i < args.Length - 1; i++)
                        if (args[i] == "--port" && int.TryParse(args[i + 1], out int p))
                            forcedPort = p;
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.ThreadException += (s, e) => Log("UI线程异常：" + e.Exception);
                AppDomain.CurrentDomain.UnhandledException +=
                    (s, e) => Log("未处理异常：" + e.ExceptionObject);
                Application.Run(new MainForm(startMinimized, forcedPort));
                Log("Application.Run 已返回（主窗体真正关闭）");
            }
        }

        private static void Log(string msg)
        {
            try
            {
                string dir = System.IO.Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "logs");
                System.IO.Directory.CreateDirectory(dir);
                System.IO.File.AppendAllText(
                    System.IO.Path.Combine(dir, "shell.log"),
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "  " + msg + Environment.NewLine);
            }
            catch { }
        }
    }
}

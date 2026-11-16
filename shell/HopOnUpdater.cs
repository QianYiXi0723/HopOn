// HopOn 独立更新器
// ---------------------------------------------------------------------------
// 为什么需要它（2026-11-16 实弹结论）：
//   Inno Setup 靠 RestartManager 关闭运行中的进程来避免文件占用，但 HopOn 的
//   引擎 pythonw 是壳挂在 Job Object（KILL_ON_JOB_CLOSE）里的子进程，内核层面
//   不接受 RestartManager 的关闭请求 → 安装器 Abort → 整体回滚（rc=5）。
//   Updater 自己不在安装目录的替换集合里，所以它可以先等主进程自然退出，
//   再用「两阶段替换 + 完整备份」的方式换文件——绕开 RestartManager 这条死路。
//
// 设计要点：
//   1) 只改 install root 内的文件，且绝不触碰保留名单（Updater 自身 / .update\
//      工作区 / engine\config.json 用户配置）
//   2) 先全部备份、再统一覆盖（不许边备份边覆盖）——中途失败可从 backup 完整还原
//   3) 覆盖后校验关键文件存在且非空；缺任一 → 回滚并重启老版本
//   4) 所有步骤写 .update\updater.log，失败时日志是唯一诊断线索
// ---------------------------------------------------------------------------
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using System.Threading;

namespace HopOnUpdater
{
    internal static class Program
    {
        // 退出码约定（便于调用方/日志机器判定）
        private const int ExitOk = 0;
        private const int ExitUsage = 1;      // 参数错误
        private const int ExitGeneric = 2;    // 未预期异常
        private const int ExitVerify = 3;     // 覆盖后校验失败（已回滚）
        private const int ExitTimeout = 4;    // 等主进程退出超时（未动任何文件）
        private const int ExitRollback = 5;   // 覆盖阶段失败（已回滚）
        private const int ExitBadRoot = 6;    // 目标目录不安全，拒绝执行

        // 保留名单：这些路径/前缀下的东西永远不覆盖、不删除
        private static readonly string[] ReservedFiles =
        {
            "HopOnUpdater.exe",
        };
        private static readonly string[] ReservedPrefixes =
        {
            ".update" + Path.DirectorySeparatorChar,
            ".update",
            Path.Combine("engine", "config.json"),
        };

        private static string _logPath;
        private static readonly object _logLock = new object();

        private static void Log(string msg)
        {
            string line = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "  " + msg;
            lock (_logLock)
            {
                try
                {
                    if (_logPath != null)
                        File.AppendAllText(_logPath, line + Environment.NewLine, new UTF8Encoding(false));
                }
                catch
                {
                    // 日志失败绝不能影响主流程
                }
            }
            Console.WriteLine(line);
        }

        private static int Main(string[] args)
        {
            try
            {
                return Run(args);
            }
            catch (Exception ex)
            {
                Log("FATAL 未预期异常：" + ex);
                return ExitGeneric;
            }
        }

        private static int Run(string[] args)
        {
            int pid = -1;
            string root = null, pkg = null;
            bool restart = false, dryRun = false;

            // 手写参数解析（不引第三方库，参数面很小）
            for (int i = 0; i < args.Length; i++)
            {
                string a = args[i];
                switch (a)
                {
                    case "--pid":
                        if (++i >= args.Length || !int.TryParse(args[i], out pid)) return Usage("--pid 需要整数");
                        break;
                    case "--root":
                        if (++i >= args.Length) return Usage("--root 缺值");
                        root = args[i];
                        break;
                    case "--pkg":
                        if (++i >= args.Length) return Usage("--pkg 缺值");
                        pkg = args[i];
                        break;
                    case "--restart":
                        restart = true;
                        break;
                    case "--norestart":
                        restart = false;
                        break;
                    case "--dry-run":
                        dryRun = true;
                        break;
                    default:
                        return Usage("未知参数：" + a);
                }
            }

            if (pid < 0) return Usage("缺 --pid");
            if (string.IsNullOrEmpty(root)) return Usage("缺 --root");
            if (string.IsNullOrEmpty(pkg)) return Usage("缺 --pkg");
            if (!Directory.Exists(root)) return Usage("--root 目录不存在：" + root);
            if (!File.Exists(pkg)) return Usage("--pkg 不存在：" + pkg);

            root = Path.GetFullPath(root);
            pkg = Path.GetFullPath(pkg);

            string updateDir = Path.Combine(root, ".update");
            _logPath = Path.Combine(updateDir, "updater.log");
            try
            {
                Directory.CreateDirectory(updateDir);
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("无法创建 .update 目录：" + ex.Message);
                return ExitBadRoot;
            }

            Log("=== HopOn Updater 启动 ===");
            Log("参数：pid=" + pid + " root=" + root + " pkg=" + pkg
                + " restart=" + restart + " dryRun=" + dryRun);

            // 1) 目标目录合法性：拒绝盘符根 / 桌面 / 下载目录（防手滑把手滑变成灾难）
            string rootCheck = CheckRootSafety(root);
            if (rootCheck != null)
            {
                Log("FATAL 目标目录不安全，拒绝执行：" + rootCheck);
                return ExitBadRoot;
            }

            // 2) 等主进程退出（不许硬装：进程还在就换文件 = 半残）
            Log("等待主进程退出（pid=" + pid + "，最多 60 秒）…");
            if (!WaitPidGone(pid, 60))
            {
                Log("ABORT 等待主进程退出超时（60s）——未修改任何文件");
                return ExitTimeout;
            }
            Log("主进程已退出");

            // 3) 解压到 .update\staging
            string staging = Path.Combine(updateDir, "staging");
            try
            {
                if (Directory.Exists(staging)) Directory.Delete(staging, true);
                Directory.CreateDirectory(staging);
                Log("解压包到 staging…");
                ZipFile.ExtractToDirectory(pkg, staging);

                // 包内可能有一层顶层目录（zip 常带 HopOn_v<x.y.z\ 前缀），自动下钻
                staging = ResolveStagingRoot(staging);
                Log("staging 实际根 = " + staging);
            }
            catch (Exception ex)
            {
                Log("FATAL 解压失败：" + ex.Message);
                return ExitGeneric;
            }

            // 4) 计算差异清单（staging vs root），排除保留名单
            List<string> toCopy;
            try
            {
                toCopy = BuildPlan(staging, root);
            }
            catch (Exception ex)
            {
                Log("FATAL 生成计划失败：" + ex.Message);
                return ExitGeneric;
            }
            Log("计划：将覆盖 " + toCopy.Count + " 个文件");

            if (dryRun)
            {
                Log("--- DRY-RUN 清单开始 ---");
                foreach (string rel in toCopy) Log("  [将覆盖] " + rel);
                Log("--- DRY-RUN 清单结束（未修改任何文件）---");
                return ExitOk;
            }

            if (toCopy.Count == 0)
            {
                Log("没有需要覆盖的文件（已是最新）");
                if (restart) StartShell(root);
                return ExitOk;
            }

            // 5a) 备份：全部先备完，才开始覆盖
            string backup = Path.Combine(updateDir, "backup");
            try
            {
                if (Directory.Exists(backup)) Directory.Delete(backup, true);
                Directory.CreateDirectory(backup);
                int backed = 0;
                foreach (string rel in toCopy)
                {
                    string src = Path.Combine(root, rel);
                    if (!File.Exists(src)) continue;   // 新文件，无需备份
                    string dst = Path.Combine(backup, rel);
                    Directory.CreateDirectory(Path.GetDirectoryName(dst));
                    File.Copy(src, dst, true);
                    backed++;
                }
                Log("备份完成：共 " + backed + " 个文件 → " + backup);
            }
            catch (Exception ex)
            {
                Log("FATAL 备份失败（未覆盖任何文件）：" + ex.Message);
                return ExitGeneric;
            }

            // 5b) 覆盖：逐个复制，被占用重试 3 次；任一最终失败 → 回滚
            int overwritten = 0;
            string failedRel = null;
            foreach (string rel in toCopy)
            {
                string src = Path.Combine(staging, rel);
                string dst = Path.Combine(root, rel);
                if (!CopyWithRetry(src, dst, 3, 500))
                {
                    failedRel = rel;
                    break;
                }
                overwritten++;
            }
            if (failedRel != null)
            {
                Log("FATAL 覆盖失败于：" + failedRel + "（已覆盖 " + overwritten + " 个）——开始回滚");
                Rollback(root, backup, toCopy);
                Log("ROLLBACK 完成，已还原到升级前状态");
                if (restart) StartShell(root);
                return ExitRollback;
            }
            Log("覆盖完成：共 " + overwritten + " 个文件");

            // 6) 校验关键文件存在且非空
            string[] required =
            {
                "HopOn.exe",
                Path.Combine("engine", "app.py"),
                Path.Combine("engine", "daemon.py"),
            };
            foreach (string rel in required)
            {
                string p = Path.Combine(root, rel);
                if (!File.Exists(p) || new FileInfo(p).Length == 0)
                {
                    Log("FATAL 校验失败：关键文件缺失或为空 → " + rel);
                    Rollback(root, backup, toCopy);
                    Log("ROLLBACK 完成，已还原到升级前状态");
                    if (restart) StartShell(root);
                    return ExitVerify;
                }
            }
            Log("校验通过：HopOn.exe / engine\\app.py / engine\\daemon.py 均在位且非空");

            // 7) 成功：清 backup，按需重启
            try
            {
                Directory.Delete(backup, true);
                Log("已清理 backup");
            }
            catch (Exception ex)
            {
                Log("WARN backup 清理失败（不影响升级结果）：" + ex.Message);
            }
            Log("=== 升级成功 ===");
            if (restart) StartShell(root);
            return ExitOk;
        }

        // ------------------------------------------------------------------
        // 目标目录安全闸：拒绝危险 root（盘符根、桌面、下载、文档根）
        // ------------------------------------------------------------------
        private static string CheckRootSafety(string root)
        {
            string full = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar);
            if (full.Length <= 2) return "目标是盘符根（如 C:\\）";
            if (Path.GetPathRoot(full).TrimEnd(Path.DirectorySeparatorChar).Length == full.Length)
                return "目标是盘符根";

            string[] dangerous =
            {
                Environment.GetFolderPath(Environment.SpecialFolder.Desktop),
                Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Downloads"),
                Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
                Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                Environment.GetFolderPath(Environment.SpecialFolder.Windows),
                Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86),
            };
            foreach (string d in dangerous)
            {
                if (string.IsNullOrEmpty(d)) continue;
                string dn = Path.GetFullPath(d).TrimEnd(Path.DirectorySeparatorChar);
                if (string.Equals(full, dn, StringComparison.OrdinalIgnoreCase))
                    return "目标是系统/用户关键目录：" + dn;
            }
            return null;
        }

        // ------------------------------------------------------------------
        // 等进程消失：GetProcessById 抛 ArgumentException 即已退出
        // ------------------------------------------------------------------
        private static bool WaitPidGone(int pid, int timeoutSec)
        {
            DateTime deadline = DateTime.UtcNow.AddSeconds(timeoutSec);
            while (DateTime.UtcNow < deadline)
            {
                try
                {
                    using (Process p = Process.GetProcessById(pid))
                    {
                        if (p.HasExited) return true;
                    }
                }
                catch (ArgumentException)
                {
                    return true;   // 进程不存在 = 已退出
                }
                catch (InvalidOperationException)
                {
                    return true;
                }
                Thread.Sleep(300);
            }
            return false;
        }

        // ------------------------------------------------------------------
        // 包内初始目录常带一层 HopOn_vX.Y.Z\，下钻到真正根
        // ------------------------------------------------------------------
        private static string ResolveStagingRoot(string staging)
        {
            string[] entries = Directory.GetFileSystemEntries(staging);
            if (entries.Length == 1 && Directory.Exists(entries[0]))
            {
                string only = entries[0];
                // 只有当它里面才含 HopOn.exe 时才下钻（避免误判）
                if (File.Exists(Path.Combine(only, "HopOn.exe"))) return only;
            }
            return staging;
        }

        // ------------------------------------------------------------------
        // 差异计划：staging 下所有文件（相对路径），排除保留名单
        // ------------------------------------------------------------------
        private static List<string> BuildPlan(string staging, string root)
        {
            var plan = new List<string>();
            foreach (string file in Directory.GetFiles(staging, "*", SearchOption.AllDirectories))
            {
                string rel = file.Substring(staging.Length).TrimStart(Path.DirectorySeparatorChar);
                if (IsReserved(rel)) continue;
                plan.Add(rel);
            }
            plan.Sort(StringComparer.OrdinalIgnoreCase);
            return plan;
        }

        private static bool IsReserved(string rel)
        {
            string norm = rel.Replace('/', Path.DirectorySeparatorChar);
            foreach (string f in ReservedFiles)
            {
                if (string.Equals(norm, f, StringComparison.OrdinalIgnoreCase)) return true;
            }
            foreach (string pre in ReservedPrefixes)
            {
                string p = pre.Replace('/', Path.DirectorySeparatorChar);
                if (norm.Equals(p, StringComparison.OrdinalIgnoreCase)) return true;
                if (norm.StartsWith(p + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) return true;
            }
            return false;
        }

        // ------------------------------------------------------------------
        // 带重试的复制（被占用时等一会再试）
        // ------------------------------------------------------------------
        private static bool CopyWithRetry(string src, string dst, int tries, int delayMs)
        {
            for (int i = 1; i <= tries; i++)
            {
                try
                {
                    string dir = Path.GetDirectoryName(dst);
                    if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
                    File.Copy(src, dst, true);
                    return true;
                }
                catch (Exception ex)
                {
                    Log("WARN 复制失败（第 " + i + "/" + tries + " 次）：" + dst + " — " + ex.Message);
                    if (i < tries) Thread.Sleep(delayMs);
                }
            }
            return false;
        }

        // ------------------------------------------------------------------
        // 回滚：用 backup 覆盖回去（backup 里没有的 = 原本不存在的新文件）
        // ------------------------------------------------------------------
        private static void Rollback(string root, string backup, List<string> touched)
        {
            Log("开始回滚…");
            int restored = 0, skipped = 0;
            foreach (string rel in touched)
            {
                string b = Path.Combine(backup, rel);
                string r = Path.Combine(root, rel);
                if (!File.Exists(b))
                {
                    // 备份里没有 = 升级前这个文件不存在，回滚时删掉新装的
                    try
                    {
                        if (File.Exists(r)) File.Delete(r);
                    }
                    catch (Exception ex)
                    {
                        Log("WARN 回滚删除失败：" + rel + " — " + ex.Message);
                    }
                    skipped++;
                    continue;
                }
                if (!CopyWithRetry(b, r, 3, 300))
                {
                    Log("ERROR 回滚还原失败：" + rel);
                    continue;
                }
                restored++;
            }
            Log("回滚明细：还原 " + restored + " 个，删除新增 " + skipped + " 个");
        }

        // ------------------------------------------------------------------
        private static void StartShell(string root)
        {
            string exe = Path.Combine(root, "HopOn.exe");
            if (!File.Exists(exe))
            {
                Log("WARN 无法重启：HopOn.exe 不存在");
                return;
            }
            try
            {
                Process.Start(new ProcessStartInfo(exe) { WorkingDirectory = root, UseShellExecute = true });
                Log("已启动：" + exe);
            }
            catch (Exception ex)
            {
                Log("WARN 启动失败：" + ex.Message);
            }
        }

        private static int Usage(string msg)
        {
            Console.Error.WriteLine("参数错误：" + msg);
            Console.Error.WriteLine("用法：HopOnUpdater.exe --pid <int> --root <dir> --pkg <zip> [--restart|--norestart] [--dry-run]");
            return ExitUsage;
        }
    }
}

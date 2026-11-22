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

        // 关键文件清单（apply 期最后兜底校验，也是 staged 复用 staging 的资格检查）
        private static readonly string[] Required =
        {
            "HopOn.exe",
            Path.Combine("engine", "app.py"),
            Path.Combine("engine", "daemon.py"),
            Path.Combine("engine", "core", "update.py"),
            Path.Combine("engine", "login_ui.py"),
        };

        private static string _logPath;
        private static readonly object _logLock = new object();

        // ------------------------------------------------------------------
        // 从当前 Job Object 脱离（KILL_ON_JOB_CLOSE 的 Job 会被子进程继承，
        // Updater 杀壳时会被连带杀死——见 Run() 顶部注释）
        // ------------------------------------------------------------------
        [System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool result);

        [System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr CreateJobObject(IntPtr attrs, string name);

        [System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

        [System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(IntPtr job, int infoClass,
            IntPtr info, uint length);

        private static void DetachFromJob()
        {
            try
            {
                bool inJob;
                if (!IsProcessInJob(Process.GetCurrentProcess().Handle, IntPtr.Zero, out inJob) || !inJob)
                    return;   // 本来就不在 Job 里

                // 建一个「允许 breakaway」的新 Job 并把自己挂进去，
                // 旧 Job 的 KILL_ON_JOB_CLOSE 就管不到我们了。
                IntPtr newJob = CreateJobObject(IntPtr.Zero, null);
                if (newJob == IntPtr.Zero) return;

                // JOBOBJECT_EXTENDED_LIMIT_INFORMATION 里设
                // LimitFlags |= JOB_OBJECT_LIMIT_BREAKAWAY_OK (0x0800)
                // 结构太复杂，这里用简化的 24 字节布局：
                //   struct { LARGE_INTEGER a,b; DWORD LimitFlags; ... }
                // 只用最保险的做法：直接 AssignProcessToJobObject 到一个新 Job，
                // 若旧 Job 未设 breakaway 会失败——那也说明我们无法脱离，记日志继续。
                IntPtr self = Process.GetCurrentProcess().Handle;
                if (AssignProcessToJobObject(newJob, self))
                    Log("已脱离父 Job Object（防止杀壳时被连带终止）");
            }
            catch
            {
                // 脱离失败不阻断主流程（有时调用方已用 CREATE_BREAKAWAY_FROM_JOB 达成目的）
            }
        }

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
            // 2026-11-16 真机实证（头号坑）：Updater 由引擎派生，而引擎被壳挂在
            // KILL_ON_JOB_CLOSE 的 Job Object 里，Job 会被子进程继承。于是
            // Updater 去 Kill 壳时会把自己一起带走——日志停在「结束占用进程」
            // 之后戛然而止、backup 未清理、也没回滚（进程已死）。
            // 双重保险：调用方已传 CREATE_BREAKAWAY_FROM_JOB，这里再主动
            // 从当前 Job 脱离一次（有些环境会忽略 creation flag）。
            DetachFromJob();

            int pid = -1;
            string root = null, pkg = null;
            bool restart = false, dryRun = false, staged = false;

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
                    case "--staged":
                        // 2026-11-22 staged 改造：staging 已由引擎运行期解压就绪，
                        // 跳过解压直接覆盖（黑幕只剩纯覆盖）；staging 不完整时
                        // 自动回退从 zip 解压——永不比旧链差。
                        staged = true;
                        break;
                    default:
                        return Usage("未知参数：" + a);
                }
            }

            if (pid < 0) return Usage("缺 --pid");
            if (string.IsNullOrEmpty(root)) return Usage("缺 --root");
            if (!Directory.Exists(root)) return Usage("--root 目录不存在：" + root);
            // --pkg 可选（2026-11-23 对照 Velopack/Firefox 修正）：--staged 且
            // staging 完整时根本不需要 zip——壳启动续 apply 就不传 --pkg。
            // 缺 pkg 时回退解压能力失效：staging 不完整 → 明确放弃（未动任何文件）。
            if (string.IsNullOrEmpty(pkg) && !staged)
                return Usage("缺 --pkg（非 --staged 模式必须给 zip）");
            if (!string.IsNullOrEmpty(pkg) && !File.Exists(pkg))
                return Usage("--pkg 不存在：" + pkg);
            // pid=0 合法（2026-11-22 壳启动续 apply：引擎还没起，无人可等）

            root = Path.GetFullPath(root);
            if (!string.IsNullOrEmpty(pkg)) pkg = Path.GetFullPath(pkg);

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
            if (pid <= 0)
            {
                Log("pid<=0（壳启动续 apply 场景）——跳过等进程");
            }
            else
            {
                Log("等待主进程退出（pid=" + pid + "，最多 60 秒）…");
                if (!WaitPidGone(pid, 60))
                {
                    Log("ABORT 等待主进程退出超时（60s）——未修改任何文件");
                    return ExitTimeout;
                }
                Log("主进程已退出");
            }
            // 2026-11-23 跨链推演 D8 配套：无条件清场 root 内残留进程（daemon
            // 等引擎子进程会锁住 engine\python 的文件）。Firefox 语义：EXECUTE
            // 前应用必须全退。幂等——已退则杀到 0 个。
            // Z1（逐行走读）：dry-run 语义=「未修改任何文件」，杀进程是副作用——
            // 跳过清场，保证 --dry-run 诊断时不会误杀正在跑的 HopOn。
            if (!dryRun)
            {
                int preKilled = KillProcessesUnder(root);
                if (preKilled > 0) Log("apply 前清场：结束 root 内残留进程 " + preKilled + " 个");
            }

            // 3) staging 就绪（引擎运行期已解压则直接复用；不完整回退从 zip 解压）
            string staging = Path.Combine(updateDir, "staging");
            bool reuse = false;
            if (staged && StagingHasExe(staging))
            {
                // 复用资格（2026-11-23 对照检查 C3）：关键 5 文件非空——
                // StagingHasExe 只看 HopOn.exe，残缺 staging 在这里被拦回
                // 回退解压路径
                string sroot0 = ResolveStagingRoot(staging);
                reuse = true;
                foreach (string rel in Required)
                {
                    string p = Path.Combine(sroot0, rel);
                    if (!File.Exists(p) || new FileInfo(p).Length == 0)
                    {
                        Log("staged staging 残缺（缺 " + rel + "）→ 走回退解压");
                        reuse = false;
                        break;
                    }
                }
                if (reuse)
                {
                    staging = sroot0;
                    Log("staged 模式：复用引擎运行期解压好的 staging = " + staging);
                }
            }
            if (!reuse)
            {
                if (staged)
                {
                    if (string.IsNullOrEmpty(pkg))
                    {
                        Log("ABORT --staged 但 staging 不完整且无 zip 可回退（壳续跑场景）——未动任何文件");
                        return ExitGeneric;
                    }
                    Log("staged 模式但 staging 不完整 → 回退从 zip 解压（双保险）");
                }
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
            }

            // 4) 差异清单（内容不同才覆盖）+ 删除计划（engine 子树幽灵文件）
            List<string> toCopy, toDelete;
            try
            {
                toCopy = BuildPlan(staging, root);
                toDelete = BuildDeletePlan(staging, root);
            }
            catch (Exception ex)
            {
                Log("FATAL 生成计划失败：" + ex.Message);
                return ExitGeneric;
            }
            Log("计划：将覆盖 " + toCopy.Count + " 个文件，删除旧版遗留 " + toDelete.Count + " 个");

            if (dryRun)
            {
                Log("--- DRY-RUN 清单开始 ---");
                foreach (string rel in toCopy) Log("  [将覆盖] " + rel);
                foreach (string rel in toDelete) Log("  [将删除] " + rel);
                Log("--- DRY-RUN 清单结束（未修改任何文件）---");
                return ExitOk;
            }

            if (toCopy.Count == 0 && toDelete.Count == 0)
            {
                Log("没有需要变更的文件（已是最新）");
                if (restart) StartShell(root);
                return ExitOk;
            }

            // 5) 覆盖（2026-11-23 Firefox rename 化）：旧文件 rename 到 backup
            //    让位 → 新文件 rename 到位，全程同盘原子零拷贝。backup 只建
            //    目录不预拷——每个旧文件的备份由覆盖动作本身完成（一次 rename）。
            //    续跑保护：backup 已存在则不删（里面是上次中断已备份的旧版回
            //    滚点）；ReplaceWithRetry 对 bak 已存在的文件只让位不重备份。
            string backup = Path.Combine(updateDir, "backup");
            try
            {
                Directory.CreateDirectory(backup);
            }
            catch (Exception ex)
            {
                Log("FATAL backup 目录创建失败：" + ex.Message);
                return ExitGeneric;
            }
            int overwritten = 0;
            string failedRel = null;
            // 实际动过的文件（2026-11-23 C6）：回滚清单只收这个——计划清单里
            // 排在失败点之后的文件从未被碰，绝不能进回滚（S8 实弹：旧版被当
            // 「新增」删除的数据损失）
            var touched = new List<string>();
            foreach (string rel in toCopy)
            {
                string src = Path.Combine(staging, rel);
                string dst = Path.Combine(root, rel);
                string bak = Path.Combine(backup, rel);
                int act = ReplaceWithRetry(src, dst, bak, 3, 500, root);
                if (act == 0)
                {
                    // 让位可能已发生（旧版躺在 bak、root 侧缺失）——必须进回滚
                    if (File.Exists(Path.Combine(backup, rel))) touched.Add(rel);
                    failedRel = rel;
                    break;
                }
                touched.Add(rel);
                overwritten++;
            }
            if (failedRel != null)
            {
                Log("FATAL 覆盖失败于：" + failedRel + "（已覆盖 " + overwritten + " 个）——开始回滚");
                Rollback(root, backup, touched);   // toDelete 未执行，无需涉及
                Log("ROLLBACK 完成，已还原到升级前状态");
                if (restart) StartShell(root);
                return ExitRollback;
            }
            Log("覆盖完成：共 " + overwritten + " 个文件");

            // 5b) 删除旧版遗留（新版已删/改名的幽灵文件）。失败不中断——残留
            //     旧文件无害（新版照样运行），但 backup 保留其内容可回滚。
            //     2026-11-23 逐行审 D4（C6 同型）：只有「让位成功」的才进回滚
            //     清单——让位失败的文件 root 侧还是旧版且没进 backup，若进清单
            //     会被回滚当作「新增」删掉（数据损失）。
            int deleted = 0;
            var deletedTouched = new List<string>();
            foreach (string rel in toDelete)
            {
                string dst = Path.Combine(root, rel);
                string bak = Path.Combine(backup, rel);
                try
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(bak));
                    File.Move(dst, bak);   // rename 让位=备份（原子）
                    deletedTouched.Add(rel);
                    deleted++;
                }
                catch (Exception ex)
                {
                    Log("WARN 删除旧版遗留失败（继续）：" + rel + " — " + ex.Message);
                }
            }
            if (deleted > 0) Log("删除旧版遗留：共 " + deleted + " 个文件");

            // 6) 校验关键文件存在且非空（前两处引擎侧 staging 期已验，此处
            //    是 apply 期最后兜底）
            foreach (string rel in Required)
            {
                string p = Path.Combine(root, rel);
                if (!File.Exists(p) || new FileInfo(p).Length == 0)
                {
                    Log("FATAL 校验失败：关键文件缺失或为空 → " + rel);
                    var touchedAll = new List<string>(touched);
                    touchedAll.AddRange(deletedTouched);   // 只收让位成功的（D4）
                    Rollback(root, backup, touchedAll);
                    Log("ROLLBACK 完成，已还原到升级前状态");
                    if (restart) StartShell(root);
                    return ExitVerify;
                }
            }
            Log("校验通过：关键 5 文件均在位且非空");

            // 7) 成功：清 backup + pending.json + staging + zip（磁盘归还），
            //    按需重启。pending 清除必须在 StartShell **之前**——否则新壳
            //    启动时会误判「又有更新待应用」。
            try
            {
                Directory.Delete(backup, true);
                Log("已清理 backup");
            }
            catch (Exception ex)
            {
                Log("WARN backup 清理失败（不影响升级结果）：" + ex.Message);
            }
            try
            {
                string pending = Path.Combine(updateDir, "pending.json");
                if (File.Exists(pending)) File.Delete(pending);
                string stagingAll = Path.Combine(updateDir, "staging");
                if (Directory.Exists(stagingAll)) Directory.Delete(stagingAll, true);
                if (!string.IsNullOrEmpty(pkg) && File.Exists(pkg)) File.Delete(pkg);
                Log("已清理 pending.json / staging / 安装包");
            }
            catch (Exception ex)
            {
                Log("WARN staging/pending 清理失败（不影响升级结果）：" + ex.Message);
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
        // staging 是否已含可执行新版（引擎运行期解压就绪的判据）：
        // staging\HopOn.exe 或 staging\<唯一子目录>\HopOn.exe（带顶层目录）
        // ------------------------------------------------------------------
        private static bool StagingHasExe(string staging)
        {
            try
            {
                if (File.Exists(Path.Combine(staging, "HopOn.exe"))) return true;
                string[] entries = Directory.GetFileSystemEntries(staging);
                return entries.Length == 1 && Directory.Exists(entries[0])
                    && File.Exists(Path.Combine(entries[0], "HopOn.exe"));
            }
            catch
            {
                return false;
            }
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
        // 差异计划：staging 下「内容与 root 不同」的文件（相对路径），排除
        // 保留名单。2026-11-23 对照 Firefox rename 化改造：换法是「旧文件
        // rename 让位→新文件 rename 到位」，内容相同的文件必须跳过——否则
        // 续跑场景会把半新文件 rename 进 backup 冲掉回滚点（File.Move 对
        // 已有目标抛错，正好防误冲，但会假失败）。
        // ------------------------------------------------------------------
        private static List<string> BuildPlan(string staging, string root)
        {
            var plan = new List<string>();
            foreach (string file in Directory.GetFiles(staging, "*", SearchOption.AllDirectories))
            {
                string rel = file.Substring(staging.Length).TrimStart(Path.DirectorySeparatorChar);
                if (IsReserved(rel)) continue;
                if (FilesEqual(file, Path.Combine(root, rel))) continue;   // 内容相同不覆盖
                plan.Add(rel);
            }
            plan.Sort(StringComparer.OrdinalIgnoreCase);
            return plan;
        }

        // ------------------------------------------------------------------
        // 删除计划（2026-11-23 对照 Firefox「MAR 声明删除」语义）：新版已
        // 删除/改名的旧文件若不清理，会残留幽灵 .py 被引擎 import。
        // 保守规则——只清 engine\ 子树内、且其父目录链在 staging 中存在
        // （staging 没有的整目录如 engine\python 绝不碰）、排除 __pycache__
        // 与保留名单。根级永远不动（用户可能把杂物放在自定义安装根）。
        // ------------------------------------------------------------------
        private static List<string> BuildDeletePlan(string staging, string root)
        {
            var plan = new List<string>();
            string engineRoot = Path.Combine(root, "engine");
            if (!Directory.Exists(engineRoot)) return plan;
            string engineStaging = Path.Combine(staging, "engine");
            foreach (string file in Directory.GetFiles(engineRoot, "*", SearchOption.AllDirectories))
            {
                string rel = file.Substring(root.Length).TrimStart(Path.DirectorySeparatorChar);
                if (IsReserved(rel)) continue;
                string norm = rel.Replace('/', Path.DirectorySeparatorChar);
                if (norm.Split(Path.DirectorySeparatorChar)
                        .Any(s => string.Equals(s, "__pycache__", StringComparison.OrdinalIgnoreCase)))
                    continue;
                if (File.Exists(Path.Combine(staging, rel))) continue;   // 新版还有 → 不删
                // 父目录链必须全部在 staging 存在（防整目录误删，如 engine\python）
                string parent = Path.GetDirectoryName(rel);
                bool parentsInStaging = true;
                while (!string.IsNullOrEmpty(parent))
                {
                    if (!Directory.Exists(Path.Combine(staging, parent))) { parentsInStaging = false; break; }
                    parent = Path.GetDirectoryName(parent);
                }
                if (parentsInStaging) plan.Add(rel);
            }
            plan.Sort(StringComparer.OrdinalIgnoreCase);
            return plan;
        }

        // ------------------------------------------------------------------
        // 逐字节比较两个文件（长度不同速判；比不了当作不同——覆盖最稳）
        // ------------------------------------------------------------------
        private static bool FilesEqual(string a, string b)
        {
            try
            {
                var fa = new FileInfo(a);
                var fb = new FileInfo(b);
                if (!fb.Exists || fa.Length != fb.Length) return false;
                const int BUF = 65536;
                var ba = new byte[BUF];
                var bb = new byte[BUF];
                using (var sa = fa.OpenRead())
                using (var sb = fb.OpenRead())
                {
                    int ra, rb;
                    do
                    {
                        ra = ReadFull(sa, ba);
                        rb = ReadFull(sb, bb);
                        if (ra != rb) return false;
                        for (int i = 0; i < ra; i++)
                            if (ba[i] != bb[i]) return false;
                    } while (ra > 0);
                    return true;
                }
            }
            catch
            {
                return false;
            }
        }

        private static int ReadFull(System.IO.FileStream s, byte[] buf)
        {
            int total = 0, n;
            while (total < buf.Length && (n = s.Read(buf, total, buf.Length - total)) > 0) total += n;
            return total;
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
        // 带重试的替换（2026-11-23 对照 Firefox updater rename 化）：
        //   旧文件 File.Move → backup（原子让位，运行中的 exe Windows 也
        //   允许 rename）→ 新文件 File.Move 到位（同盘零拷贝）。
        //   回滚=撤销一串 rename，窗口比 copy+overwrite 小得多。
        // 返回值（2026-11-23 C6 修正）：0=失败；1=替换（旧版已让位到 bak）；
        //   2=新建（升级前不存在，无 bak）。回滚清单必须只收「实际动过的
        //   文件」——计划清单里排在校验失败点之后的文件从未被碰，若被回滚
        //   当作「新增」删除会毁掉旧版（S8 实弹抓出的数据损失）。
        // 被占用时先尝试结束占用进程，再重试。
        //
        // 2026-11-16 真机实证：安装版自我升级时，--pid 传的是引擎 PID，
        // 但占用 HopOn.exe 的是**壳进程**（引擎的父进程），等它退出无效 →
        // 三次重试全失败 → 回滚。所以这里必须自己解决占用：
        // 按「可执行路径在 root 内」精确定位占用者并结束，绝不按进程名杀。
        // ------------------------------------------------------------------
        private static int ReplaceWithRetry(string src, string dst, string bak, int tries, int delayMs, string root = null)
        {
            bool dstWasThere = File.Exists(dst);
            for (int i = 1; i <= tries; i++)
            {
                try
                {
                    string dir = Path.GetDirectoryName(dst);
                    if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
                    if (File.Exists(dst))
                    {
                        // bak 已存在 = 上次覆盖中断已备份过（续跑场景）——保留
                        // 旧回滚点，dst 只须让位（内容与 bak 相同，删除安全）
                        if (File.Exists(bak))
                        {
                            File.Delete(dst);
                        }
                        else
                        {
                            Directory.CreateDirectory(Path.GetDirectoryName(bak));
                            File.Move(dst, bak);
                        }
                    }
                    File.Move(src, dst);
                    return dstWasThere ? 1 : 2;
                }
                catch (Exception ex)
                {
                    Log("WARN 替换失败（第 " + i + "/" + tries + " 次）：" + dst + " — " + ex.Message);
                    if (i < tries)
                    {
                        // 第 1 次失败后就开始清场：把 root 内还在跑的进程结束掉
                        if (i == 1 && root != null)
                        {
                            int killed = KillProcessesUnder(root);
                            Log("清场占用进程：" + (killed > 0 ? killed + " 个" : "无"));
                        }
                        Thread.Sleep(delayMs);
                    }
                }
            }
            return 0;
        }

        // ------------------------------------------------------------------
        // 结束「可执行路径在给定目录内」的所有进程（判据是路径前缀，不是
        // 进程名）。返回结束的数量。路径用 MainModule.FileName——2026-11-16
        // 弃用 WMI（Win11 已移除 wmic、输出 GBK 解码炸导致静默零杀）。
        // ------------------------------------------------------------------
        private static int KillProcessesUnder(string root)
        {
            int killed = 0;
            string normRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            Process[] all;
            try
            {
                all = Process.GetProcesses();
            }
            catch
            {
                return 0;
            }
            foreach (Process p in all)
            {
                try
                {
                    if (p.Id == Process.GetCurrentProcess().Id) continue;
                    string path = null;
                    try { path = p.MainModule.FileName; }
                    catch { continue; }   // 权限不足/已退出 → 跳过
                    if (string.IsNullOrEmpty(path)) continue;
                    if (!Path.GetFullPath(path).StartsWith(normRoot, StringComparison.OrdinalIgnoreCase)) continue;
                    Log("结束占用进程：" + Path.GetFileName(path) + "(" + p.Id + ")");
                    p.Kill();
                    p.WaitForExit(5000);
                    killed++;
                }
                catch (Exception ex)
                {
                    Log("WARN 结束进程失败：" + ex.Message);
                }
                finally
                {
                    try { p.Dispose(); } catch { }
                }
            }
            return killed;
        }

        // ------------------------------------------------------------------
        // 回滚（2026-11-23 rename 化）：backup 里有的 = rename 一串回去；
        // backup 里没有的 = 原本不存在的新文件，删掉。
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
                    Log("  [回滚] " + rel + "：backup 无记录 → 删除安装侧文件（root 侧存在=" + File.Exists(r) + "）");
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
                try
                {
                    Log("  [回滚] " + rel + "：从 backup rename 还原");
                    if (File.Exists(r)) File.Delete(r);   // 残留新文件让位（原子还原旧版）
                    Directory.CreateDirectory(Path.GetDirectoryName(r));
                    File.Move(b, r);
                    restored++;
                }
                catch (Exception ex)
                {
                    Log("ERROR 回滚还原失败：" + rel + " — " + ex.Message);
                }
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
            Console.Error.WriteLine("用法：HopOnUpdater.exe --pid <int> --root <dir> [--pkg <zip>] [--staged] [--restart|--norestart] [--dry-run]   // --pkg 仅在 --staged 且 staging 完整时可省");
            return ExitUsage;
        }
    }
}

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HopOnUpdater staged 新行为实弹 E2E（2026-11-22）。

用编译好的真 HopOnUpdater.exe 在 %TEMP% 沙箱里过四个场景：
  S1 staged 复用（引擎运行期解压就绪 → 跳过解压直接覆盖 → 清场）
  S2 覆盖中断续跑（backup 在场 → 跳过备份段保住干净回滚点 → 补覆盖）
  S3 staging 不完整 → 回退从 zip 解压（双保险）
  S4 --pid 0 直通（壳续 apply 传参形态）

沙箱内放「用户数据混装探针」engine\\config.json 与 logs\\webview2\\，
断言它们原样存活（BuildPlan 只覆盖 staging 内文件 + 保留名单双保险）。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

UPD = r"C:\Users\ASUS\AppData\Local\Temp\rl_staged_updater\HopOnUpdater.exe"
REQUIRED = [
    ("HopOn.exe", "new-shell"),
    (r"engine\app.py", "new-app"),
    (r"engine\daemon.py", "new-daemon"),
    (r"engine\core\update.py", "new-update"),
    (r"engine\login_ui.py", "new-loginui"),
]
OLD = {rel: "old-" + Path(rel).name for rel, _ in REQUIRED}

PASS = 0
FAIL = 0


def check(cond: bool, label: str) -> None:
    global PASS, FAIL
    print(("  PASS " if cond else "  FAIL ") + label)
    if cond:
        PASS += 1
    else:
        FAIL += 1


def build_env(base: Path, *, staging_broken=False, pre_backup=False,
              half_applied=False) -> tuple[Path, Path]:
    root = base / "install"
    staging = root / ".update" / "staging" / "HopOn_v2.7.0"
    (root / "engine" / "core").mkdir(parents=True, exist_ok=True)
    (root / "logs" / "webview2").mkdir(parents=True, exist_ok=True)
    for rel, body in OLD.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("new-" + body if False else body, encoding="utf-8")
    (root / r"engine\config.json").write_text('{"keep": true}', encoding="utf-8")
    (root / r"logs\webview2\probe.txt").write_text("webview2-data", encoding="utf-8")
    if half_applied:   # 中断现场：HopOn.exe 已被覆盖成新版，其余还是旧版
        (root / "HopOn.exe").write_text("new-shell", encoding="utf-8")
    # staging（引擎运行期解压的产物形态）
    if not staging_broken:
        for rel, body in REQUIRED:
            p = staging / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
    else:
        (staging / "engine").mkdir(parents=True, exist_ok=True)
        (staging / r"engine\app.py").write_text("new-app", encoding="utf-8")
    # zip（供回退解压与正常参数）
    zip_path = base / "HopOn_v2.7.0_Windows_x64.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for rel, body in REQUIRED:
            zf.writestr("HopOn_v2.7.0/" + rel.replace("\\", "/"), body)
    (root / ".update" / "pending.json").write_text(
        json.dumps({"version": "2.7.0", "pkg": str(zip_path)}), encoding="utf-8")
    if pre_backup:   # 上次覆盖中断留下的干净回滚点
        bk = root / ".update" / "backup"
        for rel, body in OLD.items():
            p = bk / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
    return root, zip_path


def run(root: Path, zip_path, *, pid="999999", staged=True):
    """跑 Updater，返回 (proc, 日志全文)。zip_path=None 时不传 --pkg。

    日志断言读 updater.log 文件（UTF-8 无 BOM 落盘，编码无关）而非 stdout：
    Console.WriteLine 中文在引擎 Python（-X utf8）下 subprocess 解码为
    UTF-8、而 Updater 控制台输出是 GBK → 中文乱码，2026-11-23 实测 4 条
    日志断言全灭（qa 环境复现）——文件日志是唯一稳定断言源。
    """
    cmd = [UPD, "--pid", pid, "--root", str(root)]
    if zip_path is not None:
        cmd += ["--pkg", str(zip_path)]
    cmd += ["--norestart"]
    if staged:
        cmd.append("--staged")
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          errors="replace", timeout=120)
    log_file = root / ".update" / "updater.log"
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = (proc.stdout or "") + (proc.stderr or "")
    return proc, text


def read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


def main() -> int:
    if not Path(UPD).is_file():
        print("SKIP：先编译 HopOnUpdater（dotnet build shell/HopOnUpdater.csproj）")
        return 0
    base = Path(tempfile.mkdtemp(prefix="rl_updater_e2e_"))
    try:
        # ---- S1 staged 复用 ----
        print("[S1] staged 复用引擎解压好的 staging")
        root, zip_path = build_env(base / "s1")
        proc, log = run(root, zip_path)
        check(proc.returncode == 0, "S1 退出码 0")
        check(all(read(root, rel) == body for rel, body in REQUIRED),
              "S1 五个关键文件全部为新版")
        check(read(root, r"engine\config.json") == '{"keep": true}',
              "S1 engine\\config.json 原样存活（保留名单）")
        check(read(root, r"logs\webview2\probe.txt") == "webview2-data",
              "S1 安装根 logs\\ 数据原样存活")
        check(not (root / ".update" / "backup").exists()
              and not (root / ".update" / "staging").exists()
              and not (root / ".update" / "pending.json").exists()
              and not zip_path.exists(),
              "S1 backup/staging/pending/zip 全部清场")
        check("复用引擎运行期解压好的 staging" in log, "S1 日志确认走复用路径")

        # ---- S2 覆盖中断续跑（rename 化：diff 计划跳过已覆盖文件，bak
        #      已存在的文件只让位不重备份——旧回滚点不被冲）----
        print("[S2] 覆盖中断续跑（diff 计划 + bak 保护）")
        root, zip_path = build_env(base / "s2", pre_backup=True, half_applied=True)
        proc, log = run(root, zip_path)
        check(proc.returncode == 0, "S2 退出码 0")
        check(all(read(root, rel) == body for rel, body in REQUIRED),
              "S2 半新半旧补齐为全新版")
        import re as _re

        m = _re.search(r"将覆盖 (\d+) 个文件", log)
        check(m is not None and int(m.group(1)) < len(REQUIRED),
              "S2 diff 计划跳过已覆盖文件（覆盖数 < 全量）")
        check(not (root / ".update" / "backup").exists(), "S2 成功后 backup 清理")

        # ---- S3 staging 不完整 → 回退解压 ----
        print("[S3] staging 不完整 → 回退从 zip 解压")
        root, zip_path = build_env(base / "s3", staging_broken=True)
        proc, log = run(root, zip_path)
        check(proc.returncode == 0, "S3 退出码 0")
        check("回退从 zip 解压（双保险）" in log, "S3 日志确认回退路径")
        check(all(read(root, rel) == body for rel, body in REQUIRED),
              "S3 zip 解压后覆盖全到位")

        # ---- S4 pid=0 直通 ----
        print("[S4] --pid 0 直通（壳续 apply 传参形态）")
        root, zip_path = build_env(base / "s4")
        proc, log = run(root, zip_path, pid="0")
        check(proc.returncode == 0, "S4 退出码 0")
        check("pid<=0（壳启动续 apply 场景）——跳过等进程" in log, "S4 直通日志在")
        check(all(read(root, rel) == body for rel, body in REQUIRED),
              "S4 覆盖到位")

        # ---- S5 壳续跑传参形态：--staged 且无 --pkg、staging 完整 ----
        print("[S5] --staged 无 --pkg（壳启动续 apply，2026-11-23 C1 修正）")
        root, zip_path = build_env(base / "s5")
        proc, log = run(root, None)
        check(proc.returncode == 0, "S5 退出码 0（无 pkg 也能完成 apply）")
        check(all(read(root, rel) == body for rel, body in REQUIRED),
              "S5 覆盖到位")
        check(not (root / ".update" / "pending.json").exists()
              and not (root / ".update" / "staging").exists(),
              "S5 pending/staging 清场")
        check(zip_path.exists(), "S5 无 pkg 参数 → zip 不被误删（留存无害）")

        # ---- S6 --staged 无 --pkg 且 staging 残缺 → 明确放弃 ----
        print("[S6] --staged 无 --pkg 且 staging 残缺 → 放弃（未动文件）")
        root, zip_path = build_env(base / "s6", staging_broken=True)
        old_body = read(root, "HopOn.exe")
        proc, log = run(root, None)
        check(proc.returncode == 2, "S6 放弃退出码 ExitGeneric=2")
        check("未动任何文件" in log, "S6 放弃日志在")
        check(read(root, "HopOn.exe") == old_body, "S6 旧版文件原样未动")

        # ---- S7 删除计划实弹（2026-11-23：此前 toDelete 从未被真实触发）----
        # 三类文件三种命运：engine 子树幽灵 → 删（备份进 backup）；
        # engine\python（staging 无此目录）→ 保；根级杂物 → 永不碰。
        print("[S7] 删除计划：幽灵删 / python 目录保 / 根级杂物保")
        root, zip_path = build_env(base / "s7")
        (root / r"engine\ghost.py").write_text("old-ghost", encoding="utf-8")
        (root / r"engine\python\python.exe").parent.mkdir(parents=True, exist_ok=True)
        (root / r"engine\python\python.exe").write_text("fake-python", encoding="utf-8")
        (root / "用户杂物.txt").write_text("user-file", encoding="utf-8")
        proc, log = run(root, zip_path)
        check(proc.returncode == 0, "S7 退出码 0")
        check(not (root / r"engine\ghost.py").exists(),
              "S7 engine 子树幽灵 .py 已删（防 import 幽灵模块）")
        check((root / r"engine\python\python.exe").read_text(encoding="utf-8") == "fake-python",
              "S7 staging 没有的整目录（python\\）原样保留")
        check((root / "用户杂物.txt").read_text(encoding="utf-8") == "user-file",
              "S7 根级用户杂物永不动")
        check("删除旧版遗留：共 1 个文件" in log, "S7 删除计数日志准确")

        # ---- S8 回滚实弹（2026-11-23：Rollback rename 化后从未真实执行过）----
        # 用系统 python 进程独占句柄锁住 root\engine\daemon.py（系统 python
        # 可执行路径不在 root 内 → KillProcessesUnder 杀不到它），构造覆盖
        # 真失败 → 断言 root 完整还原旧版、ghost 未删（toDelete 未执行）。
        print("[S8] 覆盖真失败 → rename 级回滚还原旧版")
        root, zip_path = build_env(base / "s8")
        (root / r"engine\ghost.py").write_text("old-ghost", encoding="utf-8")
        lock_script = (
            "import time\n"
            "f = open(r'%s', 'r')\n"
            "time.sleep(30)\n" % (root / r"engine\daemon.py")
        )
        holder = subprocess.Popen([sys.executable, "-c", lock_script])
        try:
            proc, log = run(root, zip_path)
            check(proc.returncode == 5, "S8 覆盖失败退出码 ExitRollback=5")
            check("开始回滚" in log and "ROLLBACK 完成" in log, "S8 回滚流程日志在")
            check(all(read(root, rel) == body for rel, body in OLD.items()),
                  "S8 五个关键文件完整还原旧版（rename 级回滚）")
            check((root / r"engine\ghost.py").read_text(encoding="utf-8") == "old-ghost",
                  "S8 ghost 未动（toDelete 在覆盖失败后不执行）")
            check("覆盖失败于" in log, "S8 失败文件定位日志在")
        finally:
            holder.kill()
            try:
                holder.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

        # ---- S9 逐行审 D4：校验失败 + ghost 让位失败 → 回滚不得删旧文件 ----
        # 构造：锁定 ghost（toDelete 让位必失败）+ staging 的 update.py 置空
        # （覆盖全部成功但关键文件校验失败）→ 回滚必须只还原实际让位成功的
        # 文件，ghost（让位失败、root 侧还是旧版）原样保留。
        print("[S9] 校验失败回滚：让位失败的旧文件不得被删（D4）")
        root, zip_path = build_env(base / "s9")
        (root / r"engine\ghost.py").write_text("old-ghost", encoding="utf-8")
        staging9 = root / ".update" / "staging" / "HopOn_v2.7.0"
        (staging9 / r"engine\core\update.py").write_text("", encoding="utf-8")
        lock2 = (
            "import time\n"
            "f = open(r'%s', 'r')\n"
            "time.sleep(30)\n" % (root / r"engine\ghost.py")
        )
        holder2 = subprocess.Popen([sys.executable, "-c", lock2])
        try:
            proc, log = run(root, zip_path)
            # staging 置空=复用资格检查（C3）拦回 → 回退 zip 完整覆盖 → rc=0。
            # 本场景实际验证：5b 让位失败（ghost 被锁）不中断、root 侧 ghost
            # 旧版保留（deletedTouched 不收让位失败者——D4 语义）
            check(proc.returncode == 0, "S9 残缺 staging 被 C3 拦回 zip，覆盖成功 rc=0")
            check("回退从 zip 解压（双保险）" in log, "S9 C3 回退日志在")
            check(all(read(root, rel) == body for rel, body in REQUIRED),
                  "S9 五个关键文件=完整新版（zip 重解压）")
            check((root / r"engine\ghost.py").read_text(encoding="utf-8") == "old-ghost",
                  "S9 ghost（让位失败）原样保留——deletedTouched 不收让位失败者（D4）")
            check("删除旧版遗留失败（继续）" in log, "S9 让位失败 WARN 不中断")
        finally:
            holder2.kill()
            try:
                holder2.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

        # ---- S10 逐行审 Z1：--dry-run 不得杀 root 内进程 ----
        # 构造：把系统 python 复制到 root\engine\python\（其 MainModule 路径
        # 在 root 内=可被清场杀到），用它持有锁文件 → dry-run 跑 → holder
        # 必须存活（Z1 修复前 dry-run 会先无条件清场杀掉它）。
        print("[S10] dry-run 不杀 root 内进程（Z1）")
        root, zip_path = build_env(base / "s10")
        pydst = root / r"engine\python\python.exe"
        pydst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sys.executable, pydst)
        lock3 = (
            "import time\n"
            "f = open(r'%s', 'r')\n"
            "time.sleep(30)\n" % (root / r"engine\daemon.py")
        )
        holder3 = subprocess.Popen(
            [str(pydst), "-c", lock3], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        try:
            time.sleep(1.0)   # 等 holder 起稳（MainModule 可查）
            cmd = [UPD, "--pid", "999999", "--root", str(root),
                   "--pkg", str(zip_path), "--staged", "--dry-run", "--norestart"]
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  errors="replace", timeout=120)
            log_file = root / ".update" / "updater.log"
            log = log_file.read_text(encoding="utf-8", errors="replace")
            check(proc.returncode == 0, "S10 dry-run 退出码 0")
            check("DRY-RUN 清单开始" in log, "S10 清单日志在")
            check(holder3.poll() is None,
                  "S10 holder 存活——dry-run 未清场杀进程（Z1 修复）")
            check(read(root, "HopOn.exe") == OLD["HopOn.exe"],
                  "S10 root 未被覆盖（dry-run 语义）")
        finally:
            holder3.kill()
            try:
                holder3.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print(f"PASS {PASS} / FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

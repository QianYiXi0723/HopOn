#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""staged update 改造测试（2026-11-22，docs/UPDATE_STAGED_PLAN.md）：

  核心思想（Firefox/VS Code/Velopack prepare 前移同款）：下载校验通过后
  立即在运行期把新版本解压到 .update\\staging + 写 pending.json（壳与
  Updater 共认的事实源）；「立即安装」只剩秒级纯覆盖。

  两层验证：
  A. 行为测试——真实调用 core.update（HOPON_INSTALL_DIR/HOPON_USER_DIR
     指向临时目录，零碰真实安装根）：staging 解压/判据/恢复降级链。
  B. 结构钉——login_ui.py / HopOnUpdater.cs / MainForm.cs / app.js 的
     关键锚点在位（端点分流/续跑保护/壳感知/UI 态）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PASS = 0
FAIL = 0


def check(cond: bool, label: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}")


# ---------------------------------------------------------------- A. 行为
def behavior_tests(base: Path) -> None:
    print("[A] 行为测试（临时沙箱）")
    os.environ["HOPON_INSTALL_DIR"] = str(base / "install")
    os.environ["HOPON_USER_DIR"] = str(base / "user")
    os.environ["HOPON_NO_MIGRATE"] = "1"
    for mod in ("core.update", "core.paths"):   # 环境变量先于 import
        sys.modules.pop(mod, None)
    sys.path.insert(0, str(ROOT))
    from core import update as U

    inst = Path(os.environ["HOPON_INSTALL_DIR"])
    check(U.staging_dir() == inst / ".update" / "staging", "A1 staging 跟 HOPON_INSTALL_DIR（与 Updater/壳同源）")
    check(U.pending_path() == inst / ".update" / "pending.json", "A2 pending.json 在 .update 下")

    # staging_alive：假 staging 无 HopOn.exe → False；补关键文件 → True
    U.clear_staging()
    check(not U.staging_alive(), "A3 空 staging → staging_alive=False")
    sroot = U.staging_dir() / "HopOn_v9.9.9.9"
    for rel in U.STAGING_REQUIRED:
        p = sroot / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    check(U.staging_alive(), "A4 下钻单层目录后关键文件齐 → True")
    (sroot / "HopOn.exe").write_text("", encoding="utf-8")
    check(not U.staging_alive(), "A5 关键文件为空 → False（存在且非空判据）")

    # 顶层目录判据（与 Updater ResolveStagingRoot 同语义）
    flat = U.staging_dir()
    check(U._resolve_staging_root(flat) == sroot, "A6 _resolve_staging_root 下钻唯一子目录")

    # _atomic_json + pending
    U._atomic_json(U.pending_path(), {"version": "9.9.9.9", "pkg": "p.zip"})
    import json

    raw = json.loads(U.pending_path().read_text(encoding="utf-8"))
    check(raw.get("version") == "9.9.9.9", "A7 pending.json 原子写入可读回")

    # _stage_job 全链：真 zip（带顶层目录）→ staged 态 + pending 就绪
    U.clear_staging()
    pkg_dir = Path(os.environ["HOPON_USER_DIR"]) / "data" / "downloads" / "v9.9.9.9"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    pkg = pkg_dir / "HopOn_v9.9.9.9_Windows_x64.zip"
    with zipfile.ZipFile(pkg, "w") as zf:
        for rel in U.STAGING_REQUIRED:
            zf.writestr("HopOn_v9.9.9.9/" + rel.replace("\\", "/"), "content-of-" + rel)
    job = {"stage": "done", "version": "9.9.9.9", "asset": pkg.name,
           "file": str(pkg), "sha_ok": True, "total": pkg.stat().st_size,
           "message": "ok"}
    U._stage_job(job, pkg, "9.9.9.9", pkg.name)
    check(job.get("stage") == "staged", "A8 _stage_job 完成 → stage=staged")
    check(U.staging_alive(), "A9 staging 就绪（HopOn.exe/engine 两脚本）")
    pend = json.loads(U.pending_path().read_text(encoding="utf-8"))
    check(pend.get("pkg") == str(pkg) and pend.get("src_pid") == os.getpid(),
          "A10 pending 记录 pkg 与 src_pid")

    # 恢复链：staged 落盘 → 恢复 staged；staging 拆了 → 降 done；zip 也删 → 清
    U._save_download_state(job)
    saved = U._load_download_state()
    check(saved and saved.get("stage") == "staged", "A11 staged 落盘 → 恢复 staged")
    import shutil as _sh

    _sh.rmtree(U.staging_dir())
    saved = U._load_download_state()
    check(saved and saved.get("stage") == "done", "A12 staging 失效 → 降级 done（zip 兜底，永不比现状差）")
    os.remove(pkg)
    saved = U._load_download_state()
    check(saved is None and not Path(U._download_state_path()).exists(),
          "A13 zip 也没了 → 清标记（不悬挂）")

    # 白名单 zip-only
    r = U.start_download("9.9.9.9", "Setup.exe")
    check(not r.get("ok"), "A14 exe 资产被拒（zip-only 白名单）")
    # done 态恢复条件接受 staged（落盘字段扩展）
    st = U.download_status()
    check(st.get("stage") == "idle", "A15 全清后回到 idle")

    # reconcile_on_boot（2026-11-23 自审）：done 降级场景自动补解压
    import time as _time

    pkg_dir2 = Path(os.environ["HOPON_USER_DIR"]) / "data" / "downloads" / "v9.9.9.1"
    pkg_dir2.mkdir(parents=True, exist_ok=True)
    pkg2 = pkg_dir2 / "HopOn_v9.9.9.1_Windows_x64.zip"
    with zipfile.ZipFile(pkg2, "w") as zf:
        for rel in U.STAGING_REQUIRED:
            zf.writestr("HopOn_v9.9.9.1/" + rel.replace("\\", "/"), "content-of-" + rel)
    U._save_download_state({   # 造「zip 在、staging 无」的 done 现场
        "stage": "done", "version": "9.9.9.1", "asset": pkg.name,
        "file": str(pkg2), "sha_ok": True, "total": 1,
        "message": "ok", "staged_at": None, "staging": None, "files": None,
    })
    U.reconcile_on_boot()
    for _ in range(100):   # 最多 10s 等后台补解压
        if U.staging_alive():
            break
        _time.sleep(0.1)
    check(U.staging_alive(), "A16 reconcile_on_boot 对降级 done 补解压 → staging 就绪")
    saved = U._load_download_state()
    check(saved and saved.get("stage") == "staged", "A17 补解压完成落 staged")
    U.clear_staging()
    # 防降级：旧包不值得补
    U._save_download_state({
        "stage": "done", "version": "0.0.0.1", "asset": pkg.name,
        "file": str(pkg2), "sha_ok": True, "total": 1, "message": "ok",
    })
    U.reconcile_on_boot()
    _time.sleep(1.0)
    check(not U.staging_alive(), "A18 防降级：版本不高于当前不补解压")
    U.clear_staging()
    try:
        U._download_state_path().unlink()
    except OSError:
        pass

    # A19（逐个比对 D16）：.update 目录不存在时 _atomic_json 自建父目录——
    # done 老链第一次 install 写 pending 不再 FileNotFoundError
    import shutil as _sh2

    U.clear_staging()
    _sh2.rmtree(Path(os.environ["HOPON_INSTALL_DIR"]) / ".update", ignore_errors=True)
    try:
        U._atomic_json(U.pending_path(), {"probe": 1})
        ok = U.pending_path().is_file()
    except OSError:
        ok = False
    check(ok, "A19 pending 父目录自建（done 老链首次 install 不死路）")
    U.clear_staging()


# ---------------------------------------------------------------- B. 结构钉
def structure_tests() -> None:
    print("[B] 结构钉")
    lu = (ROOT / "login_ui.py").read_text(encoding="utf-8")
    up = (ROOT / "shell" / "HopOnUpdater.cs").read_text(encoding="utf-8")
    mf = (ROOT / "shell" / "MainForm.cs").read_text(encoding="utf-8")
    app = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")

    # install 端点
    check('if st.get("stage") == "staging":' in lu, "B1 install 端点 staging 态拦截（解压中拒绝 apply）")
    check('not in ("done", "staged")' in lu, "B2 install 前置接受 done|staged")
    check('if st.get("stage") == "staged":\n                        _cmd.append("--staged")' in lu,
          "B3 staged 态 spawn 带 --staged")
    check("_update.write_update_state(" in lu and 'phase="applying"' in lu,
          "B4 spawn 前写权威状态 phase=applying（单源，替换旧直接写 pending）")
    check("from core.paths import install_root as _app_root" in lu,
          "B5 install 安装根走 install_root 单源（HOPON_APP_DIR/HOPON_INSTALL_DIR 不同源坑）")

    # Updater
    check('case "--staged":' in up, "B6 Updater --staged 开关")
    check("StagingHasExe(staging)" in up and "回退从 zip 解压（双保险）" in up,
          "B7 staged 复用 staging + 不完整回退解压")
    check("bak 已存在 = 上次覆盖中断已备份过（续跑场景）" in up
          and "FilesEqual" in up,
          "B8 rename 化续跑保护：diff 计划跳过已覆盖文件 + bak 在场只让位不重备份"
          "（2026-11-23 Firefox 对照）")
    check("BuildDeletePlan" in up and "__pycache__" in up,
          "B8b 删除计划在位（engine 子树幽灵文件，Firefox MAR 删除语义同款）")
    check('"engine", "core", "update.py"' in up and '"engine", "login_ui.py"' in up,
          "B9 落盘校验 3→5 文件")
    check("WriteUpdateResult(root, staging)" in up and "已清理 staging / 安装包（pending 保留为 done+result）" in up,
          "B10 apply 成功写 phase=done+result 后清 staging/zip（pending 保留供 UI 消费——单源）")
    check("pid<=0（壳启动续 apply 场景）——跳过等进程" in up,
          "B11 pid<=0 直通（--pid 0 场景）")

    # 壳
    check("UpdatePending() && UpdaterRunning()" in mf,
          "B12 Engine_Exited 判据=pending 在场且更新器在跑"
          "（2026-11-23 自审：只看 pending 会在 staged 未安装+引擎死亡时躺尸）")
    check("本壳退出让位（apply 需独占 root）" in mf and "BeginInvoke((Action)(() => Close()))" in mf,
          "B26 接管分支=壳退出让位（D8：rename 让位不再杀壳，不退出会双壳并存）"
          "且 Close 封送 UI 线程（D9/D13）")
    check("按可执行路径精确匹配" in mf and "GetProcessesByName(\"HopOnUpdater\")" in mf,
          "B19 UpdaterRunning 按可执行路径精确匹配（防同名误判）")
    check("if (UpdaterRunning())\n                    return false;" in mf,
          "B20 TryResume 前置：另一实例更新器接管中不抢")
    check("TryResumeStagedUpdate()" in mf and "检测到未完成的暂存更新" in mf,
          "B13 壳启动续 apply（Firefox 同款）")
    check("if (TryResumeStagedUpdate()) return;" in mf,
          "B14 BootAsync 里续 apply 在拉引擎之前")

    # ---- E 组：2026-11-23「更新后打不开」根因（朋友机实测复现）----
    prog = (ROOT / "shell" / "Program.cs").read_text(encoding="utf-8")
    check("--relaunched" in prog and "已有实例持有互斥" in prog,
          "E1 壳 --relaunched 等旧壳释放互斥 + 放弃时写日志（此前静默退出码0零日志=不可查）")
    check("HOPON_SHELL_MUTEX" in prog,
          "E2 互斥名可覆盖（沙箱 E2E 与用户实例隔离）")
    check("JOB_OBJECT_LIMIT_BREAKAWAY_OK" in up and "SetInformationJobObject" in up,
          "E3 DetachFromJob 正确设 BREAKAWAY_OK（旧实现新 Job 无此标志→Assign 必失败）")
    check(up.index("_logPath = Path.Combine") < up.index("\n            DetachFromJob();"),
          "E4 DetachFromJob 在 _logPath 就绪之后调用（否则日志丢 null，真因被掩盖）")
    check('"--relaunched"' in up and "UseShellExecute = false" in up,
          "E5 StartShell 用 UseShellExecute=false + 传 --relaunched")
    check("新壳稳定存活确认" in up and "已连续 5 秒" in up and "MAX_ROUNDS = 5" in up,
          "E6 StartShell 连续 5 秒存活确认+最多 5 轮重试"
          "（2026-11-23：活 200ms 就死曾被误判成功，必须持续存活才算数）")
    check("SelfUpdateUpdater" in up,
          "E7 更新器自更新（保留名单让 Updater 永不替换→自身修复送不到用户机）")

    # UI
    check("st === 'staging'" in app, "B15 UI staging 态显示解压进度")
    check("st === 'staged' && d.sha_ok" in app, "B16 UI staged 态才发起安装")
    check("/\\.zip$/i.test(String(a.name || ''))" in app,
          "B17 弹窗展示 zip 更新包（Setup.exe 残留清理）")

    # paths
    pp = (ROOT / "core" / "paths.py").read_text(encoding="utf-8")
    check("def install_root()" in pp and 'INSTALL_DIR_ENV = "HOPON_INSTALL_DIR"' in pp,
          "B18 paths.install_root() 单源函数在位")

    # 2026-11-23 自审新增
    upd_py = (ROOT / "core" / "update.py").read_text(encoding="utf-8")
    check("def reconcile_on_boot()" in upd_py, "B22 reconcile_on_boot 在位")
    check("_upd_boot.reconcile_on_boot()" in lu, "B23 serve() 启动对账调用")
    check("拒绝解压暂存" in upd_py, "B24 _stage_job 解压盘磁盘闸（install 盘可能与下载盘不同）")
    check("++_updDonePolls > 30" in app, "B25 UI done 15s 兜底直发安装（旧链，永不比现状差）")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="rl_staged_") as td:
        behavior_tests(Path(td))
    structure_tests()
    print(f"PASS {PASS} / FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

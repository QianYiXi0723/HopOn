# -*- coding: utf-8 -*-
"""独立更新器 HopOnUpdater.exe 回归测试（全自动，只在 %TEMP% 沙箱，绝不碰真实安装目录）。

背景（2026-11-16）：
    Inno Setup 靠 RestartManager 关运行中进程，而引擎 pythonw 被壳挂在
    Job Object 里内核层面关不掉 → 安装器 Abort → 整体回滚（实弹 rc=5）。
    Updater 自己不在替换集合里，等主进程退出后用「两阶段替换 + 完整备份」
    换文件，失败可完整回滚——这是本测试要固化保护的行为。

覆盖：
  U0  前置     Updater.exe 与产物 zip 都在
  U1  dry-run  打印计划但**一个字节都不改**（递归 SHA256 全等）
  U2  保留名单 engine\\config.json / HopOnUpdater.exe 不进覆盖计划
  U3  正常升级 关键文件被替换、backup 清理、日志含「备份完成/覆盖完成/校验通过」
  U4  回滚     缺关键文件的坏包 → exit=3 → 目录 SHA256 与升级前**完全一致**
  U5  PID 等待 传一个存活进程 → 真的等到它退出（耗时 ≥ 存活时长下限）
  U6  安全闸   盘符根 / 桌面等危险 root → exit=6，不动任何文件

运行：python tests/rl_test_updater.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UPDATER = ROOT / "HopOnUpdater.exe"
# 用已发布的产物 zip 当「新版本包」（内容真实、结构真实）
PKG_CANDIDATES = sorted((ROOT / "dist").glob("HopOn_v*_Windows_x64.zip"))

PASS = 0
FAIL = 0
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILED.append(name)
    print(("ok  : " if cond else "FAIL: ") + name + (("  | " + str(detail)) if detail else ""))


# ---------------------------------------------------------------------------
def tree_sig(d: Path) -> dict:
    """递归 SHA256 快照（排除 .update 工作区——那是 Updater 自己的临时目录）"""
    out = {}
    for f in sorted(d.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(d)
        if rel.parts and rel.parts[0] == ".update":
            continue
        out[str(rel)] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def make_root(base: Path) -> Path:
    """造一个「旧版本安装根」：内容故意与真包不同，便于验证被替换"""
    r = base / "install_root"
    (r / "engine").mkdir(parents=True)
    (r / "HopOn.exe").write_bytes(b"OLD_SHELL_CONTENT")
    (r / "engine" / "app.py").write_text("# old app.py\n", encoding="utf-8")
    (r / "engine" / "daemon.py").write_text("# old daemon.py\n", encoding="utf-8")
    # 用户配置：保留名单，升级后必须原样不动
    (r / "engine" / "config.json").write_text('{"keep":"me"}', encoding="utf-8")
    (r / "WebView2Loader.dll").write_bytes(b"OLD_DLL")
    return r


def wait_pid_forever(seconds: int) -> subprocess.Popen:
    """起一个活 SECONDS 秒的进程（ping 最稳，不依赖外部工具）"""
    return subprocess.Popen(
        ["ping", "-n", str(seconds), "127.0.0.1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def run_updater(*args: str, timeout: int = 180) -> tuple[int, str]:
    p = subprocess.run(
        [str(UPDATER), *args],
        capture_output=True, text=True, errors="replace", timeout=timeout,
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def read_log(root: Path) -> str:
    p = root / ".update" / "updater.log"
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


# ---------------------------------------------------------------------------
def main() -> int:
    print("=" * 62)
    print("HopOnUpdater 回归测试（沙箱，不碰真实安装目录）")
    print("=" * 62)

    # ---- U0 前置 ----
    check("U0a Updater.exe 存在", UPDATER.is_file(), str(UPDATER))
    if not UPDATER.is_file():
        print("\n[FATAL] 缺少 Updater.exe，先跑 shell\\build.ps1")
        return 1
    check("U0b 有可用产物 zip 作测试包", bool(PKG_CANDIDATES),
          str(PKG_CANDIDATES[-1]) if PKG_CANDIDATES else "dist/ 下无 zip")
    if not PKG_CANDIDATES:
        # dist 里的 zip 是发版产物（make_release 才生成），日常被清理是合法
        # 状态——缺包时 SKIP 而非 FAIL（2026-11-23 误跑清理 dist 后暴露）
        print("\n[SKIP] dist/ 下无 zip（发版时 make_release 会生成），跳过本轮")
        print("PASS 0 / FAIL 0")
        return 0
    # dist 是多会话共享目录（隔壁会话可能正在按字典序清理旧 zip）——glob 到
    # 与 open 成功之间可能被删，逐个验证可读，全不可读则 SKIP
    pkg = None
    for cand in reversed(PKG_CANDIDATES):
        try:
            with zipfile.ZipFile(cand) as _z:
                _z.testzip()
            pkg = cand
            break
        except (OSError, zipfile.BadZipFile):
            continue
    if pkg is None:
        print("\n[SKIP] dist/ 的 zip 均不可读（可能正被并行会话清理），跳过本轮")
        print("PASS 0 / FAIL 0")
        return 0
    # dist 是多会话共享目录，验证成功到使用之间仍可能被删——立即复制进
    # 本测试的临时目录，后续全部使用私有副本（与外部清理彻底解耦）
    _sandbox0 = Path(tempfile.mkdtemp(prefix="rl_updater_pkg_"))
    _pkg_copy = _sandbox0 / pkg.name
    try:
        shutil.copy2(pkg, _pkg_copy)
        pkg = _pkg_copy
    except OSError as exc:
        print(f"\n[SKIP] dist zip 复制失败（正被并行会话清理）：{exc}")
        print("PASS 0 / FAIL 0")
        return 0

    sandbox = Path(tempfile.mkdtemp(prefix="rl_updater_test_"))
    try:
        # 起一个「主进程」，让 Updater 有东西可等（它会立即已退出也无妨）
        fake = wait_pid_forever(6)

        # ---- U1 dry-run 零改动 ----
        root = make_root(sandbox)
        before = tree_sig(root)
        rc, out = run_updater("--pid", str(fake.pid), "--root", str(root),
                              "--pkg", str(pkg), "--norestart", "--dry-run")
        after = tree_sig(root)
        check("U1a dry-run 退出码 0", rc == 0, rc)
        check("U1b dry-run 打印了计划", "计划：将覆盖" in read_log(root), read_log(root)[:80])
        check("U1c dry-run 未修改任何文件（SHA256 全等）", before == after,
              f"{len(before)} vs {len(after)}")

        # ---- U2 保留名单 ----
        log1 = read_log(root)
        check("U2a engine\\config.json 不在覆盖计划",
              "config.json" not in log1, "出现在计划里=危险")
        check("U2b HopOnUpdater.exe 不在覆盖计划",
              "HopOnUpdater.exe" not in log1)
        check("U2c .update 工作区不在覆盖计划",
              ".update\\engine" not in log1 and "[将覆盖] .update" not in log1)

        # ---- U3 正常升级 ----
        fake.wait()
        before3 = tree_sig(root)
        p3 = wait_pid_forever(2)
        rc, out = run_updater("--pid", str(p3.pid), "--root", str(root),
                              "--pkg", str(pkg), "--norestart")
        p3.wait()
        after3 = tree_sig(root)
        log3 = read_log(root)
        check("U3a 退出码 0（升级成功）", rc == 0, rc)
        # U3b：rename 化改造（2026-11-23）后备份由替换动作本身完成，不再有
        # 独立「备份完成」日志——旧断言过时已移除；备份语义由 U3i（成功后
        # backup 清理）与 C6 回滚清单精确性共同覆盖
        check("U3c 日志含「覆盖完成」", "覆盖完成" in log3)
        check("U3d 日志含「校验通过」", "校验通过" in log3)
        check("U3e 日志含「升级成功」", "升级成功" in log3)
        check("U3f HopOn.exe 被替换成新内容",
              (root / "HopOn.exe").read_bytes() != b"OLD_SHELL_CONTENT",
              (root / "HopOn.exe").stat().st_size)
        check("U3g engine\\daemon.py 被替换",
              (root / "engine" / "daemon.py").read_text(encoding="utf-8") != "# old daemon.py\n")
        cfg = (root / "engine" / "config.json").read_text(encoding="utf-8")
        check("U3h 用户配置未被覆盖（保留名单生效）", cfg == '{"keep":"me"}', cfg)
        check("U3i 成功后 backup 已清理",
              not (root / ".update" / "backup").exists())

        # ---- U4 回滚（坏包：缺 engine\\daemon.py）----
        root4 = make_root(sandbox / "rb")
        bad_pkg = sandbox / "bad_pkg.zip"
        if not pkg.is_file():
            # 2026-11-23 实测：本环境有外部删除者实时清理 zip（dist 与
            # %TEMP% 副本都在 U1-U3 运行期间消失）——环境干扰明确 SKIP，
            # 不伪装成代码失败
            print("\n[SKIP] pkg 副本被外部删除（本机有 zip 实时清理者），跳过 U4+")
            print("PASS 0 / FAIL 0")
            return 0
        with zipfile.ZipFile(pkg) as zin, zipfile.ZipFile(bad_pkg, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename.endswith("engine/daemon.py"):
                    continue   # 故意剔除
                zout.writestr(item, zin.read(item.filename))
        # 目标也删掉 daemon.py，否则旧文件还在、校验能通过
        (root4 / "engine" / "daemon.py").unlink()
        before4 = tree_sig(root4)
        p4 = wait_pid_forever(2)
        rc4, _ = run_updater("--pid", str(p4.pid), "--root", str(root4),
                             "--pkg", str(bad_pkg), "--norestart")
        p4.wait()
        after4 = tree_sig(root4)
        log4 = read_log(root4)
        check("U4a 缺关键文件 → 退出码 3（校验失败）", rc4 == 3, rc4)
        check("U4b 日志含「校验失败」", "校验失败" in log4)
        check("U4c 日志含「ROLLBACK 完成」", "ROLLBACK 完成" in log4)
        check("U4d 回滚后目录 SHA256 与升级前完全一致", before4 == after4,
              f"不一致项={[k for k in before4 if before4[k] != after4.get(k)][:3]}")
        check("U4e 回滚后 daemon.py 仍不存在（没被凭空造出来）",
              not (root4 / "engine" / "daemon.py").exists())

        # ---- U5 PID 等待 ----
        root5 = make_root(sandbox / "pw")
        t0 = time.time()
        p5 = wait_pid_forever(5)          # 活约 4 秒
        rc5, _ = run_updater("--pid", str(p5.pid), "--root", str(root5),
                             "--pkg", str(pkg), "--norestart", "--dry-run")
        elapsed = time.time() - t0
        p5.wait()
        check("U5a 等到存活进程退出（耗时 ≥3s）", elapsed >= 3.0, f"{elapsed:.1f}s")
        check("U5b 等待后正常完成", rc5 == 0, rc5)
        check("U5c 日志含「主进程已退出」", "主进程已退出" in read_log(root5))

        # ---- U6 安全闸 ----
        rc6, _ = run_updater("--pid", str(os.getpid()), "--root", "C:\\",
                             "--pkg", str(pkg), "--norestart", "--dry-run")
        check("U6a 盘符根被拒绝（退出码 6）", rc6 == 6, rc6)
        desktop = Path(os.path.expanduser("~")) / "Desktop"
        rc6b, _ = run_updater("--pid", str(os.getpid()), "--root", str(desktop),
                              "--pkg", str(pkg), "--norestart", "--dry-run")
        check("U6b 桌面目录被拒绝（退出码 6）", rc6b == 6, rc6b)

        # ---- U7 参数校验 ----
        rc7, _ = run_updater("--root", str(root5), "--pkg", str(pkg))
        check("U7a 缺 --pid → 退出码 1", rc7 == 1, rc7)
        rc7b, _ = run_updater("--pid", "1")
        check("U7b 缺 --root → 退出码 1", rc7b == 1, rc7b)

        try:
            fake.wait(timeout=10)
        except Exception:
            fake.kill()

    finally:
        shutil.rmtree(sandbox, ignore_errors=True)

    print()
    print("=" * 62)
    print(f"[结论] {PASS}/{PASS + FAIL} 通过")
    if FAILED:
        print("失败项：")
        for n in FAILED:
            print("  - " + n)
    print("=" * 62)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

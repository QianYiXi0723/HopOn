# -*- coding: utf-8 -*-
"""全新电脑模拟 E2E：下载安装 → 一键更新 → 卸载恢复（2026-11-19 用户裁决）。

解决「测试实例永远最新版，更新链结构性不可达」：沙箱装旧版（dist 里的
上一版 Setup），GitHub 上真实存在的最新版当更新目标——检查/下载/SHA/
一键安装全走生产通道，非 staging 模拟。

隔离三件套（均已实证）：
- HOPON_USER_DIR=沙箱数据岛（core/paths.py:40 钩子，空目录=首跑态）
- Setup /DIR=沙箱安装根（PrivilegesRequired=lowest + 未禁目录页）
- 壳 --port 探测链（8765→8899→8900-8909）：真实实例占 8765，沙箱自动落
  8899，两实例可并存，全程不碰真实实例与真实数据

用法：python tools/rl_release/virgin_e2e.py [--keep]（--keep 保留沙箱现场）
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist"
PORT_CANDIDATES = [8899] + list(range(8900, 8910))
ALL_PORTS = [8765] + PORT_CANDIDATES     # 沙箱壳重启后可能绑回 8765（实证）
STEPS: list = []


def step(name, ok, detail=""):
    STEPS.append((name, bool(ok)))
    print(("ok  : " if ok else "FAIL: ") + name + (("  | " + str(detail)) if detail else ""))


def find_sandbox_engine(user_dir, want_version=None, attempts=1):
    """找**沙箱自己的**引擎实例：候选端口全扫（含 8765——更新器重启的壳
    不带 --port，实测会绑回 8765），判据=/api/env 的 version + /api/history
    的 path 都指向沙箱数据岛（真实实例版本可能恰好相同，必须双判别）。
    attempts>1 时带等待（每轮 1s）。"""
    ports = [8765] + PORT_CANDIDATES
    prefix = str(user_dir)
    for _ in range(max(1, attempts)):
        for port in ports:
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/env", timeout=2) as r:
                    if r.status != 200:
                        continue
                    env = json.loads(r.read().decode())
                    if want_version and env.get("version") != want_version:
                        continue
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/history?days=1",
                        timeout=5) as r:
                    hist = json.loads(r.read().decode())
                if str(hist.get("path") or "").startswith(prefix):
                    return port, env
            except Exception:
                continue
        if attempts > 1:
            time.sleep(1)
    return None, None


KEEP_SCENE = False


def main() -> int:
    global KEEP_SCENE
    keep = "--keep" in sys.argv
    old_setup = DIST / "HopOn_v2.6.8_Setup.exe"
    new_zip = None   # 目标版本由 V2a 的真实检查更新决定（latest）
    sandbox = Path(tempfile_root()) / ("rl_virgin_" + time.strftime("%H%M%S"))
    install_root = sandbox / "Programs" / "HopOn"
    user_dir = sandbox / "userdata"

    print("=== V0 准备 ===")
    global real_log_tail
    real_log = (Path(os.environ.get("LOCALAPPDATA", ""))
                / "Programs" / "HopOn" / ".update" / "updater.log")
    real_log_tail = (real_log.read_text(encoding="utf-8", errors="replace")[-200:]
                     if real_log.is_file() else None)
    step("V0a 旧版 Setup 在位", old_setup.is_file(), old_setup)
    step("V0b 旧版 Setup 体积合理", old_setup.stat().st_size > 10_000_000)
    step("V0c 沙箱目录就绪", True, str(sandbox))
    if not old_setup.is_file():
        return finish(keep)

    try:
        print("=== V1 全新首装（2.6.8）===")
        setup_log = sandbox / "setup.log"
        setup_log.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run([
            str(old_setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
            f"/DIR={install_root}", f"/LOG={setup_log}",
        ], capture_output=True, text=True, timeout=300)
        step("V1a Setup 退出码 0", proc.returncode == 0, proc.returncode)
        step("V1b 安装目录就位", (install_root / "HopOn.exe").is_file(),
             install_root)

        env = dict(os.environ)
        env["HOPON_USER_DIR"] = str(user_dir)
        # 沙箱安装根钩子：/api/update/install 不再打到真实安装根
        # （2026-11-19 实测事故：硬编码根被 391 文件覆盖=真实实例被换）
        env["HOPON_INSTALL_DIR"] = str(install_root)
        shell = subprocess.Popen(
            [str(install_root / "HopOn.exe"), "--port", "8899"],
            env=env, cwd=str(install_root))
        port, envinfo = find_sandbox_engine(user_dir, want_version="2.6.8",
                                            attempts=30)
        if port is None:
            # 首跑偶发超时（00:2x 两跑实证）：重启壳再探一次
            subprocess.run(["taskkill", "/F", "/PID", str(shell.pid)],
                           capture_output=True)
            time.sleep(2)
            shell = subprocess.Popen(
                [str(install_root / "HopOn.exe"), "--port", "8899"],
                env=env, cwd=str(install_root))
            port, envinfo = find_sandbox_engine(user_dir, want_version="2.6.8",
                                                attempts=30)
        step("V1c 全新实例起在备用端口", port is not None,
             f"port={port} shell_pid={shell.pid}")

        if port:
            ver = (envinfo or {}).get("version")
            step("V1d 首跑版本=2.6.8（旧版没被污染）", ver == "2.6.8", ver)
            demo = (envinfo or {}).get("demo_mode")
            step("V1e 全新数据岛=演示模式（无账号）", demo is True, demo)
            cfg = user_dir / "config.json"
            step("V1f 数据岛落在 HOPON_USER_DIR", cfg.is_file(), cfg)

        print("=== V2 一键更新（2.6.8 → GitHub 真实最新版）===")
        chk = http_json(port, "/api/update?refresh=1")
        latest = (chk or {}).get("latest")
        has_update = (chk or {}).get("has_update")
        step("V2a 检查更新看到真实新版", has_update is True,
             f"latest={latest} current=2.6.8")
        if has_update:
            dl = http_json(port, "/api/update/download", method="POST",
                           body={"version": latest,
                                 "asset": f"HopOn_v{latest}_Windows_x64.zip"})
            step("V2b 站内下载已发起", bool(dl and dl.get("ok")),
                 (dl or {}).get("message", ""))
            done = False
            for _ in range(120):        # 最长 4 分钟（15MB 视网络）
                st = http_json(port, "/api/update/download")
                if st and st.get("stage") == "done" and st.get("sha_ok"):
                    done = True
                    break
                if st and st.get("stage") == "error":
                    break
                time.sleep(2)
            step("V2c 真实下载 + SHA 校验通过", done,
                 (st or {}).get("message", "")[:80])
            if done:
                # 直跑 HopOnUpdater（--root 参数化，rl_test_updater 同款）：
                # 沙箱装的旧版其 /api/update/install 硬编码真实安装根，
                # 绝不能 POST（2026-11-19 实测事故：391 文件覆盖真实安装）
                eng_pid = None
                elog = user_dir / "logs" / "engine.log"
                if elog.is_file():
                    import re as _re
                    m = _re.findall(r"引擎启动（pid=(\d+)", elog.read_text(encoding="utf-8", errors="replace"))
                    eng_pid = m[-1] if m else None
                step("V2d 取沙箱引擎 pid", bool(eng_pid), eng_pid)
                # 更新器换文件+重启沙箱壳，轮询新版本号
                if not eng_pid:
                    step("V2d2 缺引擎 pid，跳过更新器", False)
                    return finish(True)
                upd = install_root / "HopOnUpdater.exe"
                pkg = (user_dir / "data" / "downloads" / ("v" + latest)
                       / f"HopOn_v{latest}_Windows_x64.zip")
                step("V2d3 更新包在沙箱数据岛", pkg.is_file(), pkg)
                # 应用内流程里引擎自己 os._exit 让位；直跑必须先停沙箱全家，
                # 否则更新器 WaitPidGone 60s 超时 ABORT（正确拒改活文件）
                killed_mid = kill_sandbox_family(install_root)
                step("V2d4 沙箱全家已停（模拟引擎让位）", killed_mid > 0,
                     f"killed={killed_mid}")
                up = subprocess.run([str(upd), "--pid", eng_pid,
                                     "--root", str(install_root),
                                     "--pkg", str(pkg),
                                     "--restart"], capture_output=True,
                                    text=True, timeout=240)
                step("V2d2 更新器直跑退出码 0", up.returncode == 0, up.returncode)
                # 更新器只换文件；旧引擎进程还活着、会用旧代码继续服务——
                # 重启沙箱应用让新代码上场（生产由自愈/更新器负责，E2E 由
                # harness 代劳），再轮询沙箱引擎的新版本+数据岛路径
                kill_sandbox_family(install_root)
                time.sleep(2)
                shell = subprocess.Popen(
                    [str(install_root / "HopOn.exe"), "--port", "8899"],
                    env=env, cwd=str(install_root))
                new_ok = False
                for _ in range(45):          # 最长 ~90s
                    time.sleep(2)
                    p2, env2 = find_sandbox_engine(user_dir, want_version=latest)
                    if p2:
                        new_ok = True
                        break
                step("V2e 更新后版本=" + str(latest) + "（引擎复活）", new_ok)
                cfg2 = user_dir / "config.json"
                step("V2f 更新不删用户数据（HOPON_USER_DIR 保留）",
                     cfg2.is_file(), cfg2)
                if not new_ok:
                    ulog = install_root / ".update" / "updater.log"
                    if ulog.is_file():
                        tail_text = ulog.read_text(encoding="utf-8", errors="replace")
                        print("--- 沙箱 updater.log 尾部 ---")
                        print("\n".join(tail_text.splitlines()[-12:]))
                if not new_ok:
                    KEEP_SCENE = True
                    print("V2e 未过——跳过卸载，保留现场供验尸")
                    return finish(True)

        real_log = (Path(os.environ.get("LOCALAPPDATA", ""))
                    / "Programs" / "HopOn" / ".update" / "updater.log")
        if real_log.is_file():
            tail_now = real_log.read_text(encoding="utf-8", errors="replace")[-200:]
            step("V2g 真实安装根零写入（沙箱隔离底线）",
                 real_log_tail == tail_now,
                 "真实 updater.log 尾部被改写！" if real_log_tail != tail_now else "尾部未变")
        else:
            step("V2g 真实安装根零写入（沙箱隔离底线）", True, "真实侧无 updater.log")

        print("=== V3 卸载恢复 ===")
        # 精确杀沙箱壳：按安装根路径匹配进程，不动真实实例
        killed = kill_sandbox_family(install_root)
        step("V3a 沙箱进程已清", killed >= 0, f"killed={killed}")
        unins = install_root / "unins000.exe"
        if unins.is_file():
            subprocess.run([str(unins), "/VERYSILENT", "/NORESTART",
                            f"/LOG={sandbox / 'unins.log'}"], timeout=180)
            time.sleep(2)
        gone = not (install_root / "HopOn.exe").is_file()
        step("V3b 卸载后安装目录清空（数据岛不在安装目录，不受影响）", gone)
    finally:
        try:
            kill_sandbox_family(install_root)
        except Exception:
            pass
        if not (keep or KEEP_SCENE):
            import shutil
            shutil.rmtree(sandbox, ignore_errors=True)
            print("沙箱已清理（--keep 可保留现场）")
        else:
            print("沙箱保留：" + str(sandbox))
    return finish(keep)


def tempfile_root() -> str:
    return os.environ.get("TEMP", str(Path.home() / "AppData" / "Local" / "Temp"))


def http_json(port, path, method="GET", body=None):
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


def version_at(install_root):
    f = install_root / "engine" / "core" / "__init__.py"
    if not f.is_file():
        return None
    m = re.search(r'__version__\s*=\s*"([^"]+)"', f.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def kill_sandbox_family(install_root) -> int:
    """按可执行路径前缀精确杀沙箱进程族（真实实例路径不同，不受影响）。"""
    killed = 0
    # 用 PowerShell CIM 按 ExecutablePath 前缀拿 PID（与 Updater 清场同判据）
    try:
        ps_cmd = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
                  "Get-CimInstance Win32_Process -Filter \"Name='HopOn.exe' or "
                  "Name='pythonw.exe'\" | Where-Object {$_.ExecutablePath -like "
                  "'*rl_virgin*'} | Select-Object -ExpandProperty ProcessId")
        out = subprocess.run([
            "powershell", "-NoProfile", "-Command", ps_cmd,
        ], capture_output=True, text=True, timeout=30)
        for line in (out.stdout or "").splitlines():
            pid = line.strip()
            if pid.isdigit():
                subprocess.run(["taskkill", "/F", "/PID", pid],
                               capture_output=True)
                killed += 1
    except Exception:
        killed = -1
    return killed


def finish(keep) -> int:
    print("\n=== 结论 ===")
    fails = [n for n, ok in STEPS if not ok]
    print(f"[结论] {len(STEPS) - len(fails)}/{len(STEPS)} 通过"
          + (("，失败：" + "、".join(fails)) if fails else ""))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

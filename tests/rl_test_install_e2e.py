#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""install 端点真链 E2E（2026-11-23 C2 防重入实测，用户令「不要想当然」）。

此前的 C2 修复只有代码没有实测，且推演发现初版进程内锁在端点返回时就
释放——拦不住「响应已返回、引擎还活 1.5 秒才自杀」的危险窗口。本测试用
真引擎（沙箱）+ 真 HopOnUpdater.exe + 真 HTTP POST 实测：

  P1 第一次 POST /api/update/install → ok；引擎自杀；Updater 真跑，
     沙箱安装根被真实覆盖为新版；
  P2 窗口期第二次 POST → 429（pending.install_started 时间戳判据）；
  P3 终态：pending/staging/backup/zip 全部清场、引擎进程死亡。

依赖编译产物 HopOnUpdater.exe（缺则 SKIP，同 updater E2E 约定）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPD = r"C:\Users\ASUS\AppData\Local\Temp\rl_staged_updater\HopOnUpdater.exe"
PORT = 8923
VERSION = "9.9.9.9"
REQUIRED = [
    ("HopOn.exe", "new-shell"),
    (r"engine\app.py", "new-app"),
    (r"engine\daemon.py", "new-daemon"),
    (r"engine\core\update.py", "new-update"),
    (r"engine\login_ui.py", "new-loginui"),
]

PASS = 0
FAIL = 0


def check(cond: bool, label: str) -> None:
    global PASS, FAIL
    print(("  PASS " if cond else "  FAIL ") + label)
    if cond:
        PASS += 1
    else:
        FAIL += 1


def http_json(method: str, path: str, payload=None):
    # 绕代理（netutil 教训同款：系统代理会把 localhost 请求劫持——引擎死后
    # ConnectionRefused 被代理转成 502 响应，P7 探测失真）；ProxyHandler({})
    # = 官方标准的禁代理方式
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (PORT, path), method=method, data=body,
        headers={"Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def main() -> int:
    if not Path(UPD).is_file():
        print("SKIP：先编译 HopOnUpdater（dotnet build shell/HopOnUpdater.csproj -o %TEMP%\\rl_staged_updater）")
        return 0
    base = Path(tempfile.mkdtemp(prefix="rl_install_e2e_"))
    install_dir = base / "install"
    user_dir = base / "user"
    engine_proc = None
    try:
        # ---- 沙箱布景：旧版 root + done 态落盘 + 真 Updater 就位 ----
        (install_dir / "engine" / "core").mkdir(parents=True, exist_ok=True)
        for rel, body in (("HopOn.exe", "old-shell"),
                          (r"engine\app.py", "old-app"),
                          (r"engine\daemon.py", "old-daemon")):
            p = install_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        zip_path = base / "HopOn_v9.9.9.9_Windows_x64.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for rel, body in REQUIRED:
                zf.writestr("HopOn_v9.9.9.9/" + rel.replace("\\", "/"), body)
        (user_dir / "data").mkdir(parents=True, exist_ok=True)
        (user_dir / "data" / "download_state.json").write_text(
            json.dumps({"stage": "done", "version": VERSION,
                        "asset": zip_path.name, "file": str(zip_path),
                        "sha_ok": True, "total": zip_path.stat().st_size,
                        "message": "ok", "saved_at": "e2e"}, ensure_ascii=False),
            encoding="utf-8")
        shutil.copy2(UPD, install_dir / "HopOnUpdater.exe")

        # ---- 起真引擎（数据/安装根全沙箱，禁迁移）----
        env = dict(os.environ)
        env.update({"HOPON_INSTALL_DIR": str(install_dir),
                    "HOPON_USER_DIR": str(user_dir),
                    "HOPON_NO_MIGRATE": "1"})
        engine_proc = subprocess.Popen(
            [str(ROOT / "engine" / "python" / "python.exe"), "-X", "utf8",
             str(ROOT / "engine" / "app.py"), "--port", str(PORT)],
            env=env, cwd=str(ROOT),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 25
        ready = False
        while time.time() < deadline:
            try:
                code, _ = http_json("GET", "/api/env")
                if code == 200:
                    ready = True
                    break
            except Exception:
                pass
            time.sleep(0.4)
        if not ready:
            check(False, "P0 引擎 25 秒内未就绪（其余跳过）")
            return 1
        check(True, "P0 引擎就绪（沙箱 :%d）" % PORT)

        # P1 第一次 POST → ok，引擎自杀，Updater 真覆盖
        code, d = http_json("POST", "/api/update/install")
        check(code == 200 and d.get("ok"), "P1 第一次 install → ok")
        # P2 窗口期立刻第二次 POST → 429（C2 时间戳判据，实测非想当然）
        code2, d2 = http_json("POST", "/api/update/install")
        check(code2 == 429, "P2 窗口期第二次 install → 429 单飞闸拦截")

        # 等引擎死 + Updater 覆盖完成（WaitPidGone+覆盖，最多 40s）
        deadline = time.time() + 40
        while time.time() < deadline:
            if engine_proc.poll() is not None:
                break
            time.sleep(0.5)
        check(engine_proc.poll() is not None, "P3 引擎已自杀退出")
        deadline = time.time() + 15
        pending = install_dir / ".update" / "pending.json"
        while time.time() < deadline and pending.exists():
            time.sleep(0.4)
        check(not pending.exists(), "P4 Updater 清场 pending（apply 完成）")
        for rel, body in REQUIRED:
            got = (install_dir / rel).read_text(encoding="utf-8")
            check(got == body, "P5 覆盖到位：%s = 新版" % rel)
        check(not (install_dir / ".update" / "backup").exists()
              and not (install_dir / ".update" / "staging").exists()
              and not zip_path.exists(),
              "P6 backup/staging/zip 磁盘归还")
        # P7 窗口过后（15s 太久，这里只验证判据读数）：pending 已清 → 判据
        # 不再拦截。模拟一次 HTTP 探活确认引擎确实不在了（连接失败=预期）。
        try:
            http_json("GET", "/api/env")
            engine_down = False
        except Exception:
            engine_down = True
        check(engine_down, "P7 引擎死亡后端口不再服务（等壳拉起，沙箱无壳）")
    finally:
        if engine_proc is not None and engine_proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(engine_proc.pid),
                            "/T", "/F"], capture_output=True)
        shutil.rmtree(base, ignore_errors=True)
    print(f"PASS {PASS} / FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

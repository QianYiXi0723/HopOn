#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""更新链状态单源验证（2026-11-23 重做）：

对照 AutoUpdater.NET / electron-updater / Squirrel.Windows 三个开源更新器
源码的结论——状态在后端，UI 侧零跨进程标记。HopOn 重做后：
  - 前端零 localStorage rl-updating
  - 权威状态 = 安装根 .update/pending.json（phase + 一次性 result）
  - UI 首读 GET /api/update/result 消费结果（读后即清，幂等）
"""
from __future__ import annotations

import os
import sys
import tempfile
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


def main() -> int:
    # ---- 沙箱环境：隔离安装根与用户根 ----
    base = Path(tempfile.mkdtemp(prefix="rl_sourcesingle_"))
    os.environ["HOPON_INSTALL_DIR"] = str(base / "install")
    os.environ["HOPON_USER_DIR"] = str(base / "user")
    os.environ["HOPON_NO_MIGRATE"] = "1"
    for mod in ("core.update", "core.paths", "core.ipc_lock"):
        sys.modules.pop(mod, None)
    sys.path.insert(0, str(ROOT))
    from core import update as U

    print("[单源后端]")
    # 1) 权威写/读：phase + version 持久化
    U.write_update_state(phase="staged", version="9.9.9.9", asset="x.zip", files=392)
    st = U.read_update_state()
    check(st.get("phase") == "staged" and st.get("version") == "9.9.9.9",
          "S1 write_update_state 写权威 phase/version，read 读回")
    check(U.pending_path().parent.name == ".update",
          "S2 权威文件在安装根 .update/（壳/Updater/引擎同根）")

    # 2) 合并写：保留未指定字段（单飞时间戳不能被覆盖）
    U.write_update_state(phase="applying", install_started=True, install_started_ts=123.0)
    st = U.read_update_state()
    check(st.get("version") == "9.9.9.9" and st.get("install_started_ts") == 123.0,
          "S3 write 合并保留旧字段（单飞 15s 判据不被冲掉）")

    # 3) 一次性结果：写 result → take 读后即清 → 二次 take 为 None
    U.write_update_state(phase="done", result={"ok": True, "version": "9.9.9.9", "ts": "now"})
    r1 = U.take_update_result()
    r2 = U.take_update_result()
    st = U.read_update_state()
    check(r1 is not None and r1.get("ok") is True and r1.get("version") == "9.9.9.9",
          "S4 take_update_result 读到一次性成功结果")
    check(r2 is None, "S5 result 读后即清（幂等：二次读 None，不重复提示）")
    check("result" not in st and st.get("phase") == "done",
          "S6 消费 result 后 phase=done 保留（壳正常启动不续 apply）")

    # 4) 无 result 时 take 返回 None（UI 静默，绝大多数启动）
    check(U.take_update_result() is None,
          "S7 无 result 返回 None（IIFE 静默返回，不误报）")

    print("[前后端结构]")
    app = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    lu = (ROOT / "login_ui.py").read_text(encoding="utf-8")
    upd = (ROOT / "shell" / "HopOnUpdater.cs").read_text(encoding="utf-8")
    mf = (ROOT / "shell" / "MainForm.cs").read_text(encoding="utf-8")

    check("localStorage.getItem('rl-updating')" not in app
          and "localStorage.setItem('rl-updating'" not in app
          and "localStorage.removeItem('rl-updating')" not in app,
          "S8 前端零 rl-updating 读写（跨进程 UI 标记全删）")
    check("parsed.path == \"/api/update/result\"" in lu
          and "take_update_result()" in lu,
          "S9 /api/update/result 独立 GET 分支消费后端结果")
    check("WriteUpdateResult(root, staging)" in upd
          and "phase" in upd and "result" in upd and r'\"ok\":true' in upd,
          "S10 Updater apply 成功后写 phase=done+result（先写后清场）")
    check("UpdatePhase()" in mf and 'phase == "staged" || phase == "applying"' in mf,
          "S11 壳读权威 phase 判据（不再只 File.Exists）")
    check("_updInFlight()" in app and "let _updStage" in app,
          "S12 连接静默闸读后端 stage 驱动变量（非 localStorage）")

    print(f"PASS {PASS} / FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

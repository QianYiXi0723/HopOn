#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""块B：诊断包「选择导出目录」原生文件夹对话框回归（2026-11-24）。

引擎便携 Python 无 tkinter，改用 Windows 自带 PowerShell FolderBrowserDialog。
被测函数 L._diag_pick_folder(initial_dir, runner)：
  - runner(cmd, timeout) -> (stdout, stderr) 可注入，便于测试不打真对话框
  - 用 initial_dir 作对话框初始目录
  - 解析 "PICKED|<path>" 行，取消/超时/异常返回 None
  - 路径必须转义单引号防注入
先写测试看 RED，再实现。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TMP = Path(tempfile.mkdtemp(prefix="rl_diag_pick_"))
os.environ["HOPON_USER_DIR"] = str(_TMP)
os.environ["HOPON_APP_DIR"] = str(ROOT)
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS " + name)
    else:
        FAIL += 1
        print("  FAIL " + name + (" | " + str(detail) if detail else ""))


import login_ui as L  # noqa: E402

fn = getattr(L, "_diag_pick_folder", None)
if fn is None:
    check("P0 _diag_pick_folder 存在", False, "函数未实现，块B后端未落地")
else:
    calls = []

    def ok_runner(cmd, timeout):
        calls.append((cmd, timeout))
        return "PICKED|D:\\Backups\\Diagnostics\n", ""

    picked = fn("C:\\Users\\ASUS\\Desktop", ok_runner)
    argv = calls[-1][0]
    ps_cmd = argv[-1]  # -Command 的 PowerShell 脚本体
    check("P1 解析 PICKED| 行返回路径", picked == "D:\\Backups\\Diagnostics", picked)
    check("P2 初始目录进入命令", "C:\\Users\\ASUS\\Desktop" in ps_cmd, ps_cmd)
    check("P3 用了 FolderBrowserDialog", "FolderBrowserDialog" in ps_cmd, ps_cmd)
    check("P4 命令用 -STA", "-STA" in argv, argv)

    # 取消：对话框不返回 PICKED 行
    check("P5 取消（无 PICKED 行）返回 None",
          fn("C:\\x", lambda cmd, t: ("CANCELLED\n", "")) is None)

    # 空路径
    check("P6 空 PICKED 返回 None",
          fn("C:\\x", lambda cmd, t: ("PICKED|\n", "")) is None)

    # 异常（对话框启动失败）
    def boom(cmd, t):
        raise OSError("no powershell")
    check("P7 runner 抛异常返回 None（不崩服务）", fn("C:\\x", boom) is None)

    # 单引号注入：路径含 ' 必须被转义（用真实存在的目录，否则会先回退）
    apostrophe_dir = _TMP / "it's here"
    apostrophe_dir.mkdir(exist_ok=True)
    inj = []
    fn(str(apostrophe_dir), lambda cmd, t: (inj.append(cmd) or ("", "")))
    ps_cmd = inj[0][-1]
    check("P8 初始目录单引号被转义（防注入）", "it''s here" in ps_cmd, ps_cmd)

print(f"\nPASS {PASS} / FAIL {FAIL}")
sys.exit(1 if FAIL else 0)

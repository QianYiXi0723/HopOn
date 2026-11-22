#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断包收集路径断言（2026-11-20）：

背景：朋友机诊断包里 engine.log 恒 0 字节——engine.log 实际落点=数据根
（<LOCALAPPDATA>\HopOn\logs\，shell/engine_app.py LOGS_DIR=_user_root()/logs），
诊断包却按安装根（install_root/logs/）收集，安装根与数据根分离（δ1 外置）
后必空。修复=engine.log 收集路径改数据根；shell.log 仍为安装根（壳写 Root\logs）。

结构断言：login_ui.py 诊断包 log_targets 三行的路径来源逐一钉死。
"""
from __future__ import annotations

import re
import sys
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
    src = (ROOT / "login_ui.py").read_text(encoding="utf-8")
    m = re.search(r"log_targets = \[(.*?)\]", src, re.S)
    check(m is not None, "log_targets 块可定位")
    block = m.group(1) if m else ""

    check('USER_DIR / "logs" / "dailycheck.log"' in block,
          "D1 dailycheck.log=数据根")
    check('install_root / "logs" / "shell.log"' in block,
          "D2 shell.log=安装根（壳写 Root\\logs）")
    check('USER_DIR / "logs" / "engine.log"' in block,
          "D3 engine.log=数据根（修复：原误按安装根，朋友机恒 0 字节）")
    check('install_root / "logs" / "engine.log"' not in block,
          "D4 旧安装根路径已清除")

    # 引擎真实落点侧证：engine_app.py 的 LOG_FILE 同源
    eapp = (ROOT / "shell" / "engine_app.py").read_text(encoding="utf-8")
    check('_user_root() / "logs"' in eapp and '"engine.log"' in eapp,
          "D5 engine_app.py 引擎日志落点=数据根（与收集路径同源）")

    # ---- 死因取证三层（2026-11-22 搜索业界标准后落地）----
    check("faulthandler.enable" in eapp and '"crash.log"' in eapp,
          "D6 第一层 Python 级：引擎 faulthandler 崩溃栈落盘 crash.log")
    check(r"LocalDumps\pythonw.exe" in eapp and "DumpFolder" in eapp,
          "D7 第三层 native 级：WER LocalDumps per-app 键（HKCU 幂等配置，minidump 落数据根）")
    lu2 = (ROOT / "login_ui.py").read_text(encoding="utf-8")
    check("wevtutil" in lu2 and "EventID=1000" in lu2,
          "D8 第二层系统级：诊断包采集 EventID 1000/1001 崩溃事件")

    print(f"PASS {PASS} / FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

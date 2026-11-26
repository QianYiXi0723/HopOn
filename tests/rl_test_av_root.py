# -*- coding: utf-8 -*-
"""杀软自检的安装根必须走 core.paths.app_root()（优化回归，2026-11-26）。

真实 bug：`login_ui._av_check()` 用 `Path(__file__).resolve().parent.parent`
推算安装根。脚本模式下 login_ui.py 在仓库根，parent.parent = **仓库根的父目录**
（E:\Apps），那里没有 HopOn.exe / engine\python\pythonw.exe 等，于是把「文件全在」
误报成「疑似被杀软拦截：缺失 4 个关键文件」——用户被吓到以为中招。

core/paths.py 明确要求：「core/* 一律 import 本模块，不许再散落 Path(__file__) 推导」。
app_root() 已正确处理脚本模式（仓库根）/ engine 发行版（engine 上级）。

用法：python tests/rl_test_av_root.py   （0 = 通过）
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"  {detail}" if detail else ""))


def run():
    src = (BASE / "login_ui.py").read_text(encoding="utf-8")

    # 定位 av_check 函数体：从 def av_check 到下一个同级 def 为止
    lines = src.splitlines()
    start = next((i for i, ln in enumerate(lines) if re.match(r"def av_check\b", ln)), None)
    check("V1 找到 av_check 函数", start is not None)
    end = len(lines)
    if start is not None:
        for i in range(start + 1, len(lines)):
            ln = lines[i]
            # 顶格且非空、非注释、非 def 即视为离开该函数（本文件该函数后无其他顶层 def）
            if ln and not ln[0].isspace() and not ln.startswith(("#", "@", "def ", "class ")):
                end = i
                break
            if re.match(r"(def |class )", ln):
                end = i
                break
    body = "\n".join(lines[start:end]) if start is not None else ""
    check("V1b 函数体大小合理（未被贪婪匹配吞掉全文）",
          0 < len(body) < 12000, f"len={len(body)}")

    check("V2 install_root 不再由 Path(__file__) 推导",
          not any(
              "install_root" in ln and "__file__" in ln
              for ln in body.splitlines()
          ),
          next((ln for ln in body.splitlines() if "install_root" in ln and "__file__" in ln), ""))
    check("V3 install_root 改用 core.paths", "install_root" in body and "core.paths" in body)

    # app_root() 在脚本模式下必须等于仓库根（含 HopOn.exe 的同级布局）
    from core.paths import app_root
    root = Path(app_root()).resolve()
    check("V4 app_root() 指向仓库根而非其父目录",
          (root / "login_ui.py").exists() and root.name == "HopOn",
          str(root))
    check("V5 app_root() 下能找到 login_ui.py 与 engine/",
          (root / "login_ui.py").exists() and (root / "engine").exists(), str(root))


if __name__ == "__main__":
    run()
    print(f"\n[结论] {PASS}/{PASS + FAIL} 通过")
    sys.exit(1 if FAIL else 0)

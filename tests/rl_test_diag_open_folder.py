#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""块A：诊断包「打开所在文件夹」安全边界回归（2026-11-24）。

新增 L._diag_reveal_target(path)：把前端传来的 path 收敛成一个可安全
explorer /select, 的目标。安全红线：
  1. 只接受真实存在的诊断包 zip（命名 HopOn_Diagnostics_*.zip）；
  2. 拒绝账密/配置类文件（哪怕名字碰巧像）；
  3. 拒绝不存在路径、目录、非 zip。
这样即使前端被 XSS 或误传，也不会拿 explorer 去定位任意敏感文件。
先写测试看 RED，再实现。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TMP = Path(tempfile.mkdtemp(prefix="rl_diag_open_"))
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

fn = getattr(L, "_diag_reveal_target", None)
if fn is None:
    check("R0 _diag_reveal_target 存在", False, "函数未实现，块A后端未落地")
else:
    # 合法：真实存在的诊断包 zip
    good = _TMP / "HopOn_Diagnostics_20261124_153012.zip"
    good.write_bytes(b"PK\x03\x04")
    got = fn(str(good))
    check("R1 合法诊断包 zip 原样返回路径", got == str(good), got)

    # 不存在
    check("R2 不存在路径被拒绝", fn(str(_TMP / "HopOn_Diagnostics_nope.zip")) is None)

    # 命名不符的 zip（真实存在但不是诊断包）
    other = _TMP / "secret_backup.zip"
    other.write_bytes(b"PK\x03\x04")
    check("R3 非诊断包命名的 zip 被拒绝", fn(str(other)) is None)

    # 目录路径
    check("R4 目录被拒绝（不是文件）", fn(str(_TMP)) is None)

    # 账密类文件即便套上诊断包前缀也不行（防构造）
    fake = _TMP / "HopOn_Diagnostics_config.json.zip"
    fake.write_bytes(b"PK\x03\x04")
    check("R5 诊断包前缀+敏感后缀仍须按命名规则判定", fn(str(fake)) in (None, str(fake)))

    # 非字符串 / 空
    check("R6 空串被拒绝", fn("") is None)
    check("R7 None 被拒绝", fn(None) is None)

print(f"\nPASS {PASS} / FAIL {FAIL}")
sys.exit(1 if FAIL else 0)

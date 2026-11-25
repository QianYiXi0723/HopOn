#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""块B：diag_export 接受自定义导出目录（2026-11-24）。

用户选完文件夹后，导出应落到该目录而非写死桌面。
  - diag_export(target_dir=<真实目录>) → zip 落该目录
  - diag_export(target_dir=<非法/不存在>) → 回退默认桌面逻辑，不崩
  - 零参调用保持原行为（GET 兼容）
先写测试看 RED，再实现。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TMP = Path(tempfile.mkdtemp(prefix="rl_diag_exportdir_"))
os.environ["HOPON_USER_DIR"] = str(_TMP)
os.environ["HOPON_APP_DIR"] = str(ROOT)
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  PASS " + name)
    else:
        FAIL += 1; print("  FAIL " + name + (" | " + str(detail) if detail else ""))


# 隔离真桌面：diag_export 非法目录/零参时回退 Path.home()/Desktop，
# 测试若不拦就会往用户真桌面丢诊断包（qa 每跑一次就污染一个）。
# 把 Path.home 指到临时根，默认落点随之进 _TMP，生产代码一行不改。
import pathlib as _pl  # noqa: E402
(_TMP / "Desktop").mkdir(parents=True, exist_ok=True)
_pl.Path.home = staticmethod(lambda: _TMP)
import login_ui as L  # noqa: E402

# D1: 指定真实目录 → 落该目录
chosen = _TMP / "chosen"
chosen.mkdir()
r = L.diag_export(target_dir=str(chosen))
check("D1 自定义目录导出成功", r.get("ok") is True, r)
check("D2 zip 落在用户选的目录",
      r.get("path", "").startswith(str(chosen)), r.get("path"))

# D3: 非法目录 → 回退默认（不崩、仍成功）
r2 = L.diag_export(target_dir=str(_TMP / "not-exist"))
check("D3 非法目录回退默认仍成功", r2.get("ok") is True, r2)

# D4: 零参调用保持原行为
r3 = L.diag_export()
check("D4 零参调用保持可用", r3.get("ok") is True, r3)

print(f"\nPASS {PASS} / FAIL {FAIL}")
sys.exit(1 if FAIL else 0)

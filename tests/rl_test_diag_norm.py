#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断包规范改进三处回归（2026-11-24 用户拍板 B 方案）。

缺陷3: Gitee 通道 latest==cur 时文案应说"已是最新版本"而非"无法确认"
缺陷1: MANIFEST 报字节数（与 ZIP 内 encode('utf-8') 口径一致），非字符数
缺陷2: 空/缺失的更新链状态文件标注"[本次无数据]"，不裸空
先写测试看 RED，再改实现。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TMP = Path(tempfile.mkdtemp(prefix="rl_diag_norm_"))
# 必须在 import login_ui 前定死沙箱（login_ui import 即解析 USER_DIR）
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


from core import update as U  # noqa: E402
import login_ui as L  # noqa: E402

print("=== 缺陷3: Gitee 通道文案 ===")
_status = getattr(U, "_status_message", None)
if _status is None:
    check("M0 _status_message 存在", False, "函数未实现")
else:
    m1 = _status("2.7.0.7", "2.7.0.7", False, "gitee")
    check("M1 latest==cur 说已是最新、不含无法确认",
          "已是最新版本" in m1 and "无法确认" not in m1, m1)
    m2 = _status("2.6.9", "2.7.0.7", False, "gitee")
    check("M2 latest<cur 仍如实说无法确认", "无法确认" in m2, m2)
    m3 = _status("2.8.0", "2.7.0.7", True, "gitee")
    check("M3 有新版说发现新版本", "发现新版本" in m3, m3)

print("=== 缺陷1: MANIFEST 字节数 ===")
_ml = getattr(L, "_manifest_line", None)
if _ml is None:
    check("N0 _manifest_line 存在", False, "函数未实现")
else:
    line = _ml("x.txt", "中文字", "说明")   # 3 汉字 = 9 字节 UTF-8
    check("N1 报字节数(9)而非字符数(3)", "字节" in line and " 9 字节" in line, line)
    line2 = _ml("y.txt", "abc", "说明")
    check("N2 纯ASCII字节数=字符数(3)", " 3 字节" in line2, line2)

print("=== 缺陷2: 空状态文件标注 ===")
_db = getattr(L, "_diag_body", None)
if _db is None:
    check("E0 _diag_body 存在", False, "函数未实现")
else:
    empty = _TMP / "empty.json"
    empty.write_text("", encoding="utf-8")
    check("E1 空文件标注无数据", "无数据" in _db(empty), _db(empty))
    full = _TMP / "full.json"
    full.write_text('{"a":1}', encoding="utf-8")
    check("E2 非空原样返回", _db(full) == '{"a":1}', _db(full))
    check("E3 不存在标注无数据", "无数据" in _db(_TMP / "missing.json"))

print(f"\nPASS {PASS} / FAIL {FAIL}")
sys.exit(1 if FAIL else 0)

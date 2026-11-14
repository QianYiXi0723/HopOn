# -*- coding: utf-8 -*-
"""双镜像一致性门禁（2026-11-14 测试优化 #1）。

背景：netutil 上线日实锤——脚本测试 import 根目录文件全绿，实机跑 engine/
副本直接 NameError（mihoyo_api 漏 import 只发生在 engine 链路被触发的场景）。
根因是「测试跑在 A 环境（根），产品跑在 B 环境（engine）」且一致性从无覆盖。

判据：复用 _dev_sync_engine.py --check 的同一套清单逻辑（权威），漂移退出码 1。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + ("  " + detail if detail else ""))


def test_sync_check():
    print("== 双镜像一致性（_dev_sync_engine --check）==")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "_dev_sync_engine.py"), "--check"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), timeout=120,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = out.strip().splitlines()[-3:] if out.strip() else []
    check("engine/ 与工作树无漂移（--check 退出码 0）", proc.returncode == 0,
          "rc=%d | %s" % (proc.returncode, " / ".join(tail)))


def test_engine_py_syntax():
    """engine 副本的 Python 文件全部可编译——导入级 NameError 在测试期拦截。"""
    print("== engine 副本语法门禁 ==")
    import ast
    bad = []
    count = 0
    for f in (ROOT / "engine").rglob("*.py"):
        if "python" in f.parts:            # 便携解释器目录跳过
            continue
        count += 1
        try:
            ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            bad.append("%s:%d %s" % (f.relative_to(ROOT / "engine"), exc.lineno, exc.msg))
    check("engine/*.py 全部可编译（%d 个）" % count, not bad, "; ".join(bad[:5]))


def test_engine_ui_present():
    """engine/ui 三件套在位——漏同步会让实机跑旧前端（页面旧 JS 实锤）。"""
    print("== engine/ui 关键文件在位 ==")
    for name in ("index.html", "app.js", "style.css"):
        p = ROOT / "engine" / "ui" / name
        check("engine/ui/" + name, p.exists() and p.stat().st_size > 1000)


if __name__ == "__main__":
    test_sync_check()
    test_engine_py_syntax()
    test_engine_ui_present()
    print("== mirror_sync: %d PASS / %d FAIL ==" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)

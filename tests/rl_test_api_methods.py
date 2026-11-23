#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前端写端点必须用 POST（2026-11-23 致命 bug 防回归）。

朋友机诊断包实锤：`fetch('/api/update/install')` 漏了 `method:'POST'`，
浏览器默认 GET → 引擎 do_GET 无此路由 → 404 → 前端走失败分支，
**POST 从未到达**。结果：点「立即安装」永远装不上，而所有后端安装逻辑
（staged 替换 / Updater 启动 / 自动重启）全部不会执行。

本测试从 login_ui.py 的 do_POST 路由表机械推导「写端点集合」，再逐个
检查 ui/app.js 里的 fetch 调用是否带了 method:'POST'（或走 postJSON 封装）。
任何漏网立即失败。
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
    lu = (ROOT / "login_ui.py").read_text(encoding="utf-8")
    app = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")

    # ---- 1) 从 do_POST 提取写端点 ----
    # 定位 do_POST 方法体范围：从 "def do_POST" 到下一个 "def do_" 顶层方法
    m = re.search(r"def do_POST\(self\)[^\n]*\n.*?(?=\n    def )", lu, re.S)
    check(m is not None, "P1 能定位 do_POST 方法体")
    post_body = m.group(0) if m else ""

    post_eps = set(re.findall(r'path == "(/api/[^"]+)"', post_body))
    for grp in re.findall(r'path in \(([^)]+)\)', post_body):
        post_eps.update(re.findall(r'"(/api/[^"]+)"', grp))
    check(len(post_eps) >= 12, f"P2 do_POST 写端点提取（{len(post_eps)} 个）")

    # ---- 2) do_GET 合法路由（同名 GET 存在则该端点可 GET 读取）----
    mg = re.search(r"def do_GET\(self\)[^\n]*\n.*?(?=\n    def )", lu, re.S)
    get_body = mg.group(0) if mg else ""
    get_eps = set(re.findall(r'parsed\.path == "(/api/[^"]+)"', get_body))
    for grp in re.findall(r'parsed\.path in \(([^)]+)\)', get_body):
        get_eps.update(re.findall(r'"(/api/[^"]+)"', grp))
    # _GET_SIMPLE_ROUTES 表也是一批合法 GET 读取端点（键名即路径）
    m_simple = re.search(r"_GET_SIMPLE_ROUTES[^=]*=\s*\{(.*?)\n\}", lu, re.S)
    if m_simple:
        get_eps.update(re.findall(r'"(/api/[^"]+)"\s*:', m_simple.group(1)))

    # 只查「纯写端点」（do_GET 里没有的）
    write_only = sorted(post_eps - get_eps)
    check(len(write_only) >= 10, f"P3 纯写端点集合（{len(write_only)} 个）")

    # ---- 3) 逐个检查前端调用是否带 POST ----
    misses = []
    for ep in write_only:
        # 找到所有 fetch('<ep>' ...) 出现处
        for mm in re.finditer(re.escape("'" + ep + "'") + r"|" + re.escape('"' + ep + '"'), app):
            line_no = app.count("\n", 0, mm.start()) + 1
            # 该行前后 4 行窗口内必须有 method:'POST' 或 postJSON
            start = app.rfind("\n", 0, max(0, mm.start() - 400))
            win = app[max(0, mm.start() - 400): mm.start() + 400]
            if re.search(r"method:\s*['\"]POST['\"]", win) or "postJSON" in win:
                continue
            # 只关心 fetch( 调用点
            if "fetch(" not in win:
                continue
            misses.append(f"{ep} @ app.js:{line_no}")

    check(not misses,
          "P4 所有纯写端点的前端调用都带 method:'POST'"
          + ("" if not misses else " —— 漏网: " + "; ".join(sorted(set(misses)))))

    # ---- 4) 关键端点定点断言（朋友机事故点）----
    check("fetch('/api/update/install', { method: 'POST' })" in app,
          "P5 /api/update/install 用 POST（诊断包实锤：曾漏成 GET → 404 → 装不上）")
    check("fetch('/api/update/open-folder', { method: 'POST' })" in app,
          "P6 /api/update/open-folder 用 POST（同批修复）")

    print(f"PASS {PASS} / FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

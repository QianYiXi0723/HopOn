# -*- coding: utf-8 -*-
"""手机连接卡「手机端在哪下载」守卫（2026-11-25 用户拍板）。

用户逐字定的口径：在手机连接抽屉里单独一行灰字说明，指向
HopOn.Mobile 的 releases/latest（不写死 v1.0，自动跟随新版）。
只陈述事实，不写「免费」、不做平台对比、不催促。

模仿 rl_test_wording.py 的守卫模式：扫用户可见面 ui/index.html。
用法：python tests/rl_test_mobile_download.py    （0 = 通过）
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
HTML = (BASE / "ui" / "index.html").read_text(encoding="utf-8")

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def main():
    print("=== 手机连接卡下载说明守卫 ===")

    # 只看手机连接抽屉的 body（mobile-body），避免误判别处的下载链接
    m = re.search(r'id="mobile-body"(.*?)</div>\s*</div>\s*</details>', HTML, re.S)
    body = m.group(1) if m else ""
    check("定位到手机连接抽屉 body", bool(body))

    # 文案：单独一行「手机端：…下载…」
    check("含「手机端」字样", "手机端" in body)
    check("含「下载」字样", "下载" in body)

    # 链接：指向手机仓 releases/latest，新标签打开、noopener（沿用 index.html 现有写法）
    link = re.search(r'<a\s[^>]*href="([^"]+)"[^>]*>([^<]*)</a>', body)
    href = link.group(1) if link else ""
    label = link.group(2) if link else ""
    check("下载链接指向 HopOn.Mobile releases/latest",
          href == "https://github.com/QianYiXi0723/HopOn.Mobile/releases/latest", href)
    check("链接文案为「下载安装」", label == "下载安装", label)
    check("链接带 target=_blank", link is not None and 'target="_blank"' in link.group(0))
    check("链接带 rel=noopener", link is not None and 'rel="noopener"' in link.group(0))

    # 违规词：面向用户文案不写「免费」、不做平台对比
    check("抽屉文案无「免费」字样", "免费" not in body)
    check("抽屉文案无平台对比（Gitee/iOS 对比等）",
          not re.search(r"Gitee|gitee|iOS|国内免梯", body))

    print(f"\n=== rl_test_mobile_download.py: {PASS} PASS / {FAIL} FAIL ===")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

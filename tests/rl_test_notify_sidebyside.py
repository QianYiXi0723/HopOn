# -*- coding: utf-8 -*-
"""通知绑定：最大化窗口两渠道并排（优化清单 #14，2026-11-26）。

用户口径：只有「最大化窗口（≥1K）」才把原来的切换改成并排；
分辨率低于 1K 当小窗口处理，保持原有「切换」行为不变。

判据：
  B14a 两个渠道编辑区被 .ch-editors 容器包住（并排布局的挂点）
  B14b CSS 有 ≥1000px 下 .ch-editors 两列并排的规则
  B14c 并排时两个编辑区都强制可见（覆盖 [hidden]），否则只能看到一个
  B14d <1000px 不并排（媒体查询上界守得住，小窗口保持切换）

用法：python tests/rl_test_notify_sidebyside.py   （0 = 通过）
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CSS = (BASE / "ui" / "style.css").read_text(encoding="utf-8")
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
        print(f"  FAIL  {name}" + (f"  {detail}" if detail else ""))


def run():
    # B14a 容器存在且包住两个编辑区
    check("B14a HTML 有 .ch-editors 容器包住两个渠道编辑区",
          'class="ch-editors"' in HTML
          and HTML.index('class="ch-editors"') < HTML.index('id="ed-serverchan"')
          and HTML.index('class="ch-editors"') < HTML.index('id="ed-qmsg"'))

    # B14b ≥1000px 并排规则
    m = re.search(r"@media\s*\(min-width:\s*1000px\)\s*\{(.{0,1200}?)\n\s*\}", CSS, re.S)
    block = m.group(1) if m else ""
    check("B14b 存在 @media(min-width:1000px) 块", bool(block), "未找到")
    check("B14b2 该块内 .ch-editors 为两列网格",
          ".ch-editors" in block and "grid-template-columns" in block,
          block[:160])

    # B14c 并排时两个编辑区都可见（覆盖 [hidden] 的 display:none）
    check("B14c 并排时强制两编辑区显示",
          re.search(r"\.ch-editors\s*>\s*div\s*\{[^}]*display\s*:\s*block", block) is not None
          or re.search(r"#ed-qmsg\s*\{[^}]*display\s*:\s*block", block) is not None,
          block[:200])

    # B14d 媒体查询下界大于 1000（<1000 保持原切换），且不是 max-width
    check("B14d 用 min-width 限定（小窗口不并排）",
          "min-width:1000px" in CSS.replace(" ", ""))


if __name__ == "__main__":
    run()
    print(f"\n[结论] {PASS}/{PASS + FAIL} 通过")
    sys.exit(1 if FAIL else 0)

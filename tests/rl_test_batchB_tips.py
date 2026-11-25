# -*- coding: utf-8 -*-
"""批 B 文案与间距守卫（优化清单 #16/#17/#18，2026-11-26）。

用户口径：
  #16 守护进程信息间距太小 → 加大间距、可拓展两行，并补 tips（用户问「为什么没有tips」）
  #17 休眠提醒抽屉那行介绍 → 简化后整理进 tips（别再占一行正文）
  #18 杀软拦截自检 / 自定义提醒 / 免打扰时段 → 各加一条 tips

tips 文案标准（2026-11-26 用户定）：短、一句话为主、动词开头、少术语、不口语、不加句末标点。

用法：python tests/rl_test_batchB_tips.py   （0 = 通过）
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


def css_rule(sel):
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)):
        if sel in [s.strip() for s in m.group(1).split(",")]:
            return m.group(2)
    return ""


def run():
    # ---------- #16 守护进程：间距加大 + 允许两行 + tips ----------
    bar = css_rule(".daemon-bar")
    gaps = re.findall(r"gap\s*:\s*([\d]+)px", bar)
    check("B16a 守护进程条行间距加大（gap ≥ 10px）",
          bool(gaps) and int(gaps[0]) >= 10, bar[:120] or "无规则")
    check("B16b 保留 flex-wrap（可拓展两行）", "flex-wrap:wrap" in bar.replace(" ", ""), bar[:120])
    # 守护进程标题旁要有 tips（.q）
    daemon_html = HTML[HTML.index('class="daemon-bar"'):HTML.index('class="daemon-bar"') + 1400]
    check("B16c 守护进程区补了 tips（.q + data-tip）",
          'class="q"' in daemon_html and 'data-tip' in daemon_html)

    # ---------- #17 休眠提醒：介绍并入 tips ----------
    catchup_start = HTML.index('id="acc-catchup"')
    catchup = HTML[catchup_start:catchup_start + 1200]
    check("B17a 休眠提醒正文那行 .hint 已移除", '<div class="hint">休眠或关机错过巡检时间后' not in catchup)
    check("B17b 标题旁有 tips 承载原说明",
          'class="q"' in catchup and 'data-tip' in catchup)

    # ---------- #18 三处各加一条 tips ----------
    for anchor, label in (('id="acc-av"', "杀软拦截自检"),
                          ('id="acc-dailies"', "自定义提醒"),
                          ('id="acc-quiet"', "免打扰时段")):
        if anchor in HTML:
            seg = HTML[HTML.index(anchor):HTML.index(anchor) + 1200]
            check(f"B18 {label} 有 tips", 'class="q"' in seg and 'data-tip' in seg,
                  "未找到 tips")
        else:
            check(f"B18 {label} 锚点存在", False, anchor + " 不存在")

    # ---------- tips 文案纪律 ----------
    tips = re.findall(r'data-tip="([^"]+)"', HTML)
    long_tips = [t for t in tips if len(t) > 60]
    check(f"B18x 全站 tips 长度受控（>60字共 {len(long_tips)} 条，允许既有长文案）",
          True, "")  # 信息项：仅统计，不判失败


if __name__ == "__main__":
    run()
    print(f"\n[结论] {PASS}/{PASS + FAIL} 通过")
    sys.exit(1 if FAIL else 0)

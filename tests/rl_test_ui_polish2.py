# -*- coding: utf-8 -*-
"""月报悬停精简 / 卡片描边层级 / 问号间距（2026-11-26 第二批 UI 调整）。

用户逐条要求：
  U1 单游戏月报里的格子悬停效果多余 → 删除（不再给 editable hover 加点子）
  U2 总表悬停臃肿 → 只留「完成 x/x」+「长按补录」，删掉日期与逐游戏明细
  U3 悬停描边（浅蓝 outline）压在左侧身份竖线之上 → 卡片要抬 z-index / 竖线让位
  U6 休眠提醒/杀软/自定义/免打扰四个问号离标题太近 → 间距与其他项一致

用法：python tests/rl_test_ui_polish2.py   （0 = 通过）
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CSS = (BASE / "ui" / "style.css").read_text(encoding="utf-8")
APP = (BASE / "ui" / "app.js").read_text(encoding="utf-8")
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
    # ---------- U1 单游戏月报：删「悬停显示的信息」，保留网格悬停效果 ----------
    # 2026-11-26 用户澄清：「我让你删的是单游戏月报悬停后显示的信息，
    # 而不是悬停后的网格的变化效果」。即删 data-tip，保 editable（hover 缩放）。
    seg = APP[APP.index("if (monthGame) {"): APP.index("} else {", APP.index("if (monthGame) {"))]
    check("U1a 单游戏月报保留网格悬停效果（editable 仍在）",
          "' editable'" in seg, seg[:160])
    check("U1b 单游戏月报不再挂 data-tip（删掉悬停信息）",
          not any("data-tip" in ln and "/*" not in ln and not ln.strip().startswith("//")
                  for ln in seg.splitlines()),
          seg[:200])

    # ---------- U2 总表：悬停只留总完成 + 长按补录 ----------
    total_seg = APP[APP.index("/* 总表：GitHub 贡献图范式"):]
    total_seg = total_seg[:total_seg.index("document.getElementById('month-body')")]
    check("U2a 总表 tip 不含日期 label", "row.label + '｜' +" not in total_seg,
          total_seg[:200])
    check("U2b 总表 tip 不含逐游戏明细（parts.join）", "parts.join" not in total_seg)
    check("U2c 总表 tip 保留总完成 x/x", "'完成 ' + done + '/' + total" in total_seg
          or "完成 ' + done" in total_seg)
    check("U2d 总表 tip 保留长按补录", "长按补录" in total_seg)

    # ---------- U3 悬停描边 vs 左侧身份竖线 ----------
    # 竖线是 ::before；悬停 outline 要压过它 → hover 时 z-index 高于竖线所在层
    gcard_hover = ""
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)):
        if ".gcard:hover" in [s.strip() for s in m.group(1).split(",")]:
            gcard_hover = m.group(2)
            break
    z = re.search(r"z-index\s*:\s*(\d+)", gcard_hover)
    check("U3a .gcard:hover 有 z-index 抬层", z is not None, gcard_hover[:120])
    check("U3b 抬层足够高（≥60）", z is not None and int(z.group(1)) >= 60, str(z))

    # ---------- U6 问号间距 ----------
    # 真因是 display:block 让 gap 失效（不是 gap 数值）——所以必须同时验布局类型
    set_txt_rules = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)):
        if ".set-txt b" in [s.strip() for s in m.group(1).split(",")]:
            set_txt_rules.append(" ".join(m.group(2).split()))
    check("U6a .set-txt b 没有任何 display:block 覆盖（block 会让 gap 失效）",
          not any("display:block" in r for r in set_txt_rules),
          " / ".join(set_txt_rules)[:200])
    check("U6b .set-txt b 最终为 inline-flex 且带 gap",
          bool(set_txt_rules) and "display:inline-flex" in set_txt_rules[-1]
          and "gap:" in set_txt_rules[-1],
          set_txt_rules[-1][:160] if set_txt_rules else "无规则")


if __name__ == "__main__":
    run()
    print(f"\n[结论] {PASS}/{PASS + FAIL} 通过")
    sys.exit(1 if FAIL else 0)

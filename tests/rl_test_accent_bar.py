# -*- coding: utf-8 -*-
"""身份色条（左缘 3px 竖线）端点收口守卫（2026-11-26）。

用户两次指出：「描边还是在竖线下面」「其实可能是标志竖线太长了，是按照方格做的
但是我们是圆角」「包括活动卡和体力卡这个竖线也有点太长了，竖线的两头是做了
圆角处理的，学习一下别人是怎么处理这种标志性竖线的」。

根因：卡片是 8px 圆角，色条却是 top:0/bottom:0 的方角矩形 —— 四角会顶到
圆角弧线外的透明区，视觉上「戳出卡片」并压住 hover 描边。

业界做法（引用块竖线 / accent edge 通行处理）：
  ① 两端做**线宽一半**的圆角收口（3px 线 → 2px 圆角），端点不被切断；
  ② 上下内缩，避开母容器圆角弧线（本卡圆角 8px，故内缩 10px）。

用法：python tests/rl_test_accent_bar.py   （0 = 通过）
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CSS = (BASE / "ui" / "style.css").read_text(encoding="utf-8")

PASS = 0
FAIL = 0

#: 所有画左缘身份色条的规则（三处卡：活动卡 / 概览卡 / 实时卡）
BAR_SELECTORS = [".acard::before", ".gcard::before", ".live-card::before"]


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"  {detail}" if detail else ""))


def rules_for(css: str, selector: str) -> list[str]:
    out = []
    plain = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", plain):
        if selector in [s.strip() for s in m.group(1).split(",")]:
            out.append(" ".join(m.group(2).split()))
    return out


def run():
    for sel in BAR_SELECTORS:
        bodies = rules_for(CSS, sel)
        name = sel.split("::")[0]
        check(f"B1 {name} 存在身份色条规则", bool(bodies), "未找到")
        if not bodies:
            continue
        body = bodies[-1]   # 后位规则生效

        # ① 上下内缩：不能是 top:0 / bottom:0
        top = re.search(r"top\s*:\s*([^;]+)", body)
        bot = re.search(r"bottom\s*:\s*([^;]+)", body)
        top_v = top.group(1).strip() if top else ""
        bot_v = bot.group(1).strip() if bot else ""
        check(f"B2 {name} 上端内缩（top 非 0）", top_v not in ("0", "0px"), f"top={top_v}")
        check(f"B2 {name} 下端内缩（bottom 非 0）", bot_v not in ("0", "0px"), f"bottom={bot_v}")

        # ② 端点圆角 = 线宽一半（3px → 2px），且不是旧的 8px 单侧写法
        radius = re.search(r"border-radius\s*:\s*([^;]+)", body)
        rv = radius.group(1).strip() if radius else ""
        check(f"B3 {name} 色条两端圆角收口（非仅左侧 8px 写法）",
              rv != "" and "8px 0 0 8px" not in rv, f"border-radius={rv}")
        check(f"B3 {name} 圆角半径 ≤ 线宽（视觉上像圆头）",
              bool(re.match(r"^(\d+(?:\.\d+)?)px$", rv)) and float(rv.replace("px", "")) <= 3,
              f"border-radius={rv}")


if __name__ == "__main__":
    run()
    print(f"\n[结论] {PASS}/{PASS + FAIL} 通过")
    sys.exit(1 if FAIL else 0)

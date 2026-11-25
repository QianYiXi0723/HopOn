# -*- coding: utf-8 -*-
"""批 A UI 改动守卫（2026-11-26）。

覆盖（每条对应优化清单批 A 的一项）：
  A10 月报内的悬停/修改弹窗必须压过 month-modal：
      dlfix-modal、monthday-modal、#tip 的 z-index 都要 > month-modal 的 z-index。
  A9  七日报表网格行高 44→52，且只作用于 .heatmap 内（不动月报 .hm-row-month）。
  A7  实时体力卡 .live-card 有与 .gcard:hover 同款 hover（outline + z-index）。

用法：python tests/rl_test_batchA_ui.py    （0 = 通过）
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CSS = (BASE / "ui" / "style.css").read_text(encoding="utf-8")

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


def zindex_of(selector_block_css: str) -> int | None:
    """从一段 CSS 文本里抓 z-index 数值。"""
    m = re.search(r"z-index\s*:\s*(\d+)", selector_block_css)
    return int(m.group(1)) if m else None


def rule(css: str, selector: str) -> str:
    """返回精确匹配某选择器的规则块（到 } 为止），找不到返回空串。
    先剥注释，否则 selector 段会混入 /*...*/ 导致精确匹配失败。"""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sels = [s.strip() for s in m.group(1).split(",")]
        if selector in sels:
            return m.group(2)
    return ""


def run():
    # ---------- A10 层级 ----------
    month_z = zindex_of(rule(CSS, ".modal-mask"))  # 1000
    day_z = zindex_of(rule(CSS, "#monthday-modal"))  # 1001（已修）
    tip_z = zindex_of(rule(CSS, "#tip"))
    dlfix_z = zindex_of(rule(CSS, "#dlfix-modal"))
    check("A10a 基准 .modal-mask(月报) z-index 读到", month_z is not None, str(month_z))
    check("A10b monthday-modal 高于 month-modal",
          day_z is not None and month_z is not None and day_z > month_z,
          f"day={day_z} month={month_z}")
    check("A10c #tip 悬停气泡高于 month-modal（否则月报内悬停被盖）",
          tip_z is not None and month_z is not None and tip_z > month_z,
          f"tip={tip_z} month={month_z}")
    check("A10d #dlfix-modal 修改弹窗高于 month-modal",
          dlfix_z is not None and month_z is not None and dlfix_z > month_z,
          f"dlfix={dlfix_z} month={month_z}")

    # ---------- A9 七日网格行高 ----------
    hm_cell = rule(CSS, ".hm-cell")
    m = re.search(r"height\s*:\s*(\d+)px", hm_cell)
    hm_cell_h = int(m.group(1)) if m else None
    check("A9a 七日 .hm-cell 行高为 52px", hm_cell_h == 52, str(hm_cell_h))
    hl = re.search(r"\.hm-row\s*\{([^{}]*)\}", CSS)
    hl_h = int(re.search(r"height\s*:\s*(\d+)px", hl.group(1)).group(1)) if hl and re.search(r"height\s*:\s*(\d+)px", hl.group(1)) else None
    check("A9b 七日 .hm-row 行高为 52px", hl_h == 52, str(hl_h))
    # 月报行高不能被误改（若是独立规则，保持原值或不存在都可，只要不是被改成 52）
    mrow = rule(CSS, ".hm-row.hm-row-month, .hm-row.hm-head.hm-row-month")
    check("A9c 月报 .hm-row-month 未被改成 52px", "height:52px" not in mrow.replace(" ", ""),
          mrow[:80])

    # ---------- A7 实时卡 hover ----------
    live_hover = rule(CSS, ".live-card:hover")
    check("A7a .live-card:hover 存在", bool(live_hover.strip()), "无此规则")
    check("A7b .live-card:hover 用 outline 而非 border",
          "outline" in live_hover and not re.search(r"\bborder\s*:", live_hover),
          live_hover[:100])
    check("A7c .live-card:hover 抬 z-index 压过卡片",
          (zindex_of(live_hover) or 0) >= 60, live_hover[:100])


if __name__ == "__main__":
    run()
    print(f"\n[结论] {PASS}/{PASS + FAIL} 通过")
    sys.exit(1 if FAIL else 0)

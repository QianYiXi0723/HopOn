#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""连胜口径统一测试（2026-11-20）：_streak/_month_stats 吃补录覆盖 + 今天未结算不判断。

背景：朋友机报表 11/16-11/22 全 ✓、卡片「连续天数 0」——_streak 只读
store 原文（补录覆盖不进连胜）+ 「今天查过但没完成」直接判断（巡检时点
不代表今天结束状态：早上活跃度没满下午打满了）。修复后口径：
  * 覆盖（history_overrides）优先于 store 原文；
  * 今天（cursor==today）查过但没完成 → 跳过不判断（今天还没结束）；
  * 历史日查过没完成 → 真断；显式 false 覆盖 = 用户撤销 → 真断；
  * _month_stats 同步吃覆盖（月统计与月报表格子口径一致）。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.history import _month_stats, _streak

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


def _day(finished=None, known=True):
    """构造一天的终末地节点（finished=None=查过但没完成的形态由调用方拼）。"""
    if finished is None:
        return {"known": True, "finished": False}
    return {"known": known, "finished": bool(finished)}


def main() -> int:
    today = datetime(2026, 11, 22)
    store = {
        "2026-11-16": {"endfield": _day(True)},
        "2026-11-17": {"endfield": _day(True)},
        "2026-11-18": {"endfield": _day(True)},
        "2026-11-19": {"endfield": _day(True)},
        "2026-11-20": {"endfield": _day(False)},   # 巡检时活跃度没满
        "2026-11-21": {"endfield": _day(True)},
        "2026-11-22": {"endfield": _day(False)},   # 今天查过但没完成（下午才打满）
    }

    # S1 新口径：今天（11/22）查过但没完成 → 未结算跳过不算断 → 从 11/21 往回数
    # （11/20 store=False 是历史真断点）→ streak = 11/21 一天
    s0 = _streak(store, "endfield", today)
    check(s0 == 1, f"S1 今天未结算跳过 → streak=1（仅 11/21；11/20 历史断点），实际 {s0}")

    # S2 空覆盖同语义
    s = _streak(store, "endfield", today, ovr={})
    check(s == 1, f"S2 空覆盖同 S1 → 1，实际 {s}")

    # S3 覆盖补录 11/20 → 连续到 11/16（今天 11/22 未结算仍跳过）
    ovr = {"2026-11-20": {"endfield": {"finished": True}}}
    s = _streak(store, "endfield", today, ovr=ovr)
    check(s == 6, f"S3 覆盖 11/20 补录 → 连续 6 天（11/16-11/21），实际 {s}")

    # S3b 11/16 也覆盖 → 仍 6（今天 11/22 未结算不 +1）
    ovr["2026-11-16"] = {"endfield": {"finished": True}}
    s = _streak(store, "endfield", today, ovr=ovr)
    check(s == 6, f"S3b 11/16 也补录 → 仍 6 天（今天未结算不 +1），实际 {s}")

    # S4 显式 false 覆盖 = 用户撤销 → 11/19 断；11/20 store=False 也断 → 只剩 11/21
    ovr2 = {"2026-11-19": {"endfield": {"finished": False}}}
    s = _streak(store, "endfield", today, ovr=ovr2)
    check(s == 1, f"S4 显式 false 覆盖=撤销 → 连续 1 天（11/21），实际 {s}")

    # S5 历史日 store 断点（11/20）无覆盖 → 真断（原语义保留）
    s = _streak(store, "endfield", today)
    check(s == 1, f"S5 历史日 store 断点无覆盖 → 连续 1 天（11/21），实际 {s}")

    # S6 今天 finished=True（已完成）→ 照常 +1
    store["2026-11-22"] = {"endfield": _day(True)}
    s = _streak(store, "endfield", today)
    check(s == 2, f"S6 今天已完成 → +1 连续 2 天（11/22+11/21），实际 {s}")

    # S7 月统计吃覆盖：11/20 补录 → 7 天全 done（含 store 已改 True 的 11/22）
    ms = _month_stats(store, "endfield", "2026-11", ovr={"2026-11-20": {"endfield": {"finished": True}}})
    check(ms["done"] == 7 and ms["missed"] == 0,
          f"S7 月统计吃覆盖：done={ms['done']}（11/16-22 全 + 补录 20）missed={ms['missed']}")
    ms0 = _month_stats(store, "endfield", "2026-11")
    check(ms0["done"] == 6 and ms0["missed"] == 1,
          f"S7b 无覆盖月统计：done={ms0['done']}（11/16-19,21,22）missed={ms0['missed']}（11/20）")

    print(f"PASS {PASS} / FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

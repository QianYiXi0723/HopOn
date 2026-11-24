#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自定义提醒多时间点回归（2026-11-25）。

需求：standalone 项可配多个提醒时刻 times[]，一天多次提醒、共用名称与账本，
打卡仍一天一格（报表/streak 不动）。单时刻旧数据（只有 time）行为不变。

被测：
  D._times(item)                → times[] 或由 time 派生
  D.standalone_due(items,...)   → 每个到期时刻一条，_slot 带时刻
  D.mark_fired(item,...,slot)   → 多时刻独立记账不串
先写测试看 RED，再实现。
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import dailies as D  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  PASS " + name)
    else:
        FAIL += 1; print("  FAIL " + name + (" | " + str(detail) if detail else ""))


tmp = Path(tempfile.mkdtemp(prefix="rl_dailies_times_"))

# ---- 1) _times 兼容 ----
check("T1 无 times 时从 time 派生单元素",
      D._times({"time": "20:00"}) == ["20:00"], D._times({"time": "20:00"}))
check("T2 times 原样返回",
      D._times({"time": "", "times": ["08:00", "12:00"]}) == ["08:00", "12:00"])
check("T3 无 time 无 times 返回空", D._times({}) == [])
check("T3b 空 times[] 回退到 time（daemon 给旧数据补空列表的坑）",
      D._times({"time": "20:00", "times": []}) == ["20:00"],
      D._times({"time": "20:00", "times": []}))

# ---- 2) 多时刻触发：8/12/18 三个时刻 ----
multi = {"id": "w", "name": "喝水", "mode": "standalone", "times": ["08:00", "12:00", "18:00"],
         "time": "", "reset": "04:00", "enabled": True, "days": [True] * 7}

def due_slots(now):
    return [i.get("_slot") for i in D.standalone_due([multi], tmp, now=now)]

# 08:00:10 → 只有 08:00 到期
check("T4 08点只有 08:00 时刻到期", due_slots(datetime(2026, 11, 25, 8, 0, 10)) == ["08:00"],
      due_slots(datetime(2026, 11, 25, 8, 0, 10)))
# 12:00:10 → 08 已过但本 tick 只报最新到期？口径：报所有已到期未 fired 的
slots_noon = due_slots(datetime(2026, 11, 25, 12, 0, 10))
check("T5 12点 08/12 两个未记账时刻都到期", slots_noon == ["08:00", "12:00"], slots_noon)

# 记账 08 后，08 不再出现，12 仍在
D.mark_fired(multi, tmp, slot="08:00", now=datetime(2026, 11, 25, 12, 0, 10))
slots_after = due_slots(datetime(2026, 11, 25, 12, 0, 20))
check("T6 记账08后只剩12", slots_after == ["12:00"], slots_after)

# render_message 的 {time} 用当前 slot
hit = [i for i in D.standalone_due([multi], tmp, now=datetime(2026, 11, 25, 12, 0, 20))][0]
check("T7 到期项的 time 字段=触发时刻（供{time}渲染）", hit.get("time") == "12:00", hit.get("time"))

# ---- 3) 旧单时刻零回归 ----
old = {"id": "o", "name": "旧项", "mode": "standalone", "time": "20:00",
       "reset": "04:00", "enabled": True, "days": [True] * 7}
check("T8 旧单时刻正常到期（无_slot）",
      [i.get("_slot") for i in D.standalone_due([old], tmp, now=datetime(2026, 11, 25, 20, 0, 10))] == [None],
      [i for i in D.standalone_due([old], tmp, now=datetime(2026, 11, 25, 20, 0, 10))])
D.mark_fired(old, tmp, now=datetime(2026, 11, 25, 20, 0, 10))
check("T9 旧单时刻 mark_fired（不传 slot）记账后不再到期",
      D.standalone_due([old], tmp, now=datetime(2026, 11, 25, 20, 0, 10)) == [])

print(f"\nPASS {PASS} / FAIL {FAIL}")
sys.exit(1 if FAIL else 0)

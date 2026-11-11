#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手动打卡（自定义提醒）核心账本测试：rl_test_dailies。

覆盖 core/dailies.py 全部公开行为——
  1. 逐项重置时刻：每项的「今日」周期键跟各自 reset 走，互不干扰
  2. 完成账本 v2：{done/fired: {item: {period: ts}}}，v1 自动迁移
  3. 勾掉/撤销/跨重置时刻自动重开
  4. streak 连续完成周期数（当前周期未勾不断签）
  5. standalone_due 独立时刻到期判定（迟过=当场补发，只早不迟）
  6. forget 清账 + render_message 模板渲染（变量/留空回落/未知占位清理）

全部用临时目录 + 注入 now 的模拟时钟，绝无真实数据/网络依赖。
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import dailies as D

PASS = 0


def check(cond, label):
    global PASS
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    PASS += 1
    print(f"  ✓ {label}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="rl_test_dailies_"))
    items = [
        {"id": "a1", "name": "游戏A日常", "mode": "merged", "reset": "04:00", "enabled": True},
        {"id": "a2", "name": "健身房", "mode": "merged", "reset": "00:00", "enabled": True},
        {"id": "a3", "name": "独立项", "mode": "standalone", "time": "20:00", "reset": "04:00", "enabled": True},
    ]

    # ---- 1) 模板渲染 ----
    print("[1] render_message")
    check(D.render_message(items[0]) == "📌 游戏A日常 还没做", "空模板回落默认")
    check(D.render_message({**items[1], "template": "💪 {name} 时间到"}) == "💪 健身房 时间到",
          "自定义模板+{name}")
    check(D.render_message({**items[2], "template": "👉 {name}（{time} 前）"})
          == "👉 独立项（20:00 前）", "{time} 变量")
    check(D.render_message({"id": "x", "name": "N", "template": "奇怪 {unknown} 字段"})
          == "奇怪 字段", "未知占位清理")

    # ---- 2) 逐项重置时刻独立翻转 ----
    print("[2] per-item period")
    now = datetime(2026, 11, 12, 23, 30)   # 深夜：a1(04:00) 属 11-12 周期，a2(00:00) 属 11-13 周期
    D.mark_done(items[0], tmp, now=now)
    D.mark_done(items[1], tmp, now=now)
    check(D.is_done(items[0], tmp, now=now) and D.is_done(items[1], tmp, now=now),
          "各自周期内均已完成")
    t_01 = datetime(2026, 11, 13, 1, 0)    # 次日 01:00：a2 已跨 00:00 重开，a1 未跨 04:00
    check(D.is_done(items[0], tmp, now=t_01), "a1 04:00 前仍是已完成")
    check(not D.is_done(items[1], tmp, now=t_01), "a2 00:00 后已重开")
    t_05 = datetime(2026, 11, 13, 5, 0)    # 次日 05:00：a1 也跨 04:00 重开
    check(not D.is_done(items[0], tmp, now=t_05), "a1 04:00 后重开")

    # ---- 3) streak ----
    print("[3] streak")
    # a1 注入 11-10/11-11 两个历史周期 → 当前周期(11-12)已勾 → 连续 3
    state = json.loads(D._state_path(tmp).read_text(encoding="utf-8"))
    state["done"].setdefault("a1", {}).update({
        "2026-11-11": "10:00", "2026-11-10": "10:00"})
    D._save_state(tmp, state)
    check(D.streak_of(items[0], tmp, now=now) == 3, "连续 3 周期")
    check(D.streak_of(items[1], tmp, now=now) == 1, "a2 当前周期已勾=1")
    # a2 在其 11-12 周期（00:00 已过）再次勾掉 → 当前周期连续 1（同周期幂等）
    D.mark_done(items[1], tmp, now=datetime(2026, 11, 12, 12, 0))
    check(D.streak_of(items[1], tmp, now=datetime(2026, 11, 12, 12, 30)) == 1,
          "同周期重复勾 streak 不变")

    # ---- 4) standalone_due：到期/已推/已勾/未到 ----
    print("[4] standalone_due")
    a3 = items[2]
    now_due = datetime(2026, 11, 12, 20, 1)     # 20:00 已过 → 到期
    check([i["id"] for i in D.standalone_due(items, tmp, now=now_due)] == ["a3"],
          "到期判定")
    D.mark_fired(a3, tmp, now=now_due)
    check(D.standalone_due(items, tmp, now=now_due) == [], "已推不重复")
    check(D.standalone_due(items, tmp, now=now_due - timedelta(hours=3)) == [],
          "未到目标不触发")
    # 勾掉当前周期后不再触发（重开自然恢复）
    D.mark_done(a3, tmp, now=now_due)
    check(D.standalone_due(items, tmp, now=now_due + timedelta(minutes=5)) == [],
          "已勾的独立项不触发")

    # ---- 5) forget 清账 ----
    print("[5] forget")
    D.mark_fired(a3, tmp, now=now_due)
    D.forget("a3", tmp)
    st = D._load_state(tmp)
    check("a3" not in st.get("done", {}) and "a3" not in st.get("fired", {}),
          "forget 清 done+fired")

    # ---- 6) v1 账本迁移 ----
    print("[6] migrate")
    old = {"done": {"2026-11-11": {"legacy": "09:00"}},
           "fired": {"2026-11-11": {"legacy": "09:00"}}}
    mig = D._migrate(old)
    check(mig["v"] == 2 and mig["done"]["legacy"]["2026-11-11"] == "09:00"
          and mig["fired"]["legacy"]["2026-11-11"] == "09:00", "v1→v2 反转迁移")

    # ---- 7) undone_today 尊重 enabled ----
    print("[7] enabled 过滤")
    D2 = [dict(i, enabled=(i["id"] != "a2")) for i in items]
    ids = [i["id"] for i in D.undone_today(D2, tmp, now=now)]
    check("a2" not in ids, "停用项不进未完成清单")

    print(f"rl_test_dailies: {PASS} 项断言全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())

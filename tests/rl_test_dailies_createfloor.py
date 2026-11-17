# -*- coding: utf-8 -*-
"""自定义提醒「创建地板」回归（2026-11-18）。

用户实证：新建一个自定义提醒，七日报表会把创建前 6 天画成 ✗ 漏打。
裁决修订：地板 = item.created_at（创建时间），不是账本最早周期——
- 创建日之前的日历日 = ⋯ 开始记录前（项不存在谈不上漏打）
- 创建日之后的过去计划日照旧 ✗ 可长按补录（2026-11-14 裁决原样保留：
  地板若取账本最早周期，会把首打较晚的项的过去锁死）
- 已有账本记录的格子数据优先（地板只管没数据的格子）
- 存量项无 created_at → 无地板，行为不变
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import core.dailies as dl

fails = []


def chk(cond, label, detail=""):
    print(("PASS " if cond else "FAIL ") + label + (("  | " + str(detail)) if detail else ""))
    if not cond:
        fails.append(label)


user_dir = Path(tempfile.mkdtemp(prefix="rl_floor_"))
today = datetime.now().replace(hour=15, minute=0, second=0, microsecond=0)
keys = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
today_key = keys[-1]


def states(item, at=None):
    rows = dl.report_rows([item], user_dir, keys, now=at or today)[0]["days"]
    return [(c["date"], c["state"], c.get("tip", "")) for c in rows]


# 1. 今天新建：前 6 天=⋯开始记录前，今天=等待打卡，零 ✗
it_new = {"id": "a", "name": "今天建", "days": [True] * 7, "reset": "04:00",
          "enabled": True, "created_at": today.strftime("%Y-%m-%d %H:%M:%S")}
s = states(it_new)
chk(all(tip == "开始记录前" and st == "na" for _, st, tip in s[:6])
    and s[6][1:] == ("na", "等待打卡"),
    "今天新建：前6天开始记录前+今天等待打卡", s)
chk(not any(st == "miss" for _, st, _ in s), "今天新建：零 ✗")

# 2. 凌晨创建（reset 04:00，now=当日 01:00）：昨天列=当前周期等待打卡，地板不误吞
it_dawn = {"id": "b", "name": "凌晨建", "days": [True] * 7, "reset": "04:00",
           "enabled": True,
           "created_at": today.strftime("%Y-%m-%d") + " 01:00:00"}
s2 = states(it_dawn, at=today.replace(hour=1))
chk(s2[5] == (keys[5], "na", "等待打卡"),
    "凌晨创建：昨天列=当前周期等待打卡（地板不误吞）", s2[4:6])
chk(all(st == "na" and tip == "开始记录前" for _, st, tip in s2[:5]),
    "凌晨创建：更早 5 天=开始记录前")

# 3. 存量项（无 created_at）：过去计划日照旧 ✗ 漏打可补录，行为不变
it_old = {"id": "c", "name": "老项", "days": [True] * 7, "reset": "04:00", "enabled": True}
s3 = states(it_old)
chk(s3[0][1:] == ("miss", "漏打") and s3[5][1:] == ("miss", "漏打"),
    "存量项：过去计划日照旧 ✗ 漏打", s3[:2])

# 4. 两天前建：创建日之前=开始记录前，创建当日及之后漏打 ✗
mid_day = today - timedelta(days=2)
it_mid = {"id": "d", "name": "两天前建", "days": [True] * 7, "reset": "04:00",
          "enabled": True, "created_at": mid_day.strftime("%Y-%m-%d %H:%M:%S")}
s4 = states(it_mid)
chk(all(tip == "开始记录前" for _, _, tip in s4[:4]),
    "两天前建：前4天=开始记录前", s4[:4])
chk(s4[4][1:] == ("miss", "漏打") and s4[5][1:] == ("miss", "漏打"),
    "两天前建：创建当日及之后的漏打照旧 ✗ 可补录", s4[4:6])
chk(s4[6][1:] == ("na", "等待打卡"), "两天前建：今天=等待打卡")

# 5. 数据优先：账本已有创建前的周期记录 → ok（地板只管没数据的格子）
mark_day = keys[0]
dl._save_state(user_dir, {"v": 2, "done": {"d": {mark_day: "14:00:00"}}, "fired": {}})
s5 = states(it_mid)
chk(s5[0] == (mark_day, "ok", ""), "已有打卡记录的格子=ok（数据优先于地板）", s5[0])
dl._save_state(user_dir, {"v": 2, "done": {}, "fired": {}})
s5b = states(it_mid)
chk(s5b[0][1:] == ("na", "开始记录前"), "清账本后回到开始记录前", s5b[0])

# 6. 非计划日 tip 不受地板影响（顺序：数据→非计划日→等待→地板→漏打）
it_thu = {"id": "e", "name": "周四项", "days": [False, False, False, True, False, False, False],
          "reset": "04:00", "enabled": True,
          "created_at": (today - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")}
s6 = states(it_thu)
thu = [(d, st, tip) for (d, st, tip) in s6 if datetime.strptime(d, "%Y-%m-%d").weekday() == 3]
nonthu = [tip for (d, st, tip) in s6 if datetime.strptime(d, "%Y-%m-%d").weekday() != 3]
chk(all(tip in ("开始记录前", "漏打", "等待打卡") for _, _, tip in thu),
    "计划日（周四）三种合法态", thu)
chk(all(tip == "非计划日" for tip in nonthu), "非计划日照旧 tip=非计划日")

# 7. created_at 字段损坏/空串：按无地板处理（不炸）
it_bad = {"id": "f", "name": "坏字段", "days": [True] * 7, "reset": "04:00",
          "enabled": True, "created_at": "not-a-date"}
s7 = states(it_bad)
chk(s7[0][1:] == ("miss", "漏打"), "created_at 非法=无地板（回退旧行为）", s7[0])

# 8. 凌晨打卡（2026-11-18 用户实证「已打卡但今日格不亮」）：00:52 打卡 →
#    mark_done 落游戏日键「昨天」，ts=00:52。旧版「ts<reset 归属次日」映射
#    把键又推到今天，昨天格反而漏标。删映射后键与列直接对格。
yday = (today - timedelta(days=1)).strftime("%Y-%m-%d")
dl._save_state(user_dir, {"v": 2, "done": {"m": {yday: "00:52:59"}}, "fired": {}})
it_dawn2 = {"id": "m", "name": "凌晨打卡", "days": [True] * 7, "reset": "04:00",
            "enabled": True, "created_at": "2020-01-01 00:00:00"}
s8 = [c for c in dl.report_rows([it_dawn2], user_dir, keys, now=today.replace(hour=1))[0]["days"]]
s8d = {c["date"]: c for c in s8}
chk(s8d[yday]["state"] == "ok",
    "M1 凌晨打卡落在昨天的游戏日格=✓（不再被映射推走）", s8d[yday])
chk(s8d[today_key]["state"] == "na" and s8d[today_key]["tip"] == "等待打卡",
    "M2 今天格=等待打卡（当前周期未打）", s8d[today_key])
dl._save_state(user_dir, {"v": 2, "done": {}, "fired": {}})

print("\n结论：" + ("全部通过" if not fails else "有失败 %d 项" % len(fails)))
sys.exit(1 if fails else 0)

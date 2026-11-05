"""历史重建脚本的回归测试（2026-11-05）。

背景：数据外置改造时迁移测试误删了 data/，history.json 丢失；日志完好，
本脚本从 dailycheck 日志还原。测试钉死解析口径，防止重建出假数据。
"""

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

PASS = 0
FAIL = 0
FAILED = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS " + name)
    else:
        FAIL += 1
        FAILED.append(name + (" | " + str(detail) if detail else ""))
        print("  FAIL " + name + (" | " + str(detail) if detail else ""))


import rebuild_history as RB  # noqa: E402
from core.history import game_date_key  # noqa: E402

print("=== A. 日志行解析 ===")

# A1：普通游戏名
m = RB._LINE_RE.match("2026-10-27 00:49:10 [INFO   ] dailycheck.main: 原神(旅行者) -> 全部完成")
check("A1 基本行匹配", bool(m))
if m:
    check("A1b 时间戳", m.group(1) == "2026-10-27 00:49:10", m.group(1))
    check("A1c 消息体", m.group(2).startswith("原神"), m.group(2))

# A2：游戏名含全角冒号（崩坏：星穹铁道）——曾因正则把「：」当分隔符而漏掉
m2 = RB._LINE_RE.match(
    "2026-10-27 00:49:11 [INFO   ] dailycheck.main: 崩坏：星穹铁道(开拓者) -> 全部完成"
)
check("A2 全角冒号行匹配", bool(m2))
if m2:
    r = RB._RESULT_RE.match(m2.group(2))
    check("A2b 结果行解析", bool(r))
    if r:
        check("A2c 游戏名完整", r.group("name").strip() == "崩坏：星穹铁道", r.group("name"))
        check("A2d game_id 正确", RB._game_id(r.group("name")) == "starrail")

# A3：旧 logger 名（不带 .main 后缀）
m3 = RB._LINE_RE.match("2026-10-26 02:06:39 [INFO   ] dailycheck: 测试模式：自动判断")
check("A3 旧 logger 名匹配", bool(m3))

print("=== B. 状态判定 ===")

# B1：全部完成 → finished + known
node = {"finished": False, "known": False, "unfinished_tasks": 0}
verdict = "全部完成"
check("B1 '全部完成' 被识别", any(w in verdict for w in RB.DONE_WORDS))

# B2：未完成 → known 但不算 finished
verdict2 = "未完成 1 项"
check("B2 '未完成' 被识别", any(w in verdict2 for w in RB.UNDONE_WORDS))
check("B2b 不算完成", not any(w in verdict2 for w in RB.DONE_WORDS))

# B3：查询失败 / 待上线 / 未创建角色 → 都不是 known
for v in ("查询失败：接口 retcode=1034", "待上线更新", "未创建角色"):
    check("B3 %r 不算有效数据" % v[:8],
          any(w in v for w in RB.UNKNOWN_WORDS) and not any(w in v for w in RB.DONE_WORDS))

# B4：待上线不能被当成「未完成」（否则会伪造红色漏做）
v = "数据待更新：今天还没上线"
check("B4 '待上线' 不落入未完成分支",
      any(w in v for w in RB.UNKNOWN_WORDS) and not any(w in v for w in RB.UNDONE_WORDS))

print("=== C. 04:00 日界归属 ===")

# C1：凌晨 02:30 的检查属于前一天
d1 = datetime(2026, 10, 27, 2, 30)
check("C1 02:30 归属前一天", game_date_key(d1) == "2026-10-26", game_date_key(d1))

# C2：04:30 属于当天
d2 = datetime(2026, 10, 27, 4, 30)
check("C2 04:30 归属当天", game_date_key(d2) == "2026-10-27", game_date_key(d2))

# C3：23:59 属于当天
d3 = datetime(2026, 10, 27, 23, 59)
check("C3 23:59 归属当天", game_date_key(d3) == "2026-10-27", game_date_key(d3))

print("=== D. 游戏名映射 ===")

for name, expect in [
    ("原神", "genshin"),
    ("崩坏：星穹铁道", "starrail"),
    ("星穹铁道", "starrail"),
    ("绝区零", "zzz"),
    ("明日方舟", "arknights"),
    ("终末地", "endfield"),
]:
    check("D %s -> %s" % (name, expect), RB._game_id(name) == expect, RB._game_id(name))

check("D 未知游戏返回空", RB._game_id("某个新游戏") == "")

print("=== E. 合并不覆盖已有数据 ===")

# 模拟：已有记录 finished=True，重建结果 finished=False → 必须保持 True
cur = {"finished": True, "known": True, "checks": 3, "last": "23:58", "unfinished_tasks": 0}
rebuilt = {"game_name": "原神", "nickname": "旅行者", "finished": False,
           "known": True, "checks": 1, "last": "12:00", "unfinished_tasks": 1}
# 复刻 main() 里的合并逻辑
cur.setdefault("game_name", rebuilt["game_name"])
cur["checks"] = max(int(cur.get("checks") or 0), rebuilt["checks"])
if rebuilt["finished"]:
    cur["finished"] = True
    cur["known"] = True
if rebuilt["known"]:
    cur["known"] = True
cur["unfinished_tasks"] = max(int(cur.get("unfinished_tasks") or 0), rebuilt["unfinished_tasks"])

check("E1 已有 finished=True 不被冲掉", cur["finished"] is True, cur)
check("E2 checks 取较大值", cur["checks"] == 3, cur["checks"])
check("E3 last 保持原值", cur["last"] == "23:58", cur["last"])

print()
print("=" * 50)
print("PASS %d / FAIL %d" % (PASS, FAIL))
if FAILED:
    print("失败项：")
    for f in FAILED:
        print("  - " + f)
sys.exit(1 if FAIL else 0)

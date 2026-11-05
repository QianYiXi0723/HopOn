"""日界前落盘（03:50 静默班次）与终末地归零判定的回归测试。

背景（2026-11-05 用户报障）：「终末地明明每天都做了却显示没做」。
根因有两层：
  1. 终末地缺少方舟那样的归零判定 —— 跨 04:00 后快照陈旧，活跃度读成 0，
     被当成「真的一项没做」；
  2. 更根本的一层：用户常在 23:59 之后、04:00 之前打完日常，而下一轮巡检
     已经是次日 13:00 —— 那时游戏早已清零。等于「做完的那一刻，永远没有
     任何一次巡检撞上」。所以要在日界前补一次只落盘、不推送的巡检。

本测试钉死这两层行为，防止回退。
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import daemon  # noqa: E402
from core.querier import (  # noqa: E402
    _endfield_zeroed_mission,
    _is_endfield_snapshot_stale,
)

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
        FAILED.append(name + (" | " + detail if detail else ""))
        print("  FAIL " + name + (" | " + detail if detail else ""))


def make_detail(now_dt, login_dt, activation=0, maximum=100):
    return {
        "currentTs": int(now_dt.timestamp()),
        "base": {"lastLoginTime": int(login_dt.timestamp())},
        "dailyMission": {
            "dailyActivation": activation,
            "maxDailyActivation": maximum,
        },
    }


print("=== A. 终末地快照陈旧判定 ===")

# A1：昨夜 19:15 登录，今晨 05:49 查询 -> 跨过今日 04:00，快照陈旧
now = datetime(2026, 11, 5, 5, 49)
login = datetime(2026, 11, 4, 19, 15)
d = make_detail(now, login, activation=0)
check("A1 昨日登录跨 04:00 -> 判陈旧", _is_endfield_snapshot_stale(d) is True)
check("A1 b 归零返回 (0, 100)", _endfield_zeroed_mission(d) == (0, 100))

# A2：今天上午登录过，中午查 -> 快照新鲜，用真实值
now2 = datetime(2026, 11, 5, 12, 59)
login2 = datetime(2026, 11, 5, 9, 0)
d2 = make_detail(now2, login2, activation=60)
check("A2 今日登录 -> 不判陈旧", _is_endfield_snapshot_stale(d2) is False)
check("A2 b 不归零（返回 None）", _endfield_zeroed_mission(d2) is None)

# A3：字段缺失 -> 宁可不判定，也不误伤
check("A3 空 dict 不判陈旧", _is_endfield_snapshot_stale({}) is False)
check("A3 b 空 dict 不归零", _endfield_zeroed_mission({}) is None)

# A4：上限非法 -> 不归零（total<=0 视为不可用）
now3 = datetime(2026, 11, 5, 5, 0)
bad = make_detail(now3, datetime(2026, 11, 4, 20, 0), maximum=0)
check("A4 上限为 0 -> 不归零", _endfield_zeroed_mission(bad) is None)

# A5：刚刚跨过 04:00 后一分钟（登录在 04:00 之前）-> 仍判陈旧
now4 = datetime(2026, 11, 5, 4, 1)
login4 = datetime(2026, 11, 5, 3, 59)
d4 = make_detail(now4, login4)
check("A5 03:59 登录、04:01 查 -> 判陈旧", _is_endfield_snapshot_stale(d4) is True)


print("=== B. 日界前班次调度 ===")

orig_enabled = daemon.read_dayend_refresh
orig_at = daemon.read_dayend_refresh_at

try:
    daemon.read_dayend_refresh = lambda: True
    daemon.read_dayend_refresh_at = lambda: "03:50"

    empty_state = {"fired": {}}

    # B1：正好 03:50 -> 该跑
    r = daemon.pending_dayend_refresh(
        now=datetime(2026, 11, 5, 3, 50, 0), state=empty_state
    )
    check("B1 03:50 命中", r is not None and r[0] == "2026-11-05 03:50", str(r))

    # B2：03:50 之前 -> 不跑
    r = daemon.pending_dayend_refresh(
        now=datetime(2026, 11, 5, 3, 40, 0), state=empty_state
    )
    check("B2 03:40 不跑", r is None, str(r))

    # B3：03:55（已过目标、未到日界）-> 补跑
    r = daemon.pending_dayend_refresh(
        now=datetime(2026, 11, 5, 3, 55, 0), state=empty_state
    )
    check("B3 03:55 补跑", r is not None, str(r))

    # B4：04:01（已过日界）-> 绝不再补，数据已被清零
    r = daemon.pending_dayend_refresh(
        now=datetime(2026, 11, 5, 4, 1, 0), state=empty_state
    )
    check("B4 04:01 不补（已过日界）", r is None, str(r))

    # B5：中午 13:00 -> 与本班次无关
    r = daemon.pending_dayend_refresh(
        now=datetime(2026, 11, 5, 13, 0, 0), state=empty_state
    )
    check("B5 13:00 不跑", r is None, str(r))

    # B6：已跑过 -> 不重复（同一份 fired 账本）
    st = {"fired": {"2026-11-05 03:50": "2026-11-05T03:50:01"}}
    r = daemon.pending_dayend_refresh(
        now=datetime(2026, 11, 5, 3, 51, 0), state=st
    )
    check("B6 已跑过不重复", r is None, str(r))

    # B7：开关关闭 -> 永不跑
    daemon.read_dayend_refresh = lambda: False
    r = daemon.pending_dayend_refresh(
        now=datetime(2026, 11, 5, 3, 50, 0), state=empty_state
    )
    check("B7 开关关闭时不跑", r is None, str(r))
    daemon.read_dayend_refresh = lambda: True

    # B8：配置了非法时刻 -> 静默跳过，不炸
    daemon.read_dayend_refresh_at = lambda: "25:99"
    r = daemon.pending_dayend_refresh(
        now=datetime(2026, 11, 5, 3, 50, 0), state=empty_state
    )
    check("B8 非法时刻不炸且不跑", r is None, str(r))
    daemon.read_dayend_refresh_at = lambda: "03:50"

    # B9：本班次不进普通补发队列（避免 04:00 后还被当成 missed 补跑）
    slots = daemon.pending_slots(
        now=datetime(2026, 11, 5, 3, 55, 0),
        check_times=["13:00"],
        state=empty_state,
        catch_up_minutes=180,
    )
    check("B9 日界班次不混入普通补发", all("03:50" not in s for s, _ in slots), str(slots))

finally:
    daemon.read_dayend_refresh = orig_enabled
    daemon.read_dayend_refresh_at = orig_at


print("=== C. quiet_push 语义 ===")

import inspect  # noqa: E402
import main as main_mod  # noqa: E402

sig = inspect.signature(main_mod.run_once)
check("C1 run_once 有 quiet_push 参数", "quiet_push" in sig.parameters)
check(
    "C2 quiet_push 默认关闭",
    sig.parameters["quiet_push"].default is False,
    str(sig.parameters["quiet_push"].default),
)

dsig = inspect.signature(daemon.run_check)
check("C3 run_check 透传 quiet_push", "quiet_push" in dsig.parameters)

# C4：quiet_push 必须是**硬闸**，连 force_alert 测试模式也挡。
# 回归背景：should_push 的判断顺序里 force_alert 排在 quiet_now 之前，
# 若只在 should_push 层面处理，用户把测试模式留在「强制提醒」时，
# 凌晨 03:50 的落盘班次仍会推送——正是这条 bug 的形态。
from core.config import (  # noqa: E402
    TEST_MODE_AUTO,
    TEST_MODE_FORCE_ALERT,
    TEST_MODE_FORCE_SILENT,
)
from core.notifier import should_push  # noqa: E402

check(
    "C4a force_alert 会绕过 should_push 的 quiet_now（说明硬闸必要）",
    should_push(TEST_MODE_FORCE_ALERT, True, 1, quiet_now=True, quiet_window="x")[0]
    is True,
)
check(
    "C4b auto 模式下 quiet_now 能挡住",
    should_push(TEST_MODE_AUTO, True, 1, quiet_now=True, quiet_window="x")[0] is False,
)
check(
    "C4c force_silent 本来就静默",
    should_push(TEST_MODE_FORCE_SILENT, True, 1, quiet_now=False)[0] is False,
)

# C5：源码级确认 hard gate 存在（quiet_push 分支把 alert 强制置 False）
# 注意 run_once 里有两处 "if quiet_push:"：第一处设 quiet_now（免打扰），
# 第二处才是硬闸。用「alert, reason = False」这句定位，避免认错。
src = inspect.getsource(main_mod.run_once)
gate_marker = "alert, reason = False"
gate_at = src.find(gate_marker)
check("C5 run_once 里有 quiet_push 硬闸", gate_at >= 0)
if gate_at >= 0:
    before = src[max(0, gate_at - 200): gate_at]
    check(
        "C5b 硬闸在 quiet_push 分支内",
        "if quiet_push:" in before,
        before[-80:].replace("\n", " "),
    )
    # 硬闸必须出现在 should_push 调用**之后**，否则会被覆盖
    call_at = src.find("alert, reason = should_push(")
    check("C5c 硬闸在 should_push 之后", call_at >= 0 and gate_at > call_at)




print()
print("=" * 50)
print("PASS %d / FAIL %d" % (PASS, FAIL))
if FAILED:
    print("失败项：")
    for f in FAILED:
        print("  - " + f)
sys.exit(1 if FAIL else 0)

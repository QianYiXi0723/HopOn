"""W1/W2 批次功能的回归测试（2026-11-05 审查发现的两个 bug 钉死）。

本文件覆盖三件事：
  A. quiet_push 硬闸 —— 连 force_alert 测试模式也挡（daemon 凌晨不打扰）
  B. 周报趋势基线 —— 上周缺数据时不许算出「假进步」
  C. 免打扰窗口边界 —— 跨零点/同日/非法输入
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

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


print("=== A. 周报趋势：不许伪造基线 ===")
from daemon import format_weekly_body  # noqa: E402

CUR = {
    "days_count": 7,
    "games": [
        {"game": "genshin", "name": "原神", "known_days": 7,
         "done_days": 5, "missed_days": 2}
    ],
}


def trend_of(prev):
    body = format_weekly_body(CUR, prev)
    return body


# A1：上周完全没有 done_days 字段 → 不许出现「较上周 +5 天」
body = trend_of({"games": [{"game": "genshin", "name": "原神"}]})
check("A1 上周缺 done_days 不带趋势", "较上周" not in body, body)

# A2：上周 done_days 非法 → 不带趋势
body = trend_of({"games": [{"game": "genshin", "name": "原神", "done_days": "abc"}]})
check("A2 上周数据非法不带趋势", "较上周" not in body, body)

# A3：上周真实 3 天 → 应显示 +2
body = trend_of({"games": [{"game": "genshin", "name": "原神", "done_days": 3}]})
check("A3 上周 3 天 -> +2", "较上周 +2 天" in body, body)

# A4：上周真实 5 天（持平）→ 不显示（delta==0）
body = trend_of({"games": [{"game": "genshin", "name": "原神", "done_days": 5}]})
check("A4 持平不显示趋势", "较上周" not in body, body)

# A5：上周真实 0 天 → 应显示 +5（这是真进步，不能和 A1 混淆）
body = trend_of({"games": [{"game": "genshin", "name": "原神", "done_days": 0}]})
check("A5 上周真 0 天 -> +5", "较上周 +5 天" in body, body)

# A6：上周 7 天、本周 5 天 → 负数要带符号
body = trend_of({"games": [{"game": "genshin", "name": "原神", "done_days": 7}]})
check("A6 退步显示 -2", "较上周 -2 天" in body, body)

# A7：无上周 digest → 不带趋势
body = format_weekly_body(CUR, None)
check("A7 无基线不带趋势", "较上周" not in body, body)

# A8：本周无数据的游戏不进周报
cur0 = {"days_count": 7, "games": [
    {"game": "genshin", "name": "原神", "known_days": 0, "done_days": 0, "missed_days": 0}]}
check("A8 本周无数据整句为空", format_weekly_body(cur0, None) == "")


print("=== B. quiet_push 硬闸 ===")
import inspect  # noqa: E402

import main as main_mod  # noqa: E402
from core.config import (  # noqa: E402
    TEST_MODE_AUTO,
    TEST_MODE_FORCE_ALERT,
)
from core.notifier import should_push  # noqa: E402

# B1：确认 force_alert 确实会绕过 should_push 的 quiet_now（所以硬闸必要）
check(
    "B1 force_alert 绕过 quiet_now",
    should_push(TEST_MODE_FORCE_ALERT, True, 1, quiet_now=True, quiet_window="x")[0]
    is True,
)
# B2：auto 模式下 quiet_now 能挡
check(
    "B2 auto + quiet_now 挡住",
    should_push(TEST_MODE_AUTO, True, 1, quiet_now=True, quiet_window="x")[0] is False,
)

src = inspect.getsource(main_mod.run_once)
gate_at = src.find("alert, reason = False")
check("B3 硬闸存在", gate_at >= 0)
if gate_at >= 0:
    before = src[max(0, gate_at - 220): gate_at]
    check("B4 硬闸在 quiet_push 分支内", "if quiet_push:" in before)
    call_at = src.find("alert, reason = should_push(")
    check("B5 硬闸在 should_push 之后", call_at >= 0 and gate_at > call_at)


print("=== C. 免打扰窗口边界 ===")
from core.notifier import in_quiet_window  # noqa: E402

cases = [
    ("23:00", "08:00", 23 * 60, True, "C1 跨零点 23:00 静默"),
    ("23:00", "08:00", 0, True, "C2 跨零点 00:00 静默"),
    ("23:00", "08:00", 7 * 60 + 59, True, "C3 跨零点 07:59 静默"),
    ("23:00", "08:00", 8 * 60, False, "C4 跨零点 08:00 恢复"),
    ("23:00", "08:00", 12 * 60, False, "C5 跨零点 12:00 推送"),
    ("09:00", "17:00", 10 * 60, True, "C6 同日 10:00 静默"),
    ("09:00", "17:00", 8 * 60, False, "C7 同日 08:00 推送"),
]
for start, end, mins, expect, name in cases:
    check(name, in_quiet_window(mins, start, end) is expect)

# C8：非法输入不崩溃
for bad in [("", ""), ("bad", "x"), ("25:00", "08:00")]:
    try:
        r = in_quiet_window(600, bad[0], bad[1])
        ok = r is False
        err = ""
    except Exception as exc:  # noqa: BLE001
        ok, err = False, str(exc)
    check("C8 非法输入 %r 不崩溃且不静默" % (bad,), ok, err)


print()
print("=" * 50)
print("PASS %d / FAIL %d" % (PASS, FAIL))
if FAILED:
    print("失败项：")
    for f in FAILED:
        print("  - " + f)
sys.exit(1 if FAIL else 0)

# -*- coding: utf-8 -*-
"""活动类型标签兜底（优化清单 #13，2026-11-26）。

真实数据实证：星铁活动「位面分裂/异器盈界/奇库寻遗」无抽卡资源(reward 空)，
但原始数据自带 act_type 字段（ActivityTypeDouble=双倍掉落 / ActivityTypeOther=其它）。
对标终末地用 calendarTag 填 reward 位的既有做法，星铁无抽卡资源时用 act_type 映射的
类型文案填 reward，避免"既没抽卡资源又没标注"。

只针对星铁：原神/绝区零都有抽卡资源，终末地已解决，鸣潮/战双当前无缓存。
用法：python tests/rl_test_act_type_label.py   （0 = 通过）
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import core.activities as A  # noqa: E402

PASS = 0
FAIL = 0

# 未来时间窗（确保不被 end<=now 过滤），不依赖固定日期
_T0 = int(time.time()) + 86400
_T1 = int(time.time()) + 3 * 86400
_TIMEINFO = {"start_ts": str(_T0), "end_ts": str(_T1)}


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"  {detail}" if detail else ""))


def run():
    check("T1 双倍掉落有标签", A._starrail_act_type_label("ActivityTypeDouble") == "双倍活动",
          repr(A._starrail_act_type_label("ActivityTypeDouble")))
    check("T2 其它活动有标签", A._starrail_act_type_label("ActivityTypeOther") == "其它活动",
          repr(A._starrail_act_type_label("ActivityTypeOther")))
    check("T3 未知类型返回空串（不编造）", A._starrail_act_type_label("ActivityTypeUnknown") == "",
          repr(A._starrail_act_type_label("ActivityTypeUnknown")))
    check("T4 空值返回空串", A._starrail_act_type_label("") == "")

    # 无抽卡资源(reward 空)时用 act_type 填 reward
    items = A._starrail_parse_act_list([{
        "id": 999, "name": "测试双倍活动", "act_type": "ActivityTypeDouble",
        "time_info": _TIMEINFO, "reward_list": [],
    }])
    check("T5 无抽卡资源时 reward 填类型标签",
          len(items) == 1 and items[0]["reward"] == "双倍活动",
          repr(items[0]["reward"]) if items else "no items")

    # 有抽卡资源时 reward 保持抽卡文案（不被类型覆盖）
    items2 = A._starrail_parse_act_list([{
        "id": 998, "name": "测试星琼活动", "act_type": "ActivityTypeDouble",
        "time_info": _TIMEINFO, "reward_list": [{"name": "星琼", "num": 900}],
    }])
    check("T6 有抽卡资源时 reward 保持抽卡文案",
          len(items2) == 1 and items2[0]["reward"] == "星琼 900",
          repr(items2[0]["reward"]) if items2 else "no items")


if __name__ == "__main__":
    run()
    print(f"\n[结论] {PASS}/{PASS + FAIL} 通过")
    sys.exit(1 if FAIL else 0)

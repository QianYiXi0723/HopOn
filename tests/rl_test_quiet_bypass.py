#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""免打扰旁路测试（2026-11-19 #164）：两条推送侧路必须纳入免打扰窗。

背景：免打扰（push.quiet_enabled/quiet_start/quiet_end）此前只对巡检主链路
生效，两条侧路直发 push_all 绕过判定——①周报 run_weekly_report（周日 21:00
槽位可能落在窗内）②独立打卡提醒 _check_manual_dailies（用户自设时刻可能落
在窗内）。修复=推送前查窗，窗内安静跳过（不打 INFO）且不记账，依赖既有补
发机制自然延后：standalone_due 触发窗 [目标-30s, +∞) → 窗后第一个 tick 补推；
weekly_due 判「周」不落 state → 窗后 tick 自动补发（weekly_slot_week 只在
下一个周日 21:00 翻周键，跨天窗成立）。

dailies/weekly 链路走真函数（tmp 目录落 state，磁盘零写入真实数据）；
config 与推送通道打桩。
"""
from __future__ import annotations

import json
import sys
import tempfile
import types
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import daemon as D
import core.dailies as dailies_mod
import core.notifier as notifier_mod
from core import config as cfgmod
from core.models import PushResult

PASS = 0


def check(cond: bool, label: str) -> None:
    global PASS
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    PASS += 1
    print(f"  ✓ {label}")


class FakeNotifier:
    records_history = False

    def __init__(self, name="bark"):
        self.name = name
        self.sent = 0

    def send(self, title, body, dry_run=False):
        if dry_run:
            return PushResult(self.name, False, "dry-run 未发送", skipped=True)
        self.sent += 1
        return PushResult(self.name, True, "推送成功")


def _push_cfg(enabled, start="23:00", end="08:00"):
    return types.SimpleNamespace(quiet_enabled=enabled,
                                 quiet_start=start, quiet_end=end)


def _item_dict():
    """目标时刻=当前时刻的 standalone 项（触发窗 [now-30s, +∞) 必到期）。"""
    return dict(id="d1", name="测试打卡", mode="standalone",
                time=datetime.now().strftime("%H:%M"),
                reset="daily", template="", enabled=True, days=[True] * 7)


def _window_covering_now():
    """覆盖当前时刻的免打扰窗（自动处理跨零点）。"""
    now = datetime.now()
    return (now - timedelta(minutes=10)).strftime("%H:%M"), \
           (now + timedelta(minutes=90)).strftime("%H:%M")


def _window_ended_now():
    """已结束的免打扰窗（end=now-1min，start 再往前推 3 小时）。"""
    now = datetime.now()
    return (now - timedelta(hours=3)).strftime("%H:%M"), \
           (now - timedelta(minutes=1)).strftime("%H:%M")


def _run_dailies(push_cfg, runs=1):
    """跑 N 次 daemon._check_manual_dailies（真 dailies 链 + tmp USER_DIR）。

    返回 ( FakeNotifier, tmp_user_dir )，send 计数即真实推送次数。
    """
    tmp = Path(tempfile.mkdtemp(prefix="rl_test_quiet_bypass_"))
    notifier = FakeNotifier()
    saved = (D.USER_DIR, cfgmod.load_config, notifier_mod.build_notifiers)
    try:
        D.USER_DIR = tmp
        cfgmod.load_config = lambda *a, **k: types.SimpleNamespace(
            push=push_cfg, custom_dailies=[types.SimpleNamespace(**_item_dict())])
        notifier_mod.build_notifiers = lambda push_cfg_: [notifier]
        for _ in range(runs):
            D._check_manual_dailies()
    finally:
        D.USER_DIR, cfgmod.load_config, notifier_mod.build_notifiers = saved
    return notifier, tmp


def _run_weekly(push_cfg):
    """跑一次 daemon._maybe_weekly_report（run_weekly_report 打桩计数+真落 state）。"""
    tmp = Path(tempfile.mkdtemp(prefix="rl_test_quiet_bypass_wk_"))
    state_file = tmp / ".last_weekly"
    calls = {"n": 0}
    saved = (D.WEEKLY_STATE_FILE, D.run_weekly_report, cfgmod.load_config)
    try:
        D.WEEKLY_STATE_FILE = state_file
        D.run_weekly_report = lambda: calls.__setitem__("n", calls["n"] + 1) or True
        cfgmod.load_config = lambda *a, **k: types.SimpleNamespace(push=push_cfg)
        D._maybe_weekly_report()
    finally:
        D.WEEKLY_STATE_FILE, D.run_weekly_report, cfgmod.load_config = saved
    return calls["n"], state_file


def main() -> int:
    # ---- 独立打卡提醒（_check_manual_dailies）----
    notifier, tmp = _run_dailies(_push_cfg(True, *_window_covering_now()))
    check(notifier.sent == 0, "Q1 窗内：安静跳过不推送（零 send）")
    check(dailies_mod.standalone_due([_item_dict()], tmp),
          "Q1b 窗内：未写 mark_fired，standalone_due 仍列到期（窗后可补推）")

    notifier, _ = _run_dailies(_push_cfg(True, *_window_ended_now()), runs=2)
    # runs=2 = 两个 tick：第一 tick 补推成功且 mark_fired 真记账；第二 tick 因
    # fired 账本命中不再重推（若 a462628 的 str 传参 bug 回归，mark_fired 必炸
    # → 账本写不进 → 第二 tick 重推 → sent=2）
    check(notifier.sent == 1,
          "Q2 窗后两个 tick 只推一次：补推成功+记账生效（str-bug 回归钉）")

    notifier, _ = _run_dailies(_push_cfg(False))
    check(notifier.sent == 1, "Q3 quiet_enabled=False：正常推送（回归）")

    notifier, _ = _run_dailies(_push_cfg(True, "bad", "08:00"))
    check(notifier.sent == 1, "Q4 时间串非法：宁可打扰不静默（in_quiet_window False）")

    # ---- 周报（_maybe_weekly_report）----
    calls, state_file = _run_weekly(_push_cfg(True, *_window_covering_now()))
    check(calls == 0, "Q5 窗内：不调 run_weekly_report")
    check(not state_file.exists(), "Q5b 窗内：不落 save_weekly_state（周内可补发）")

    calls, state_file = _run_weekly(_push_cfg(True, *_window_ended_now()))
    check(calls == 1, "Q6 窗后：补发周报")
    check(state_file.exists()
          and json.loads(state_file.read_text(encoding="utf-8"))["week"]
          == D.weekly_slot_week(datetime.now()),
          "Q6b 补发后 state 落盘，week 键=当前槽位周")

    calls, state_file = _run_weekly(_push_cfg(False))
    check(calls == 1 and state_file.exists(),
          "Q7 quiet_enabled=False：正常发送+落 state（回归）")

    # ---- 跨天窗周键不变量（周报补发链的纯函数证据）----
    sun = datetime(2026, 11, 15)          # 2026-11-15=周日
    late_sun = sun.replace(hour=23, minute=30)     # 窗内跳过的周日夜
    mon_early = sun.replace(day=16, hour=0, minute=30)   # 次日凌晨仍在窗内
    mon_after = sun.replace(day=16, hour=7, minute=30)   # 窗结束后第一个 tick
    key_late = D.weekly_slot_week(late_sun)
    check(key_late == D.weekly_slot_week(mon_early)
          == D.weekly_slot_week(mon_after),
          "Q8 跨天窗：周日夜→次日凌晨→窗后，周键不变（补发链成立）")
    check(key_late != D.weekly_slot_week(sun.replace(hour=20, minute=59)),
          "Q8b 槽位边界：周日 21:00 前属上周键（翻键边界）")

    # ---- _quiet_now 直测（跨零点/边界/非法串/未启用）----
    cfg = _push_cfg(True, "23:00", "08:00")
    check(D._quiet_now(cfg, now=datetime(2026, 11, 19, 0, 30)),
          "Q9a 跨零点窗 23:00-08:00 @00:30 → 窗内")
    check(D._quiet_now(cfg, now=datetime(2026, 11, 19, 23, 0)),
          "Q9b 起点含端 23:00 整 → 窗内")
    check(not D._quiet_now(cfg, now=datetime(2026, 11, 19, 8, 0)),
          "Q9c 终点含端 08:00 整 → 窗外（now<end 语义）")
    check(not D._quiet_now(cfg, now=datetime(2026, 11, 19, 12, 0)),
          "Q9d 正午 → 窗外")
    check(not D._quiet_now(_push_cfg(True, "bad", "08:00")),
          "Q9e 时间串非法 → False（宁可打扰）")
    check(not D._quiet_now(_push_cfg(False)),
          "Q9f quiet_enabled=False → False")

    print(f"rl_test_quiet_bypass: {PASS} 项断言全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""推送送达记账测试（2026-11-19）：toast 页内镜像不得打穿防重推保护。

背景：ToastNotifier.send 恒 ok=True（写页内队列即返回，与页面是否打开无关），
又恒排在 build_notifiers 首位——「任一渠道成功就算送达」的记账判定被它打穿：
手机渠道全挂时提醒被记「已推」而丢失（独立打卡当天丢、活动上新终身只推一次
=永久丢、体力回满复核被跳过）。修复后判定=远程渠道至少一个成功；一个远程
渠道都没配时退回原判定（否则只配页内的用户每轮重推）。

两处判定点都测：
  * daemon._check_manual_dailies → mark_fired
  * main.run_once 3.5 块 → mark_tier_notified / mark_notified / _mark_resin_pushed

推送走真实 core.notifier.push_all 管线（fake 通道 records_history=False，
绝不碰 data/push_history.json）；push_guard / history / activities /
dailies 记账全部打桩，磁盘零写入。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import daemon as D
import main as M
import core.activities as activities
import core.dailies as dailies_mod
import core.history as history
import core.notifier as notifier_mod
import core.push_guard as push_guard
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
    """假推送通道：行为镜像 BaseNotifier 契约；records_history=False 不写台账。"""

    records_history = False

    def __init__(self, name, ok=True, raises=None):
        self.name = name
        self._ok = ok
        self._raises = raises

    def send(self, title, body, dry_run=False):
        if dry_run:
            return PushResult(self.name, False, "dry-run 未发送", skipped=True)
        if self._raises is not None:
            raise self._raises
        return PushResult(self.name, self._ok,
                          "推送成功" if self._ok else "接口返回失败")


def _toast():
    return FakeNotifier("toast", ok=True)   # 恒 ok，与真实 ToastNotifier 同款


def _alert(game_id):
    return types.SimpleNamespace(game_id=game_id,
                                 warn_line=lambda: "测试预警行")


def _fake_report():
    return types.SimpleNamespace(
        need_alert=True, unfinished=[], finished=[], failed=[], statuses=[],
        unfinished_task_count=0,
        resource_alerts=[_alert("genshin")],
        activity_alerts=[_alert("genshin")],
        activity_start_alerts=[_alert("genshin")],
        to_push_payload=lambda: ("测试标题", "测试正文"),
        to_console_text=lambda: "",
    )


def _fake_config():
    return types.SimpleNamespace(
        test_mode=False,
        activity_alert_enabled=False,   # 1.5 活动缓存整段跳过
        custom_dailies=[],              # 1.7 手动打卡整段跳过
        resource_thresholds={},
        enabled_accounts=[],
        config_path=Path(__file__),     # 只被打桩函数消费，绝不真读
        push=types.SimpleNamespace(
            quiet_enabled=False, quiet_start="23:00", quiet_end="07:00",
            always_notify=False, channels=["bark"],
        ),
    )


_MAIN_PATCH_KEYS = ("collect_statuses", "build_report", "should_push",
                    "_active_notifiers", "_record_resin_watch",
                    "_refresh_mihoyo_nicknames", "_mark_resin_pushed")


def run_main_scenario(notifiers, dry_run=False):
    """跑一轮 main.run_once，返回 (退出码, 记账记录)。所有落盘点全部打桩。"""
    marks = {"tier": 0, "start": 0, "resin": 0, "resin_games": []}
    saved_main = {k: getattr(M, k) for k in _MAIN_PATCH_KEYS}
    saved_core = (push_guard.record, history.record,
                  activities.mark_tier_notified, activities.mark_notified)

    def _mark_resin(games):
        marks["resin"] += 1
        marks["resin_games"] = list(games)

    try:
        M.collect_statuses = lambda cfg: []
        M.build_report = lambda *a, **k: _fake_report()
        M.should_push = lambda *a, **k: (True, "测试模式")
        M._active_notifiers = lambda push_cfg: list(notifiers)
        M._record_resin_watch = lambda *a, **k: None
        M._refresh_mihoyo_nicknames = lambda *a, **k: None
        M._mark_resin_pushed = _mark_resin
        push_guard.record = lambda channel, ok, **k: None
        history.record = lambda report: None
        activities.mark_tier_notified = lambda alerts: marks.__setitem__(
            "tier", marks["tier"] + 1)
        activities.mark_notified = lambda alerts: marks.__setitem__(
            "start", marks["start"] + 1)
        rc = M.run_once(_fake_config(), dry_run=dry_run)
    finally:
        for k, v in saved_main.items():
            setattr(M, k, v)
        (push_guard.record, history.record,
         activities.mark_tier_notified, activities.mark_notified) = saved_core
    return rc, marks


def run_daemon_scenario(notifiers):
    """跑一次 daemon._check_manual_dailies，返回 (mark_fired 收到的 id 列表, 入参类型)。"""
    fired = []
    kinds = []
    saved = (cfgmod.load_config, dailies_mod.standalone_due,
             dailies_mod.render_message, dailies_mod.mark_fired,
             notifier_mod.build_notifiers)
    try:
        cfgmod.load_config = lambda *a, **k: types.SimpleNamespace(
            push=None,
            custom_dailies=[types.SimpleNamespace(
                id="d1", name="测试打卡", mode="standalone", time="09:00",
                reset="daily", template="", enabled=True, days=[True] * 7)],
        )
        dailies_mod.standalone_due = lambda items, user_dir: [
            {"id": "d1", "name": "测试打卡", "time": "09:00"}]
        dailies_mod.render_message = lambda it, done: "测试正文"

        def _fake_mark_fired(item, user_dir):
            # 2026-11-19 修复后 daemon 传完整 item dict（与 core.mark_fired 合同
            # 一致）；兼容 str 只是历史形态容错
            fired.append(item.get("id") if isinstance(item, dict) else str(item))
            kinds.append(type(item).__name__)

        dailies_mod.mark_fired = _fake_mark_fired
        notifier_mod.build_notifiers = lambda push_cfg: list(notifiers)
        D._check_manual_dailies()
    finally:
        (cfgmod.load_config, dailies_mod.standalone_due,
         dailies_mod.render_message, dailies_mod.mark_fired,
         notifier_mod.build_notifiers) = saved
    return fired, kinds


def main() -> int:
    # ---- daemon 侧：_check_manual_dailies 的 mark_fired 记账判定 ----
    fired, kinds = run_daemon_scenario([_toast(), FakeNotifier("bark", ok=True)])
    check(fired == ["d1"], "D1 toast ok + 远程 ok → 记账 mark_fired")
    check(kinds == ["dict"],
          "D1b mark_fired 收到完整 item dict（core 合同；a462628 的 str 传参回归钉）")

    fired, _ = run_daemon_scenario([_toast(), FakeNotifier("bark", ok=False),
                                    FakeNotifier("serverchan", ok=False)])
    check(fired == [], "D2 toast ok + 远程全 fail → 不记账（核心回归）")

    fired, _ = run_daemon_scenario([_toast()])
    check(fired == ["d1"], "D3 只配页内（无远程渠道）→ 退回判定记账")

    fired, _ = run_daemon_scenario([FakeNotifier("toast", raises=RuntimeError("boom")),
                                    FakeNotifier("bark", ok=True)])
    check(fired == ["d1"], "D4 toast 异常(ok=False) + 远程 ok → 记账")

    # ---- main 侧：run_once 3.5 块的三处记账 ----
    rc, marks = run_main_scenario([_toast(), FakeNotifier("bark", ok=True)])
    check(rc == 0 and marks["tier"] == 1 and marks["start"] == 1
          and marks["resin"] == 1 and marks["resin_games"] == ["genshin"],
          "M1 toast ok + 远程 ok → 三处记账齐发（resin 收到本轮预警游戏）")

    rc, marks = run_main_scenario([_toast(), FakeNotifier("bark", ok=False)])
    check(rc == 0 and marks["tier"] == 0 and marks["start"] == 0
          and marks["resin"] == 0,
          "M2 toast ok + 远程全 fail → 三处记账全零（核心回归；rc 仍 0=1108 退出码分支现状）")

    rc, marks = run_main_scenario([_toast()])
    check(rc == 0 and marks["tier"] == 1 and marks["start"] == 1
          and marks["resin"] == 1,
          "M3 只配页内（无远程渠道）→ 退回判定记账")

    rc, marks = run_main_scenario([FakeNotifier("toast", raises=RuntimeError("boom")),
                                   FakeNotifier("bark", ok=True)])
    check(rc == 0 and marks["tier"] == 1 and marks["start"] == 1
          and marks["resin"] == 1,
          "M4 toast 异常(ok=False) + 远程 ok → 记账")

    rc, marks = run_main_scenario([_toast(), FakeNotifier("bark", ok=True)],
                                  dry_run=True)
    check(rc == 0 and marks["tier"] == 0 and marks["start"] == 0
          and marks["resin"] == 0,
          "M5 dry_run → 三处记账全零")

    rc, marks = run_main_scenario([FakeNotifier("toast", raises=RuntimeError("boom")),
                                   FakeNotifier("bark", ok=False)])
    check(marks["tier"] == 0 and marks["start"] == 0 and marks["resin"] == 0,
          "M6 toast 异常 + 远程也 fail（真全败）→ 不记账")

    print(f"rl_test_push_accounting: {PASS} 项断言全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())

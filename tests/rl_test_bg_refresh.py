#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""后台静默刷新调度测试（2026-11-13）：三档间隔/总开关/巡检邻域/自停护栏。

核心=daemon._bg_refresh_tick 的决策逻辑：不该打的电话一个都不打，
该打的一次打到（HTTP 层被 monkeypatch，绝不真查游戏）。
"""
from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import daemon as D
from core import config as cfgmod

PASS = 0


def check(cond: bool, label: str) -> None:
    global PASS
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    PASS += 1
    print(f"  ✓ {label}")


def _stub_config(enabled=True, games=("genshin", "endfield"), times=()):
    accs = [types.SimpleNamespace(game=g) for g in games]
    return types.SimpleNamespace(bg_refresh_enabled=enabled,
                                 check_times=list(times),
                                 enabled_accounts=accs)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="rl_test_bg_refresh_"))
    state_path = tmp / "bg_refresh_state.json"
    calls: list[str] = []

    D._bg_state_path = lambda: state_path   # 状态落 tmp，绝不碰真实 data/
    D.CONFIG_PATH = tmp / "config.json"     # tick 里读不到就当 disabled 跳过
    cfgmod.load_config = lambda *a, **k: _stub_config()

    def install_call(fn):
        def wrap(fam):
            calls.append(fam)
            return fn(fam)
        D._bg_refresh_call = wrap
        calls.clear()

    now = __import__("datetime").datetime.now()

    # ---- 1) 首轮：米游社/森空岛两族（有启用游戏）都到期触发 ----
    install_call(lambda fam: (True, False))
    D._bg_refresh_tick()
    check(sorted(calls) == ["mihoyo", "skland"], "首轮两族都触发（库街区无启用账号不触发）")
    st = D._bg_state_read()
    check(st["last"].get("mihoyo") and st["last"].get("skland"), "两族时间戳已记账")

    # ---- 2) 刚刷过：间隔内不重打 ----
    install_call(lambda fam: (True, False))
    D._bg_refresh_tick()
    check(calls == [], "间隔内不重打（60/180 分钟档未到）")

    # ---- 3) 总开关关：直接跳过 ----
    cfgmod.load_config = lambda *a, **k: _stub_config(enabled=False)
    install_call(lambda fam: (True, False))
    D._bg_refresh_tick()
    check(calls == [], "总开关关=一个电话都不打")
    cfgmod.load_config = lambda *a, **k: _stub_config()

    # ---- 4) 巡检邻域（±3 分钟）跳过：把 last 拨回 2 小时前制造"到期" ----
    old = now.timestamp() - 7200
    st = D._bg_state_read()
    st["last"] = {"mihoyo": old, "skland": old}
    D._bg_state_write(st)
    near = __import__("datetime").datetime.now().replace(
        hour=(now + __import__("datetime").timedelta(minutes=2)).hour,
        minute=(now.minute + 2) % 60)
    cfgmod.load_config = lambda *a, **k: _stub_config(times=[f"{near.hour:02d}:{near.minute:02d}"])
    D._bg_refresh_tick()
    check(calls == [], "巡检 ±3 分钟内不刷（防双打）")
    cfgmod.load_config = lambda *a, **k: _stub_config()

    # ---- 5) 森空岛自停护栏：查询失败 → 该族冷却 6 小时，米游社不受牵连 ----
    def skland_fails(fam):
        return (True, fam == "skland")   # 森空岛族有游戏 ok=false
    install_call(skland_fails)
    older = now.timestamp() - 14400   # 拨回 4 小时：60/180 分钟档全部到期
    st = D._bg_state_read()
    st["last"] = {"mihoyo": older, "skland": older}
    D._bg_state_write(st)
    D._bg_refresh_tick()
    check(calls == ["mihoyo", "skland"], "两族都到期都触发")
    st = D._bg_state_read()
    check(st["cooldown_until"].get("skland", 0) > now.timestamp(), "森空岛失败已进 6h 冷却")
    install_call(lambda fam: (True, False))
    D._bg_refresh_tick()
    check(calls == [], "冷却期内森空岛不再打（米游社间隔未到同样不打）")

    # ---- 6) 拨快时钟跨过米游社 60 分钟档：只有米游社到期 ----
    st = D._bg_state_read()
    st["last"] = {"mihoyo": now.timestamp() - 4200, "skland": now.timestamp()}
    st.pop("cooldown_until", None)
    D._bg_state_write(st)
    D._bg_refresh_tick()
    check(calls == ["mihoyo"], "60 分钟档到期只触发米游社（森空岛 180 分钟未到）")

    # ---- 7) 控制台没起：调用抛异常也记账不连发 ----
    def boom(fam):
        raise ConnectionError("console down")
    install_call(boom)
    st = D._bg_state_read()
    st["last"] = {"mihoyo": older, "skland": older}
    st.pop("cooldown_until", None)
    D._bg_state_write(st)
    D._bg_refresh_tick()
    check(D._bg_state_read()["last"]["mihoyo"] >= old, "失败也记账（下个间隔再试，不轰炸）")

    print(f"rl_test_bg_refresh: {PASS} 项断言全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())

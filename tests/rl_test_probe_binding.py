#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""绑定完成当场探针测试（2026-11-19 #78）。

probe_bindings：直调 collect_statuses（querier 生产入口）验证刚写入的凭证，
只展示不计账。测试核心断言：
  1. 结果透传：成功 / 失败原因 / 凭证有效但无角色 / 数据待更新
  2. 三零写入：cred_health / push_history / live 磁盘缓存 全不被碰
     （绊线断言——任何记账/写缓存调用都让测试当场红）
  3. 风控冷却只读不写：冷却中的游戏不发起查询（stub 零调用），账本只经
     _live_risk_read 纯读
  4. mock 拒绝 / 未知游戏过滤 / 无账号节点如实报因 / 多游戏单次并行调用
collect_statuses 与 load_config 打桩，全部路径落 tmp，磁盘零写入。
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import login_ui as L
import main as M
import core.cred_health as cred_health
import core.notifier as notifier_mod
from core import config as cfgmod
from core.config import parse_config
from core.models import GameStatus

PASS = 0


def check(cond, label):
    global PASS
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    PASS += 1
    print(f"  ✓ {label}")


def _acc(game, enabled=True):
    return {"game": game, "nickname": game, "enabled": enabled,
            "credential": {}}


def _cfg(accounts, provider="miyoushe"):
    return parse_config(
        {"query": {"provider": provider, "mihoyo_enabled": True},
         "accounts": accounts},
        source=Path(tempfile.mkdtemp(prefix="rl_test_probe_cfg_")) / "config.json")


def _gs(game, name, **kw):
    return GameStatus(game_id=game, game_name=name, **kw)


def _run_probe(games, cfg, statuses, risk=None):
    """跑一次 L.probe_bindings：环境全隔离 + 记账绊线。

    返回 (结果 dict, stub 调用记录)；绊线=_live_disk_write/_live_risk_record/
    cred_health.record/record_push_history 一被调用测试当场红。
    """
    tmp = Path(tempfile.mkdtemp(prefix="rl_test_probe_binding_"))
    calls = {"n": 0, "accounts": []}

    def _stub_collect(c):
        calls["n"] += 1
        calls["accounts"] = [a.game for a in c.accounts]
        return statuses

    def _boom(*a, **k):
        raise AssertionError("探针碰了禁止写入的记账/缓存点")

    saved = (L.CONFIG_PATH, cfgmod.load_config, M.collect_statuses,
             L._LIVE_RISK_FILE, L._live_disk_write, L._live_risk_record,
             cred_health.record, notifier_mod.record_push_history)
    try:
        L.CONFIG_PATH = tmp / "config.json"
        cfgmod.load_config = lambda *a, **k: cfg
        M.collect_statuses = _stub_collect
        L._LIVE_RISK_FILE = tmp / "risk.json"
        if risk is not None:
            L._LIVE_RISK_FILE.write_text(json.dumps(risk), encoding="utf-8")
        L._live_disk_write = _boom
        L._live_risk_record = _boom
        cred_health.record = _boom
        notifier_mod.record_push_history = _boom
        result = L.probe_bindings(games)
    finally:
        (L.CONFIG_PATH, cfgmod.load_config, M.collect_statuses,
         L._LIVE_RISK_FILE, L._live_disk_write, L._live_risk_record,
         cred_health.record, notifier_mod.record_push_history) = saved
    return result, calls, tmp


def main() -> int:
    # ---- P1 成功透传 ----
    r, calls, tmp = _run_probe(
        ["genshin"], _cfg([_acc("genshin")]),
        [_gs("genshin", "原神")])
    check(r["ok"] and r["games"][0]["ok"] is True and r["games"][0]["error"] == ""
          and r["games"][0]["game_name"] == "原神",
          "P1 查询成功 → ok=True 原样透传（游戏名/零错误）")
    check(calls["n"] == 1 and calls["accounts"] == ["genshin"],
          "P1b 直调 collect_statuses 恰一次，picked=绑定游戏账号")

    # ---- P2 失败透传 + 三零写入 ----
    r, calls, tmp = _run_probe(
        ["genshin"], _cfg([_acc("genshin")]),
        [_gs("genshin", "原神", error="凭证无效（retcode=-100）", cred_kind="auth")])
    check(r["games"][0]["ok"] is False
          and "凭证无效" in r["games"][0]["error"],
          "P2 查询失败 → 红色原因原样透传（凭证无效）")
    check(list(tmp.iterdir()) == [],
          "P2b 三零写入：tmp 全程空（无 live_cache/push_history/cred_health 任何文件）")

    # ---- P3 风控冷却：只读账本、不发起查询 ----
    r, calls, tmp = _run_probe(
        ["genshin"], _cfg([_acc("genshin")]), [],
        risk={"genshin": {"until": time.time() + 3600, "retcode": 1034}})
    check(r["games"][0]["cooled"] is True and r["games"][0]["cooled_until"],
          "P3 冷却中 → cooled 标记+到期时刻（前端显示「稍后自动再验」）")
    check(calls["n"] == 0, "P3b 冷却中的游戏不发起任何查询（stub 零调用）")
    book = json.loads((tmp / "risk.json").read_text(encoding="utf-8"))
    check(book.get("genshin", {}).get("retcode") == 1034,
          "P3c 冷却账本原样保留（纯读，未触发清理写回）")

    # ---- P4 mock 拒绝 ----
    r, calls, _ = _run_probe(["genshin"], _cfg([_acc("genshin")],
                                               provider="mock"), [])
    check(r["ok"] is False and "演示" in r["error"],
          "P4 演示模式（出厂态）→ 拒绝探针不放假成功")

    # ---- P5 未知游戏过滤 + 无账号节点 ----
    r, calls, _ = _run_probe(
        ["genshin", "zzz", "not_a_game"], _cfg([_acc("genshin")]),
        [_gs("genshin", "原神")])
    check(len(r["games"]) == 2 and r["games"][0]["game"] == "genshin"
          and r["games"][1]["game"] == "zzz",
          "P5a 白名单外游戏被过滤（not_a_game 不出现）")
    check(r["games"][1]["ok"] is False
          and "账号节点" in r["games"][1]["error"],
          "P5b 无账号节点 → 如实报因")
    check(calls["accounts"] == ["genshin"],
          "P5c 无节点的游戏不进查询集合")

    # ---- P6 多游戏单次并行调用 + 冷却排除 ----
    r, calls, _ = _run_probe(
        ["wuwa", "pgr", "genshin"],
        _cfg([_acc("wuwa"), _acc("pgr"), _acc("genshin")]),
        [_gs("wuwa", "鸣潮"), _gs("pgr", "战双帕弥什")],
        risk={"genshin": {"until": time.time() + 3600}})
    check(calls["n"] == 1 and calls["accounts"] == ["wuwa", "pgr"],
          "P6a 多游戏合成单次 collect_statuses 并行调用；冷却中的 genshin 被排除")
    check(r["games"][2]["cooled"] is True
          and r["games"][0]["ok"] is True and r["games"][1]["ok"] is True,
          "P6b 鸣潮/战双正常透传，genshin 只标冷却")

    # ---- P7 凭证有效但无角色 / 数据待更新（ok 派生 property 语义）----
    r, calls, _ = _run_probe(
        ["genshin", "starrail"],
        _cfg([_acc("genshin"), _acc("starrail")]),
        [_gs("genshin", "原神", no_account="账号下没有这款游戏"),
         _gs("starrail", "崩坏：星穹铁道", stale="上次在线 10-26 21:46")])
    check(r["games"][0]["ok"] is True and r["games"][0]["no_account"]
          .startswith("账号下没有"),
          "P7a no_account 透传（凭证有效、暂无角色=绿）")
    check(r["games"][1]["ok"] is True and "上次在线" in r["games"][1]["stale"],
          "P7b stale 透传（凭证有效、数据待更新=绿）")

    # ---- P8 collect_statuses 丢行（账号被禁用等）→ 如实报因 ----
    r, calls, _ = _run_probe(["genshin"], _cfg([_acc("genshin")]), [])
    check(r["games"][0]["ok"] is False
          and "未参与查询" in r["games"][0]["error"],
          "P8 查询集合丢行 → 报「未参与查询」而非假成功/假失败")

    print(f"rl_test_probe_binding: {PASS} 项断言全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())

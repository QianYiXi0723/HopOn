# -*- coding: utf-8 -*-
"""实时查询风控退避测试（2026-11-01，用户真机复现：重扫码/换热点/隔天仍 1034+10041）。

背景：命中米游社风控（1034 需人机验证 / 10041 拒绝）后，控制台「刷新」每次
都对被标记游戏再打一轮接口 —— 每次撞墙都在给风控模型喂信号，越刷标记越深。
本次给 /api/live 路径加冷却：命中即记 6 小时账本，期间该游戏不再实时查询，
前端显示「风控冷却中」+ 上次缓存数据。

覆盖（R1-R8）：
  R1  记账：1034/10041 的错误文本写入账本，until ≈ now+6h
  R2  记账：非风控失败（网络超时等）不入账
  R3  活跃判定：冷却期内命中；过期条目被清理写回
  R4  注解：冷却中的游戏打 cooled + cooldown_left_minutes（≥1）
  R5  注解：缓存里残留的过期 cooled 标记被摘除（自愈）
  R6  live_status 集成：冷却中的游戏不进 collect_statuses 的账号子集，
      其条目从磁盘缓存回放并标 cooled；未冷却游戏照常查询
  R7  live_status 集成：全部游戏都在冷却时 collect_statuses 收到空账号表，
      一轮零请求
  R8  live_status 集成：本轮新命中的 1034 记入账本（下轮开始退避）
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import core.config as cfg_mod  # noqa: E402
import login_ui as L  # noqa: E402
from core.config import AccountConfig, AppConfig, QueryConfig  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def make_cfg(games: list) -> AppConfig:
    """provider 必须是 miyoushe：默认 mock 会触发演示模式短路，走不到查询路径。"""
    return AppConfig(
        accounts=[AccountConfig(game=g, nickname="x", credential={"cookie": "x"}) for g in games],
        query=QueryConfig(provider="miyoushe"),
    )


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="rl_risk_"))
    risk_file = tmp / "live_risk_backoff.json"
    live_cache = tmp / "live_cache.json"
    L._LIVE_RISK_FILE = risk_file
    L._LIVE_DISK_CACHE = live_cache

    print("== R1/R2 记账 ==")
    L._live_risk_record([
        ("genshin", "查询异常：接口 retcode=1034：触发风控"),
        ("zzz", "查询异常：接口 retcode=10041：风控拒绝"),
        ("starrail", "查询异常：ConnectionTimeout"),  # 非风控：不入账
    ])
    book = L._live_risk_read()
    check("R1 1034 入账", "genshin" in book and int(book["genshin"]["retcode"]) == 1034)
    check("R1 until≈now+6h", abs(float(book["genshin"]["until"]) - (time.time() + 6 * 3600)) < 120,
          f"until={book.get('genshin', {}).get('until')}")
    check("R1 10041 入账", "zzz" in book and int(book["zzz"]["retcode"]) == 10041)
    check("R2 非风控失败不入账", "starrail" not in book)

    print("== R3 活跃判定与过期清理 ==")
    active = L._live_risk_active()
    check("R3 冷却期内命中", "genshin" in active and "zzz" in active)
    # 把 until 拨到过去 → 过期条目清理写回
    book["genshin"]["until"] = time.time() - 10
    L._live_risk_write(book)
    active = L._live_risk_active()
    check("R3 过期不再活跃", "genshin" not in active and "zzz" in active)
    on_disk = json.loads(risk_file.read_text(encoding="utf-8"))
    check("R3 清理写回", "genshin" not in on_disk)

    print("== R4/R5 注解 ==")
    payload = {"ok": True, "games": [
        {"game": "genshin", "game_name": "原神", "ok": False, "error": "旧失败", "cooled": True},
        {"game": "zzz", "game_name": "绝区零", "ok": True, "finished": True,
         "resource": {"current": 100, "maximum": 200, "name": "电量"}},
        {"game": "starrail", "game_name": "星铁", "ok": True},
    ]}
    # zzz 仍在冷却；genshin 的 until 已过期
    out = L._annotate_live_cool(payload)
    gz = [g for g in out["games"] if g["game"] == "genshin"][0]
    zz = [g for g in out["games"] if g["game"] == "zzz"][0]
    sr = [g for g in out["games"] if g["game"] == "starrail"][0]
    check("R4 冷却中打标", zz.get("cooled") is True and zz.get("cooldown_left_minutes", 0) >= 1)
    check("R4 冷却中带剩余分钟", isinstance(zz.get("cooldown_left_minutes"), int))
    check("R5 过期 cooled 标记被摘除", "cooled" not in gz and "cooldown_left_minutes" not in gz)
    check("R5 未涉游戏原样", "cooled" not in sr)
    check("R5 原文件不被改动", "cooled" in payload["games"][0])

    print("== R6/R7/R8 live_status 集成 ==")
    import main as M
    real_collect = M.collect_statuses
    L._LIVE_CACHE["at"] = 0
    L._LIVE_CACHE["payload"] = None
    # 重置账本：只有 genshin 在冷却（清掉 R1-R3 的遗留，保证场景自洽）
    L._live_risk_write({"genshin": {"until": time.time() + 3600, "retcode": 1034}})

    # 磁盘缓存：genshin 有上次失败结果（供冷却回放）
    live_cache.write_text(json.dumps({
        "ok": True, "generated_at": "2026-11-01 12:00:00",
        "games": [
            {"game": "genshin", "game_name": "原神", "ok": False,
             "error": "接口 retcode=1034", "resource": {"current": 1, "maximum": 160, "name": "树脂"}},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    # R6：genshin 冷却中 → collect 只收到 starrail/zzz…先只冷却 genshin
    calls = []

    def fake_collect(config):
        calls.append([a.game for a in config.enabled_accounts])
        return [SimpleNamespace(
            game_id="starrail", game_name="崩坏：星穹铁道", nickname="x", ok=True,
            finished=False, error="", stale="", no_account="",
            tasks=[], counted_tasks=[],
        )]

    M.collect_statuses = fake_collect
    real_load = cfg_mod.load_config

    def fake_load(path):
        return make_cfg(["genshin", "starrail"])

    cfg_mod.load_config = fake_load
    try:
        out = L.live_status(force=True)
        check("R6 冷却游戏不进查询子集", calls and calls[-1] == ["starrail"],
              f"calls={calls}")
        games = {g["game"]: g for g in out["games"]}
        check("R6 冷却游戏缓存回放+cooled",
              games.get("genshin", {}).get("cooled") is True
              and games["genshin"]["resource"]["current"] == 1)
        check("R6 未冷却游戏照常", games.get("starrail", {}).get("ok") is True)

        # R8：本轮 starrail 若命中 1034 → 入账（下轮退避）
        def fake_collect_fail(config):
            calls.append([a.game for a in config.enabled_accounts])
            return [SimpleNamespace(
                game_id="starrail", game_name="崩坏：星穹铁道", nickname="x", ok=False,
                finished=False, error="查询异常：接口 retcode=1034：触发风控", stale="",
                no_account="", tasks=[], counted_tasks=[],
            )]

        M.collect_statuses = fake_collect_fail
        # 进程内 45s 新鲜度是产品行为（等锁吃缓存）——R6 刚刷新过，必须重置缓存
        # 让 R8 真正走到查询路径，否则命中的 1034 永远记不了账
        L._LIVE_CACHE["at"] = 0
        L._LIVE_CACHE["payload"] = None
        L.live_status(force=True)
        book = L._live_risk_read()
        check("R8 新命中入账", "starrail" in book and int(book["starrail"]["retcode"]) == 1034)

        # R7：全部游戏都在冷却 → collect 收到空账号表（一轮零请求）
        def fake_collect_empty(config):
            calls.append([a.game for a in config.enabled_accounts])
            return []

        M.collect_statuses = fake_collect_empty
        L._LIVE_CACHE["at"] = 0
        L._LIVE_CACHE["payload"] = None
        out = L.live_status(force=True)
        check("R7 全冷却时账号表为空", calls and calls[-1] == [],
              f"calls={calls}")
        check("R7 全冷却仍有完整面板", {g["game"] for g in out["games"]} == {"genshin", "starrail"})
    finally:
        M.collect_statuses = real_collect
        cfg_mod.load_config = real_load

    print(f"\n结果：PASS={PASS} FAIL={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

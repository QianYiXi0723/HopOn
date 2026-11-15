# -*- coding: utf-8 -*-
"""实时查询 90 秒最短间隔护栏（2026-11-16）。

背景：F5 / 连点「游戏监控」开关会在一分钟内对同一 gid 反复打私有接口，
45 秒进程缓存只覆盖 0-45s；本护栏补 45-90s 窗口——距上次真实发起查询
不足 _LIVE_MIN_INTERVAL_SECONDS 的 gid 本轮不查，磁盘缓存原样回放
（不设 cooled/cooldown，那属于风控护栏，两者独立）。

沙箱纪律：HOPON_USER_DIR 在 import login_ui 之前指向临时目录；
collect_statuses 打桩计数（monkeypatch main.collect_statuses——live_status
是 `from main import collect_statuses` 调用时取值，桩必须落在源模块上）；
零网络铁律。缓存语义：45 秒进程缓存与 90 秒间隔**叠加而非替换**，
所以用例 2/4 先把进程缓存拨旧（模拟 45-90s 窗口）再验证护栏。

  T1 首次调用 → 真查 1 次，响应无 throttled 字段（既有契约不变）
  T2 间隔内第二次（force=True）→ 计数不增，throttled=True，
     games=磁盘缓存旧条目（ok 字段保留）
  T3 时间戳拨回 91 秒 → 恢复真查（计数 +1，无 throttled）
  T4 不同 gid 互不影响：只刷 genshin 后刷 starrail → starrail 仍真查
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_SANDBOX = tempfile.mkdtemp(prefix="rl_live_throttle_")
os.environ["HOPON_USER_DIR"] = _SANDBOX

import login_ui  # noqa: E402
import main  # noqa: E402  # live_status 调用时 `from main import collect_statuses`
from core.config import DEFAULT_CONFIG  # noqa: E402
from core.models import GameStatus  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("ok  : " if ok else "FAIL: ") + name + (("  | " + str(detail)) if detail else ""))


def fresh_sandbox():
    """genshin + starrail 两款启用；进程缓存拨旧；磁盘缓存写入两款旧条目。"""
    base = json.loads(json.dumps(DEFAULT_CONFIG))
    base["accounts"] = [
        {"game": "genshin", "nickname": "原神", "enabled": True, "credential": {}},
        {"game": "starrail", "nickname": "星铁", "enabled": True, "credential": {}},
    ]
    base.setdefault("query", {})["provider"] = "miyoushe"
    Path(login_ui.CONFIG_PATH).write_text(
        json.dumps(base, ensure_ascii=False), encoding="utf-8")
    login_ui._LIVE_CACHE["at"] = 0.0          # 进程缓存视为陈旧（模拟 45-90s 窗口）
    login_ui._LIVE_CACHE["payload"] = None
    login_ui._LIVE_LAST_QUERY.clear()
    login_ui._LIVE_DISK_CACHE.parent.mkdir(parents=True, exist_ok=True)
    login_ui._LIVE_DISK_CACHE.write_text(json.dumps({
        "generated_at": "2026-11-16 03:00:00",
        "games": [
            {"game": "genshin", "game_name": "原神", "nickname": "甲", "ok": True,
             "finished": True, "error": "", "stale": "", "no_account": "",
             "resource": None, "tasks": []},
            {"game": "starrail", "game_name": "星穹铁道", "nickname": "乙", "ok": True,
             "finished": False, "error": "", "stale": "", "no_account": "",
             "resource": None, "tasks": []},
        ],
    }, ensure_ascii=False), encoding="utf-8")


calls = []


def fake_collect(cfg):
    calls.append([a.game for a in cfg.accounts])
    return [GameStatus(game_id=a.game, game_name=a.game, nickname="n")
            for a in cfg.accounts]


main.collect_statuses = fake_collect


def run():
    # ---- T1 首次调用：真查，无 throttled ----
    fresh_sandbox()
    d1 = login_ui.live_status(force=True)
    check("T1a 首次调用 collect_statuses 恰 1 次", len(calls) == 1, calls)
    check("T1b 响应无 throttled 字段（既有契约）", "throttled" not in d1,
          d1.get("throttled"))
    check("T1c ok=True", d1.get("ok") is True)

    # ---- T2 间隔内第二次（force 也受限）：计数不增，磁盘缓存回放 ----
    # T1 刚写过进程缓存（45s TTL 内会直接吃缓存）——把 at 拨旧 50s，
    # 模拟护栏真正值守的 45-90s 窗口（0-45s 由进程缓存挡，同样是零真查）
    login_ui._LIVE_CACHE["at"] -= 50.0
    n_before = len(calls)
    d2 = login_ui.live_status(force=True)
    check("T2a 间隔内 force 不再真查（计数不增）", len(calls) == n_before,
          {"before": n_before, "after": len(calls), "calls": calls})
    check("T2b throttled=True", d2.get("throttled") is True, d2.get("throttled"))
    check("T2c ok 仍为 True（绝不返回 ok:false）", d2.get("ok") is True)
    games2 = {g.get("game"): g for g in d2.get("games", [])}
    check("T2d games=磁盘缓存旧条目且 ok 字段保留",
          games2.get("genshin", {}).get("ok") is True
          and games2.get("starrail", {}).get("ok") is True
          and "cooled" not in games2.get("genshin", {}),
          {k: v.get("ok") for k, v in games2.items()})
    check("T2e 磁盘缓存未被限频响应污染",
          "throttled" not in (json.loads(
              login_ui._LIVE_DISK_CACHE.read_text(encoding="utf-8"))))

    # ---- T3 拨回 91 秒 → 恢复真查 ----
    # T2 走的是回放路径（不写进程缓存），at 仍是 T1-50s——拨回 0 保证
    # 到达护栏判定段，隔离出「间隔」这一个变量
    login_ui._LIVE_CACHE["at"] = 0.0
    for gid in login_ui._LIVE_LAST_QUERY:
        login_ui._LIVE_LAST_QUERY[gid] -= 91.0
    d3 = login_ui.live_status(force=True)
    check("T3a 超过间隔恢复真查（计数 +1）", len(calls) == n_before + 1,
          {"before": n_before, "after": len(calls)})
    check("T3b 正常响应无 throttled", "throttled" not in d3, d3.get("throttled"))

    # ---- T4 不同 gid 互不影响 ----
    login_ui._LIVE_LAST_QUERY.clear()
    login_ui._LIVE_LAST_QUERY["genshin"] = time.time()   # 只有 genshin 刚查过
    login_ui._LIVE_CACHE["at"] = 0.0                     # 进程缓存拨旧，走护栏判定
    login_ui._LIVE_CACHE["payload"] = None
    d4 = login_ui.live_status(force=True)
    check("T4a 只真查未限频的 starrail", calls[-1] == ["starrail"], calls[-1])
    check("T4b 响应带 throttled（genshin 被限频回放）", d4.get("throttled") is True)
    games4 = {g.get("game"): g for g in d4.get("games", [])}
    check("T4c genshin 旧条目回放 + starrail 新鲜结果并存",
          games4.get("genshin", {}).get("ok") is True
          and games4.get("starrail", {}).get("ok") is True
          and set(games4) == {"genshin", "starrail"},
          sorted(games4))

    failed = [n for n, ok in results if not ok]
    print(f"\n== {len(results) - len(failed)}/{len(results)} 通过 ==")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())

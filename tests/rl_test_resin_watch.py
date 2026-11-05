# -*- coding: utf-8 -*-
"""回满即推 watcher（daemon._check_resin_watch）单元测试。

monkeypatch 到 %TEMP%，绝不写仓库 logs\。game_moment 同样打桩。

覆盖：
  R1 状态文件只有 {"pushed": {}}（无 predictions）→ 不抛异常、不触发巡检
     ——2026-11-05 实证：此形态曾因 due/due_games NameError 炸死 check-loop
       线程（心跳停在启动笔、巡检全停摆），且只在 .resin_watch.json 存在时
       才走到炸点，岛上旧 daemon 无此文件所以从未暴露；
  R2 预测到点（full_at 过去 30s）且未推过 → run_check 恰好 1 次、
     参数=体力回满复核；复核后 predictions 里对应条目被清掉；
  R3 当天已推过（pushed=今天）→ 即使预测到点也不触发；
  R4 预测未到点（full_at 未来 1h）→ 不触发；
  R5 状态文件不存在 → 静默返回（岛上语义保持）。
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import daemon  # noqa: E402  （仅模块级常量，不会启动守护进程）
import core.history as history_mod  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="rl_resinw_"))
WATCH = TMP / ".resin_watch.json"
daemon.LOGS_DIR = TMP  # 与 DIR 一起换，防止其他常量仍指向仓库 logs
WATCH_PATHS_OK = str(daemon.LOGS_DIR / ".resin_watch.json") == str(WATCH)

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS {name}")
    else:
        FAIL += 1
        print(f"FAIL {name}  {detail}")


def write_state(payload: dict) -> None:
    WATCH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def run() -> None:
    calls: list[str] = []
    fixed_now = datetime(2026, 11, 5, 12, 0, 0)

    def fake_run_check(reason: str, dry_run: bool = False) -> int:
        calls.append(reason)
        return 0

    def _watch():
        daemon._check_resin_watch()

    with mock.patch.object(history_mod, "game_moment", return_value=fixed_now), \
         mock.patch.object(daemon, "run_check", side_effect=fake_run_check):
        # R1 空状态（正是炸死线程的那份文件形态）
        write_state({"pushed": {}})
        try:
            _watch()
            check("R1 空状态不抛异常且不触发", calls == [],
                  f"calls={calls}")
        except Exception as exc:  # noqa: BLE001
            check("R1 空状态不抛异常且不触发", False, f"异常: {exc!r}")

        # R2 预测到点 + 未推过 → 触发复核，且 predictions 条目被清
        # （horizon 用真实 datetime.now()，full_at 必须相对真实时钟取值）
        due_at = (datetime.now() - timedelta(seconds=30)).isoformat(timespec="seconds")
        write_state({"pushed": {},
                     "predictions": {"genshin": {"full_at": due_at},
                                     "zzz": {"full_at": (datetime.now() + timedelta(hours=3)).isoformat(timespec="seconds")}}})
        calls.clear()
        _watch()
        check("R2 到点触发恰好 1 次", len(calls) == 1, f"calls={calls}")
        check("R2 触发理由=体力回满复核",
              bool(calls) and calls[0] == "体力回满复核", f"calls={calls}")
        after = json.loads(WATCH.read_text(encoding="utf-8"))
        check("R2 已触发条目被清理、未到点保留",
              "genshin" not in (after.get("predictions") or {})
              and "zzz" in (after.get("predictions") or {}),
              f"state={after}")

        # R3 当天已推过 → 不触发（跨机制去重）
        write_state({"pushed": {"genshin": fixed_now.strftime("%Y-%m-%d")},
                     "predictions": {"genshin": {"full_at": due_at}}})
        calls.clear()
        _watch()
        check("R3 当天已推不触发", calls == [], f"calls={calls}")

        # R4 预测未到点 → 不触发
        write_state({"pushed": {},
                     "predictions": {"zzz": {"full_at": (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds")}}})
        calls.clear()
        _watch()
        check("R4 未到点不触发", calls == [], f"calls={calls}")

        # R5 状态文件不存在 → 静默返回
        WATCH.unlink(missing_ok=True)
        calls.clear()
        try:
            _watch()
            check("R5 无状态文件静默返回", calls == [], f"calls={calls}")
        except Exception as exc:  # noqa: BLE001
            check("R5 无状态文件静默返回", False, f"异常: {exc!r}")

    check("R0 LOGS_DIR 已整体打桩", WATCH_PATHS_OK)

    # ---- W 组：main._record_resin_watch（预测记录器）----
    # 2026-11-06 实证：run_once 从 W2 首日就误传 config 当 alert_games，
    # TypeError 被「忽略」兜底吞掉 → 预测从未写进去，回满即推整条链是死的。
    import main as main_mod  # noqa: E402
    from core.models import GameStatus, TaskItem  # noqa: E402

    main_mod._RESIN_WATCH_PATH = TMP / ".resin_watch.json"
    due_future = {"full_at": "2099-01-01 08:00:00"}

    def status_with_recover(gid, recover):
        return GameStatus(
            game_id=gid, game_name=gid, nickname="",
            tasks=[TaskItem(name="体力", done=False, progress="0/240",
                            recover_seconds=recover)],
        )

    # W1 正常记录：有恢复倒计时的游戏写预测
    main_mod._RESIN_WATCH_PATH.unlink(missing_ok=True)
    main_mod._record_resin_watch(
        [status_with_recover("genshin", 3600)], set())
    st = json.loads(main_mod._RESIN_WATCH_PATH.read_text(encoding="utf-8"))
    check("W1 有倒计时写预测", "genshin" in (st.get("predictions") or {}),
          str(st)[:120])

    # W2 本轮已到预警阈值的游戏不写预测（调用方传 resource_alerts 的 id 集）
    main_mod._RESIN_WATCH_PATH.unlink(missing_ok=True)
    main_mod._record_resin_watch(
        [status_with_recover("zzz", 3600)], {"zzz"})
    st = json.loads(main_mod._RESIN_WATCH_PATH.read_text(encoding="utf-8"))
    check("W2 预警中的游戏跳过预测",
          "zzz" not in (st.get("predictions") or {}), str(st)[:120])

    # W3 调用契约：第二实参必须是可迭代的 game_id 集合——传 AppConfig 这类
    # 不可迭代对象必须当场 TypeError（不许被兜底吞成静默失效）。
    # 注意 statuses 不能为空：空列表走不到 `gid in alert_games` 那一行。
    try:
        main_mod._record_resin_watch(
            [status_with_recover("x", 60)], object())
        check("W3 非集合实参当场 TypeError", False, "没有抛异常")
    except TypeError:
        check("W3 非集合实参当场 TypeError", True)


if __name__ == "__main__":
    run()
    print(f"\n[结论] {PASS}/{PASS + FAIL} 通过")
    sys.exit(1 if FAIL else 0)

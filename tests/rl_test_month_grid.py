# -*- coding: utf-8 -*-
"""月报下钻（history.month_grid）测试——数据根指向 %TEMP%，绝不碰真实 data。

覆盖：
  M1 基本形态：label/prev/next/整月天数（30 天的 11 月）
  M2 格子口径：known+finished → done；known 无 finished → missed；
     查过但失败（err）→ 计 nodata 不算漏；未知日 → 无数据格
  M3 未来钳制：当月未来的天标 future=True 且不读存档（不算任何统计）
  M4 月份导航：prev=上月 / next=当月之后为 None（不提供未来月）
  M5 非法月份回落当月；跨年 12 月的 prev/next 正确（12 月 prev=11 月）
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import core.history as hist  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="rl_monthgrid_"))
hist.DATA_DIR = TMP
hist.HISTORY_PATH = TMP / "history.json"

NOW = datetime(2026, 11, 6, 10, 0, 0)   # 钉死「今天」：11 月 6 日

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS {name}")
    else:
        FAIL += 1
        print(f"FAIL {name}  {detail}")


def seed(day: str, game: str, **cell) -> None:
    p = hist.HISTORY_PATH
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"days": {}}
    data["days"].setdefault(day, {})[game] = cell
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def run() -> None:
    # 铺 11 月数据：星铁 1/2 号完成、3 号做了没做完、4 号查询失败、5 号无记录
    seed("2026-11-01", "starrail", known=True, finished=True, checks=2, last="12:00")
    seed("2026-11-02", "starrail", known=True, finished=True, checks=1, last="13:00")
    seed("2026-11-03", "starrail", known=True, finished=False, checks=1, last="09:00")
    seed("2026-11-04", "starrail", known=False, finished=False, checks=1,
         last="08:00", err="登录凭证已失效")

    d = hist.month_grid("2026-11", today=NOW, enabled_games=["starrail"])

    # M1 形态
    check("M1a label/prev/next", d["label"] == "2026 年 11 月"
          and d["prev"] == "2026-10" and d["next"] is None, str(d["label"]))
    check("M1b 整月 30 行", len(d["days"]) == 30, len(d["days"]))
    check("M1c 6 号是今天", d["days"][5]["is_today"] is True)

    cells = {r["date"]: r["games"]["starrail"] for r in d["days"]}
    check("M2a 完成→done", cells["2026-11-01"]["finished"] is True)
    check("M2b 做了没完→missed（err 格）",
          cells["2026-11-03"]["finished"] is False and cells["2026-11-03"]["known"] is True)
    stats = {g["game"]: g for g in d["games"]}
    check("M2c 统计 done=2 missed=1 nodata=1",
          stats["starrail"]["done"] == 2 and stats["starrail"]["missed"] == 1
          and stats["starrail"]["nodata"] == 1, str(stats["starrail"]))
    check("M2d 失败日带 err 尾巴", cells["2026-11-04"].get("err") == "登录凭证已失效")

    # M3 未来钳制
    check("M3 未来日标 future 且无数据", cells["2026-11-30"].get("future") is True
          and cells["2026-11-30"]["checks"] == 0)

    # M4 导航：next 为 None；12 月的 prev 是 11 月（跨年）
    d12 = hist.month_grid("2025-12", today=datetime(2026, 1, 15, 9, 0, 0),
                          enabled_games=["starrail"])
    check("M4 跨年 prev=2025-11", d12["prev"] == "2025-11", d12["prev"])
    check("M4b 过期月的 next=2026-01", d12["next"] == "2026-01", d12["next"])

    # M5 非法月份回落当月
    d_bad = hist.month_grid("2099-13", today=NOW, enabled_games=["starrail"])
    check("M5 非法月份回落当月", d_bad["month"] == "2026-11", d_bad["month"])

    # M6 未来月份钳回当月
    d_future = hist.month_grid("2027-01", today=NOW, enabled_games=["starrail"])
    check("M6 未来月份钳回当月", d_future["month"] == "2026-11", d_future["month"])


if __name__ == "__main__":
    run()
    print(f"\n[结论] {PASS}/{PASS + FAIL} 通过")
    sys.exit(1 if FAIL else 0)

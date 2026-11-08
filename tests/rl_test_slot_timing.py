# -*- coding: utf-8 -*-
"""时间点触发精度测试（2026-11-09 用户裁决：**只允许早到不允许迟到**）。

窗口 = [目标时刻 - 30, 目标时刻]（提前量 = 一个轮询周期）：
  E1  早于窗口不触发（11:59:00 / 11:59:29 → 空）
  E2  进窗即触发（11:59:30 / 11:59:59 / 12:00:00，delta<=0）
  E3  迟过目标 → 补发分支仍触发（12:00:30，delta=30>0）
  E4  关补发时迟过目标不触发（catch_up=0）
  E5  跨 0 点补发不回归（00:30 补昨天 23:59）
  E6  fired 账本去重：同一班不双发
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import daemon                          # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("ok  : " if ok else "FAIL: ") + name + (("  | " + str(detail)) if detail else ""))


def slots(now, times, catch_up=180, fired=None):
    return daemon.pending_slots(now=now, check_times=times,
                                state={"fired": fired or {}},
                                catch_up_minutes=catch_up)


def run():
    times = ["12:00"]

    # ---- E1 早于窗口（提前量外）不触发 ----
    for h, m, s in ((11, 59, 0), (11, 59, 29)):
        got = slots(datetime(2026, 11, 9, h, m, s), times)
        check(f"E1 {h:02d}:{m:02d}:{s:02d} 不触发", got == [], got)

    # ---- E2 进窗即触发（提前量内 / 正点）----
    got = slots(datetime(2026, 11, 9, 11, 59, 30), times)
    check("E2a 11:59:30 触发（提前 30s）", len(got) == 1 and got[0][1] == -30, got)
    got = slots(datetime(2026, 11, 9, 11, 59, 59), times)
    check("E2b 11:59:59 触发（提前 1s）", len(got) == 1 and got[0][1] == -1, got)
    got = slots(datetime(2026, 11, 9, 12, 0, 0), times)
    check("E2c 12:00:00 触发（正点）", len(got) == 1 and got[0][1] == 0, got)

    # ---- E3 迟过目标 → 补发分支仍兜底 ----
    got = slots(datetime(2026, 11, 9, 12, 0, 30), times)
    check("E3 12:00:30 走补发仍触发", len(got) == 1 and got[0][1] == 30, got)

    # ---- E4 关补发时迟过目标不触发 ----
    got = slots(datetime(2026, 11, 9, 12, 0, 30), times, catch_up=0)
    check("E4 catch_up=0 迟到不补", got == [], got)

    # ---- E5 跨 0 点补发 ----
    got = slots(datetime(2026, 10, 30, 0, 30, 0), ["23:59"])
    check("E5 00:30 补昨天 23:59", len(got) == 1
          and got[0][0] == "2026-10-29 23:59", got)

    # ---- E6 fired 去重 ----
    fired = {"2026-11-09 12:00"}
    got = slots(datetime(2026, 11, 9, 11, 59, 45), times, fired=fired)
    check("E6 已跑过的班不双发", got == [], got)

    failed = [n for n, ok in results if not ok]
    print(f"\n== {len(results) - len(failed)}/{len(results)} 通过 ==")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())

# -*- coding: utf-8 -*-
"""时间点触发精度测试（2026-11-09 用户实证「推送固定早一分钟」回归）。

根因：SLOT_TOLERANCE=60 的**对称**容差让 11:59:xx 的轮询 tick 把 12:00
的班判成到点提前跑掉。修后到点窗口为 [0, 60]——早到一律不触发。

覆盖：
  E1  早到不触发（11:59:01 / 11:59:30 / 11:59:59 → 空）
  E2  到点/迟到触发（12:00:00 / 12:00:30 / 12:00:59 → 各一次，delta>=0）
  E3  迟到出容差 → 走补发窗口（12:01:30 仍补，delta=90）
  E4  补发窗口外 → 不触发（12:05，默认 180 分钟窗口内会补——此处用
      catch_up_minutes=0 验证"关补发"语义不变）
  E5  跨 0 点补发不受影响（00:30 补昨天 23:59）
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.history import game_moment  # noqa: E402  (确保 import 顺序无碍)
import daemon                          # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("ok  : " if ok else "FAIL: ") + name + (("  | " + str(detail)) if detail else ""))


def slots(now, times, catch_up=180):
    return daemon.pending_slots(now=now, check_times=times,
                                state={"fired": {}}, catch_up_minutes=catch_up)


def run():
    times = ["12:00"]

    # ---- E1 早到一律不触发 ----
    for h, m, s in ((11, 59, 1), (11, 59, 30), (11, 59, 59)):
        got = slots(datetime(2026, 11, 9, h, m, s), times)
        check(f"E1 {h:02d}:{m:02d}:{s:02d} 不触发", got == [], got)

    # ---- E2 到点/迟到窗口内触发 ----
    got = slots(datetime(2026, 11, 9, 12, 0, 0), times)
    check("E2a 12:00:00 触发", len(got) == 1 and got[0][1] == 0, got)
    got = slots(datetime(2026, 11, 9, 12, 0, 30), times)
    check("E2b 12:00:30 触发 delta=30", len(got) == 1 and got[0][1] == 30, got)
    got = slots(datetime(2026, 11, 9, 12, 0, 59), times)
    check("E2c 12:00:59 触发 delta=59", len(got) == 1 and got[0][1] == 59, got)

    # ---- E3 迟到出容差 → 补发窗口 ----
    got = slots(datetime(2026, 11, 9, 12, 1, 30), times)
    check("E3 12:01:30 仍补发", len(got) == 1 and got[0][1] == 90, got)

    # ---- E4 关补发时超出容差不触发 ----
    got = slots(datetime(2026, 11, 9, 12, 1, 30), times, catch_up=0)
    check("E4 catch_up=0 不补发", got == [], got)

    # ---- E5 跨 0 点补发不回归 ----
    got = slots(datetime(2026, 10, 30, 0, 30, 0), ["23:59"])
    check("E5 00:30 补昨天 23:59", len(got) == 1
          and got[0][0] == "2026-10-29 23:59", got)

    failed = [n for n, ok in results if not ok]
    print(f"\n== {len(results) - len(failed)}/{len(results)} 通过 ==")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())

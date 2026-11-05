# -*- coding: utf-8 -*-
"""history 每日滚动快照测试——数据根整体指向 %TEMP%，绝不碰真实 data。

覆盖：
  B1 首次快照：当天文件不存在 → 复制生成 history.YYYY-MM-DD.json
  B2 同日幂等：当天快照已存在 → 不重复生成（直接返回既有路径）
  B3 滚动保留：9 份历史快照 → 只留最新 HISTORY_BACKUP_KEEP=7 份
  B4 无源文件：history.json 不存在 → 静默返回 None 不报错
  B5 快照内容与源文件一致（copy2 字节级）
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import core.history as hist  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="rl_histbak_"))
hist.DATA_DIR = TMP          # 与 DIR 一起换：DATA_DIR/HISTORY_PATH/backup 全部进沙箱
hist.HISTORY_PATH = TMP / "history.json"

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS {name}")
    else:
        FAIL += 1
        print(f"FAIL {name}  {detail}")


def run() -> None:
    hist.HISTORY_PATH.write_text(
        json.dumps({"days": {"2026-11-06": {"genshin": {"known": True}}}}, ensure_ascii=False),
        encoding="utf-8")

    # B1 首次快照
    day1 = datetime(2026, 11, 6, 9, 0, 0)
    target = hist._snapshot_history_daily(now=day1)
    check("B1 首次快照生成", target is not None and target.exists()
          and target.name == "history.2026-11-06.json", str(target))

    # B5 内容一致
    check("B5 快照内容与源一致",
          target is not None and target.read_text(encoding="utf-8")
          == hist.HISTORY_PATH.read_text(encoding="utf-8"))

    # B2 同日幂等
    again = hist._snapshot_history_daily(now=datetime(2026, 11, 6, 21, 0, 0))
    check("B2 同日幂等（不重复生成）", again == target)

    # B3 滚动保留：手工铺 9 份 → 快照一次 → 只剩 7 份
    backup_dir = TMP / "backup"
    for i in range(1, 10):
        (backup_dir / ("history.2026-10-%02d.json" % (20 + i))).write_text("{}", encoding="utf-8")
    hist._snapshot_history_daily(now=datetime(2026, 11, 7, 9, 0, 0))
    kept = sorted(p.name for p in backup_dir.glob("history.*.json"))
    check("B3 滚动后只剩 7 份", len(kept) == hist.HISTORY_BACKUP_KEEP, str(kept))
    check("B3b 保留的是最新 7 份", kept[0] == "history.2026-10-25.json"
          and kept[-1] == "history.2026-11-07.json", str(kept))

    # B4 无源文件静默
    hist.HISTORY_PATH.unlink()
    check("B4 无源文件返回 None", hist._snapshot_history_daily(
        now=datetime(2026, 11, 8, 9, 0, 0)) is None)

    # B6 真实 record() 链路会触发快照（直接调 _snapshot 等价验证已覆盖，
    # 这里只确认 record 引用存在，防止未来重构悄悄断链）
    import inspect
    src = inspect.getsource(hist.record)
    check("B6 record() 调用快照", "_snapshot_history_daily()" in src)


if __name__ == "__main__":
    run()
    print(f"\n[结论] {PASS}/{PASS + FAIL} 通过")
    sys.exit(1 if FAIL else 0)

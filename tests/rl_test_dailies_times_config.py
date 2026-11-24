#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""config 层多时间点往返（2026-11-25）。times[] 必须穿过 CustomDaily 的解析与序列化，
否则 daemon（读 config.custom_dailies）拿不到多时刻。先 RED 再 GREEN。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import config as C  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  PASS " + name)
    else:
        FAIL += 1; print("  FAIL " + name + (" | " + str(detail) if detail else ""))


# 1) 解析 times[]
raw = [{"id": "x", "name": "喝水", "mode": "standalone",
        "times": ["08:00", "12:00", "18:00"], "days": [True] * 7}]
items = C._parse_custom_dailies(raw)
check("C1 times 解析进 CustomDaily",
      hasattr(items[0], "times") and list(items[0].times) == ["08:00", "12:00", "18:00"],
      getattr(items[0], "times", "NO FIELD"))

# 2) 旧数据（只有 time）兼容
old = C._parse_custom_dailies([{"id": "o", "name": "旧", "mode": "standalone", "time": "20:00"}])
check("C2 旧 time 项仍解析、times 为空或派生",
      old[0].time == "20:00" and (not getattr(old[0], "times", None) or list(old[0].times) == []),
      (old[0].time, getattr(old[0], "times", "NO FIELD")))

print(f"\nPASS {PASS} / FAIL {FAIL}")
sys.exit(1 if FAIL else 0)

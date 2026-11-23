#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gitee_backfill 分页 bug 回归（2026-11-23）。

bug：releases 列表接口默认只返回第一页，新版 release 排在后面查不到
→ _release_exists 误判「不存在」→ 走创建分支 → Gitee 报 400（同名已存在）。

本测试 mock _api：第 1 页不含目标 tag、第 2 页含 → 断言 _release_exists 应返回 True。
修复前必红（只查第 1 页），修复后绿。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PASS = FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}" + (f"  | {detail}" if detail else ""))


spec = importlib.util.spec_from_file_location(
    "gb", ROOT / "tools" / "rl_release" / "gitee_backfill.py")
gb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gb)

# mock：page 1 是满页（100 条，真实前提才该翻页），page 2 才有目标
# 注：Gitee 每页满 100 条才会继续翻页；第一页只有 2 条属于「已到最后一页」，
# 实现不翻页是正确的——所以 mock 必须构造满页才能测出翻页逻辑。
PAGE1 = [{"tag_name": f"v0.0.{i}", "id": i} for i in range(100)]
PAGE2 = [{"tag_name": "v2.7.0.7", "id": 999}]
calls = []


def fake_api(url, payload=None):
    calls.append(url)
    if "page=2" in url:
        return PAGE2
    if "page=1" in url:
        return PAGE1
    return PAGE1


gb._api = fake_api

print("[分页回归]")
found = gb._release_exists("tok", "v2.7.0.7")
check(found is True,
      "_release_exists 能翻页找到第 2 页的 release（修复前必红）",
      f"返回={found} 调用={len(calls)}次")
check(any("page=2" in u or "per_page" in u for u in calls),
      "实现确实翻了页/或带了 per_page（不再只查第一页）",
      str(calls[:3]))

# 边界：不该把「确实不存在」误判成存在
gb._api = lambda url, payload=None: PAGE1
check(gb._release_exists("tok", "v9.9.9") is False,
      "不存在的 tag 仍返回 False（没有把翻页改成永远 True）")

print(f"\nPASS {PASS} / FAIL {FAIL}")
sys.exit(1 if FAIL else 0)

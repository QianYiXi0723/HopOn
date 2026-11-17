# -*- coding: utf-8 -*-
"""活动黑名单自动清理回归（2026-11-18 用户裁决「过期的自动删除」）。

规则：uid 不在当前活动缓存 → 摘除（活动结束下榜/换榜换 id）；
缓存为空/读不到 → 绝不 prune（拉取失败会把所有 uid 判成失踪，
全体摘除 = 被删活动集体复活）。
"""
import json
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import activities  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("ok  : " if ok else "FAIL: ") + name + (("  | " + str(detail)) if detail else ""))


tmp = Path(tempfile.mkdtemp(prefix="rl-actprune-"))
cache_bak, ign_bak = activities.CACHE_PATH, activities.IGNORED_PATH
activities.CACHE_PATH = tmp / "activities.json"
activities.IGNORED_PATH = tmp / "ignored_activities.json"
try:
    def write_cache(items):
        activities.CACHE_PATH.write_text(json.dumps(
            {"version": 1, "items": items}, ensure_ascii=False), encoding="utf-8")

    item_a = {"game": "genshin", "kind": "act", "remote_id": "100",
              "title": "在榜活动", "start": "2026-11-01 04:00:00",
              "end": "2026-12-01 04:00:00", "reward": "原石 420"}
    # 1. 正常 prune：A 在榜保留，B/C 已下榜摘除
    write_cache([item_a])
    activities.IGNORED_PATH.write_text(json.dumps(
        {"version": 1, "ids": ["genshin:act:100", "genshin:act:200", "starrail:act:283"]},
        ensure_ascii=False), encoding="utf-8")
    n = activities._prune_ignored_stale()
    check("P1 下榜 uid 摘除数 = 2", n == 2, n)
    ids = activities.load_ignored().get("ids")
    check("P2 在榜 uid 保留", ids == ["genshin:act:100"], ids)

    # 2. 幂等：再跑一次不再有摘除
    check("P3 幂等（无变化返回 0）", activities._prune_ignored_stale() == 0)

    # 3. 安全阀：缓存为空 → 绝不 prune
    write_cache([])
    activities.IGNORED_PATH.write_text(json.dumps(
        {"version": 1, "ids": ["genshin:act:100", "genshin:act:200"]},
        ensure_ascii=False), encoding="utf-8")
    check("P4 缓存为空 = 不 prune（防全体复活）",
          activities._prune_ignored_stale() == 0
          and activities.load_ignored().get("ids") == ["genshin:act:100", "genshin:act:200"])

    # 4. ignored_detail：prune 后不再出现 bare 行，在榜行带元数据
    write_cache([item_a])
    activities.IGNORED_PATH.write_text(json.dumps(
        {"version": 1, "ids": ["genshin:act:100", "genshin:act:200"]},
        ensure_ascii=False), encoding="utf-8")
    d = activities.ignored_detail()
    check("P5 detail 只剩在榜行（bare 行已被自动清掉）",
          d.get("count") == 1 and d["rows"][0]["found"] is True
          and d["rows"][0]["title"] == "在榜活动", d.get("rows"))
finally:
    activities.CACHE_PATH, activities.IGNORED_PATH = cache_bak, ign_bak

passed = sum(1 for _, ok in results if ok)
print("\n[结论] %d/%d 通过%s" % (passed, len(results),
      "" if passed == len(results) else "  ← 存在 FAIL"))
sys.exit(0 if passed == len(results) else 1)

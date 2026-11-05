# -*- coding: utf-8 -*-
"""从 dailycheck 日志重建 data/history.json（2026-11-05）。

**背景**：数据外置改造时，迁移测试误删了 data/ 目录，history.json 里
10-26 起「每日完成」记录丢失。日志文件完好，每轮巡检都记了各游戏的
完成状态，可据此还原。

**还原口径**（与 core.history.record 完全一致，避免重建出假数据）：
* 归属日期用 ``game_date_key()``（04:00 日界），不是日志行的时间戳日期；
* ``finished`` 是「粘」的：当天只要有一次查到全做完，这天就算完成；
* ``known`` 只在拿到有效数据时置真（查询失败/待上线/未建号都不算）；
* ``checks`` 累加、``last`` 取最后一次的时间。
* **不重建 ``unfinished_tasks`` 的明细数值**——日志只给了「未完成 N 项」
  这个数字本身（还是汇总值），逐个游戏的任务明细没记，硬编就是造假。
  这里只用「全部完成 / 未完成」这一个可靠信号。

用法：
    python tools/rebuild_history.py --dry-run     # 只看能还原什么
    python tools/rebuild_history.py --write       # 真正写盘（先备份原文件）
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.history import game_date_key  # noqa: E402
from core.paths import user_root  # noqa: E402

#: 日志行：`2026-10-27 00:49:10 [INFO   ] dailycheck.main: 原神(旅行者) -> 全部完成`
#: logger 名与消息之间只有一个半角冒号+空格，且 logger 名不含空格/冒号，
#: 所以用 `dailycheck[\w.]*:\s*` 精确切；消息体里的全角冒号不受影响。
_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+\[\w+\s*\]\s+dailycheck[\w.]*:\s*(.*)$"
)
#: 结果行：<游戏名>(<昵称>) -> <结论>
#: 游戏名里可能含全角冒号（「崩坏：星穹铁道」），所以不能把「：」当分隔符，
#: 只能靠末尾的 (昵称) -> 括号结构来切——名字部分用**贪婪**匹配取最后一段。
_RESULT_RE = re.compile(r"^(?P<name>.+?)\((?P<nick>[^)]*)\)\s*->\s*(?P<verdict>.+)$")

#: 游戏名 → game_id（与 KNOWN_GAMES 对齐）
GAME_IDS = {
    "原神": "genshin",
    "崩坏：星穹铁道": "starrail",
    "星穹铁道": "starrail",
    "绝区零": "zzz",
    "明日方舟": "arknights",
    "终末地": "endfield",
}

#: 明确「拿到了有效数据且完成」的措辞
DONE_WORDS = ("全部完成",)
#: 明确「拿到了有效数据但未完成」
UNDONE_WORDS = ("未完成",)
#: 「没拿到当日数据」的措辞——不算 known，也不能算未完成
UNKNOWN_WORDS = ("查询失败", "待上线", "未创建角色", "数据待更新")


def _game_id(name: str) -> str:
    name = name.strip()
    if name in GAME_IDS:
        return GAME_IDS[name]
    for key, gid in GAME_IDS.items():
        if key in name or name in key:
            return gid
    return ""


def parse_logs() -> tuple[dict, dict]:
    """扫描全部日志，返回 (days, stats)。"""
    files = sorted(
        glob.glob(str(ROOT / "logs" / "dailycheck.log*")),
        key=lambda p: os.path.getmtime(p),
    )
    days: dict = {}
    stats = {
        "files": [os.path.basename(f) for f in files],
        "lines_total": 0,
        "lines_matched": 0,
        "results": 0,
        "unknown_skipped": 0,
        "per_game": {},
        "per_day": {},
    }

    for path in files:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            stats["lines_total"] += 1
            m = _LINE_RE.match(line)
            if not m:
                continue
            stats["lines_matched"] += 1
            stamp, body = m.group(1), m.group(2).strip()

            rm = _RESULT_RE.match(body)
            if not rm:
                continue
            gid = _game_id(rm.group("name"))
            if not gid:
                continue
            verdict = rm.group("verdict").strip()
            stats["results"] += 1

            try:
                moment = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            date_key = game_date_key(moment)
            time_text = moment.strftime("%H:%M")

            node = days.setdefault(date_key, {}).setdefault(
                gid,
                {
                    "game_name": rm.group("name").strip(),
                    "nickname": rm.group("nick").strip(),
                    "finished": False,
                    "known": False,
                    "checks": 0,
                    "last": "",
                    "unfinished_tasks": 0,
                },
            )
            node["checks"] += 1
            node["last"] = time_text
            if rm.group("nick").strip():
                node["nickname"] = rm.group("nick").strip()

            if any(w in verdict for w in UNKNOWN_WORDS):
                stats["unknown_skipped"] += 1
                continue          # 没拿到数据：不算 known、不算完成
            if any(w in verdict for w in DONE_WORDS):
                node["finished"] = True
                node["known"] = True
                node["unfinished_tasks"] = 0
            elif any(w in verdict for w in UNDONE_WORDS):
                node["known"] = True
                # 只记「有未完成」这个事实，不编具体项数（日志没逐项记）
                node["unfinished_tasks"] = max(node["unfinished_tasks"], 1)

            stats["per_game"][gid] = stats["per_game"].get(gid, 0) + 1
            stats["per_day"][date_key] = stats["per_day"].get(date_key, 0) + 1

    return days, stats


def main() -> int:
    ap = argparse.ArgumentParser(description="从日志重建 history.json")
    ap.add_argument("--dry-run", action="store_true", help="只打印结果，不写盘")
    ap.add_argument("--write", action="store_true", help="真正写入（先备份原文件）")
    args = ap.parse_args()

    if not args.dry_run and not args.write:
        args.dry_run = True

    days, stats = parse_logs()

    print("=" * 60)
    print("扫描日志文件 %d 个" % len(stats["files"]))
    print("结果行匹配 %d 条，其中「无数据」跳过 %d 条" % (stats["results"], stats["unknown_skipped"]))
    print()
    print("按游戏命中次数：")
    for gid, cnt in sorted(stats["per_game"].items(), key=lambda kv: -kv[1]):
        print("  %-10s %d" % (gid, cnt))
    print()
    print("按游戏日（04:00 日界）分布：")
    for day in sorted(days):
        games = days[day]
        done = [g for g, n in games.items() if n["finished"]]
        known = [g for g, n in games.items() if n["known"]]
        print("  %s  记录 %d 款 / 已知 %d 款 / 完成 %d 款%s" % (
            day, len(games), len(known), len(done),
            ("  [" + ",".join(sorted(done)) + "]") if done else "",
        ))

    target = user_root() / "data" / "history.json"
    print()
    print("目标文件：%s" % target)

    if args.dry_run:
        print("（dry-run，未写盘。加 --write 执行）")
        return 0

    # 备份原文件
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = target.with_suffix(".json.bak-rebuild-%s" % stamp)
        shutil.copy2(target, bak)
        print("已备份原文件 → %s" % bak.name)

    # 与已有数据**合并**（不覆盖现有更新的记录）
    existing: dict = {}
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
    merged_days = existing.get("days") or {}

    for day, games in days.items():
        node_day = merged_days.setdefault(day, {})
        for gid, rebuilt in games.items():
            cur = node_day.get(gid)
            if not isinstance(cur, dict):
                node_day[gid] = rebuilt
                continue
            # 已有记录优先：只在重建结果更「完整」时补齐，绝不把 finished 冲成 False
            cur.setdefault("game_name", rebuilt["game_name"])
            if rebuilt.get("nickname"):
                cur["nickname"] = cur.get("nickname") or rebuilt["nickname"]
            cur["checks"] = max(int(cur.get("checks") or 0), rebuilt["checks"])
            if rebuilt.get("last") and not cur.get("last"):
                cur["last"] = rebuilt["last"]
            if rebuilt["finished"]:
                cur["finished"] = True
                cur["known"] = True
            if rebuilt["known"]:
                cur["known"] = True
            cur["unfinished_tasks"] = max(
                int(cur.get("unfinished_tasks") or 0), rebuilt["unfinished_tasks"]
            )

    payload = {"version": existing.get("version", 1), "days": merged_days}
    tmp = str(target) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, target)
    print("已写入 %d 个游戏日" % len(merged_days))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

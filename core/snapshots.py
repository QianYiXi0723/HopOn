#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据快照轮转 + 启动校验恢复（2026-11-11 第一队列 #6·②）。

模仿 GFS 祖父-父-子轮转（Wikipedia/Backblaze）：daily keep 7 + 周日晋升
weekly keep 4——纯轮转有「整周期丢失」退化风险，分两层保留对冲。
反面教材（为何必须自己做）：Chromium Preferences 损坏=重置默认、VS Code
官方明说「无法恢复之前设置」——巨头都不自动备份，用户只能求 OS 文件历史；
HopOn 自动快照 + 启动校验 + 静默恢复 + 主动告知（toasts）。

边界：快照防「文件损坏/被删」，防不了「写错内容」——那靠 config 的
.bak-1/2/3 滚动备份（见 login_ui._backup_config_before_write）。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dailycheck.snapshots")

#: 纳入快照的主数据（相对 user_dir）
SNAPSHOT_FILES = ("config.json", "data/history.json", "data/activities.json")
SNAPSHOT_DIR = Path("data") / "snapshots"
#: GFS-lite 保留档位：daily 7 天、weekly 4 周
DAILY_KEEP_DAYS = 7
WEEKLY_KEEP_DAYS = 28
_STAMP_FILE = ".last_snapshot_date"


def _root(user_dir: Path) -> Path:
    return user_dir / SNAPSHOT_DIR


def _date_from_name(name: str) -> Optional[datetime]:
    """从 ``stem.daily-YYYYMMDD.json`` / ``stem.weekly-YYYYMMDD.json`` 取日期。"""
    for tag in (".daily-", ".weekly-"):
        at = name.find(tag)
        if at >= 0:
            raw = name[at + len(tag):].split(".")[0]
            try:
                return datetime.strptime(raw, "%Y%m%d")
            except ValueError:
                return None
    return None


def _prune(root: Path, *, now: Optional[datetime] = None) -> int:
    """按档位保留期清理：daily 过 7 天、weekly 过 4 周即删。

    时钟与 snapshot_daily 同源（传入 now 或真实时钟）——两套时钟混用会把
    刚拍的档判成旧档当场清掉（沙盒测试实证过这个坑）。
    """
    now = now or datetime.now()
    removed = 0
    for f in root.glob("*.json"):
        date = _date_from_name(f.name)
        if date is None:
            continue  # 不认识的文件不动（含 .recover-tmp 之外的异常名）
        keep_days = DAILY_KEEP_DAYS if ".daily-" in f.name else WEEKLY_KEEP_DAYS
        if (now - date).days >= keep_days:
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def snapshot_daily(user_dir: Path, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """跨日拍快照（幂等：同一天重复调用只拍一次，未跨日≈一次 stamp 读）。

    周日（GFS 父层）额外把当天 daily 晋升为 weekly。拍一半的快照
    （tmp 残留）不会被当作恢复点——先写 tmp 再 os.replace。
    """
    now = now or datetime.now()
    root = _root(user_dir)
    root.mkdir(parents=True, exist_ok=True)
    stamp = root / _STAMP_FILE
    today = now.strftime("%Y-%m-%d")
    try:
        if stamp.read_text(encoding="utf-8").strip() == today:
            return {"took": False, "files": [], "pruned": 0}
    except OSError:
        pass

    taken: List[str] = []
    stamp_day = now.strftime("%Y%m%d")
    for rel in SNAPSHOT_FILES:
        src = user_dir / rel
        if not src.exists():
            continue
        dst = root / f"{Path(rel).stem}.daily-{stamp_day}.json"
        try:
            tmp = dst.with_suffix(".tmp")
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
            taken.append(dst.name)
        except OSError as exc:
            logger.warning("快照 %s 失败（不影响本轮检查）：%s", rel, exc)
    pruned = _prune(root, now=now)
    if now.weekday() == 6:   # 周日：把今天的 daily 晋升为 weekly
        for name in list(taken):
            src = root / name
            try:
                weekly = src.name.replace(".daily-", ".weekly-")
                tmp = (root / weekly).with_suffix(".tmp")
                shutil.copy2(src, tmp)
                os.replace(tmp, root / weekly)
            except OSError as exc:
                logger.warning("weekly 晋升失败（无害，下个周日再试）：%s", exc)
    try:
        stamp.write_text(today + "\n", encoding="utf-8")
    except OSError as exc:
        logger.warning("快照日期戳写入失败（明天会重拍，无害）：%s", exc)
    if taken:
        logger.info("数据快照完成：%s（清理旧档 %d 个）", "、".join(taken), pruned)
    return {"took": bool(taken), "files": taken, "pruned": pruned}


def validate_and_recover(user_dir: Path) -> Dict[str, Any]:
    """启动校验：主数据 JSON 损坏 → 最新可用快照回填 → log + toasts 告知。

    候选快照先读一遍校验是合法 JSON 才用（快照自己也可能坏）；回填走
    tmp + os.replace，回填失败不影响原文件（顶多保持损坏，下轮再试）。
    """
    recovered: List[Dict[str, str]] = []
    checked = 0
    root = _root(user_dir)
    for rel in SNAPSHOT_FILES:
        path = user_dir / rel
        if not path.exists():
            continue
        checked += 1
        try:
            json.loads(path.read_text(encoding="utf-8"))
            continue   # 健康
        except Exception:  # noqa: BLE001 - JSON 解析失败 = 损坏
            pass
        stem = Path(rel).stem
        for cand in sorted(root.glob(f"{stem}.*.json"), reverse=True):
            try:
                json.loads(cand.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 - 快照也坏，试更旧的
                continue
            try:
                tmp = path.with_name(path.name + ".recover-tmp")
                shutil.copy2(cand, tmp)
                os.replace(tmp, path)
                recovered.append({"file": rel, "from": cand.name})
                logger.warning(
                    "数据自愈：%s 损坏，已从快照 %s 回填", rel, cand.name)
            except OSError as exc:
                logger.warning("数据自愈回填 %s 失败：%s", rel, exc)
            break
    if recovered:
        try:
            from core.toasts import add_toasts

            for item in recovered:
                add_toasts(
                    "HopOn 数据自愈",
                    f"{Path(item['file']).name} 检测到损坏，已从快照恢复"
                    f"（{item['from']}）——如发现数据异常请用诊断包反馈",
                )
        except Exception:  # noqa: BLE001 - 告知失败不影响恢复本身
            pass
    return {"checked": checked, "recovered": recovered}

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手动打卡：完成账本 + 未完成查询 + 独立时刻到期判定 + 模板渲染。

日界 = 04:00 游戏日界（game_moment 唯一入口，与游戏日常重置同频）——
账本按 day_key 记「今日已完成」，跨日自然失效 = 次日自动重开
（Habitica CDS 同语义，但日界与游戏日常对齐）。
消息模板在 Python 端渲染（{name}/{time}），前端只预览不伪造推送结果。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.ipc_lock import blocking as _ipc_blocking

logger = logging.getLogger("dailycheck.dailies")

DEFAULT_TEMPLATE = "📌 {name} 还没做，记得处理，完成后在托盘勾掉"
STANDALONE_TITLE = "⏰ 手动打卡"

#: 账本保留天数（day_key 过期即清，防无限膨胀）
_KEEP_DAYS = 14


def _state_path(user_dir: Path) -> Path:
    return Path(user_dir) / "data" / "custom_dailies_state.json"


def _day_key(now: Optional[datetime] = None) -> str:
    from core.history import game_moment

    return (now or game_moment()).strftime("%Y-%m-%d")


def _load_state(user_dir: Path) -> Dict[str, Any]:
    try:
        data = json.loads(_state_path(user_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - 损坏/不存在 = 空账本（快照自愈族会兜）
        return {}


def _save_state(user_dir: Path, state: Dict[str, Any]) -> None:
    path = _state_path(user_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _ipc_blocking(str(path) + ".lock"):
        path.write_text(
            json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _prune(state: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now()
    keep: Dict[str, Any] = {}
    for section in ("done", "fired"):
        rows = state.get(section) if isinstance(state.get(section), dict) else {}
        kept_rows: Dict[str, Any] = {}
        for day_key, value in rows.items():
            try:
                day = datetime.strptime(str(day_key), "%Y-%m-%d")
            except ValueError:
                continue
            if (now - day).days < _KEEP_DAYS:
                kept_rows[day_key] = value
        keep[section] = kept_rows
    return keep


def render_message(item: Dict[str, Any]) -> str:
    """模板渲染（{name}/{time}），空模板回落默认；未知花括号占位清掉。"""
    name = str(item.get("name") or "")
    tpl = str(item.get("template") or "").strip() or DEFAULT_TEMPLATE
    out = tpl.replace("{name}", name).replace("{time}", str(item.get("time") or ""))
    out = re.sub(r"\{[a-zA-Z_]+\}", "", out)
    out = re.sub(r"  +", " ", out).strip()
    return out or DEFAULT_TEMPLATE.replace("{name}", name).replace("{time}", "")


def is_done(item_id: str, user_dir: Path, *, now: Optional[datetime] = None) -> bool:
    state = _load_state(user_dir)
    rows = state.get("done") if isinstance(state.get("done"), dict) else {}
    return item_id in (rows.get(_day_key(now)) or {})


def mark_done(item_id: str, user_dir: Path, *, undo: bool = False,
              now: Optional[datetime] = None) -> None:
    """勾掉/撤销今日完成（跨进程锁；撤销=清当日记录）。"""
    state = _load_state(user_dir)
    state = _prune(state, now)
    day_key = _day_key(now)
    done = state.get("done") if isinstance(state.get("done"), dict) else {}
    rows = dict(done.get(day_key) or {})
    if undo:
        rows.pop(item_id, None)
    else:
        rows[item_id] = datetime.now().strftime("%H:%M:%S")
    state["done"] = {**done, day_key: rows}
    _save_state(user_dir, state)


def undone_today(items: List[Dict[str, Any]], user_dir: Path,
                 *, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """启用且今日未勾掉的项（merged 班次推送用）。"""
    return [it for it in items
            if it.get("enabled", True) and not is_done(str(it.get("id") or ""), user_dir, now=now)]


def standalone_due(items: List[Dict[str, Any]], user_dir: Path,
                   *, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """独立时刻到期判定：越过目标且当日未推、未勾掉（迟过=当场补发，只早不迟）。"""
    now = now or datetime.now()
    day_key = _day_key(now)
    state = _load_state(user_dir)
    fired = state.get("fired") if isinstance(state.get("fired"), dict) else {}
    fired_today = fired.get(day_key) if isinstance(fired.get(day_key), dict) else {}
    done = state.get("done") if isinstance(state.get("done"), dict) else {}
    done_today = done.get(day_key) if isinstance(done.get(day_key), dict) else {}
    now_hm = now.strftime("%H:%M")
    out: List[Dict[str, Any]] = []
    for it in items:
        if not it.get("enabled", True) or it.get("mode") != "standalone":
            continue
        target = str(it.get("time") or "")
        if not re.match(r"^\d{1,2}:\d{2}$", target):
            continue
        iid = str(it.get("id") or "")
        if fired_today.get(iid) or iid in done_today:
            continue
        if now_hm >= target:
            out.append(it)
    return out


def mark_fired(item_id: str, user_dir: Path, *, now: Optional[datetime] = None) -> None:
    """独立提醒已推记账（当日一次；04:00 日界翻转自动重置）。"""
    state = _load_state(user_dir)
    state = _prune(state, now)
    day_key = _day_key(now)
    fired = state.get("fired") if isinstance(state.get("fired"), dict) else {}
    rows = dict(fired.get(day_key) or {})
    rows[item_id] = datetime.now().strftime("%H:%M:%S")
    state["fired"] = {**fired, day_key: rows}
    _save_state(user_dir, state)


def forget(item_id: str, user_dir: Path) -> None:
    """删除项时清掉它的全部账本记录（done/fired 所有日期）。"""
    state = _load_state(user_dir)
    for section in ("done", "fired"):
        rows = state.get(section) if isinstance(state.get(section), dict) else {}
        state[section] = {
            day: {k: v for k, v in (value or {}).items() if k != item_id}
            for day, value in rows.items()
        }
    _save_state(user_dir, state)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自定义提醒：完成账本 + 未完成查询 + 独立时刻到期判定 + 模板渲染。

**每项自带重置时刻**（item.reset，默认 04:00 与游戏日常重置同频，可自定义
如健身房用 00:00）——勾掉的「今日」= 从该项最近一次 reset 到下一次 reset，
到点自动重开。周期键 = (now - reset) 平移后取日期，逐项独立互不干扰。

消息模板在 Python 端渲染（{name}/{time}），前端只预览不伪造推送结果。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.ipc_lock import blocking as _ipc_blocking

logger = logging.getLogger("dailycheck.dailies")

DEFAULT_TEMPLATE = "📌 {name} 还没做"
DEFAULT_RESET = "04:00"
STANDALONE_TITLE = "⏰ 自定义提醒"

#: 账本保留天数（周期过期即清，防无限膨胀）
_KEEP_DAYS = 14


def _state_path(user_dir: Path) -> Path:
    return Path(user_dir) / "data" / "custom_dailies_state.json"


def _reset_minutes(item: Dict[str, Any]) -> int:
    reset = str(item.get("reset") or DEFAULT_RESET)
    m = re.match(r"^(\d{1,2}):(\d{2})$", reset)
    if not m:
        return 240
    return int(m.group(1)) * 60 + int(m.group(2))


def _period_key(item: Dict[str, Any], now: Optional[datetime] = None) -> str:
    """该项的「今日」周期键：now 平移 -reset 后取日期（reset 时刻翻日）。"""
    now = now or datetime.now()
    shifted = now - timedelta(minutes=_reset_minutes(item))
    return shifted.strftime("%Y-%m-%d")


def _period_date(item: Dict[str, Any], now: Optional[datetime] = None):
    """当前周期的日期对象（供周计划判定 weekday）。"""
    now = now or datetime.now()
    return (now - timedelta(minutes=_reset_minutes(item))).date()


def _planned_weekday(item: Dict[str, Any], day_date) -> bool:
    """周计划判定：day_date（date）是否在该项的提醒日里。

    days = [一,二,三,四,五,六,日] 七元布尔（索引 0=周一，Python weekday 口径）；
    缺失/格式不对 = 每天（向后兼容旧数据）。
    """
    days = item.get("days")
    if not isinstance(days, list) or len(days) != 7:
        return True
    wd = day_date.weekday()   # 0=周一 … 6=周日
    return bool(days[wd])


def _load_state(user_dir: Path) -> Dict[str, Any]:
    try:
        data = json.loads(_state_path(user_dir).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"v": 2, "done": {}, "fired": {}}
    except Exception:  # noqa: BLE001 - 损坏/不存在 = 空账本（快照自愈族会兜）
        return {"v": 2, "done": {}, "fired": {}}
    return _migrate(data)


def _migrate(state: Dict[str, Any]) -> Dict[str, Any]:
    """v1（全局 04:00 day_key 桶）→ v2（逐项周期键）：旧桶按 04:00 语义反转。"""
    if state.get("v") == 2:
        return state
    out: Dict[str, Any] = {"v": 2, "done": {}, "fired": {}}
    for section in ("done", "fired"):
        rows = state.get(section) if isinstance(state.get(section), dict) else {}
        for day, value in rows.items():
            if isinstance(value, dict):
                for iid, ts in value.items():
                    out[section].setdefault(str(iid), {})[str(day)] = ts
    return out


def _save_state(user_dir: Path, state: Dict[str, Any]) -> None:
    path = _state_path(user_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _ipc_blocking(str(path) + ".lock"):
        path.write_text(
            json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _parse_period(p: str) -> Optional[datetime]:
    try:
        return datetime.strptime(str(p), "%Y-%m-%d")
    except ValueError:
        return None


def _prune(state: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    cutoff = (now or datetime.now()) - timedelta(days=_KEEP_DAYS)
    out: Dict[str, Any] = {"v": 2}
    for section in ("done", "fired"):
        rows = state.get(section) if isinstance(state.get(section), dict) else {}
        kept: Dict[str, Dict[str, Any]] = {}
        for iid, periods in rows.items():
            if not isinstance(periods, dict):
                continue
            kept_periods = {
                p: ts for p, ts in periods.items()
                if (_parse_period(p) or datetime.min) >= cutoff
            }
            if kept_periods:
                kept[iid] = kept_periods
        out[section] = kept
    return out


def render_message(item: Dict[str, Any]) -> str:
    """模板渲染（{name}/{time}），空模板回落默认；未知花括号占位清掉。"""
    name = str(item.get("name") or "")
    tpl = str(item.get("template") or "").strip() or DEFAULT_TEMPLATE
    out = tpl.replace("{name}", name).replace("{time}", str(item.get("time") or ""))
    out = re.sub(r"\{[a-zA-Z_]+\}", "", out)
    out = re.sub(r"  +", " ", out).strip()
    return out or DEFAULT_TEMPLATE.replace("{name}", name).replace("{time}", "")


def is_done(item: Dict[str, Any], user_dir: Path,
            *, now: Optional[datetime] = None) -> bool:
    state = _load_state(user_dir)
    done = state.get("done") if isinstance(state.get("done"), dict) else {}
    periods = done.get(str(item.get("id") or "")) or {}
    return _period_key(item, now) in periods


def mark_done(item: Dict[str, Any], user_dir: Path, *, undo: bool = False,
              now: Optional[datetime] = None) -> None:
    """勾掉/撤销该项的当前周期完成（撤销=清当前周期记录）。"""
    iid = str(item.get("id") or "")
    if not iid:
        return
    state = _load_state(user_dir)
    state = _prune(state, now)
    period = _period_key(item, now)
    done = state.get("done") if isinstance(state.get("done"), dict) else {}
    periods = dict(done.get(iid) or {})
    if undo:
        periods.pop(period, None)
    else:
        periods[period] = datetime.now().strftime("%H:%M:%S")
    state["done"] = {**done, iid: periods}
    _save_state(user_dir, state)


def streak_of(item: Dict[str, Any], user_dir: Path,
              *, now: Optional[datetime] = None) -> int:
    """连续完成周期数：当前周期已勾含当前，否则从上一周期往回数
    （与游戏 streak 同语义——今天没做完不断签）。账本 14 天，上限即 14。"""
    iid = str(item.get("id") or "")
    state = _load_state(user_dir)
    done = state.get("done") if isinstance(state.get("done"), dict) else {}
    periods = done.get(iid) or {}
    now = now or datetime.now()
    shifted = now - timedelta(minutes=_reset_minutes(item))
    start_k = 0 if shifted.strftime("%Y-%m-%d") in periods else 1
    streak = 0
    for k in range(start_k, _KEEP_DAYS + 1):
        day = (shifted - timedelta(days=k)).date()
        key = day.strftime("%Y-%m-%d")
        if not _planned_weekday(item, day):
            continue   # 非计划日：不打卡不断签，跳过继续往回数
        if key in periods:
            streak += 1
        else:
            break
    return streak


def undone_today(items: List[Dict[str, Any]], user_dir: Path,
                 *, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """启用、今天在周计划内且当前周期未勾掉的项（merged 班次推送用）。"""
    out: List[Dict[str, Any]] = []
    for it in items:
        if not it.get("enabled", True):
            continue
        if not _planned_weekday(it, _period_date(it, now)):
            continue
        if is_done(it, user_dir, now=now):
            continue
        out.append(it)
    return out


def standalone_due(items: List[Dict[str, Any]], user_dir: Path,
                   *, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """独立时刻到期判定：越过目标且本周期未推、未勾掉（迟过=当场补发，只早不迟）。"""
    now = now or datetime.now()
    state = _load_state(user_dir)
    fired = state.get("fired") if isinstance(state.get("fired"), dict) else {}
    done = state.get("done") if isinstance(state.get("done"), dict) else {}
    now_hm = now.strftime("%H:%M")
    out: List[Dict[str, Any]] = []
    for it in items:
        if not it.get("enabled", True) or it.get("mode") != "standalone":
            continue
        if not _planned_weekday(it, _period_date(it, now)):
            continue   # 非计划日：不提醒（手动打卡不受限）
        target = str(it.get("time") or "")
        if not re.match(r"^\d{1,2}:\d{2}$", target):
            continue
        iid = str(it.get("id") or "")
        period = _period_key(it, now)
        if iid in done and period in (done[iid] or {}):
            continue
        if period in (fired.get(iid) or {}):
            continue
        if now_hm >= target:
            out.append(it)
    return out


def mark_fired(item: Dict[str, Any], user_dir: Path,
               *, now: Optional[datetime] = None) -> None:
    """独立提醒已推记账（本周期一次；重置时刻翻转自动重置）。"""
    iid = str(item.get("id") or "")
    if not iid:
        return
    state = _load_state(user_dir)
    state = _prune(state, now)
    period = _period_key(item, now)
    fired = state.get("fired") if isinstance(state.get("fired"), dict) else {}
    periods = dict(fired.get(iid) or {})
    periods[period] = datetime.now().strftime("%H:%M:%S")
    state["fired"] = {**fired, iid: periods}
    _save_state(user_dir, state)


def forget(item_id: str, user_dir: Path) -> None:
    """删除项时清掉它的全部账本记录（done/fired 所有周期）。"""
    state = _load_state(user_dir)
    for section in ("done", "fired"):
        rows = state.get(section) if isinstance(state.get(section), dict) else {}
        rows.pop(item_id, None)
        state[section] = rows
    _save_state(user_dir, state)

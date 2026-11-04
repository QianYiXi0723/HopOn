# -*- coding: utf-8 -*-
"""页内通知队列：daemon 推送同步投递到打开着的控制台页面。

数据：data/toasts.json（环形队列，保留最近 KEEP 条）。写入方是
core.notifier.ToastNotifier（daemon 巡检 + 控制台测试推送，跨进程），
读方是前端轮询 GET /api/toasts —— **取走即标已读**（消费型队列）。
跨进程写走 core.ipc_lock（进程死亡内核自动释放，无陈旧锁）。
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

from core import ipc_lock

BASE_DIR = Path(__file__).resolve().parent.parent
TOASTS_PATH = BASE_DIR / "data" / "toasts.json"
KEEP = 30


def _load() -> List[Dict[str, Any]]:
    try:
        raw = json.loads(TOASTS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return raw if isinstance(raw, list) else []


def _save(items: List[Dict[str, Any]]) -> None:
    TOASTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".toasts-", suffix=".tmp", dir=str(TOASTS_PATH.parent)
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(items, ensure_ascii=False, indent=2))
        os.replace(tmp, TOASTS_PATH)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def add_toasts(title: str, body: str) -> None:
    """追加一条页内通知（跨进程安全，环形保留最近 KEEP 条）。"""
    with ipc_lock.blocking(str(TOASTS_PATH) + ".lock"):
        items = _load()
        items.append({
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "title": title,
            "body": body,
            "read": False,
        })
        _save(items[-KEEP:])


def take_unread(*, fresh_seconds: int = 600) -> List[Dict[str, Any]]:
    """取走未读且新鲜的（取走即标已读），新在前。页面轮询用。

    新鲜度过滤：控制台一周没开的话，积压的旧通知不该在下次打开时
    全部轰炸——超过 fresh_seconds 的未读直接过期丢弃。
    """
    cutoff = time.time() - fresh_seconds
    with ipc_lock.blocking(str(TOASTS_PATH) + ".lock"):
        items = _load()
        unread = []
        for item in items:
            if item.get("read"):
                continue
            try:
                fresh = time.mktime(
                    time.strptime(str(item.get("ts")), "%Y-%m-%d %H:%M:%S")
                ) >= cutoff
            except ValueError:
                fresh = False
            if fresh:
                unread.append(item)
            item["read"] = True
        if unread:
            _save(items)
        return list(reversed(unread))

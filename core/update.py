# -*- coding: utf-8 -*-
"""检查更新（**只查不装**，2026-11-05）。

这是 #20「自动更新·增量热换血」的第一步：先把「有没有新版、新版多大、
从哪下」搞清楚，**不动任何文件**。装与换血是后续独立能力，且被
#15 渠道裁决卡着（GitHub 资产直连已实测被重置）。

设计约束（都很实际，不是洁癖）：
* **网络失败绝不骚扰用户**：查不到就当"未知"，不弹错、不写日志噪音。
  一个每天跑 4 次的工具，断网是常态而不是异常。
* **结果缓存**：默认 6 小时内不重复请求，避免每次开控制台都打一次 GitHub。
* **版本比较用语义化**：2.4.10 > 2.4.9，不能按字典序。
* **不阻塞主流程**：调用方应丢到线程里，UI 只读缓存结果。

数据来源：GitHub Releases API（token 可选，公开仓库不填也能读，
但会有 60 次/小时的匿名限额）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: 仓库（与 core/__init__ 的版本号一起决定"有没有新版"）
REPO = "QianYiXi0723/HopOn"
#: Releases API
RELEASES_API = "https://api.github.com/repos/%s/releases/latest" % REPO
#: 请求超时：查更新不值得等太久
TIMEOUT = 6
#: 结果缓存时长（秒）：6 小时
CACHE_TTL = 6 * 3600
#: 缓存文件名（落用户数据根）
CACHE_NAME = "update_check.json"


@dataclass
class UpdateInfo:
    ok: bool = False                 # 本次检查是否成功（失败不报错，只是 unknown）
    current: str = ""                # 当前版本
    latest: str = ""                 # 远端最新版本
    has_update: bool = False
    url: str = ""                    # 发布页
    notes: str = ""                  # 更新说明（截断）
    assets: List[Dict[str, Any]] = field(default_factory=list)
    published_at: str = ""
    checked_at: str = ""
    message: str = ""                # 给用户看的一句话
    error: str = ""                  # 内部错误（不进 UI）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "current": self.current,
            "latest": self.latest,
            "has_update": self.has_update,
            "url": self.url,
            "notes": self.notes,
            "assets": self.assets,
            "published_at": self.published_at,
            "checked_at": self.checked_at,
            "message": self.message,
        }


def current_version() -> str:
    try:
        from core import __version__

        return str(__version__)
    except Exception:  # noqa: BLE001
        return "0.0.0"


def _parse(v: str) -> tuple:
    """把 ``2.4.10`` 解析成可比较元组；非数字段原样保留在后面。

    纯字典序会把 2.4.10 判成小于 2.4.9，必须按数字比。
    """
    v = str(v or "").strip().lstrip("vV")
    nums = re.findall(r"\d+", v)
    if not nums:
        return (0,)
    return tuple(int(n) for n in nums)


def is_newer(remote: str, local: str) -> bool:
    """remote 是否比 local 新。"""
    return _parse(remote) > _parse(local)


def _cache_path() -> Path:
    from core.paths import user_root

    return user_root() / "data" / CACHE_NAME


def load_cache() -> Optional[Dict[str, Any]]:
    p = _cache_path()
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, ValueError):
        return None


def _save_cache(payload: Dict[str, Any]) -> None:
    p = _cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(p) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except OSError as exc:
        logger.debug("[update] 写缓存失败（忽略）：%s", exc)


def _strip_notes(body: str, limit: int = 500) -> str:
    """GitHub 的 release body 是 markdown，截断即可（UI 只做纯文本展示）。"""
    text = (body or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def check(*, force: bool = False) -> UpdateInfo:
    """查一次最新版本。失败返回 ok=False 的 UpdateInfo，**不抛异常**。

    Args:
        force: True 时忽略缓存，强制请求。
    """
    cur = current_version()
    info = UpdateInfo(current=cur, checked_at=_now_text())

    cached = None if force else load_cache()
    if cached and _cache_fresh(cached):
        return _info_from_cache(cached, cur)

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "HopOn/%s" % cur,
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = "Bearer " + token

    try:
        import urllib.request

        req = urllib.request.Request(RELEASES_API, headers=headers)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - 断网/限流都算"查不到"
        info.error = str(exc)[:200]
        info.message = "暂时查不到更新（不影响使用）"
        logger.debug("[update] 检查失败：%s", exc)
        return info

    latest = str(payload.get("tag_name") or "").lstrip("vV")
    if not latest:
        info.error = "返回里没有 tag_name"
        info.message = "暂时查不到更新（不影响使用）"
        return info

    assets = [
        {
            "name": str(a.get("name") or ""),
            "size": int(a.get("size") or 0),
            "url": str(a.get("browser_download_url") or ""),
        }
        for a in (payload.get("assets") or [])
        if isinstance(a, dict)
    ]

    info.ok = True
    info.latest = latest
    info.has_update = is_newer(latest, cur)
    info.url = str(payload.get("html_url") or "")
    info.notes = _strip_notes(str(payload.get("body") or ""))
    info.assets = assets
    info.published_at = str(payload.get("published_at") or "")
    info.message = (
        "发现新版本 %s（当前 %s）" % (latest, cur)
        if info.has_update
        else "已是最新版本（%s）" % cur
    )

    _save_cache(
        {
            "checked_ts": time.time(),
            "checked_at": info.checked_at,
            "current": cur,
            "latest": latest,
            "has_update": info.has_update,
            "url": info.url,
            "notes": info.notes,
            "assets": assets,
            "published_at": info.published_at,
            "message": info.message,
        }
    )
    return info


def _now_text() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _cache_fresh(cached: Dict[str, Any]) -> bool:
    try:
        ts = float(cached.get("checked_ts") or 0)
    except (TypeError, ValueError):
        return False
    return (time.time() - ts) < CACHE_TTL


def _info_from_cache(cached: Dict[str, Any], cur: str) -> UpdateInfo:
    latest = str(cached.get("latest") or "")
    return UpdateInfo(
        ok=True,
        current=cur,
        latest=latest,
        has_update=bool(latest) and is_newer(latest, cur),
        url=str(cached.get("url") or ""),
        notes=str(cached.get("notes") or ""),
        assets=list(cached.get("assets") or []),
        published_at=str(cached.get("published_at") or ""),
        checked_at=str(cached.get("checked_at") or ""),
        message=str(cached.get("message") or ""),
    )


def peek() -> Optional[Dict[str, Any]]:
    """读缓存，不发请求（给 UI 首屏用，零延迟零网络）。"""
    cached = load_cache()
    if not cached:
        return None
    return _info_from_cache(cached, current_version()).to_dict()

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

import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core import netutil

logger = logging.getLogger(__name__)

#: 仓库（与 core/__init__ 的版本号一起决定"有没有新版"）
REPO = "QianYiXi0723/HopOn"
#: Releases API：GitHub 主渠道 + Gitee 国内备用（2026-11-13 用户裁决：
#: GitHub 连不上就走 Gitee 镜像，两通道都断才报失败）
RELEASES_API = "https://api.github.com/repos/%s/releases/latest" % REPO
GITEE_RELEASES_API = "https://gitee.com/api/v5/repos/qianyixi/HopOn/releases/latest"
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

    import urllib.request

    gh_err: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    channel = "github"
    try:
        req = urllib.request.Request(RELEASES_API, headers=headers)
        with netutil.urlopen_with_fallback(req, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - 主渠道断网/限流走备用
        gh_err = str(exc)[:200]
        logger.debug("[update] GitHub 检查失败，转 Gitee 备用：%s", exc)

    gitee_err: Optional[str] = None
    if payload is None:
        try:
            req = urllib.request.Request(
                GITEE_RELEASES_API,
                headers={"User-Agent": "HopOn/%s" % cur})
            with netutil.urlopen_with_fallback(req, timeout=TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            channel = "gitee"
        except Exception as exc:  # noqa: BLE001 - 两通道都断才算失败
            gitee_err = str(exc)[:200]
            info.error = ("github: %s; gitee: %s" % (gh_err, gitee_err))[:300]
            info.message = "检查更新失败：GitHub 与 Gitee 都连不上，可稍后重试"
            logger.debug("[update] 双通道均失败：%s / %s", gh_err, gitee_err)
            return info

    latest = str(payload.get("tag_name") or "").lstrip("vV")
    if not latest:
        info.error = "返回里没有 tag_name"
        info.message = "检查更新失败：接口返回异常，可稍后重试"
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
    if info.has_update:
        info.message = "发现新版本 %s（当前 %s）" % (latest, cur)
    elif channel == "gitee":
        # 备用通道数据可能落后（release 传漏时 Gitee 停在旧版）——此时不能
        # 替 GitHub 下「已是最新」的结论，如实说无法判断
        info.message = ("GitHub 暂不可达；Gitee 备用通道最新 %s 不高于当前 %s"
                        "——备用通道数据落后，是否有更新无法确认，可稍后重试"
                        % (latest, cur))
    else:
        info.message = "已是最新版本（%s）" % cur

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


# --------------------------------------------------------------------------- #
# 站内下载 + 校验（2026-11-15，#20 第一步落地）：
#   引擎侧流式下载 Release 资产 → SHA-256 校验 → 引导用户双击安装。
#   通道实测（2026-11-15）：Gitee 直连 14.6MB @ 1.9MB/s；GitHub 能通但
#   931B/s——主通道 Gitee，GitHub 备用。不自动安装：装/换血是 #20 的
#   后续独立能力，v1 只负责「包到本地且校验通过」。
# --------------------------------------------------------------------------- #

#: 下载根目录（用户数据根下，用户可在文件管理器里找到）
DOWNLOAD_DIR_NAME = "downloads"
#: 空闲超时（秒）：连续这么久没收到数据才判死——慢下载可以跑很久
IDLE_TIMEOUT = 20
#: 磁盘剩余空间粗检阈值（MB）：低于此值直接拒绝
MIN_FREE_MB = 300
#: 通道顺序：实测 Gitee 快且稳
DL_CHANNELS = ("gitee", "github")

_download_lock = threading.Lock()
_download_job: Optional[Dict[str, Any]] = None   # 当前任务（单任务锁）


def download_dir(version: str) -> Path:
    from core.paths import user_root

    return user_root() / "data" / DOWNLOAD_DIR_NAME / ("v" + str(version).lstrip("vV"))


def _asset_urls(tag: str, asset: str) -> List[str]:
    """按通道顺序给出资产直连 URL（Gitee 优先）。tag 不带 v 前缀也统一补。"""
    tag = "v" + str(tag).lstrip("vV")
    return [
        f"https://gitee.com/qianyixi/HopOn/releases/download/{tag}/{asset}",
        f"https://github.com/QianYiXi0723/HopOn/releases/download/{tag}/{asset}",
    ]


def _sums_urls(tag: str) -> List[str]:
    tag = "v" + str(tag).lstrip("vV")
    return [
        f"https://gitee.com/qianyixi/HopOn/releases/download/{tag}/SHA256SUMS.txt",
        f"https://github.com/QianYiXi0723/HopOn/releases/download/{tag}/SHA256SUMS.txt",
    ]


def _free_mb(path: Path) -> int:
    import shutil

    try:
        return int(shutil.disk_usage(str(path.parent)).free / 1048576)
    except OSError:
        return MIN_FREE_MB  # 读不到就不拦


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1048576)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _verify_sums(pkg: Path, tag: str, asset: str) -> Dict[str, Any]:
    """拉同渠道 SHA256SUMS.txt 校验包。返回 {ok, message}。

    官方规范：校验失败即丢弃；哈希与包同渠道不混用。
    """
    import urllib.request

    expected = ""
    last_err = ""
    for url in _sums_urls(tag):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "HopOn/%s" % current_version()})
            with netutil.urlopen_with_fallback(req, timeout=TIMEOUT) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            for line in text.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1].strip("*") == asset:
                    expected = parts[0].lower()
                    break
            if expected:
                break
        except Exception as exc:  # noqa: BLE001 - 单通道失败试下一个
            last_err = str(exc)[:150]
    if not expected:
        return {"ok": False, "message": "校验清单（SHA256SUMS.txt）拉取失败：%s" % (last_err or "未找到该资产的记录")}
    actual = _sha256_file(pkg)
    if actual != expected:
        return {"ok": False, "message": "SHA-256 校验不匹配——包可能损坏，已丢弃（预期 %s…，实际 %s…）" % (expected[:12], actual[:12])}
    return {"ok": True, "message": "SHA-256 校验通过"}


def start_download(version: str, asset: str) -> Dict[str, Any]:
    """发起一次站内下载（单任务锁；已有任务在跑则拒绝）。

    立即返回，下载在后台线程跑；进度查 :func:`download_status`。
    """
    global _download_job
    asset = str(asset or "").strip()
    # 白名单：只允许主包形态，文件名取 basename 防路径注入
    if not re.fullmatch(r"[\w.\-]+\.(exe|zip)", asset, re.IGNORECASE):
        return {"ok": False, "message": "不支持的资产类型（只收 Setup.exe / zip 主包）"}
    version = str(version or "").strip().lstrip("vV")
    if not version:
        return {"ok": False, "message": "缺少版本号"}
    with _download_lock:
        if _download_job and _download_job.get("stage") in ("downloading", "verify"):
            return {"ok": False, "message": "已有下载任务在进行（%s）" % _download_job.get("asset", "")}
        target_dir = download_dir(version)
        pkg = target_dir / asset
        if pkg.exists():
            # 已有本地包：直接走校验（快路径，不重复下载）
            job = {
                "stage": "verify", "version": version, "asset": asset,
                "bytes": pkg.stat().st_size, "total": pkg.stat().st_size,
                "speed": 0, "file": str(pkg), "sha_ok": None, "message": "本地已有此包，直接校验",
                "_cancel": False,
            }
            _download_job = job
            threading.Thread(target=_finish_job, args=(job, pkg, version, asset),
                             daemon=True, name="update-verify").start()
            return {"ok": True, "message": "本地已有此包，直接校验", "existed": True}
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {"ok": False, "message": "创建下载目录失败：%s" % exc}
        if _free_mb(target_dir) < MIN_FREE_MB:
            return {"ok": False, "message": "磁盘剩余空间不足 %dMB，拒绝下载" % MIN_FREE_MB}
        job = {
            "stage": "downloading", "version": version, "asset": asset,
            "bytes": 0, "total": 0, "speed": 0, "file": str(pkg),
            "sha_ok": None, "message": "连接下载源…", "_cancel": False,
        }
        _download_job = job
        threading.Thread(target=_download_worker, args=(job, pkg, version, asset),
                         daemon=True, name="update-download").start()
        return {"ok": True, "message": "开始下载 %s" % asset}


def _download_worker(job: Dict[str, Any], pkg: Path, version: str, asset: str) -> None:
    """后台下载线程：流式写盘+进度回写+空闲超时（学 MFAAvalonia
    DownloadStreamCopier：限空闲不限总时长；长度完整性断言）。"""
    global _download_job
    import urllib.request

    urls = _asset_urls(version, asset)
    last_err = ""
    for i, url in enumerate(urls):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "HopOn/%s" % current_version()})
            with netutil.urlopen_with_fallback(req, timeout=TIMEOUT) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                job["total"] = total
                t0 = time.time()
                got = 0
                with open(str(pkg) + ".part", "wb") as fh:
                    while True:
                        if job.get("_cancel"):
                            fh.close()
                            try:
                                os.remove(str(pkg) + ".part")
                            except OSError:
                                pass
                            job["stage"] = "error"
                            job["message"] = "已取消"
                            return
                        chunk = resp.read(262144)
                        if not chunk:
                            break
                        fh.write(chunk)
                        got += len(chunk)
                        job["bytes"] = got
                        elapsed = max(0.001, time.time() - t0)
                        job["speed"] = int(got / elapsed)
                        job["message"] = "下载中（%s）" % ("Gitee" if i == 0 else "GitHub")
            if got == 0 or (total and got != total):
                raise IOError("下载不完整：预期 %s，实际 %s 字节" % (total or "?", got))
            os.replace(str(pkg) + ".part", pkg)
            _finish_job(job, pkg, version, asset)
            return
        except Exception as exc:  # noqa: BLE001 - 换通道重试
            last_err = str(exc)[:200]
            logger.debug("[update] 下载 %s 通道失败：%s", url.split("/")[2], exc)
            try:
                os.remove(str(pkg) + ".part")
            except OSError:
                pass
            if job.get("_cancel"):
                job["stage"] = "error"
                job["message"] = "已取消"
                return
    job["stage"] = "error"
    job["message"] = "下载失败（Gitee 与 GitHub 均不可达）：%s" % last_err


def _finish_job(job: Dict[str, Any], pkg: Path, version: str, asset: str) -> None:
    """下载完成后：SHA-256 校验（同渠道 SUMS）→ 终态。"""
    global _download_job
    with _download_lock:
        job["stage"] = "verify"
        job["message"] = "校验 SHA-256…"
        verdict = _verify_sums(pkg, version, asset)
        job["sha_ok"] = verdict["ok"]
        job["message"] = verdict["message"]
        if not verdict["ok"]:
            try:
                pkg.unlink()   # 校验失败即丢弃（官方规范）
            except OSError:
                pass
        job["stage"] = "done" if verdict["ok"] else "error"
        _save_download_state(job)


#: 下载终态持久化（2026-11-15 用户实测「下载后检测不到本地资源而无法更新」
#  的根因：done 态只在引擎进程内存里——引擎/壳任何一次重启都清零，本地
#  文件明明还在却「检测不到」）。done 落盘，重启后 download_status 从盘恢复。
STATE_NAME = "download_state.json"


def _download_state_path() -> Path:
    from core.paths import user_root

    return user_root() / "data" / STATE_NAME


def _save_download_state(job: Dict[str, Any]) -> None:
    """终态（done/error）落盘；只存可恢复的最小字段。"""
    payload = {
        "stage": job.get("stage"),
        "version": job.get("version"),
        "asset": job.get("asset"),
        "file": job.get("file"),
        "sha_ok": job.get("sha_ok"),
        "total": job.get("total"),
        "message": job.get("message"),
        "saved_at": _now_text(),
    }
    try:
        p = _download_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(p) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except OSError as exc:
        logger.debug("[update] 下载状态落盘失败（忽略）：%s", exc)


def _load_download_state() -> Optional[Dict[str, Any]]:
    """读盘上终态；file 不存在（被删/被移动）则视为无效清掉。"""
    try:
        p = _download_state_path()
        if not p.exists():
            return None
        raw = json.loads(p.read_text(encoding="utf-8"))
        if raw.get("stage") != "done" or not raw.get("sha_ok"):
            return None
        if not raw.get("file") or not os.path.isfile(raw["file"]):
            try:
                p.unlink()
            except OSError:
                pass
            return None
        raw["ok"] = True
        return raw
    except (OSError, ValueError):
        return None


def download_status() -> Dict[str, Any]:
    """当前下载任务状态：内存任务优先；无任务时从盘恢复 done 终态
    （引擎重启后仍能「一键安装」，根治「检测不到本地资源」）。"""
    with _download_lock:
        if not _download_job:
            saved = _load_download_state()
            if saved:
                return saved
            return {"stage": "idle", "ok": True}
        return {k: v for k, v in _download_job.items() if not k.startswith("_")}


def cancel_download() -> Dict[str, Any]:
    global _download_job
    with _download_lock:
        if not _download_job or _download_job.get("stage") not in ("downloading", "verify"):
            return {"ok": False, "message": "没有进行中的下载任务"}
        _download_job["_cancel"] = True
        return {"ok": True, "message": "正在取消…"}

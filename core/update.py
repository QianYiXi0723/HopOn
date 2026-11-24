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


def _status_message(latest: str, cur: str, has_update: bool,
                    channel: str) -> str:
    """检查更新的用户文案（2026-11-24 逐字审修订）。

    逐句口径：
    - 有新版：说清版本号即停，不追加催促（用户文案准则①）。
    - Gitee 通道且 latest==cur：这就是「已是最新」，不再说「无法确认/
      稍后重试」——那种措辞让用户以为更新没成功（诊断包实证的误导）。
    - Gitee 通道且 latest<cur：备用通道数据确实落后，**如实**说无法确认，
      这是事实不是情绪，保留（删情绪不删信息，见 lesson 0mwe2sbs）。
    """
    if has_update:
        return "发现新版本 %s（当前 %s）" % (latest, cur)
    if channel == "gitee" and _parse(latest) < _parse(cur):
        return ("GitHub 暂不可达；Gitee 备用通道最新 %s 低于当前 %s"
                "——备用通道数据落后，是否有更新暂无法确认" % (latest, cur))
    return "已是最新版本（%s）" % cur


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
    info.message = _status_message(latest, cur, info.has_update, channel)

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
#: 更新工作区（安装根下）：staging 暂存 + pending.json + updater.log。
#: 与 HopOnUpdater.exe 的 updateDir 同一处——引擎运行期解压（prepare 前移），
#: Updater 退出后只做纯覆盖（2026-11-22 staged 改造，Firefox/VS Code 同款）
UPDATE_DIR_NAME = ".update"
#: staging 就绪判据（=Updater 落盘校验 Required 清单的 staging 期子集，
#: 递进校验：引擎验 3 项、apply 期 Updater 兜底验 5 项）：
#: 任一缺失/为空 = staging 无效
STAGING_REQUIRED = ("HopOn.exe", r"engine\app.py", r"engine\daemon.py")
#: pending.json：staged 就绪的唯一事实源（引擎写、壳与 Updater 读）
PENDING_NAME = "pending.json"
#: 空闲超时（秒）：连续这么久没收到数据才判死——慢下载可以跑很久
IDLE_TIMEOUT = 20
#: 磁盘剩余空间粗检阈值（MB）：低于此值直接拒绝
MIN_FREE_MB = 300
#: 通道顺序：GitHub 优先，开头 5 秒太慢自动退 Gitee（2026-11-19 用户裁决）
DL_CHANNELS = ("github", "gitee")
#: 慢速退避：下载开始 5 秒后，平均速度低于此值（B/s）就换备用通道
SLOW_PROBE_SECONDS = 5
SLOW_PROBE_MIN_BPS = 300 * 1024

_download_lock = threading.Lock()
_download_job: Optional[Dict[str, Any]] = None   # 当前任务（单任务锁）
#: staging 动作串行闸（2026-11-23 逐行审 P3）：reconcile_on_boot 的补解压
#: 与用户新发起的下载解压都会调 _stage_job——两线程写同一 staging 目录会
#: 交错出残缺文件。串行化后后者排队（clear_staging 又清一次，结果一致）。
_STAGE_LOCK = threading.Lock()


def download_dir(version: str) -> Path:
    from core.paths import user_root

    return user_root() / "data" / DOWNLOAD_DIR_NAME / ("v" + str(version).lstrip("vV"))


def update_dir() -> Path:
    """更新工作区（安装根\\.update）。必须走 install_root() 与 Updater 同源。"""
    from core.paths import install_root

    return install_root() / UPDATE_DIR_NAME


def staging_dir() -> Path:
    return update_dir() / "staging"


def pending_path() -> Path:
    return update_dir() / PENDING_NAME


def _resolve_staging_root(staging: Path) -> Path:
    """zip 顶层常带一层 HopOn_v<x.y.z>\\，下钻到真正根（与 Updater 同判据）。"""
    try:
        entries = list(staging.iterdir())
    except OSError:
        return staging
    if len(entries) == 1 and entries[0].is_dir() and (entries[0] / "HopOn.exe").is_file():
        return entries[0]
    return staging


def staging_alive() -> bool:
    """staging 是否就绪：关键文件存在且非空（下钻后判）。"""
    root = _resolve_staging_root(staging_dir())
    for rel in STAGING_REQUIRED:
        p = root / rel
        try:
            if not p.is_file() or p.stat().st_size == 0:
                return False
        except OSError:
            return False
    return True


def clear_staging() -> None:
    """清 staging 与 pending（版本残留错配/显式复位用）。"""
    import shutil

    shutil.rmtree(staging_dir(), ignore_errors=True)
    try:
        pending_path().unlink()
    except OSError:
        pass


def _channel_url(channel: str, tag: str, asset: str) -> str:
    host = "github.com/QianYiXi0723/HopOn" if channel == "github" else "gitee.com/qianyixi/HopOn"
    return f"https://{host}/releases/download/{tag}/{asset}"


def _asset_urls(tag: str, asset: str) -> List[str]:
    """按 DL_CHANNELS 顺序给出资产直连 URL。tag 不带 v 前缀也统一补。"""
    tag = "v" + str(tag).lstrip("vV")
    return [_channel_url(ch, tag, asset) for ch in DL_CHANNELS]


def _sums_urls(tag: str) -> List[str]:
    tag = "v" + str(tag).lstrip("vV")
    return [_channel_url(ch, tag, "SHA256SUMS.txt") for ch in DL_CHANNELS]


def is_too_slow(got_bytes: int, elapsed: float) -> bool:
    """开头探测窗口内平均速度是否过低（触发退避换通道）。

    未到探测时长不判（慢启动保护）；到了以后按 ``累计字节/已用秒`` 算。
    """
    if elapsed < SLOW_PROBE_SECONDS:
        return False
    return (got_bytes / max(elapsed, 0.001)) < SLOW_PROBE_MIN_BPS


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
    # 白名单：只收 zip 更新包（2026-11-22 staged 改造：HopOnUpdater 只解
    # zip；Setup.exe 仅首装/手动装，不走应用内链），文件名取 basename 防路径注入
    if not re.fullmatch(r"[\w.\-]+\.zip", asset, re.IGNORECASE):
        return {"ok": False, "message": "不支持的资产类型（只收 zip 更新包）"}
    version = str(version or "").strip().lstrip("vV")
    if not version:
        return {"ok": False, "message": "缺少版本号"}
    with _download_lock:
        if _download_job and _download_job.get("stage") in ("downloading", "verify", "staging"):
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
                        job["message"] = "下载中（%s）" % (DL_CHANNELS[i])
                        # 慢速退避：首选通道开头 5 秒太慢就换备用（只判一次）
                        if i == 0 and not job.get("_probe_done") and elapsed >= SLOW_PROBE_SECONDS:
                            job["_probe_done"] = True
                            if is_too_slow(got, elapsed):
                                raise IOError("开头 %d 秒平均 %dKB/s，低于 %dKB/s，换备用通道"
                                              % (SLOW_PROBE_SECONDS, got / elapsed / 1024,
                                                 SLOW_PROBE_MIN_BPS // 1024))
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
    """下载完成后：SHA-256 校验（同渠道 SUMS）→ 终态 → 运行期解压 staging。

    2026-11-22 staged 改造（Firefox/VS Code prepare 前移同款）：done 不再
    是流程终点——SHA 通过后立即在本线程（**锁外**，解压十几秒不能阻塞
    download_status 轮询）把新版本解压到 .update\\staging 并写 pending.json，
    「立即安装」时只剩纯覆盖。失败回 error（包仍有效，重试走本地快路径）。
    """
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
    if job["stage"] != "done":
        return
    _stage_job(job, pkg, version, asset)


def _stage_job(job: Dict[str, Any], pkg: Path, version: str, asset: str) -> None:
    """运行期把校验通过的包解压到 .update\\staging（prepare 前移）。

    校验链 = 整包 SHA（下载期）+ zip CRC（extractall 逐 entry 校验，损坏即抛）
    + 关键文件存在非空（staging 期）+ Updater 落盘校验（apply 期兜底）。
    成功写 pending.json（壳与 Updater 共认的「有更新待应用」事实源）并置
    stage=staged 落盘；引擎此后随时可被 /api/update/install 触发 apply。
    """
    global _download_job
    import zipfile

    with _download_lock:
        job["stage"] = "staging"
        job["message"] = "解压准备更新…"
    if not _STAGE_LOCK.acquire(timeout=180):
        # 另一个 staging（reconcile/前一次下载）还在解压——不抢，报错让
        # 用户稍后重试（本地包快路径随时可再触发）
        with _download_lock:
            job["stage"] = "error"
            job["message"] = "另一个更新准备还在进行，请稍候重试"
            _save_download_state(job)
        return
    try:
        staging = staging_dir()
        # 磁盘闸（2026-11-23 自审补）：此前只查下载盘，解压目标（install
        # 盘）可能与下载盘不同——zip+解压双份 ~100MB+，install 盘也要够。
        # 解压总量闸（2026-11-23 逐行审 P4）：按 zip 声明的解压大小合计
        # 与剩余空间比对（Firefox/Chrome 同款防线），防磁盘写满才发现
        if _free_mb(staging) < MIN_FREE_MB:
            raise IOError("磁盘剩余空间不足 %dMB，拒绝解压暂存" % MIN_FREE_MB)
        clear_staging()   # 防跨版本残留
        staging.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(pkg) as zf:
            total_unpacked = sum(i.file_size for i in zf.infolist())
            if total_unpacked > (_free_mb(staging) - MIN_FREE_MB) * 1048576:
                raise IOError("解压总量 %dMB 超过磁盘余量，拒绝解压暂存"
                              % (total_unpacked // 1048576))
            zf.extractall(staging)   # CRC 损坏在此抛 BadZipFile
        sroot = _resolve_staging_root(staging)
        for rel in STAGING_REQUIRED:
            p = sroot / rel
            if not p.is_file() or p.stat().st_size == 0:
                raise IOError("staging 关键文件缺失或为空：%s" % rel)
        n_files = sum(1 for f in sroot.rglob("*") if f.is_file())
        # 单源：写权威状态（phase=staged），不再直接 _atomic_json pending
        write_update_state(phase="staged", version=version, asset=asset,
                           pkg=str(pkg), staged_at=_now_text(),
                           src_pid=os.getpid(), files=n_files)
    except Exception as exc:  # noqa: BLE001 - 解压失败回 error，不比现状差
        clear_staging()
        with _download_lock:
            job["stage"] = "error"
            job["message"] = "解压准备更新失败：%s" % str(exc)[:200]
            _save_download_state(job)
        logger.debug("[update] staging 解压失败：%s", exc)
        return
    finally:
        _STAGE_LOCK.release()
    with _download_lock:
        job["stage"] = "staged"
        job["staged_at"] = _now_text()
        job["staging"] = str(sroot)   # 逐行审 P5：直接用已解析的 sroot
        job["files"] = n_files
        job["message"] = "新版本已就绪，安装将关闭应用并在几秒内自动回来"
        _save_download_state(job)


def _atomic_json(path: Path, payload: Dict[str, Any]) -> None:
    """原子写 JSON（pending.json 等事实源文件用）。"""
    # D16（逐个比对）：父目录必须自建——done 老链第一次 install 时 .update
    # 可能从未建立过（staging 失效/清场后），open(tmp) 会 FileNotFoundError，
    # install 端点会把老链安装拒成 500
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# 更新链唯一权威状态（2026-11-23 重做：状态单源在后端安装根）
#
# 对照 AutoUpdater.NET / electron-updater / Squirrel.Windows 三个开源更新器
# 的真实源码：状态都在后端/安装目录，UI 侧零跨进程标记。此前 HopOn 有三处
# 状态源（前端 localStorage + download_state.json + pending.json），互相
# 同步导致 A/D/E/F/G 五个「清标记」bug。现在 pending.json 升级为唯一权威：
#   phase: staged(待应用) / applying(接管中) / done(完成)
#   result: {ok, version, ts}  一次性更新结果，UI 首读即消费
# download_state.json 降级为纯下载进度缓存（可随时重建，非判据）。
# --------------------------------------------------------------------------- #

def read_update_state() -> Dict[str, Any]:
    """读权威状态；缺失/损坏返回空 dict（不抛异常）。"""
    try:
        raw = json.loads(pending_path().read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def write_update_state(**fields: Any) -> None:
    """合并写权威状态（保留未指定的旧字段，如 install_started_ts）。

    跨进程锁保证与 take_update_result 的「读即消费」互斥（复用 ipc_lock）。
    """
    from core import ipc_lock

    with ipc_lock.blocking(str(pending_path()) + ".lock"):
        cur = read_update_state()
        cur.update(fields)
        cur["saved_at"] = _now_text()
        _atomic_json(pending_path(), cur)


def take_update_result() -> Optional[Dict[str, Any]]:
    """取走一次性更新结果（读后删除 result 字段），供 UI 首读消费。

    复用 core/toasts.py:take_unread 的「读即消费+新鲜度+跨进程锁」模式。
    拿不到结果返回 None（前端幂等：不报错，下次加载再试）。
    """
    from core import ipc_lock

    with ipc_lock.blocking(str(pending_path()) + ".lock"):
        cur = read_update_state()
        result = cur.get("result")
        if not isinstance(result, dict):
            return None
        cur.pop("result", None)
        try:
            _atomic_json(pending_path(), cur)
        except OSError:
            return None
        return result


#: 下载终态持久化（2026-11-15 用户实测「下载后检测不到本地资源而无法更新」
#  的根因：done 态只在引擎进程内存里——引擎/壳任何一次重启都清零，本地
#  文件明明还在却「检测不到」）。done 落盘，重启后 download_status 从盘恢复。
STATE_NAME = "download_state.json"


def _download_state_path() -> Path:
    from core.paths import user_root

    return user_root() / "data" / STATE_NAME


def _save_download_state(job: Dict[str, Any]) -> None:
    """终态（done/staged/error）落盘；只存可恢复的最小字段。"""
    payload = {
        "stage": job.get("stage"),
        "version": job.get("version"),
        "asset": job.get("asset"),
        "file": job.get("file"),
        "sha_ok": job.get("sha_ok"),
        "total": job.get("total"),
        "message": job.get("message"),
        "staged_at": job.get("staged_at"),
        "staging": job.get("staging"),
        "files": job.get("files"),
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
    """读盘上终态；对应产物不存在则降级/清除。

    恢复规则（2026-11-22 staged 改造）：
    * done：zip 在场 → 恢复 done；zip 没了 → 清（旧 Updater 自己解压路径）。
    * staged：staging 就绪 → 恢复 staged；staging 没了但 zip 在 → 降级 done
      （Updater 从 zip 重新解压，永不比现状差）；都没了 → 清。
    """
    try:
        p = _download_state_path()
        if not p.exists():
            return None
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not raw.get("sha_ok"):
            return None
        stage = raw.get("stage")
        if stage == "staged":
            stg = str(raw.get("staging") or "")
            alive = bool(stg) and staging_alive()
            if not alive:
                raw["stage"] = "done"   # staging 失效，降级走 zip 解压
                stage = "done"
        if stage == "done" and not (raw.get("file") and os.path.isfile(raw["file"])):
            try:
                p.unlink()
            except OSError:
                pass
            return None
        if stage not in ("done", "staged"):
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


def reconcile_on_boot() -> None:
    """启动对账（2026-11-23 自审）：盘上 done 态（zip 在）但 staging 缺失
    或损坏 → 后台补解压，让 UI 快链自动恢复。

    为什么必须有：staged 改造后 UI 在 staged 才发安装；引擎重启后 staging
    失效会被降级成 done——不补解压的话 done 永远不会自己变 staged，UI 的
    done 分支只能靠超时兜底走旧链。补齐后用户无感回到秒级快链；补失败也
    不影响——install 端点 done 路径走 Updater 自己解压的旧链（永不比现状差）。
    """
    try:
        st = download_status()
    except Exception:  # noqa: BLE001
        return
    if st.get("stage") != "done" or not st.get("sha_ok"):
        return
    version, file_path = str(st.get("version") or ""), str(st.get("file") or "")
    if not version or not file_path:
        return
    pkg = Path(file_path)
    if not pkg.is_file() or staging_alive():
        return
    if _parse(version) <= _parse(current_version()):
        return   # 防降级闸一致：旧包不值得补
    job = dict(st)
    job["_cancel"] = False
    threading.Thread(target=_stage_job, args=(job, pkg, version, str(st.get("asset") or "")),
                     daemon=True, name="update-reconcile").start()
    logger.debug("[update] 启动对账：done 态 staging 失效，后台补解压 %s", version)


def cancel_download() -> Dict[str, Any]:
    global _download_job
    with _download_lock:
        if not _download_job or _download_job.get("stage") not in ("downloading", "verify"):
            return {"ok": False, "message": "没有进行中的下载任务"}
        _download_job["_cancel"] = True
        return {"ok": True, "message": "正在取消…"}

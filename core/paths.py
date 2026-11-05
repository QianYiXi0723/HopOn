# -*- coding: utf-8 -*-
"""应用锚点：统一解析「安装根」与「用户数据根」。

**两个根必须分开**（2026-11-05 数据外置）：

* ``app_root()``  = 程序安装位置（只读，卸载时整个目录会被删）
  - 脚本模式：仓库根
  - engine 发行版：engine\\ 的上级（与 HopOn.exe 同级）
* ``user_root()`` = 用户数据位置（可写，卸载时**必须保留**）
  - ``%LOCALAPPDATA%\\HopOn``

为什么必须分开：安装器卸载时按目录删除，若 config.json（含全部登录
凭证）/ data\\ / logs\\ 还躺在程序目录里，用户重装一次就会连 cookie、
token、设备身份、半年历史一起丢掉。这是安装器能做的前提条件。

**环境变量覆盖**（测试与调试用，优先级最高）：
``HOPON_USER_DIR``——指向任意目录充当用户数据根。

**迁移**：旧版数据在程序目录里。首次运行若发现 ``user_root()`` 尚无数据、
而 ``app_root()`` 有旧数据，则由 :func:`ensure_layout` 自动搬过去，
并留 ``.migrated.json`` 标记；搬迁失败不破坏原文件。

入口脚本（daemon/app/main/login_ui）在 sys.path 就绪前就要用到根目录，
故它们各自内联 ``_BASE`` 判据；core/* 一律 import 本模块，不许再散落
``Path(__file__)`` 推导。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

#: 应用名：决定 %LOCALAPPDATA% 下的子目录名
APP_NAME = "HopOn"

#: 覆盖用户数据根的环境变量（测试/调试用）
USER_DIR_ENV = "HOPON_USER_DIR"

#: 需要外置的数据（相对路径）
DATA_ITEMS = ("config.json", "data", "logs")

#: 迁移完成标记文件名（放在 user_root 下）
MIGRATION_MARKER = ".migrated.json"

#: 覆盖程序根的准环境变量（测试沙箱用；生产不该设置）
APP_DIR_ENV = "HOPON_APP_DIR"

#: 迁移迁移开关：设为 "0" 可完全禁用迁移（生产排障的紧急闸）
MIGRATION_ENV = "HOPON_NO_MIGRATE"


def app_root() -> Path:
    """程序安装位置：脚本模式=仓库根；engine 发行版=engine 的上级。

    **测试可用 ``HOPON_APP_DIR`` 覆盖**——2026-11-05 惨痛教训：迁移测试
    因无法覆盖本函数，真的把开发机的 config.json / data\\ 搬走并被临时目录
    清理掉了。任何会写数据的路径都必须能被指向沙箱。
    """
    override = os.environ.get(APP_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    here = Path(__file__).resolve().parent.parent  # core/ 的上级
    return here.parent if here.name == "engine" else here


def user_root() -> Path:
    """用户数据根：环境变量 > %LOCALAPPDATA%\\HopOn > 程序目录（兜底）。

    兜底到程序目录是为了让「读不到 LOCALAPPDATA」的极端环境仍能跑，
    而不是直接崩——宁可数据放得不对，也不能让程序起不来。
    """
    override = os.environ.get(USER_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        return Path(local) / APP_NAME
    logger_free_warn = (
        "[paths] 读不到 %LOCALAPPDATA%，用户数据将留在程序目录"
        "（卸载会丢失，请手动迁移）"
    )
    print(logger_free_warn)
    return app_root()


def data_path(*parts: str) -> Path:
    """拼一个用户数据路径，例如 ``data_path("logs", "daemon.pid")``。"""
    return user_root().joinpath(*parts)


def legacy_path(*parts: str) -> Path:
    """拼一个旧布局（程序目录）路径，迁移与兼容读用。"""
    return app_root().joinpath(*parts)


def _has_any(root: Path, items=DATA_ITEMS) -> bool:
    """root 下是否已存在任一项数据。"""
    return any((root / name).exists() for name in items)


def _atomic_marker(target: Path, payload: dict) -> None:
    """原子写入迁移标记。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _move_item(src: Path, dst: Path) -> str:
    """把一项数据从 src 搬到 dst。

    返回 ``"moved"`` / ``"copied"`` / ``"exists"``。同盘用 rename（瞬时），
    跨盘退化为 copytree + 校验。目标已存在则**不覆盖**——宁可不迁，也不
    用旧数据盖掉新数据。
    """
    if not src.exists():
        return "missing"
    if dst.exists():
        return "exists"
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(src, dst)          # 同盘：原子 rename
        return "moved"
    except OSError:
        pass
    # 跨盘：复制后再删源
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)
    if not dst.exists():
        raise OSError("复制后目标仍不存在：%s" % dst)
    if dst.is_dir():
        shutil.rmtree(src)
    else:
        src.unlink()
    return "copied"


def ensure_layout(*, dry_run: bool = False, allow_migrate: bool = False) -> dict:
    """确保用户数据根就绪；旧布局数据**在明确许可下**迁移。

    调用时机：各入口脚本拿到 sys.path 后、读 config 之前。幂等——已就绪
    时只做一次 ``exists()`` 检查就返回。

    **迁移默认不执行**（``allow_migrate=False``）。2026-11-05 的教训：迁移
    最初写成"只要发现旧数据就自动搬"，结果单元测试一跑就把开发机的
    config.json 和 data\\ 搬进了临时目录、随 TemporaryDirectory 一起消失。
    搬数据的代码必须有明确的调用方许可 + 可指向沙箱的根目录，两者缺一不可。

    真正需要迁移的时机只有一个：**安装器/用户显式升级**。故各入口脚本
    传 ``allow_migrate=True``（它本来就该在启动时迁移），测试则永远不传。

    **失败不破坏原文件**：任一项搬迁抛错即记入 ``errors`` 并继续下一项；
    已搬的记在 ``moved`` 里，未搬的仍在原位。
    """
    src_root = app_root()
    dst_root = user_root()
    report = {
        "app_root": str(src_root),
        "user_root": str(dst_root),
        "externalized": dst_root != src_root,
        "action": "none",
        "moved": {},
        "errors": [],
    }

    if dst_root == src_root:
        # 兜底模式（无 LOCALAPPDATA 或被覆盖成同一处）：不迁移
        report["action"] = "in_place"
        return report

    marker = dst_root / MIGRATION_MARKER
    if marker.exists():
        report["action"] = "already"
        return report

    if not _has_any(src_root):
        # 全新安装：建好目录，不需要搬东西
        if not dry_run:
            dst_root.mkdir(parents=True, exist_ok=True)
            _atomic_marker(marker, {"migrated_at": _now_text(), "from": str(src_root),
                                    "items": {}, "note": "全新安装，无旧数据"})
        report["action"] = "fresh"
        return report

    # 旧布局有数据：只有在被明确许可、且没被紧急闸关掉时才搬
    disabled = os.environ.get(MIGRATION_ENV, "").strip() in ("1", "true", "yes")
    if not allow_migrate or disabled:
        report["action"] = "pending"      # 有旧数据待迁，但本轮不动它
        report["note"] = ("迁移被禁用（%s）" % MIGRATION_ENV) if disabled else "未授予迁移许可"
        return report

    report["action"] = "migrate"
    for name in DATA_ITEMS:
        src, dst = src_root / name, dst_root / name
        try:
            if dry_run:
                report["moved"][name] = "would-move" if (src.exists() and not dst.exists()) else "skip"
                continue
            report["moved"][name] = _move_item(src, dst)
        except Exception as exc:  # noqa: BLE001 - 单项失败不拖垮整体
            report["errors"].append("%s: %s" % (name, exc))

    if not dry_run and not report["errors"]:
        _atomic_marker(marker, {"migrated_at": _now_text(), "from": str(src_root),
                                "items": report["moved"]})
    return report


def _now_text() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def describe() -> str:
    """一行描述当前布局，供日志与诊断接口用。"""
    a, u = app_root(), user_root()
    tag = "外置" if a != u else "同目录（未外置）"
    return "程序=%s | 数据=%s | %s" % (a, u, tag)

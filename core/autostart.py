# -*- coding: utf-8 -*-
"""开机自启（Windows 注册表 Run 键）。

为什么用 ``HKCU\\...\\CurrentVersion\\Run`` 而不是任务计划 / 启动文件夹：
    任务计划要管理员权限、还得输密码，改一次要重装；启动文件夹会闪黑框。
    HKCU 的 Run 键**不需要管理员权限**，登录时由 explorer.exe 静默执行，
    值就是一个命令行字符串，增删都只影响当前用户。

启动什么：
    ``pythonw.exe "<项目绝对路径>\\daemon.py"`` —— 守护进程自带右下角托盘、
    没有控制台窗口，开机后静默待在托盘里。用户要开界面时点托盘右键
    「打开控制台」，此时才会拉起 app.py 的窗口（app.py 会弹窗，不能拿来自启）。

关键细节：
    1. 必须用 ``pythonw.exe`` 的**绝对路径**：用 ``python.exe`` 会闪一个黑框；
       只写 ``pythonw.exe`` 依赖 PATH，开机时环境变量可能还没就绪。
    2. 脚本路径也必须绝对：注册表启动项的当前目录是 ``C:\\Windows\\System32``，
       相对路径必然找不到文件。
    3. 两个路径都用双引号包住，防止「Program Files」这类带空格的目录被截断。

路径迁移的处理：
    项目目录被移动 / 换了 Python 之后，注册表里的旧值会指向不存在的文件 ——
    开机时会静默失败，且用户完全看不出来。所以 :func:`status` 会比对
    "注册表里的命令" 与 "当前应该写的命令"，不一致时返回 ``stale=True``，
    界面上提示重新保存一次。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("dailycheck.autostart")

from core.paths import app_root

BASE_DIR = app_root()

#: 注册表路径（当前用户，无需管理员权限）
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
#: 值名。同名值只会有一条，重复写入等价于覆盖。
VALUE_NAME = "HopOn"
#: 改名前的旧值名（2026-11 起废弃）。enable() 写新键时顺手清理，防止朋友机器上
#: 旧键残留造成双自启。
LEGACY_VALUE_NAME = "ResinLoop"
#: 自启的入口脚本（守护进程 + 托盘，无窗口）
TARGET_SCRIPT = "daemon.py"

#: 平台是否支持（非 Windows 只是不显示入口，不影响其它功能）
SUPPORTED = os.name == "nt"

_NO_WINDOW_HINT = (
    "仅支持 Windows（当前系统没有 HKCU Run 键）。macOS / Linux 请用系统的"
    "「登录项 / systemd --user」自行配置。"
)


# --------------------------------------------------------------------------- #
# 命令拼接
# --------------------------------------------------------------------------- #


def pythonw_path() -> Path:
    """pythonw.exe 的绝对路径；找不到时退回当前解释器。"""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    if candidate.exists():
        return candidate
    return exe


def target_path() -> Path:
    """要自启的脚本绝对路径。"""
    return BASE_DIR / TARGET_SCRIPT


def shell_exe_path() -> Optional[Path]:
    """exe 发行版壳的路径：engine 里在上级目录，源码仓里就在项目根。"""
    # HopOn 优先；改名过渡期的老壳 ResinLoop.exe 作为回退（一个版本后移除）
    for cand in (
        BASE_DIR.parent / "HopOn.exe",
        BASE_DIR / "HopOn.exe",
        BASE_DIR.parent / "ResinLoop.exe",
        BASE_DIR / "ResinLoop.exe",
    ):
        if cand.exists():
            return cand
    return None


def build_command() -> str:
    """拼出写进注册表的完整命令行（路径都带引号）。

    exe 发行版优先：``HopOn.exe --tray`` —— 壳负责静默启动、托盘常驻、
    引擎托管与内核级收尸；脚本模式回落 pythonw daemon.py。
    stale 检测按字符串比对，两种形态互切后界面会提示重开一次开关。
    """
    exe = shell_exe_path()
    if exe is not None:
        return f'"{exe}" --tray'
    return f'"{pythonw_path()}" "{target_path()}"'


# --------------------------------------------------------------------------- #
# 注册表读写
# --------------------------------------------------------------------------- #


def read_value() -> Optional[str]:
    """读当前注册表值；不存在 / 平台不支持返回 None。"""
    if not SUPPORTED:
        return None
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_QUERY_VALUE
        ) as key:
            value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
    except FileNotFoundError:
        return None
    except OSError as exc:  # 权限 / 注册表被策略锁住
        logger.warning("读取开机自启注册表失败：%s", exc)
        return None
    return str(value) if value is not None else None


def is_enabled() -> bool:
    """当前用户是否已经设置了本程序的开机自启。"""
    return read_value() is not None


def enable() -> Dict[str, Any]:
    """写入自启项（幂等：已存在就覆盖成当前命令）。"""
    if not SUPPORTED:
        return {"ok": False, "message": _NO_WINDOW_HINT}

    import winreg

    command = build_command()
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
    except OSError as exc:
        logger.exception("写入开机自启失败")
        return {"ok": False, "message": f"写入注册表失败：{exc}"}

    _remove_legacy_value()   # 改名迁移：清掉旧 ResinLoop 自启键，防双自启
    logger.info("已开启开机自启：%s", command)
    return {
        "ok": True,
        "message": "已开启：下次登录 Windows 会自动在托盘启动",
        "value": command,
    }


def disable() -> Dict[str, Any]:
    """删除自启项（幂等：本来就没有也算成功）。"""
    if not SUPPORTED:
        return {"ok": False, "message": _NO_WINDOW_HINT}

    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        logger.info("开机自启本来就是关闭状态")
        return {"ok": True, "message": "已关闭（本来就是关的）", "value": None}
    except OSError as exc:
        logger.exception("删除开机自启失败")
        return {"ok": False, "message": f"删除注册表项失败：{exc}"}

    logger.info("已关闭开机自启")
    return {"ok": True, "message": "已关闭：下次登录不会自动启动", "value": None}


def _remove_legacy_value() -> None:
    """删除改名前的旧 ResinLoop 自启键（幂等：不存在 / 失败都不影响主流程）。"""
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, LEGACY_VALUE_NAME)
        logger.info("已清理旧版自启键 %s（改名迁移）", LEGACY_VALUE_NAME)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("旧版自启键清理失败（不影响本次写入）：%s", exc)


def cleanup_legacy_autostart() -> bool:
    """启动时清理旧版自启键（公开入口，供各入口脚本首启调用）。

    老版本（改名前）注册的自启键值名是 ResinLoop、指向旧目录——新版本
    装好后它若残留，开机会出现新旧两套同时拉起抢 8765。本函数无条件
    删除该键（改名后任何 ResinLoop 自启项都是失效遗物），返回是否删了。
    """
    if not SUPPORTED:
        return False
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, LEGACY_VALUE_NAME)
        logger.info("首启清理：已移除旧版自启键 %s", LEGACY_VALUE_NAME)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("旧版自启键清理失败（忽略）：%s", exc)
        return False


# --------------------------------------------------------------------------- #
# 汇总状态（给 /api/autostart 用）
# --------------------------------------------------------------------------- #


def self_heal(desired: bool) -> str:
    """开机自启自愈：用户意图为开、但注册表键丢失/过期时重写。

    背景（2026-11-09 实证）：本机 Run 键被外部工具整条删除（日志只有
    enable 无 disable，StartupApproved 也无禁用标记——典型安全软件
    「开机加速」行为）。光靠用户重新拨开关还会被再删，所以启动链
    （daemon / login_ui 首启）按配置里的 autostart_desired 意图自愈。

    Returns:
        动作说明；无需动作时返回空串（调用方不用记日志）。
    """
    if not SUPPORTED or not desired:
        return ""
    value = read_value()
    expected = build_command()
    if value is not None and value.strip().lower() == expected.strip().lower():
        return ""
    result = enable()
    if result.get("ok"):
        logger.info(
            "自启自愈触发：注册表键%s，已按用户意图重写（%s）",
            "路径过期" if value else "丢失（疑似被外部工具清理）",
            result.get("message", ""),
        )
        return ("注册表键路径过期，已自愈重写" if value
                else "注册表键丢失（疑似被外部工具清理），已自愈重写")
    logger.warning("自启自愈失败：%s", result.get("message", ""))
    return "自愈失败：" + str(result.get("message") or "")


def status() -> Dict[str, Any]:
    """当前状态 + 是否与当前路径一致（stale 表示注册表里是旧路径）。"""
    if not SUPPORTED:
        return {
            "ok": True,
            "supported": False,
            "enabled": False,
            "stale": False,
            "value": None,
            "command": "",
            "target": str(target_path()),
            "message": _NO_WINDOW_HINT,
        }

    current_value = read_value()
    expected = build_command()
    stale = bool(current_value) and current_value.strip().lower() != expected.strip().lower()

    return {
        "ok": True,
        "supported": True,
        "enabled": current_value is not None,
        # 注册表里是旧路径（搬过目录 / 换过 Python）：开机时会静默失败
        "stale": stale,
        "value": current_value,
        "command": expected,
        "target": str(target_path()),
        "message": (
            "注册表里是旧路径，开机可能起不来，请重新打开一次开关"
            if stale
            else ("已开启" if current_value else "未开启")
        ),
    }


def apply(enable_flag: Any) -> Dict[str, Any]:
    """按请求开关自启，返回最新状态。"""
    # 只有明确的真值才当"开"，避免字符串 "false" 被当成 True
    want = enable_flag is True or str(enable_flag).strip().lower() in ("1", "true", "on", "yes")

    result = enable() if want else disable()
    payload = status()
    payload.update({k: v for k, v in result.items() if k in ("ok", "message")})
    # ok 以注册表操作结果为准；状态里的 ok 只代表"读取成功"
    payload["ok"] = bool(result.get("ok"))
    return payload

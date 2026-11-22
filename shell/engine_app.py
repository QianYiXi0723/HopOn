#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HopOn 引擎入口（exe 发行版专用）——纯本地 HTTP 服务，无任何窗口。

由 C# 壳（shell/HopOn.exe）以 ``pythonw.exe engine/app.py`` 静默拉起：
    1. 若目标端口已有控制台在跑，直接待命退出（壳会复用现有实例）；
    2. 启动 login_ui.serve（控制台页面 + 全部 /api + 可选的 APK 局域网下载服务）；
    3. 原地阻塞，直到被壳结束进程树（或 Ctrl+C）。

窗口、托盘、开机自启全部由 C# 壳负责；脚本模式的旧 app.py（pywebview 窗口）
保留在源码仓库里，与本文件并存互不影响。
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
import threading
from pathlib import Path

# engine 发行版布局锚点修复，判据与 core.paths.app_root 一致（见彼处注释）
_BASE = Path(__file__).resolve().parent
BASE_DIR = _BASE.parent if _BASE.name == "engine" else _BASE
# sys.path 保持「代码=本脚本所在树」：engine 发行版导入 engine\ 下的模块；
# 数据根（BASE_DIR）只决定 config/data/logs 落点，不参与 import 解析。
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

# 数据外置（2026-11-06 修漏）：控制台是壳拉起的第一个进程，layout 就绪
# （含旧布局迁移）必须在 import login_ui 之前完成——login_ui 内部也会调
# ensure_layout（幂等），这里先调是为了 engine.log 的落点别抢先建出
# 程序目录的 logs\，把迁移判成「目标已存在」而漏搬。
from core.paths import ensure_layout as _ensure_layout, user_root as _user_root

_LAYOUT = _ensure_layout(allow_migrate=True)

import login_ui

logger = logging.getLogger("dailycheck.engine")

# 引擎日志跟数据根走（卸载不丢排障记录）；与 desktop.log 同目录
LOGS_DIR = _user_root() / "logs"
LOG_FILE = LOGS_DIR / "engine.log"


def setup_logging() -> None:
    """引擎日志独立落盘（<数据根>/logs/engine.log），壳崩溃时也有据可查。"""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=500_000, backupCount=1, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # faulthandler（2026-11-22 朋友机引擎静默死亡实证）：pythonw 无 stderr，
    # 硬崩溃（段错误/C 扩展崩溃）时 Python 级 traceback 无处可去——faulthandler
    # 把它落盘到 crash.log（stderr 重定向到文件是 pythonw 的标准解）。
    # 正常运行零写入，只在崩溃瞬间 dump，无增长风险。
    import faulthandler

    try:
        faulthandler.enable(open(LOGS_DIR / "crash.log", "a", encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 取证兜底失败绝不影响引擎启动
        pass
    _ensure_wer_localdumps()


def _ensure_wer_localdumps() -> None:
    """WER LocalDumps per-app 键（2026-11-22 死因取证第三层）：pythonw 真崩溃
    （C 扩展段错误等 native 级）时 Windows 自动生成 minidump 到数据根
    logs\\dumps——faulthandler 只覆盖 Python 级，native 级靠这个。

    写 HKCU（无需管理员，红线不破）；DumpType=1 minidump、DumpCount=5 自动
    轮换。幂等：键已配置则跳过。失败静默（取证增强不影响主功能）。
    注意覆盖边界：只有真崩溃（unhandled exception）才生成 dump——进程被
    TerminateProcess 杀掉不留 dump，那类死因靠 Event Viewer System 日志。
    """
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\Windows Error Reporting\LocalDumps\pythonw.exe"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as k:
            try:
                winreg.QueryValueEx(k, "DumpFolder")
                return   # 已配置过：幂等跳过
            except FileNotFoundError:
                pass
        dump_dir = LOGS_DIR / "dumps"
        dump_dir.mkdir(parents=True, exist_ok=True)
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "DumpFolder", 0, winreg.REG_EXPAND_SZ, str(dump_dir))
            winreg.SetValueEx(k, "DumpType", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(k, "DumpCount", 0, winreg.REG_DWORD, 5)
    except Exception:  # noqa: BLE001 - 取证增强失败绝不影响引擎启动
        pass
    # faulthandler（2026-11-22 朋友机引擎静默死亡实证）：pythonw 无 stderr，
    # 硬崩溃（段错误/C 扩展崩溃）时 Python 级 traceback 无处可去——faulthandler
    # 把它落盘到 crash.log（stderr 重定向到文件是 pythonw 的标准解）。
    # 正常运行零写入，只在崩溃瞬间 dump，无增长风险。
    import faulthandler

    try:
        faulthandler.enable(open(LOGS_DIR / "crash.log", "a", encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 取证兜底失败绝不影响引擎启动
        pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="HopOn · 本地引擎（无界面）")
    parser.add_argument("--port", type=int, default=8765, help="控制台端口，默认 8765")
    parser.add_argument(
        "--no-lan-tools", action="store_true",
        help="不启动 APK 局域网下载服务",
    )
    args = parser.parse_args(argv)

    setup_logging()
    logger.info("=" * 56)
    logger.info("引擎启动（pid=%s，python=%s）", os.getpid(), sys.executable)

    # 端口已有实例在跑：壳会复用它，引擎不再重复起服务（避免 pick_port 漂到别的端口）
    if login_ui.console_alive(args.port):
        logger.info("端口 %d 已有控制台实例，引擎待命退出（复用现有实例）", args.port)
        return 0

    try:
        info = login_ui.serve(args.port, lan_tools=not args.no_lan_tools)
    except Exception as exc:  # noqa: BLE001
        logger.exception("引擎服务启动失败：%s", exc)
        return 1

    logger.info("引擎已就绪：%s（等待壳加载页面）", info["url"])
    waiter = threading.Event()
    try:
        while True:
            # 原地常驻；停机由 C# 壳结束进程树（shutdown 挂在 finally 兜底）
            waiter.wait(3600)
    except KeyboardInterrupt:
        logger.info("收到中断，引擎退出")
    finally:
        login_ui.stop_server(info)
    return 0


if __name__ == "__main__":
    sys.exit(main())

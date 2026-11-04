# -*- coding: utf-8 -*-
"""应用锚点：统一解析「安装根目录」。

exe 发行版把 Python 代码整体复制进 engine\\ 子目录、由 C# 壳以
``pythonw engine\\app.py`` / ``pythonw engine\\daemon.py`` 拉起；脚本模式
则直接从仓库根运行。两种布局下 config.json / data\\ / logs\\ 都必须落在
**安装根**（与 HopOn.exe 同级）：否则发行版会在 engine\\ 里开出一个
空数据岛——2026-11-05 实证回归：daemon 读到 engine\\config.json（mock
模板）、历史/推送/心跳全部断链，而用户真实数据完好地留在安装根。

判据：core\\ 的上一级若名为 engine，即发行版布局，锚点再上一级。
入口脚本（daemon/app/main/login_ui）在 sys.path 就绪前就要用到根目录，
故它们各自内联同一判据；core/* 一律 import 本模块，不许再散落 __file__ 推导。
"""
from __future__ import annotations

from pathlib import Path


def app_root() -> Path:
    """安装根目录：脚本模式=仓库根；engine 发行版布局=engine 的上级。"""
    here = Path(__file__).resolve().parent.parent  # core/ 的上级
    return here.parent if here.name == "engine" else here

# -*- coding: utf-8 -*-
"""重建 engine/python：解压 Python 3.11 Embeddable 并启用本地 site-packages。"""
import shutil
import zipfile
from pathlib import Path

ROOT = Path(r"E:\Apps\HopOn")
ENGINE_PY = ROOT / "engine" / "python"
ZIP = ROOT / "build_pyembed.zip"

if ENGINE_PY.exists():
    print("[skip] engine/python 已存在，先手动确认是否要重建")
    raise SystemExit(1)

ENGINE_PY.mkdir(parents=True)

with zipfile.ZipFile(ZIP) as z:
    z.extractall(ENGINE_PY)
print("解压完成：%d 个条目" % len(list(ENGINE_PY.iterdir())))

# _pth：内置 zip + 当前目录 + 本地 site-packages
# 刻意不启用 import site：避免把系统的用户级 site-packages 牵进来（隔离性验收要求
# pystray/webview 在 engine 里必须 ImportError）
pth = ENGINE_PY / "python311._pth"
pth.write_text(
    "python311.zip\n"
    ".\n"
    "Lib\\site-packages\n"
    "\n"
    "# 保持 isolated：不 import site，避免牵入系统 user site-packages\n",
    encoding="utf-8",
)
print("_pth 写入：")
print(pth.read_text(encoding="utf-8"))

(ENGINE_PY / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)
print("site-packages 目录就绪")

# 冒烟：解释器能起来
import subprocess

r = subprocess.run(
    [str(ENGINE_PY / "python.exe"), "-c",
     "import sys; print('ver', sys.version.split()[0]); print('isolated', sys.flags.isolated)"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
)
print("--- 冒烟 ---")
print(r.stdout.strip())
print(r.stderr.strip()[:400])

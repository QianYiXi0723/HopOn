# -*- coding: utf-8 -*-
"""ILRepack 把 WebView2 托管 DLL 合进壳，产出仓库根的 HopOn.exe。

坑：/out: 带盘符路径在 Git Bash 会被 MSYS 转换吃掉（报 No output file given），
所以必须用 subprocess 列表传参，不经过 shell。
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"E:\Apps\HopOn")
PUB = ROOT / "shell" / "publish"
ILREPACK = ROOT / "build_ilrepack" / "tools" / "ILRepack.exe"
TARGET = ROOT / "HopOn.exe"

main = PUB / "HopOn.exe"
core = PUB / "Microsoft.Web.WebView2.Core.dll"
winforms = PUB / "Microsoft.Web.WebView2.WinForms.dll"

for f in (ILREPACK, main, core, winforms):
    if not f.exists():
        raise SystemExit("[FATAL] 缺少 %s" % f)

# 备份旧 exe（覆盖前留一手）
if TARGET.exists():
    bak = ROOT / "_shell_backup_HopOn.exe"
    shutil.copy2(TARGET, bak)
    print("旧 exe 备份 ->", bak.name, TARGET.stat().st_size, "bytes")

cmd = [
    str(ILREPACK),
    "/out:" + str(TARGET),
    "/internalize",
    "/ndebug",
    str(main),
    str(core),
    str(winforms),
]
print("=== ILRepack ===")
print(" ".join('"%s"' % c if " " in c else c for c in cmd))
proc = subprocess.run(cmd, cwd=str(PUB), capture_output=True, text=True,
                      encoding="utf-8", errors="replace")
print(proc.stdout[-2500:])
if proc.returncode != 0:
    print("--- STDERR ---")
    print(proc.stderr[-2000:])
    sys.exit(proc.returncode)

# 原生 loader 必须与 exe 同目录
loader = PUB / "WebView2Loader.dll"
if loader.exists():
    shutil.copy2(loader, ROOT / "WebView2Loader.dll")
    print("WebView2Loader.dll -> 根目录", loader.stat().st_size)

print("=== 结果 ===")
print("HopOn.exe      %d bytes" % TARGET.stat().st_size)
print("WebView2Loader.dll %d bytes" % (ROOT / "WebView2Loader.dll").stat().st_size)

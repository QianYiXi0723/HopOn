# -*- coding: utf-8 -*-
"""重编译 C# 壳：dotnet publish shell -> shell/publish。

先统一 csproj 的 <Version> 到 2.1.0（原来停在 1.2.0）。
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"E:\Apps\HopOn")
CSPROJ = ROOT / "shell" / "HopOn.csproj"
DOTNET = Path(r"C:\Program Files\dotnet\dotnet.exe")

text = CSPROJ.read_text(encoding="utf-8")
new, n = re.subn(r"<Version>[^<]*</Version>", "<Version>2.1.0</Version>", text)
if n != 1:
    print("[WARN] <Version> 替换命中 %d 次" % n)
else:
    CSPROJ.write_text(new, encoding="utf-8", newline="")
    print("csproj Version -> 2.1.0")

print("=== dotnet publish ===")
proc = subprocess.run(
    [str(DOTNET), "publish", str(ROOT / "shell" / "HopOn.csproj"),
     "-c", "Release", "-o", str(ROOT / "shell" / "publish")],
    cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
)
print(proc.stdout[-3000:])
if proc.returncode != 0:
    print("--- STDERR ---")
    print(proc.stderr[-3000:])
    sys.exit(proc.returncode)

out = ROOT / "shell" / "publish"
print("=== publish 产物 ===")
for f in sorted(out.rglob("*")):
    if f.is_file():
        print("  %-45s %8d" % (f.relative_to(out), f.stat().st_size))

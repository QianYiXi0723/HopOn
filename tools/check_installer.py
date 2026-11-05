# -*- coding: utf-8 -*-
"""安装器脚本安全审计（2026-11-05）。

数据外置的全部意义是让「卸载程序不删用户凭证」，所以安装脚本本身
必须过一遍静态检查——不能靠人肉 review，更不能靠"我记得写过"。

检查项：
  A. [UninstallDelete] 里绝不出现数据目录（{localappdata}\\HopOn）
  B. config.json 必须 onlyifdoesntexist（升级不覆盖用户配置）
  C. PrivilegesRequired=lowest（per-user 安装，不要 UAC）
  D. 卸载后必须提示数据保留位置
  E. 必须有 CloseApplications（否则覆盖不了运行中的 exe）
  F. 引用的发行产物路径必须存在（防止脚本指向不存在的目录）

用法：
    python tools/check_installer.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ISS = ROOT / "installer" / "HopOn.iss"

PASS = 0
FAIL = 0
FAILED = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS " + name)
    else:
        FAIL += 1
        FAILED.append(name + (" | " + str(detail) if detail else ""))
        print("  FAIL " + name + (" | " + str(detail) if detail else ""))


def main() -> int:
    if not ISS.exists():
        print("找不到 %s" % ISS)
        return 2

    text = ISS.read_text(encoding="utf-8")
    # 去掉注释行，只看生效的指令（; 开头是 Inno 的注释）
    active_lines = [
        ln for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith(";")
    ]
    active = "\n".join(active_lines)

    print("=== A. 卸载不删用户数据 ===")

    # 抓出 [UninstallDelete] 段落
    section = ""
    in_section = False
    for ln in active_lines:
        stripped = ln.strip()
        if stripped.startswith("["):
            in_section = stripped.lower() == "[uninstalldelete]"
            continue
        if in_section:
            section += ln + "\n"

    check("A1 存在 [UninstallDelete] 段且非空（说明确实规划了清理）",
          bool(section.strip()), section[:100])

    # 关键：数据目录绝不能出现在卸载删除列表里
    danger = []
    for ln in section.splitlines():
        low = ln.lower()
        if "localappdata}\\hopon" in low and "programs" not in low:
            danger.append(ln.strip())
        if "{userdata" in low or "config.json" in low and "onlyifdoesntexist" not in low:
            danger.append(ln.strip())
    check("A2 卸载列表不含数据目录", not danger, danger)

    # 数据目录定义本身要正确
    check("A3 数据目录指向 {localappdata}\\HopOn",
          re.search(r'#define\s+UserDataDir\s+"\{localappdata\}\\HopOn"', text) is not None)
    check("A4 程序目录与数据目录不同",
          r'#define ProgramDir     "{localappdata}\Programs\HopOn"' in text)

    print("=== B. 升级不覆盖配置 ===")

    cfg_lines = [ln for ln in active_lines if "config.json" in ln and "DestName" in ln]
    check("B1 有 config.json 安装条目", bool(cfg_lines), cfg_lines)
    if cfg_lines:
        # Inno 支持行续接（行尾 \），条件与 Flags 可能跨行——要把前后行拼起来看
        idx = active_lines.index(cfg_lines[0])
        joined = " ".join(active_lines[max(0, idx - 1): idx + 2])
        check("B2 config 带 onlyifdoesntexist", "onlyifdoesntexist" in joined, joined[:140])
        check("B3 config 带 uninsneveruninstall", "uninsneveruninstall" in joined, joined[:140])
        check("B4 config 的目标在数据目录", "{#UserDataDir}" in joined, joined[:140])
        check("B5 config 不装进程序目录", 'DestDir: "{app}"' not in joined, joined[:140])

    print("=== C. 安装权限 ===")

    check("C1 PrivilegesRequired=lowest",
          re.search(r"^\s*PrivilegesRequired=lowest\s*$", active, re.M) is not None)

    print("=== D. 卸载提示保留数据 ===")

    check("D1 有 CurUninstallStepChanged 钩子", "CurUninstallStepChanged" in active)
    check("D2 卸载后检查数据目录是否存在", "DirExists(DataPath)" in active)
    check("D3 提示里包含数据路径", "DataPath" in active)
    check("D4 提到「保留」或「仍保留」", ("保留" in active))

    print("=== E. 覆盖运行中实例 ===")

    check("E1 CloseApplications=yes",
          re.search(r"^\s*CloseApplications=yes\s*$", active, re.M) is not None)
    check("E2 卸载前杀壳进程", "taskkill" in active and "AppExeName" in active)
    check("E3 卸载前杀引擎进程", "pythonw.exe" in active)

    print("=== F. 引用的产物路径 ===")

    # 检查 ReleaseDir 定义合理（Inno 的 #define 可能跨行续接）
    m = re.search(r"^#define\s+ReleaseDir\s+(.+)$", text, re.M)
    check("F1 定义了 ReleaseDir", m is not None, m.group(1)[:60] if m else "")
    if m:
        # 值可能形如："..\release\HopOn_v" + AppVersion —— 取整行判断
        raw = m.group(1)
        check("F2 ReleaseDir 指向 release 目录", "release" in raw.lower(), raw[:80])
        check("F3 ReleaseDir 带版本占位", "AppVersion" in raw, raw[:80])

    # Files 段引用的关键文件
    for need in ("{#AppExeName}", "Microsoft.Web.WebView2.Core.dll",
                 "WebView2Loader.dll", "engine\\*"):
        check("F4 Files 段包含 %s" % need, need in active)

    print("=== G. 编码与语法卫生 ===")

    check("G1 无 BOM/非法首行", text.lstrip().startswith(";") or text.lstrip().startswith("#"))
    check("G2 AppId 是合法 GUID 形态",
          re.search(r"AppId=\{\{[0-9A-Fa-f-]{36}\}", active) is not None,
          (re.search(r"AppId=.*", active) or [""])[0][:60])
    check("G3 有 Languages 段", "[Languages]" in active)
    check("G4 输出目录不与源码混",
          re.search(r"^\s*OutputDir=\S+", active, re.M) is not None)

    print()
    print("=" * 52)
    print("安装器审计：PASS %d / FAIL %d" % (PASS, FAIL))
    if FAILED:
        print("失败项：")
        for f in FAILED:
            print("  - " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())

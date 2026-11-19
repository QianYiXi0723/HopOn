# -*- coding: utf-8 -*-
"""安装前自动清场回归（2026-11-19）。

手动双击 Setup.exe 时 Inno 的 RestartManager 关不掉 Job Object 内的壳，
弹「无法自动关闭所有应用程序」。对标 UniGetUI / MSIME-Windows / OpenBiliClaw
的成熟做法：PrepareToInstall 里循环 taskkill 具名进程 + CloseApplications=force 兜底。

本测试钉住 iss 源码文本的关键不变量（只测源码，不编译、不装）：
A. CloseApplications=force 兜底；
B. TaskKillWait 循环写法（Exec taskkill /f /im、ewWaitUntilTerminated、Sleep 重试）；
C. 不用 /T（MSIME 事故：/T 会顺着进程树杀掉安装程序自己）；
D. PrepareToInstall 调 KillRunningApps，杀的是 HopOn.exe 壳。

运行：python tests/rl_test_installer_cleanup.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ISS = (ROOT / "installer" / "HopOn.iss").read_text(encoding="utf-8")
# 注释里也提到 [Code]，必须取「独占一行」的那个真段标记，不能用 split。
_m = re.search(r"^\[Code\]\s*$", ISS, re.M)
CODE = ISS[_m.end():] if _m else ""

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


print("=== A. CloseApplications=force 兜底 ===")
check("A1 CloseApplications=force", "CloseApplications=force" in ISS,
      "Restart Manager 关不掉 Job 内进程，force 是兜底不是主手段")

print("=== B. TaskKillWait 循环写法（学 UniGetUI） ===")
check("B1 定义了 TaskKillWait", "procedure TaskKillWait" in CODE or "function TaskKillWait" in CODE)
check("B2 用 taskkill /f /im", "taskkill.exe" in CODE and "/f /im" in CODE)
check("B3 等待进程终止 ewWaitUntilTerminated", "ewWaitUntilTerminated" in CODE)
check("B4 有 Sleep 重试（等 OS 释放句柄）", "Sleep(" in CODE)

print("=== C. 不用 /T（MSIME 事故教训） ===")
check("C1 taskkill 参数中没有 /T", "/f /im" in CODE and "/T" not in
      CODE[CODE.find("TaskKillWait"):CODE.find("TaskKillWait") + 600])

print("=== D. PrepareToInstall 调清场，杀壳 ===")
check("D1 有 PrepareToInstall", "function PrepareToInstall" in CODE)
check("D2 PrepareToInstall 调 KillRunningApps", "KillRunningApps" in CODE)
# 清场目标走宏 AppExeName（顶部 #define "HopOn.exe"）——改 exe 名一处生效
check("D3 清场目标是 HopOn.exe 壳（经 AppExeName 宏）",
      re.search(r'#define\s+AppExeName\s+"HopOn\.exe"', ISS) is not None
      and "TaskKillWait('{#AppExeName}')" in CODE)
check("D4 PrepareToInstall 返回空串继续安装", "Result := ''" in CODE)

print()
print("=" * 50)
print("PASS %d / FAIL %d" % (PASS, FAIL))
if FAILED:
    print("失败项：")
    for f in FAILED:
        print("  - " + f)
sys.exit(1 if FAIL else 0)

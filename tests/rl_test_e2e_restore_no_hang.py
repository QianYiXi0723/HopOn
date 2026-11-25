# -*- coding: utf-8 -*-
"""守卫 virgin_e2e.py 末尾「恢复真实实例」不得用 capture_output 启动常驻 GUI。

根因（2026-11-26 faulthandler 实测）：
    subprocess.run(["cmd","/c","start","",HopOn.exe,"--tray"], capture_output=True)
  HopOn 壳拉起的 engine/daemon 子进程继承 stdout/stderr 管道，常驻不退出 →
  管道永不 EOF → communicate() 永久挂死。三次 E2E 都卡在这里。

正解（对标 login_ui.py:4487 / daemon.py 的成熟写法）：
    subprocess.Popen([exe, "--tray"], creationflags=DETACHED_PROCESS,
                     stdout=DEVNULL, stderr=DEVNULL, stdin=DEVNULL)
  不建管道、脱离父进程，启动后立即返回。

用法：python tests/rl_test_e2e_restore_no_hang.py   （0 = 通过）
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = (BASE / "tools" / "rl_release" / "virgin_e2e.py").read_text(encoding="utf-8")

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def main():
    print("=== E2E 恢复实例不挂死守卫 ===")

    # 锁定「恢复真实实例」代码块
    marker = "# 恢复真实实例"
    idx = SRC.find(marker)
    check("定位到恢复实例代码块", idx >= 0)
    block = SRC[idx:idx + 700] if idx >= 0 else ""

    # 只看真实代码行（剥离 # 注释）——注释里会提到根因词，不能据此判定
    code_lines = [ln for ln in block.splitlines() if not ln.strip().startswith("#")]
    code = "\n".join(code_lines)

    # 反模式：run + capture_output 启动常驻 GUI = 挂死根因
    check("恢复实例代码无 capture_output", "capture_output" not in code, code[:200])
    check("恢复实例代码无 cmd /c start",
          '"/c"' not in code and "'/c'" not in code, code[:200])

    # 正模式：Popen + DETACHED + DEVNULL（对标项目成熟写法）
    check("恢复实例用 Popen", "Popen" in block)
    check("恢复实例用 DETACHED_PROCESS 或 DETACHED", "DETACHED" in block)
    check("恢复实例把 stdout/stderr 指向 DEVNULL",
          "DEVNULL" in block)

    print(f"\n=== rl_test_e2e_restore_no_hang.py: {PASS} PASS / {FAIL} FAIL ===")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""更新失败 result → 前端提示闭环（update-parity Task4）。

qa 的 L1 只发现 tests/rl_test_*.py，js 行为测试不进全量。本包装器调用
rl_test_update_failure_toast.js（vm 抽取 app.js 启动确认 IIFE，真行为断言），
把结论行透传给 qa 解析器，确保这条闭环进 run_all_tests 全量守护。

js 测试内容：失败 result 触发错误 toast / 无 result 静默 / 成功 result 回归。
若 node 不可用则明确 FAIL（不静默跳过——用户纪律：没守护的不变量等于不存在）。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = Path(__file__).with_suffix(".js")


def main() -> int:
    node = shutil.which("node")
    if not node:
        # 必须让 qa 的结论行解析为 FAIL：qa/run.py 只看 `PASS n / FAIL m` 的
        # m==0，忽略退出码。旧文案「0 项断言全绿」会被解析成 (0,0) → 误判绿，
        # 无 node 的机器整条回归静默不守护（review C1）。
        print("PASS 0 / FAIL 1（node 不可用，失败 result 前端消费闭环无法执行）")
        return 1
    try:
        proc = subprocess.run(
            [node, str(JS)],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL 执行 js 闭环测试失败：{exc}")
        return 1
    out = (proc.stdout or "") + (proc.stderr or "")
    sys.stdout.write(out)
    # js 结论行 `PASS 6 / FAIL 0` 会被 qa 解析；这里仅保证退出码一致
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())

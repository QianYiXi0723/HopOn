#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""托盘打卡后概览重拉绑定（方案A）验证的 qa 包装器。

qa/run.py 的 L1 只发现 tests/rl_test_*.py，不发现 .js。本包装器调用同目录的
rl_test_dailies_refresh.js（vm 抽取 app.js 真实绑定块、触发 focus/visibilitychange
断言 loadDailies 重拉），把结论行透传给 qa 解析器。node 不可用时明确 FAIL，不静默跳过。
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
        # qa/run.py 只看 `PASS n / FAIL m` 的 m==0，忽略退出码——必须让结论行为 FAIL
        print("PASS 0 / FAIL 1（node 不可用，概览重拉绑定无法验证）")
        return 1
    try:
        proc = subprocess.run(
            [node, str(JS)],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"PASS 0 / FAIL 1（执行 js 测试失败：{exc}）")
        return 1
    sys.stdout.write((proc.stdout or "") + (proc.stderr or ""))
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())

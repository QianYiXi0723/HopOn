# -*- coding: utf-8 -*-
"""活动「已结束」判据必须看 end_time，不能看 status（优化 #13 后续，2026-11-26）。

问题：星铁接口的 status='已完成' 表示**玩家任务进度做完了**，不代表活动结束。
实例：奇库寻遗 act_status=OtherActStatusFinish、进度 120/120，但 end_time 还剩 20 多天，
却被前端按 status 收进「已完成」折叠抽屉，用户以为活动过期了。

方案 B（用户拍板）：只有 end_time 已过才算「已结束」进抽屉；任务做完但未结束的
留在主列表，状态文案显示「任务已完成」。

用法：python tests/rl_test_act_ended.py   （0 = 通过）
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
APP = (BASE / "ui" / "app.js").read_text(encoding="utf-8")

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"  {detail}" if detail else ""))


def run():
    # A1 有 actEnded 纯函数
    check("A1 定义了 actEnded(it) 判据", "function actEnded" in APP)

    # A2 actEnded 依据 end 时间，而不是 status
    start = APP.find("function actEnded")
    check("A2 找到函数体", start >= 0)
    body = APP[start:start + 600] if start >= 0 else ""
    check("A2 actEnded 读取 it.end", "it.end" in body, body[:200])
    check("A2 actEnded 不依赖 status", "status" not in body, body[:200])

    # A3 分组处改用 actEnded
    check("A3 finished 分组用 actEnded（不再用 status==='已完成'）",
          "actEnded(x)" in APP and
          "x.status !== '已完成'" not in APP.split("const ongoing")[1][:400],
          "")
    check("A3b ongoing 分组不再排除 status==='已完成'",
          "x.status !== '已完成'" not in APP)
    check("A3c finished 分组不再用 status==='已完成'",
          "x.status === '已完成'" not in APP)

    # A4 卡片渲染的 ended 也改判据
    check("A4 actCardHtml 的 ended 改用 actEnded",
          "const ended = actEnded(it)" in APP)

    # A5 JS 语法
    node = "node"
    proc = subprocess.run([node, "--check", str(BASE / "ui" / "app.js")],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("A5 app.js 语法可解析", proc.returncode == 0,
          (proc.stderr or "").strip()[-200:])


if __name__ == "__main__":
    run()
    print(f"\n[结论] {PASS}/{PASS + FAIL} 通过")
    sys.exit(1 if FAIL else 0)

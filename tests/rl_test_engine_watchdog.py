#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""壳侧引擎守护（supervisor 模式）结构断言（2026-11-22）：

背景：朋友机诊断包实锤「引擎中途死亡后壳不管，页面持续『无法连接本地』
直到人工重启」——壳只在启动时 WaitEngineReady 一次，引擎中途死亡无监测。
修复=Process.Exited 事件 + Engine_Exited 自动重启：
  * 主动退出（_reallyExit）不触发重启（退出收尸语义保留）；
  * 端口已有 HopOn 实例在服务 → 不重复拉起；
  * 重启后 20 秒未就绪 → 计数；连续 3 次失败停止自动拉起（防崩溃循环）；
  * 成功 → 清计数，页面探活横幅自动消失（前端 3d6b232 已有）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PASS = 0
FAIL = 0


def check(cond: bool, label: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}")


def main() -> int:
    cs = (ROOT / "shell" / "MainForm.cs").read_text(encoding="utf-8")

    # ---- 守护逻辑四要素 ----
    check("_engine.EnableRaisingEvents = true" in cs,
          "W1 引擎退出事件已启用（Process.Exited 内核级监测）")
    check("_engine.Exited += Engine_Exited" in cs,
          "W2 Exited 事件绑定 Engine_Exited 处理器")
    check("if (_reallyExit) return;" in cs,
          "W3 主动退出不触发自动重启（退出收尸语义保留）")
    check("IsHopOnAlive(_port)) return;" in cs,
          "W4 端口已有 HopOn 实例在服务 → 不重复拉起")

    handler = re.search(r"private void Engine_Exited\(object sender, EventArgs e\)\s*\{.*?\n        \}",
                        cs, re.S)
    check(handler is not None, "W5 Engine_Exited 处理器可定位")
    body = handler.group(0) if handler else ""
    check("_engineRestartFails" in body and ">= 3" in body,
          "W6 连续 3 次重启失败退避停止（防崩溃循环）")
    check("引擎意外退出" in body and "自动重启" in body,
          "W7 日志记录意外退出与自动重启（可观测）")
    check("ExitCode" in body,
          "W9 退出码入日志（0x40010004=外部终结族 / 0xC0000005=真崩溃，死因分类零成本）")
    check("WaitEngineReady(_port, 20000)" in body,
          "W8 重启后等待就绪（20 秒预算，与首启同口径）")

    print(f"PASS {PASS} / FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

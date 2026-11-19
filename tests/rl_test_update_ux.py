#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""更新黑屏三修的结构断言（2026-11-20）：

  F1 [Run] 去掉 skipifsilent——静默装完安装器自己拉起 HopOn（桥接被拦的
     朋友机不再全员「无法连接本地服务」），runasoriginaluser 防提权带身份；
  F2 probe 超时兜底在失败路径同样生效——tries 判定挪到 fetch 之前，
     黑幕约 90 秒必撤并给手动启动指引（超时分支带 updDlReset 复位）；
  F3 黑屏期连接类 toast 静默——toastConn 闸读 rl-updating 标记，后台
     轮询失联不再轰炸；操作类失败（updDlReset 双站点）保留原样。

纯文本结构断言：读 installer/HopOn.iss 与 ui/app.js 按锚点切片比对。
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
    app = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    iss = (ROOT / "installer" / "HopOn.iss").read_text(encoding="utf-8")

    # ---- F1 [Run] 静默装完拉起 ----
    run_section = iss.split("[Run]", 1)[1].split("[UninstallRun]", 1)[0]
    exe_row = next(line for line in run_section.splitlines()
                   if "{#AppExeName}" in line and "Filename" in line)
    flags = run_section[run_section.index(exe_row):]
    check("skipifsilent" not in flags, "F1a [Run] 无 skipifsilent（静默装完也拉起）")
    check("runasoriginaluser" in flags, "F1b [Run] 带 runasoriginaluser（防提权带身份）")

    # ---- F2 probe 超时兜底挪到失败路径 ----
    m = re.search(r"const probe = function\(\)\{.*?setTimeout\(probe, 6000\);",
                  app, re.S)
    check(m is not None, "F2a probe 函数体可定位")
    body = m.group(0) if m else ""
    tries_pos = body.find("if (tries > 60)")
    fetch_pos = body.find("fetch('/api/update/download')")
    check(0 <= tries_pos < fetch_pos,
          "F2b tries>60 判定在 fetch 之前（失败路径同样计数）")
    timeout_zone = body[tries_pos:fetch_pos] if tries_pos >= 0 else ""
    check("updDlReset" in timeout_zone,
          "F2c 超时分支带 updDlReset（按钮复位可重试）")
    check("rl-updating" in timeout_zone,
          "F2d 超时分支卸 rl-updating 标记（黑幕不悬挂）")

    # ---- F3 toastConn 静默闸 ----
    m2 = re.search(r"function toastConn\(text\)\{.*?\n\}", app, re.S)
    check(m2 is not None and "rl-updating" in (m2.group(0) if m2 else ""),
          "F3a toastConn 读 rl-updating 标记")
    check(len(re.findall(r"toastConn\('无法连接本地服务'\)", app)) >= 4,
          "F3b 后台轮询失联站点已换 toastConn（≥4）")
    check(len(re.findall(r"toastConn\('当场验货无法连接本地服务'\)", app)) == 1,
          "F3c 当场验货失联站点已换 toastConn")
    check(len(re.findall(r"updDlReset\(\); toast\('无法连接本地服务'", app)) == 2,
          "F3d 操作类失败双站点保留原样（不静默）")

    print(f"PASS {PASS} / FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

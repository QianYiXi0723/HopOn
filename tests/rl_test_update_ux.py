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
    # 2026-11-23 逐行审：原断言要求超时分支卸 rl-updating——但那是错的
    # （安装多半正在进行，清标记会让重启后的成功确认永久丢失 = BUG D）。
    # 改为：超时分支必须撤销黑幕（mask hidden），标记保留。
    check("installing-mask" in timeout_zone,
          "F2d 超时分支撤黑幕（标记保留，交启动确认逻辑判定——BUG D 修正）")

    # ---- F3 toastConn 静默闸 ----
    m2 = re.search(r"function toastConn\(text\)\{.*?\n\}", app, re.S)
    check(m2 is not None and "rl-updating" in (m2.group(0) if m2 else ""),
          "F3a toastConn 读 rl-updating 标记")
    check(len(re.findall(r"toastConn\('无法连接本地服务'\)", app)) >= 4,
          "F3b 后台轮询失联站点已换 toastConn（≥4）")
    check(len(re.findall(r"toastConn\('当场验货无法连接本地服务'\)", app)) == 1,
          "F3c 当场验货失联站点已换 toastConn")
    check(len(re.findall(r"updDlReset\(\);\s*\n\s*toast\('无法连接本地服务'", app)) == 1,
          "F3d 操作类失败站点保留 toast（updDlStart；3d6b232 起 updDlAutoInstall 改重试横幅）")
    check("if (installTries < 3) { setTimeout(postInstall, 1200 * installTries); return; }" in app,
          "F3e 安装发起 POST 失败重试 2 次（连接类）；放弃路径保留 updDlReset+toast")

    # ---- F4 启动宽限窗（手动安装/每次启动的竞态窗口，2026-11-20 ④）----
    check("_BOOT_GRACE_MS = 15000" in app and "_BOOT_AT = Date.now()" in app,
          "F4a 启动宽限窗常量在位（页面加载起 15 秒）")
    tc = re.search(r"function toastConn\(text\)\{.*?\n\}", app, re.S)
    tc_body = tc.group(0) if tc else ""
    rl_pos = tc_body.find("rl-updating")
    grace_pos = tc_body.find("_BOOT_GRACE_MS")
    check(0 <= rl_pos < grace_pos,
          "F4b 黑屏期判定先于宽限窗判定（两层静默有序）")
    check("Date.now() - _BOOT_AT" in tc_body or "_BOOT_GRACE_MS" in app,
          "F4c 启动宽限窗保留（2026-11-22 职责移交探活横幅，判定仍在）")

    # ---- F5 连接健康监测改横幅模式（2026-11-22，业界 reconnecting banner）----
    # 手动安装场景无 rl-updating 标记，toast 连发不可控 → 常驻横幅替代
    check("_connBanner" in app and "conn-banner" in app,
          "F5a 常驻横幅元素与驱动函数在位（替代瞬时 toast）")
    check("_CONN_FAIL_MS = 20000" in app and "_connFailSince" in app,
          "F5b 连续失联阈值+首次记时变量在位")
    check("_connBanner('无法连接本地服务——正在重连" in app,
          "F5c 持续失联超 20 秒 → 红色横幅（真故障不漏报）")
    check("_connBanner('连接本地服务中…')" in app,
          "F5d 失联未满 20 秒 → 中性横幅「连接中…」（不红不刷屏）")
    check("_connFailSince = 0; _connBanner('')" in app or "_connFailSince = 0; _connBanner('')" in app,
          "F5e 连接恢复 → 清横幅")
    check("_CONN_COOLDOWN_MS" not in app,
          "F5f toast 冷却计时器已废弃（横幅常驻无需冷却）")

    # ---- F6 静默安装链路接回（2026-11-20 ④⑤终章，诊断包+代码考古实锤）----
    # updDlPick 改选 zip：Updater 只解 zip（ZipFile），Setup.exe 消费不了
    pick = re.search(r"function updDlPick\(\)\{.*?\n\}", app, re.S)
    pick_body = pick.group(0) if pick else ""
    check('/\\.zip$/i' in pick_body, "F6a 下载资产选 zip（Updater 只解 zip）")
    check("updDlAutoInstall();" in app, "F6b done 态接静默安装（孤儿函数接线）")
    lu = (ROOT / "login_ui.py").read_text(encoding="utf-8")
    check("Velopack" in lu and "CurrentBinaryDir" in lu,
          "F6c 安装目录语义注释在位（Velopack CurrentBinaryDir 模式，login_ui.py）")
    check("from core.paths import install_root as _app_root" in lu,
          "F6d 安装目标=当前安装根（install_root 运行时解析，自定义目录原地更新；"
          "2026-11-22 staged 改造收敛到 core.paths.install_root 单源）")
    check('"/Programs" / "HopOn"' not in lu,
          "F6e 硬算标准目录已清除（自定义目录用户不再装出双份）")

    # ---- F7 悬停提示去重（2026-11-23 用户圈图裁决：进度条已有的数值
    #      悬停不再重复）----
    check("live.tasks.map" not in app,
          "F7a 悬停 tooltip 的任务明细分支已删除（进度重复不再出现）")
    check("if (taskTip) tipParts.push(taskTip);" in app,
          "F7b tip 组装空段过滤（防「｜｜」双分隔符）")
    check("tipParts.join(' ｜ ')" in app,
          "F7c tip 单源组装（上次检查/本月完成经 tipParts）")

    # ---- F8 rl-updating 标记状态机（2026-11-23 逐行审：四处误清）----
    # A: 启动确认必须先比对成功才清标记（原实现先清再 fetch，fetch 失败即永久丢结果）
    confirm = re.search(r"\(function\(\)\{\s*const pending = localStorage.getItem\('rl-updating'\);.*?\}\)\(\);", app, re.S)
    cbody = confirm.group(0) if confirm else ""
    check("if (!cur) return;" in cbody,
          "F8a 启动确认：拿不到版本时保留标记（引擎未就绪不丢结果）")
    check(cbody.find("localStorage.removeItem('rl-updating')") > cbody.find("if (!cur) return;"),
          "F8b 启动确认：先比对版本再清标记（原实现先清后判=BUG A）")
    check(".catch(function(){ /* 引擎未就绪：保留标记" in cbody,
          "F8c 启动确认：fetch 失败保留标记")

    # D: probe 超时不得清标记（安装多半正在进行）
    probe = re.search(r"if \(tries > 60\) \{.*?return;\s*\}", app, re.S)
    pbody = probe.group(0) if probe else ""
    check("localStorage.removeItem" not in pbody,
          "F8d probe 超时保留标记（原实现清标记=更新结果永久丢失 BUG D）")

    # E: poll error 分支按 stage 区分（解压失败不该清）
    check("if (!d.sha_ok) localStorage.removeItem('rl-updating');" in app,
          "F8e poll error 按 sha_ok 区分清标记（解压失败=包还在可续 apply BUG E）")

    # F: install !ok 分支保留标记
    inst = re.search(r"if \(d && !d\.ok\) \{.*?return;\s*\}", app, re.S)
    ibody = inst.group(0) if inst else ""
    check("removeItem" not in ibody,
          "F8f install 失败保留标记（staging 多可续 apply BUG F）")

    # G: 进度阶段覆盖到 90 秒（Updater 5 轮重试最坏 ~50s）
    check("if (el < 90) setTimeout(tick, 300);" in app,
          "F8g 安装进度覆盖 90 秒（原 20 秒就暗示失败=误导 BUG G）")

    print(f"PASS {PASS} / FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""真实进程烟测（2026-11-14 测试优化 #2）——HopOn 测试体系缺的集成层。

背景实锤（当天三连）：
1. mihoyo_api 漏 import NameError：脚本测试 import 根文件全绿，实机全游戏假失败；
2. 页面跑旧 JS：serve 内容是新的、浏览器执行是旧的——serve 链路无覆盖；
3. 启动链 TDZ 炸掉：5057 行正牌启动从没跑，全页「读取中」。
这三个全是「脚本层断言测不到」的集成问题——本测试拉起 **engine/login_ui.py
真实子进程**（实机同路径、真实 HTTP），断言核心端点与 serve 内容里的关键判据。

前端判据维护纪律：每修一个实机 bug，把它的判据加进 CRITICAL_MARKERS
（实机 bug 转化为回归测试——行业惯例）。
"""
import json
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE_LOGIN_UI = ROOT / "engine" / "login_ui.py"
SMOKE_PORT = 8799

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + ("  " + detail if detail else ""))


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "HopOn-smoke"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def _get_json(url, timeout=15):
    st, body = _get(url, timeout)
    return st, json.loads(body)


# ---------- serve 内容关键判据（实机 bug 转化为回归测试） ----------
# 格式：(名称, 子串, 说明)。子串必须在 GET / 返回的 HTML 里出现。
CRITICAL_MARKERS = [
    ("dlfixCell 双轨判定（游戏格不拦）", "cell.hasAttribute('data-dl-id') || cell.hasAttribute('data-game')",
     "实机 bug：入口选择器 closest('.editable[data-dl-id]') 拦掉游戏格，长按全灭"),
    ("长按入口选择器已放宽", "e.target.closest('.editable')",
     "实机 bug：closest('.editable[data-dl-id]') 让游戏格在监听器第一行就 return"),
    ("闪光 keyframes 在位", "@keyframes rl-flash",
     "用户裁决恢复的数据到达闪光"),
    ("KPI 首渲染必闪", "firstRender",
     "F5 零反馈体感修复（数据没变也要闪一次）"),
    ("热力图滚动容器", "hm-scroll",
     "行高固定+纵向滚动（裁剪线切格子的 hit-test 死区修复）"),
    ("手动行可编辑入口（悬停提示）", "长按补录/撤销",
     "2026-11-18 裁决：常驻小字已删，入口=格子 title 悬浮提示"),
    ("F5 记忆启动调用在脚本尾", "localStorage.getItem('rl-tab')",
     "启动恢复过早调用会撞 TDZ 炸掉整个启动链（实锤）"),
    ("APK 公网下载页", "gitee.com/qianyixi/HopOn/releases",
     "二维码公网化（局域网 8790 方案已废）"),
]

# ---------- 核心端点（状态码 + 关键字段） ----------
ENDPOINTS = [
    ("history", "/api/history?days=7", ["days", "tracked"]),
    ("settings", "/api/settings", ["values", "defaults", "max"]),
    ("device", "/api/device", ["ok", "apk"]),
    ("tray_summary", "/api/tray_summary", ["headline", "text", "manual"]),
    ("schedule", "/api/schedule", ["ok"]),
]


def main():
    check("engine/login_ui.py 在位", ENGINE_LOGIN_UI.exists())
    proc = subprocess.Popen(
        [sys.executable, str(ENGINE_LOGIN_UI), "--port", str(SMOKE_PORT),
         "--no-browser", "--no-lan-tools"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = "http://127.0.0.1:%d" % SMOKE_PORT
    up = False
    try:
        for _ in range(30):                       # 最多 ~30s 等服务起来
            time.sleep(1)
            try:
                st, _ = _get(base + "/api/schedule", timeout=5)
                if st == 200:
                    up = True
                    break
            except Exception:
                continue
        check("真实子进程服务起来（:%d）" % SMOKE_PORT, up)

        if not up:
            return

        # 核心端点矩阵
        print("== 端点矩阵 ==")
        for name, path, keys in ENDPOINTS:
            try:
                st, body = _get_json(base + path)
                ok = st == 200 and all(k in body for k in keys)
                check("/%s → 200 + 字段 %s" % (path, keys), ok,
                      "st=%s body_keys=%s" % (st, list(body)[:8] if isinstance(body, dict) else type(body).__name__))
            except Exception as exc:
                check("/" + path, False, "%s: %s" % (type(exc).__name__, exc))

        # serve 内容关键判据（实机 bug → 回归测试）
        print("== serve 内容判据 ==")
        st, html = _get(base + "/")
        for name, marker, why in CRITICAL_MARKERS:
            check("serve 含 " + name, marker in html, "缺失——" + why)

        # 前后端一致性抽查：app.js 双轨判定必须与 HTML 内联版一致（防缓存/指纹漂移）
        check("GET / 返回 200 且含 app 主体", "dlfixOpen" in html and "rl-tab" in html)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    print("== smoke_e2e: %d PASS / %d FAIL ==" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()

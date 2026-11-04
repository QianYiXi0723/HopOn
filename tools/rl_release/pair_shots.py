# -*- coding: utf-8 -*-
"""在真实服务页面上出「修前 / 修后」对比图：深色主题、与用户窗口同宽。"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import time

import requests
import websocket

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8803/"
WIDTHS = [int(x) for x in (sys.argv[2].split(",") if len(sys.argv) > 2 else ["1050"])]
PORT = 9339
OUT = r"D:\WorkBuddy\rl_release\uitest"
S = requests.Session()
S.trust_env = False


class Conn:
    def __init__(self, url):
        self.ws = websocket.create_connection(url, timeout=40, suppress_origin=True)
        self.i = 0

    def send(self, method, **params):
        self.i += 1
        self.ws.send(json.dumps({"id": self.i, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self.i:
                if "error" in m:
                    raise RuntimeError("%s -> %s" % (method, m["error"]))
                return m.get("result", {})

    def eval(self, expr, wait=False):
        r = self.send("Runtime.evaluate", expression=expr, returnByValue=True,
                      awaitPromise=wait, userGesture=True)
        if "exceptionDetails" in r:
            return {"__exc": str(r["exceptionDetails"])[:300]}
        return r.get("result", {}).get("value")


SELECT_GRID = """[...document.querySelectorAll('#tab-account section.card')]
  .find(c => (c.querySelector('h2')||{}).textContent?.includes('游戏监控'))
  .querySelector('.sel-grid')"""


def shot(c, name):
    data = c.send("Page.captureScreenshot", format="png")["data"]
    p = os.path.join(OUT, name)
    open(p, "wb").write(base64.b64decode(data))
    print("  SHOT", name, os.path.getsize(p))
    return p


def main():
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    proc = subprocess.Popen([chrome, "--headless=new", "--remote-debugging-port=%d" % PORT,
                             "--user-data-dir=" + tempfile.mkdtemp(prefix="rl_pair_"),
                             "--no-first-run", "--no-default-browser-check",
                             "--disable-gpu", "--hide-scrollbars", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws_url = None
    for _ in range(60):
        try:
            t = S.get("http://127.0.0.1:%d/json/list" % PORT, timeout=2).json()
            p = [x for x in t if x.get("type") == "page"]
            if p:
                ws_url = p[0]["webSocketDebuggerUrl"]
                break
        except Exception:
            pass
        time.sleep(0.3)
    if not ws_url:
        proc.terminate()
        print("chrome 未就绪")
        return 2

    c = Conn(ws_url)
    c.send("Page.enable")
    c.send("Runtime.enable")
    c.send("Page.navigate", url=URL)
    time.sleep(3.0)
    # 切深色 + 账号绑定页
    c.eval("""(() => {
        document.documentElement.setAttribute('data-theme', 'dark');
        document.body.setAttribute('data-theme', 'dark');
        const b = document.querySelector('.nav button[data-tab="account"]');
        if (b) b.click();
    })()""")
    time.sleep(2.0)
    print("theme", c.eval("document.documentElement.getAttribute('data-theme')"))

    for w in WIDTHS:
        c.send("Emulation.setDeviceMetricsOverride", width=w, height=780,
               deviceScaleFactor=1, mobile=False)
        time.sleep(0.6)
        # 修前：轨道退回旧写法
        c.eval("%s.style.gridTemplateColumns = 'repeat(2, 1fr)';" % SELECT_GRID)
        time.sleep(0.4)
        before = c.eval("""(() => {
            const g = %s; const card = g.closest('.card');
            const br = card.getBoundingClientRect();
            const items = [...g.children].map(e => e.getBoundingClientRect());
            return {cols: getComputedStyle(g).gridTemplateColumns,
                    over: +(Math.max(...items.map(r => r.right)) - br.right).toFixed(1)};
        })()""" % SELECT_GRID)
        shot(c, "pair_before_%d.png" % w)
        # 修后
        c.eval("%s.style.gridTemplateColumns = '';" % SELECT_GRID)
        time.sleep(0.4)
        after = c.eval("""(() => {
            const g = %s; const card = g.closest('.card');
            const br = card.getBoundingClientRect();
            const items = [...g.children].map(e => e.getBoundingClientRect());
            return {cols: getComputedStyle(g).gridTemplateColumns,
                    over: +(Math.max(...items.map(r => r.right)) - br.right).toFixed(1)};
        })()""" % SELECT_GRID)
        shot(c, "pair_after_%d.png" % w)
        print("vw=%d  修前 cols=%s 溢出 %+.1f ｜ 修后 cols=%s 溢出 %+.1f"
              % (w, before["cols"], before["over"], after["cols"], after["over"]))

    c.send("Emulation.clearDeviceMetricsOverride")
    c.ws.close()
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())

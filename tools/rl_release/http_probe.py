# -*- coding: utf-8 -*-
"""headless Chrome 打开真实服务页面（app.js 全跑、数据真实），
在同一页面里对比旧写法 repeat(2,1fr) 与修复后的 minmax(0,1fr)。"""
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
PORT = 9338
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
            return {"__exc": str(r["exceptionDetails"])[:400]}
        return r.get("result", {}).get("value")


PROBE = r"""
(() => {
  const card = [...document.querySelectorAll('#tab-account section.card')]
      .find(c => (c.querySelector('h2')||{}).textContent?.includes('游戏监控'));
  if (!card) return {err: 'no card'};
  const grid = card.querySelector('.sel-grid');
  if (!grid) return {err: 'no grid'};
  const items = [...grid.children];
  const R = e => { const r = e.getBoundingClientRect();
                   return {x: +r.x.toFixed(1), w: +r.width.toFixed(1), r: +r.right.toFixed(1)}; };
  const names = items.map(i => {
     const n = i.querySelector('.sel-name'); const ico = i.querySelector('.sel-ico');
     const ir = ico ? ico.getBoundingClientRect() : null;
     return {name: n ? n.textContent.trim() : '?',
             textW: n ? Math.round(n.scrollWidth) : 0,
             boxW: n ? Math.round(n.clientWidth) : 0,
             icoW: ir ? Math.round(ir.width) : 0,
             tag: ico ? ico.tagName : '-'};
  });
  return {vw: innerWidth, card: R(card), grid: R(grid),
          cols: getComputedStyle(grid).gridTemplateColumns,
          gridClient: grid.clientWidth, gridScroll: grid.scrollWidth,
          items: items.map(R), names,
          over: +(Math.max(...items.map(i => i.getBoundingClientRect().right)) -
                  card.getBoundingClientRect().right).toFixed(1)};
})()
"""


def main():
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    proc = subprocess.Popen([chrome, "--headless=new", "--remote-debugging-port=%d" % PORT,
                             "--user-data-dir=" + tempfile.mkdtemp(prefix="rl_real_"),
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
    print("URL", c.eval("location.href"), "| title", c.eval("document.title"))

    c.eval("""document.querySelector('.nav button[data-tab="account"]').click()""")
    time.sleep(2.5)

    for w in (1024, 1100, 1180, 1280, 1440):
        c.send("Emulation.setDeviceMetricsOverride", width=w, height=780,
               deviceScaleFactor=1, mobile=False)
        time.sleep(0.6)
        fixed = c.eval(PROBE)
        c.eval("""(() => {
            const g = [...document.querySelectorAll('#tab-account section.card')]
              .find(c => (c.querySelector('h2')||{}).textContent?.includes('游戏监控'))
              .querySelector('.sel-grid');
            g.style.gridTemplateColumns = 'repeat(2, 1fr)';
        })()""")
        time.sleep(0.4)
        old = c.eval(PROBE)
        c.eval("""(() => {
            const g = [...document.querySelectorAll('#tab-account section.card')]
              .find(c => (c.querySelector('h2')||{}).textContent?.includes('游戏监控'))
              .querySelector('.sel-grid');
            g.style.gridTemplateColumns = '';
        })()""")
        if not fixed or not old or fixed.get("err") or old.get("err"):
            print(w, "ERR", fixed, old)
            continue
        print("\n=== vw=%d ===" % fixed["vw"])
        print("  卡片宽 %.0f | 网格可用 %.0f | 列宽 %s"
              % (fixed["card"]["w"], fixed["gridClient"], fixed["cols"]))
        print("  修复后：右溢出 %+6.1f  scrollW=%d" % (fixed["over"], fixed["gridScroll"]))
        print("  旧写法：右溢出 %+6.1f  scrollW=%d  列宽 %s"
              % (old["over"], old["gridScroll"], old["cols"]))
        for n, it in zip(fixed["names"], fixed["items"]):
            print("     %-9s 卡片%5.0f  文字可见%3d/%3d  <%s %dpx>"
                  % (n["name"], it["w"], n["boxW"], n["textW"], n["tag"], n["icoW"]))
        if w in (1024, 1440):
            data = c.send("Page.captureScreenshot", format="png")["data"]
            p = os.path.join(OUT, "real_%d.png" % w)
            open(p, "wb").write(base64.b64decode(data))
            print("  SHOT", p)

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

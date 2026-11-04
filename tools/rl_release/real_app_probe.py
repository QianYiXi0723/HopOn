# -*- coding: utf-8 -*-
"""在真实应用（app.py + WebView2 + app.js 全跑）上验证游戏监控网格：
同一页面里对比「旧写法 repeat(2,1fr)」与「修好的 minmax(0,1fr)」。"""
import base64
import json
import os
import sys
import time

import requests
import websocket

CDP = "http://127.0.0.1:9337"
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
  const cols = getComputedStyle(grid).gridTemplateColumns;
  const names = items.map(i => {
     const n = i.querySelector('.sel-name'); const ico = i.querySelector('.sel-ico');
     const ir = ico ? ico.getBoundingClientRect() : null;
     return {name: n ? n.textContent.trim() : '?',
             textW: n ? Math.round(n.scrollWidth) : 0,
             boxW: n ? Math.round(n.clientWidth) : 0,
             icoW: ir ? Math.round(ir.width) : 0,
             tag: ico ? ico.tagName : '-'};
  });
  return {vw: innerWidth, card: R(card), grid: R(grid), cols,
          gridClient: grid.clientWidth, gridScroll: grid.scrollWidth,
          items: items.map(R), names,
          over: +(Math.max(...items.map(i => i.getBoundingClientRect().right)) -
                  card.getBoundingClientRect().right).toFixed(1)};
})()
"""


def main():
    tgt = None
    for _ in range(40):
        try:
            for t in S.get(CDP + "/json/list", timeout=3).json():
                if t.get("type") == "page" and "devtools" not in (t.get("url") or ""):
                    tgt = t
                    break
            if tgt:
                break
        except Exception:
            pass
        time.sleep(0.5)
    if not tgt:
        print("找不到页面")
        return 2
    print("TARGET", tgt.get("url"))
    c = Conn(tgt["webSocketDebuggerUrl"])
    c.send("Runtime.enable")
    c.send("Page.enable")
    c.eval("""document.querySelector('.nav button[data-tab="account"]').click()""")
    time.sleep(2.0)
    print("native", json.dumps(c.eval("({w:innerWidth,h:innerHeight,dpr:devicePixelRatio})")))

    for w in (1024, 1100, 1180, 1280, 1440):
        c.send("Emulation.setDeviceMetricsOverride", width=w, height=780,
               deviceScaleFactor=1, mobile=False)
        time.sleep(0.5)
        fixed = c.eval(PROBE)
        # 临时把轨道写法退回旧版，验证这就是复发点
        c.eval("""(() => {
            const g = [...document.querySelectorAll('#tab-account section.card')]
              .find(c => (c.querySelector('h2')||{}).textContent?.includes('游戏监控'))
              .querySelector('.sel-grid');
            g.style.gridTemplateColumns = 'repeat(2, 1fr)';
        })()""")
        time.sleep(0.35)
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
        print("  卡片宽 %.0f | 可用 %.0f" % (fixed["card"]["w"], fixed["gridClient"]))
        print("  修复后 cols=%-28s 右溢出 %+6.1f  scrollW=%d"
              % (fixed["cols"], fixed["over"], fixed["gridScroll"]))
        print("  旧写法 cols=%-28s 右溢出 %+6.1f  scrollW=%d"
              % (old["cols"], old["over"], old["gridScroll"]))
        for n, it in zip(fixed["names"], fixed["items"]):
            print("     %-9s 卡片%5.0f  文字%3d/%3d  %s %dpx"
                  % (n["name"], it["w"], n["boxW"], n["textW"], n["tag"], n["icoW"]))
        if w in (1024, 1440):
            data = c.send("Page.captureScreenshot", format="png")["data"]
            p = os.path.join(OUT, "real_%d.png" % w)
            open(p, "wb").write(base64.b64decode(data))
            print("  SHOT", p)

    c.send("Emulation.clearDeviceMetricsOverride")
    c.ws.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

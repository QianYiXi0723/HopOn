# -*- coding: utf-8 -*-
"""量一量「游戏监控」网格在各窗口宽度下的盒子尺寸，定位溢出源。只读，不改仓库文件。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import requests
import websocket

ROOT = r"E:\Apps\HopOn"
WORK = r"D:\WorkBuddy\rl_release\uitest"
S = requests.Session()
S.trust_env = False

PROBE = r"""
(() => {
  const tab = document.getElementById('tab-account');
  tab.classList.add('on');
  const card = [...tab.querySelectorAll('section.card')]
      .find(c => (c.querySelector('h2')||{}).textContent?.includes('游戏监控'));
  if (!card) return {err: 'no card'};
  const body = card.querySelector('.card-body');
  const grid = card.querySelector('.sel-grid');
  const items = [...grid.children];
  const R = e => { const r = e.getBoundingClientRect();
                   return {x: +r.x.toFixed(1), w: +r.width.toFixed(1), r: +r.right.toFixed(1)}; };
  const cs = getComputedStyle(grid);
  const csBody = getComputedStyle(body);
  const col = document.querySelector('.acct-col.s7');
  return {
    vw: innerWidth,
    col: R(col),
    card: R(card),
    body: Object.assign(R(body), {pad: csBody.paddingLeft + '/' + csBody.paddingRight,
                                  scrollW: body.scrollWidth, clientW: body.clientWidth}),
    grid: Object.assign(R(grid), {scrollW: grid.scrollWidth, clientW: grid.clientWidth,
                                  cols: cs.gridTemplateColumns, gap: cs.columnGap,
                                  minW: cs.minWidth, width: cs.width}),
    items: items.map(i => ({name: (i.querySelector('.sel-name')||{}).textContent, ...R(i)})),
    cardMinW: getComputedStyle(card).minWidth,
    colMinW: getComputedStyle(col).minWidth,
  };
})()
"""


def build_page():
    os.makedirs(WORK, exist_ok=True)
    html = open(os.path.join(ROOT, "ui", "index.html"), encoding="utf-8").read()
    css = open(os.path.join(ROOT, "ui", "style.css"), encoding="utf-8").read()
    # app.js 只负责取数/切页，布局无关；留空避免一堆 API 报错干扰
    html = html.replace("__STYLE__", css).replace("__APP_JS__", "")
    p = os.path.join(WORK, "page.html")
    open(p, "w", encoding="utf-8").write(html)
    return p


class CDP:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
        self.i = 0

    def send(self, method, **params):
        self.i += 1
        mid = self.i
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError("%s -> %s" % (method, msg["error"]))
                return msg.get("result", {})

    def eval(self, expr):
        r = self.send("Runtime.evaluate", expression=expr, returnByValue=True, userGesture=True)
        if "exceptionDetails" in r:
            return {"__exc": str(r["exceptionDetails"])[:300]}
        return r.get("result", {}).get("value")


def main():
    page = build_page()
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    profile = os.path.join(tempfile.gettempdir(), "rl_uiprobe_profile")
    proc = subprocess.Popen([
        chrome, "--headless=new", "--remote-debugging-port=9334",
        "--user-data-dir=" + profile, "--no-first-run", "--no-default-browser-check",
        "--disable-gpu", "--hide-scrollbars", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ws_url = None
    for _ in range(60):
        try:
            t = S.get("http://127.0.0.1:9334/json/list", timeout=2).json()
            pages = [x for x in t if x.get("type") == "page"]
            if pages:
                ws_url = pages[0]["webSocketDebuggerUrl"]
                break
        except Exception:
            pass
        time.sleep(0.3)
    if not ws_url:
        proc.terminate()
        print("chrome 未就绪")
        return 2

    c = CDP(ws_url)
    c.send("Page.enable")
    c.send("Runtime.enable")
    c.send("Page.navigate", url="file:///" + page.replace("\\", "/"))
    time.sleep(1.5)

    for w, h in [(1000, 820), (1024, 820), (1100, 820), (1180, 820), (1280, 820),
                 (1366, 820), (1440, 820), (1600, 820), (1920, 900)]:
        c.send("Emulation.setDeviceMetricsOverride", width=w, height=h,
               deviceScaleFactor=1, mobile=False)
        time.sleep(0.35)
        d = c.eval(PROBE)
        if not d or d.get("err"):
            print(w, "ERR", d)
            continue
        over = max(i["r"] for i in d["items"]) - d["card"]["r"]
        print("\n=== vw=%d  overflow=%+.1fpx ===" % (d["vw"], over))
        print("  col   x=%7.1f w=%7.1f minW=%s" % (d["col"]["x"], d["col"]["w"], d["colMinW"]))
        print("  card  x=%7.1f w=%7.1f minW=%s" % (d["card"]["x"], d["card"]["w"], d["cardMinW"]))
        print("  body  x=%7.1f w=%7.1f pad=%s clientW=%d scrollW=%d"
              % (d["body"]["x"], d["body"]["w"], d["body"]["pad"],
                 d["body"]["clientW"], d["body"]["scrollW"]))
        print("  grid  x=%7.1f w=%7.1f cols=%s gap=%s clientW=%d scrollW=%d"
              % (d["grid"]["x"], d["grid"]["w"], d["grid"]["cols"], d["grid"]["gap"],
                 d["grid"]["clientW"], d["grid"]["scrollW"]))
        for it in d["items"]:
            print("     item w=%7.1f  right=%7.1f  %s" % (it["w"], it["r"], it["name"]))

    c.ws.close()
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())

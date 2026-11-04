# -*- coding: utf-8 -*-
"""全 tab 横向溢出扫描：找出右边界越过所属卡片右边界（或内容被横向裁切）的元素。只读。"""
import json
import os
import subprocess
import sys
import tempfile
import time

import requests
import websocket

from measure_selgrid import CDP, WORK, build_page, S  # 复用连接与页面构建

SCAN = r"""
(() => {
  const tabId = '%s';
  document.querySelectorAll('.tabpanel').forEach(p => p.classList.remove('on'));
  document.getElementById(tabId).classList.add('on');
  const tab = document.getElementById(tabId);
  const bad = [];
  const sel = e => {
    let s = e.tagName.toLowerCase();
    if (e.id) s += '#' + e.id;
    if (e.classList.length) s += '.' + [...e.classList].slice(0, 3).join('.');
    return s;
  };
  const cards = [...tab.querySelectorAll('.card')];
  for (const card of cards) {
    const cr = card.getBoundingClientRect();
    if (cr.width === 0) continue;
    for (const e of card.querySelectorAll('*')) {
      const r = e.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue;
      const over = r.right - cr.right;
      if (over > 1.5) {
        bad.push({kind: 'out', over: +over.toFixed(1), el: sel(e),
                  card: sel(card), w: +r.width.toFixed(1)});
      }
    }
  }
  // 横向被裁的元素（自身可滚但设了 hidden）
  for (const e of tab.querySelectorAll('*')) {
    const cs = getComputedStyle(e);
    if (cs.overflowX === 'hidden' && e.scrollWidth > e.clientWidth + 2 && e.clientWidth > 0) {
      bad.push({kind: 'clip', over: e.scrollWidth - e.clientWidth, el: sel(e), card: '-'});
    }
  }
  const uniq = [];
  const seen = new Set();
  for (const b of bad) {
    const k = b.kind + b.el + b.card;
    if (seen.has(k)) continue;
    seen.add(k); uniq.push(b);
  }
  uniq.sort((a, b) => b.over - a.over);
  return {tab: tabId, vw: innerWidth, count: uniq.length, top: uniq.slice(0, 12)};
})()
"""


def main():
    page = build_page()
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    profile = tempfile.mkdtemp(prefix="rl_uiprobe_")
    proc = subprocess.Popen([chrome, "--headless=new", "--remote-debugging-port=9335",
                             "--user-data-dir=" + profile, "--no-first-run",
                             "--no-default-browser-check", "--disable-gpu",
                             "--hide-scrollbars", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws_url = None
    for _ in range(60):
        try:
            t = S.get("http://127.0.0.1:9335/json/list", timeout=2).json()
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
    c.send("Page.navigate", url="file:///" + page.replace("\\", "/"))
    time.sleep(1.5)
    print("URL", c.eval("location.href"), "| tabpanels", c.eval("document.querySelectorAll('.tabpanel').length"))

    for w in (1024, 1180, 1440):
        c.send("Emulation.setDeviceMetricsOverride", width=w, height=820,
               deviceScaleFactor=1, mobile=False)
        time.sleep(0.3)
        for tab in ("history", "query", "account", "notify", "schedule"):
            d = c.eval(SCAN % ("tab-" + tab))
            if not d or "count" not in d:
                print("SKIP vw=%d %-9s -> %s" % (w, tab, str(d)[:160]))
                continue
            flag = "OK  " if d["count"] == 0 else "BADC"
            print("%s vw=%d %-9s 溢出元素 %d" % (flag, d["vw"], d["tab"], d["count"]))
            for b in d["top"]:
                print("      %-4s +%-6.1f %-42s (in %s)" % (b["kind"], b["over"], b["el"], b["card"]))
    c.ws.close()
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""修复前后对比截图：账号绑定页在 1024 / 1440 下的样子。只读。"""
import base64
import os
import subprocess
import sys
import tempfile
import time

from measure_selgrid import CDP, S, build_page

OUT = r"D:\WorkBuddy\rl_release\uitest"


def main():
    page = build_page()
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    proc = subprocess.Popen([chrome, "--headless=new", "--remote-debugging-port=9336",
                             "--user-data-dir=" + tempfile.mkdtemp(prefix="rl_shot_"),
                             "--no-first-run", "--no-default-browser-check",
                             "--disable-gpu", "--hide-scrollbars", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws = None
    for _ in range(60):
        try:
            t = S.get("http://127.0.0.1:9336/json/list", timeout=2).json()
            p = [x for x in t if x.get("type") == "page"]
            if p:
                ws = p[0]["webSocketDebuggerUrl"]
                break
        except Exception:
            pass
        time.sleep(0.3)
    c = CDP(ws)
    c.send("Page.enable")
    c.send("Page.navigate", url="file:///" + page.replace("\\", "/"))
    time.sleep(1.5)

    for w in (1024, 1440):
        c.send("Emulation.setDeviceMetricsOverride", width=w, height=760,
               deviceScaleFactor=1, mobile=False)
        time.sleep(0.4)
        c.eval("""(() => {
            document.querySelectorAll('.tabpanel').forEach(p => p.classList.remove('on'));
            document.getElementById('tab-account').classList.add('on');
            const a = document.querySelector('.acct-col.s7');
            const card = [...a.querySelectorAll('section.card')]
                .find(c => (c.querySelector('h2')||{}).textContent?.includes('游戏监控'));
            card.scrollIntoView({block: 'center'});
        })()""")
        time.sleep(0.5)
        data = c.send("Page.captureScreenshot", format="png")["data"]
        p = os.path.join(OUT, "account_%d.png" % w)
        open(p, "wb").write(base64.b64decode(data))
        print("SHOT", p, os.path.getsize(p))

    c.ws.close()
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())

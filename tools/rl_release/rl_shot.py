# -*- coding: utf-8 -*-
"""Selenium 截图 + 溢出测量：headless 失败自动退回有头窗口挪出屏幕。"""
import sys, time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

URL = "http://127.0.0.1:8793/"
OUT = r"D:\WorkBuddy"

def make_driver(headless: bool):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-proxy-server")
    opts.add_argument("--no-first-run")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--window-position=-32000,-32000")
    opts.add_argument("--hide-scrollbars")
    opts.add_argument("--user-data-dir=D:/WorkBuddy/rl_sel_profile")
    opts.add_argument("--log-level=3")
    return webdriver.Chrome(options=opts)

def shot_all(headless: bool):
    d = make_driver(headless)
    try:
        d.set_window_size(1920, 1080)
        tabs = [("", "home"), ("#query", "query"), ("#account", "account"),
                ("#notify", "notify"), ("#schedule", "schedule")]
        for frag, name in tabs:
            d.get(URL + frag)
            time.sleep(2.5)
            metrics = d.execute_script(
                "var b=document.body;return JSON.stringify({sw:b.scrollWidth, iw:window.innerWidth,"
                " sh:document.documentElement.scrollHeight, ih:window.innerHeight,"
                " t:(document.querySelector('.tabpanel.on')||{}).id||''});")
            print(name, metrics)
            d.save_screenshot(OUT + "\\b2_" + name + ".png")
        # 日志控制台展开态
        d.get(URL)
        time.sleep(2)
        d.execute_script(
            "document.getElementById('log-console').open=true;"
            "document.getElementById('log-console').scrollIntoView();")
        time.sleep(1.5)
        d.save_screenshot(OUT + "\\b2_console.png")
        print("console ok")
        return True
    finally:
        d.quit()

if __name__ == "__main__":
    try:
        shot_all(headless=True)
        print("HEADLESS_OK")
    except Exception as exc:
        print("headless failed:", exc)
        try:
            shot_all(headless=False)
            print("HEADED_OK")
        except Exception as exc2:
            print("headed failed:", exc2)
            sys.exit(1)

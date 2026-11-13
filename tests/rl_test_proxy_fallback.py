# -*- coding: utf-8 -*-
"""代理拒连直连兜底（core/netutil）回归：2026-11-14。

结构：首跳 = requests.get/post（或注入的 requests_module）；重试 = 真
requests.Session(trust_env=False)。测试拦首跳入口 + 拦 Session 构造。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import urllib.error
import urllib.request

import requests

from core import netutil

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


def proxy_refusal():
    """构造与实锤一致的异常链：ProxyError(Unable to connect to proxy)。"""
    inner = requests.exceptions.ConnectionError(
        "Unable to connect to proxy: Failed to establish a new connection: "
        "[WinError 10061] 由于目标计算机积极拒绝，无法连接。")
    return requests.exceptions.ProxyError(inner)


class FakeSession:
    """拦 Session 构造：记录 trust_env 与重试参数。"""
    seen_trust_env = None
    calls = []

    def __init__(self):
        self.trust_env = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def request(self, method, url, **kw):
        FakeSession.calls.append(kw)
        FakeSession.seen_trust_env = self.trust_env
        return "DIRECT-OK"


def test_requests_fallback():
    print("== requests 层 ==")
    FakeSession.calls.clear()
    orig_cls = requests.Session
    orig_post = requests.post
    first_call = {"n": 0}

    def refuse_first(url, **kw):
        first_call["n"] += 1
        raise proxy_refusal()

    requests.post = refuse_first
    requests.Session = FakeSession
    try:
        r = netutil.http_post("https://example.com", json={"a": 1}, timeout=5)
    finally:
        requests.Session = orig_cls
        requests.post = orig_post
    check("首跳拒连被触发", first_call["n"] == 1)
    check("拒连后直连重试成功", r == "DIRECT-OK")
    check("直连 Session 关 trust_env（行业标杆）", FakeSession.seen_trust_env is False)
    check("重试带 proxies={'http':None,'https':None}",
          FakeSession.calls[0].get("proxies") == {"http": None, "https": None},
          str(FakeSession.calls))
    check("其余参数原样透传",
          FakeSession.calls[0].get("json") == {"a": 1}
          and FakeSession.calls[0].get("timeout") == 5)

    # 两连败：抛第二次，__cause__=第一次
    class AlwaysRefuse(FakeSession):
        def request(self, method, url, **kw):
            raise proxy_refusal()

    orig_cls = requests.Session
    orig_post = requests.post
    sessions = {"n": 0}
    real_init = FakeSession.__init__

    def counting_init(self):
        real_init(self)
        sessions["n"] += 1

    AlwaysRefuse.__init__ = counting_init
    requests.post = lambda url, **kw: (_ for _ in ()).throw(proxy_refusal())
    requests.Session = AlwaysRefuse
    try:
        try:
            netutil.http_post("https://example.com", timeout=5)
            check("两连败应抛异常", False)
        except requests.exceptions.RequestException as e2:
            check("两连败抛第二次异常（重试建 1 个 Session）", sessions["n"] == 1,
                  str(sessions))
            check("__cause__ 挂第一次", isinstance(e2.__cause__,
                  requests.exceptions.ProxyError))
    finally:
        requests.Session = orig_cls
        requests.post = orig_post

    # 非代理拒连（目标超时）不重试
    FakeSession.calls.clear()
    orig_post = requests.post
    requests.post = lambda url, **kw: (
        _ for _ in ()).throw(requests.exceptions.Timeout("read timed out"))
    requests.Session = FakeSession
    try:
        try:
            netutil.http_post("https://example.com", timeout=5)
        except requests.exceptions.Timeout:
            pass
    finally:
        requests.Session = orig_cls
        requests.post = orig_post
    check("非拒连异常原样抛且只试一次（Session 未创建）",
          len(FakeSession.calls) == 0, str(FakeSession.calls))


def test_injected_module():
    print("== requests_module 注入（桩模式）==")
    class FakeModule:
        calls = []

        def post(self, url, **kw):
            FakeModule.calls.append(kw)
            return "STUB-OK"

    r = netutil.http_post("https://example.com", json={"a": 1},
                          requests_module=FakeModule())
    check("注入模块的首跳被使用", r == "STUB-OK")
    check("注入模块不走真 requests", len(FakeModule.calls) == 1)


def test_is_proxy_refusal():
    print("== is_proxy_refusal ==")
    check("ProxyError 识别", netutil.is_proxy_refusal(proxy_refusal()))
    chained = ValueError("外层")
    chained.__cause__ = proxy_refusal()
    check("链上 ProxyError 也识别", netutil.is_proxy_refusal(chained))
    check("普通异常不误报", not netutil.is_proxy_refusal(ValueError("x")))
    check("Timeout 不误报",
          not netutil.is_proxy_refusal(requests.exceptions.Timeout("t")))


def test_urllib_fallback():
    print("== urllib 层（检查更新通道）==")
    refused = urllib.error.URLError(ConnectionRefusedError(10061, "积极拒绝"))
    sentinel = object()
    opened = []

    class FakeOpener:
        def open(self, req, timeout=None):
            opened.append(("opener", timeout))
            return sentinel

    orig_urlopen = urllib.request.urlopen
    orig_build = urllib.request.build_opener
    urllib.request.urlopen = lambda req, timeout=None: (
        _ for _ in ()).throw(refused)
    urllib.request.build_opener = lambda *handlers: (
        check("直连 opener 用 ProxyHandler({})",
              len(handlers) == 1 and isinstance(handlers[0], urllib.request.ProxyHandler),
              str(handlers)) or FakeOpener())
    try:
        req = urllib.request.Request("https://api.github.com/x")
        r = netutil.urlopen_with_fallback(req, timeout=8)
        check("拒连后直连 opener 返回", r is sentinel)
        check("超时透传", opened and opened[0][1] == 8)
    finally:
        urllib.request.urlopen = orig_urlopen
        urllib.request.build_opener = orig_build

    urllib.request.urlopen = lambda req, timeout=None: (
        _ for _ in ()).throw(urllib.error.URLError("dns broken"))
    try:
        try:
            netutil.urlopen_with_fallback(urllib.request.Request("https://x"), timeout=5)
            check("非拒连 URLError 应抛", False)
        except urllib.error.URLError:
            check("非拒连 URLError 原样抛", True)
    finally:
        urllib.request.urlopen = orig_urlopen


if __name__ == "__main__":
    test_requests_fallback()
    test_injected_module()
    test_is_proxy_refusal()
    test_urllib_fallback()
    print("== proxy_fallback: %d PASS / %d FAIL ==" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)

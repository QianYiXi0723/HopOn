"""出站 HTTP 的「代理拒连」兜底（2026-11-14 用户裁决）。

背景：系统代理（加速器/代理工具）跟着 Windows 注册表走，工具关了注册表
开关还在——requests/urllib 仍然往 127.0.0.1:15732 连，全线撞死
（WinError 10061，实锤于 11-13 16:49 森空岛换凭证）。

策略：撞上「代理端口拒连」→ 绕过代理**直连重试一次**（鹰角/米哈游/库街区
接口实测直连可达）。只兜拒连这一种：代理活着但目标挂了、超时、风控
都不在兜底范围，保持原有错误语义。

urllib 侧没有独立 ProxyError 类型，拒连表现为 URLError(reason=
ConnectionRefusedError)；直连目标也拒连时同样形态——多试一次无副作用，
第二次的原样抛出（__cause__ 挂第一次，排查时两层都能看到）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

#: 绕过代理直连（显式 None 压过 trust_env 的系统代理注入）
_NO_PROXY: Dict[str, Optional[str]] = {"http": None, "https": None}


def is_proxy_refusal(exc: BaseException) -> bool:
    """异常链上有没有 requests 的 ProxyError（=代理端口连不上）。"""
    import requests

    seen = 0
    while exc is not None and seen < 8:
        if isinstance(exc, requests.exceptions.ProxyError):
            return True
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return False


def _retry_direct(method: str, url: str, kwargs: Dict[str, Any]):
    """直连重试：行业标杆写法（requests 社区共识）= 全新 Session 关 trust_env
    （env/注册表代理注入整体失效）+ 显式 proxies=None 字典双保险。"""
    import requests

    kwargs = dict(kwargs)
    kwargs["proxies"] = _NO_PROXY
    with requests.Session() as session:
        session.trust_env = False
        return session.request(method, url, **kwargs)


def request_with_proxy_fallback(method: str, url: str,
                                requests_module: Any = None, **kwargs: Any):
    """requests.get/post 的代理拒连兜底版。

    第一次原样发（跟系统代理）；ProxyError → 清掉代理直连重试一次；
    直连也失败就把第二次异常抛出并以第一次为 __cause__。

    requests_module：调用方注入自己的 requests 对象（kuro_api/mihoyo_api
    走 _import_requests() 桩模式，桩测试全程替换）——首跳必须用它，
    否则桩拦不到；兜底重试层固定用真 requests（桩不会抛 ProxyError，
    永远进不了重试）。
    """
    import requests as _real

    req = requests_module or _real
    sender = req.post if method.upper() == "POST" else req.get
    try:
        return sender(url, **kwargs)
    except _real.exceptions.RequestException as e1:
        if not is_proxy_refusal(e1):
            raise
        try:
            return _retry_direct(method, url, kwargs)
        except _real.exceptions.RequestException as e2:
            raise e2 from e1


def http_get(url: str, requests_module: Any = None, **kwargs: Any):
    return request_with_proxy_fallback("GET", url,
                                       requests_module=requests_module, **kwargs)


def http_post(url: str, requests_module: Any = None, **kwargs: Any):
    return request_with_proxy_fallback("POST", url,
                                       requests_module=requests_module, **kwargs)


def urlopen_with_fallback(req, timeout: float):
    """urllib.request.urlopen 的代理拒连兜底版（检查更新用）。

    URLError(reason=ConnectionRefusedError) → ProxyHandler({}) 空 opener
    直连重试一次；其余异常原样抛。
    """
    import urllib.error
    import urllib.request

    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as e1:
        if not isinstance(getattr(e1, "reason", None), ConnectionRefusedError):
            raise
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            return opener.open(req, timeout=timeout)
        except urllib.error.URLError as e2:
            raise e2 from e1

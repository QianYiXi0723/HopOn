# -*- coding: utf-8 -*-
"""config.json 凭证字段的 DPAPI 落盘加密（2026-11-05 P3）。

威胁模型：config.json 连同滚动备份 .bak-1/2/3 被整包带走（OneDrive 等网盘
自动同步、压缩包发给别人）时，明文 cookie/token 等于把米游社/森空岛登录态
一起送出去。DPAPI（CurrentUser 作用域）把密钥绑在「这台机器的这个用户」，
拿走的文件在别处解不开。

落盘格式：``dpapi:v1:<base64>``。约定：
- 读端（load_raw_config / load_config）一律先过 decrypt_config_dict——
  旧明文原样通过（自动兼容迁移），密文解不开时原样返回密文（认证会失败，
  引导用户重扫码，绝不抛异常打断配置读写）；
- 写端（save_raw_config）一律先过 encrypt_config_dict——空值/已加密值不动，
  滚动备份是从「磁盘上已加密的旧文件」复制的，天然也是密文。
"""
from __future__ import annotations

import base64
import ctypes
from typing import Any, Dict

MARK_PREFIX = "dpapi:v1:"

#: accounts[].credential 里的机密字段（uid/server/updated_at 非机密，不加密）
SECRET_KEYS = ("cookie", "token")

#: push 节点里的机密字段（按渠道，2026-11-06 P3 收口）：webhook 地址本身
#: 含接入点密钥，一并加密
PUSH_SECRET_MAP = {
    "serverchan": ("sendkey",),
    "qmsg": ("key",),
    "bark": ("device_key",),
    "feishu": ("webhook", "secret"),
    "dingtalk": ("webhook", "secret"),
}

_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _dpapi_transform(data: bytes, protect: bool) -> bytes:
    """Crypt(Protect|Unprotect)Data 薄封装；失败抛 OSError。仅 Windows 可用。"""
    if not hasattr(ctypes, "windll"):
        raise OSError("DPAPI 仅 Windows 可用")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _DATA_BLOB(
        len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))
    )
    blob_out = _DATA_BLOB()
    ok = (
        crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out),
        )
        if protect
        else crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out),
        )
    )
    if not ok:
        raise OSError("DPAPI %s 失败（错误码 %s）" % (
            "加密" if protect else "解密", ctypes.GetLastError()))
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def is_encrypted(value: object) -> bool:
    return str(value or "").startswith(MARK_PREFIX)


def encrypt_value(value: object) -> str:
    """明文 → dpapi:v1: 密文；空值 / 已加密 / 出厂占位符 / 非 Windows 原样返回。

    「请填写」占位符不加密：密文会骗过 cred_filled 的占位符检测（密文里
    没有"请填写"字样），让全新安装被误判成已绑定。
    """
    text = str(value or "")
    if not text or is_encrypted(text) or "请填写" in text:
        return text
    try:
        blob = _dpapi_transform(text.encode("utf-8"), protect=True)
    except OSError:
        return text   # 非 Windows 环境（脚本开发机）退化为明文，行为不变
    return MARK_PREFIX + base64.b64encode(blob).decode("ascii")


def decrypt_value(value: object) -> str:
    """dpapi:v1: 密文 → 明文；非密文原样返回；解密失败（跨机/换用户）返回
    原密文——认证会失败并引导重扫码，绝不抛异常打断配置读写。"""
    text = str(value or "")
    if not is_encrypted(text):
        return text
    try:
        blob = base64.b64decode(text[len(MARK_PREFIX):], validate=True)
        return _dpapi_transform(blob, protect=False).decode("utf-8")
    except (OSError, ValueError):
        return text


def _walk_credentials(config: Dict[str, Any]):
    for acc in (config or {}).get("accounts") or []:
        if not isinstance(acc, dict):
            continue
        cred = acc.get("credential")
        if isinstance(cred, dict):
            yield cred


def _iter_secret_fields(config: Dict[str, Any]):
    """遍历 (宿主 dict, 字段名)：账号凭证 + 推送渠道密钥两类机密字段。"""
    for cred in _walk_credentials(config):
        for key in SECRET_KEYS:
            yield cred, key
    push = (config or {}).get("push")
    if isinstance(push, dict):
        for channel, fields in PUSH_SECRET_MAP.items():
            node = push.get(channel)
            if isinstance(node, dict):
                for field in fields:
                    yield node, field


def encrypt_config_dict(config: Dict[str, Any]) -> Dict[str, Any]:
    """把账号凭证与推送渠道密钥就地加密（写盘前调用）。"""
    for host, field in _iter_secret_fields(config):
        if host.get(field) is not None:
            host[field] = encrypt_value(host[field])
    return config


def decrypt_config_dict(config: Dict[str, Any]) -> Dict[str, Any]:
    """把账号凭证与推送渠道密钥就地解密（读盘后调用）。"""
    for host, field in _iter_secret_fields(config):
        if host.get(field) is not None:
            host[field] = decrypt_value(host[field])
    return config

"""库街区（KuroBBS）只读查询层——鸣潮接入的数据源。

情报来源：TomyJan/Kuro-API-Collection（社区抓包实证，2025-10 仍在维护）。
与米游社/森空岛最大的不同：**没有签名算法、没有设备指纹校验**——
`devCode` 官方"必须但不校验"，token 就是唯一凭证（JWT，社区实证数月级寿命），
所以本层不需要森空岛那套「换票+缓存」流程，也不需要米游社那套 DS 签名。

本层只做三件事（全部只读，绝不代签）：
    1. 校验粘贴进来的 token（list_roles 顺带拿角色，验证有效性）
    2. 查询绑定角色（gamer/role/list，gameId=3 即鸣潮）
    3. 查询小组件数据（gamer/widget/game3/refresh，一次拿全体力+日常+周常）

不做任何写操作：签到接口（encourage/signIn/v2 等）在代码里不存在。
风控提示：社区报告签到类 POST 有风控，只读查询无风控报告——本层恰好只有查询。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dailycheck.kuro")

# --------------------------------------------------------------------------- #
# 常量（抄自 Kuro-API-Collection 的 PARAMS.md / 各端点文档）
# --------------------------------------------------------------------------- #

ADDR_KURO = "https://api.kurobbs.com"

#: gameId：鸣潮 = 3（战双 = 2，留作后续扩展）
GAME_ID_WUWA = 3

#: 鸣潮国服 serverId（库街区只覆盖国服；接口不传 serverId 时返回默认角色，故仅作参考）
WUWA_SERVER_CN = "76402e5b20be2c39f095a152090afddc"

USER_AGENT = "okhttp/3.11.0"

#: `devCode` 官方"必须但不校验"（PARAMS.md 原文）——社区通用静态常量，非个人设备标识
DEV_CODE = "073A9EFAC18FC50616DD15808DAE719DBCB904B7"

#: 埋点用 distinct_id（任意 UUID 即可，社区工具均为固定值）
DISTINCT_ID = "96b1567b-b5e6-422f-a1dd-7cb1e58c5db7"

#: 单次请求超时（秒），与 skland_api 对齐
REQUEST_TIMEOUT = 10

#: 业务码（PARAMS.md 响应体约定）
CODE_OK = 200
CODE_TOKEN_EXPIRED = 220
CODE_NO_ROLE = 6001


def _app_headers(token: str) -> Dict[str, str]:
    """APP 端风格公共请求头（PARAMS.md 共有请求头表）。"""
    return {
        "devCode": DEV_CODE,
        "source": "android",
        "version": "2.2.0",
        "versionCode": "2200",
        "token": token,
        "osVersion": "Android",
        "distinct_id": DISTINCT_ID,
        "countryCode": "CN",
        "model": "23127PN0CC",
        "lang": "zh-Hans",
        "channelId": "2",
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip",
    }


class KuroQueryError(Exception):
    """库街区查询失败（网络 / HTTP / 业务错误码 / 字段缺失）。"""


class KuroAuthError(KuroQueryError):
    """库街区 token 缺失 / 失效（code 220）。

    凭证失效类别随异常类自带（与 skland_api.SklandAuthError 同款约定）——
    cred_health 与 main._query_account 的告警分诊靠类属性识别。
    """

    cred_kind = "auth"


class GameNotBoundError(KuroQueryError):
    """token 有效，但这个库街区账号下没有绑定鸣潮角色（code 6001）。

    与 skland_api.GameNotBoundError 同款语义：不该显示成红色「查询失败」，
    也不该触发凭证告警（凭证是好的），玩家在游戏里建号后会自动生效。
    """


def _import_requests():
    try:
        import requests  # noqa: PLC0415 - 故意延迟导入
    except ImportError as exc:
        raise KuroQueryError("缺少依赖 requests，请先执行：pip install -r requirements.txt") from exc
    return requests


# --------------------------------------------------------------------------- #
# 请求封装
# --------------------------------------------------------------------------- #


def request_kuro(
    path: str,
    token: str,
    body: Optional[Dict[str, Any]] = None,
    timeout: int = REQUEST_TIMEOUT,
) -> Dict[str, Any]:
    """调用库街区接口，校验业务码后返回 data 字段。

    库街区统一响应：{code, msg, success, data}；code 200 成功、220 token 失效。
    POST form-urlencoded（PARAMS.md：除图片上传外请求体均为字符串拼接）。

    Raises:
        KuroAuthError: code 220（token 失效）。
        GameNotBoundError: code 6001（未绑定角色）。
        KuroQueryError: 网络异常 / HTTP 非 200 / 其他业务码 / data 缺失。
    """
    requests = _import_requests()
    url = ADDR_KURO + path
    headers = _app_headers(str(token or "").strip())
    headers["Content-Type"] = "application/x-www-form-urlencoded"

    logger.debug("[kuro] POST %s body=%s", path, body or {})
    try:
        response = requests.post(url, data=body or {}, headers=headers, timeout=timeout)
    except Exception as exc:
        raise KuroQueryError(f"库街区网络请求失败：{exc}") from exc

    if response.status_code != 200:
        logger.debug("[kuro] HTTP %s 原始响应：%s", response.status_code, response.text[:500])
        raise KuroQueryError(f"库街区接口暂时没有响应（HTTP {response.status_code}），请稍后再试")

    try:
        payload = response.json()
    except ValueError as exc:
        logger.debug("[kuro] 非 JSON 原始响应：%s", response.text[:500])
        raise KuroQueryError("库街区返回了无法解析的数据，请稍后再试") from exc

    code = payload.get("code")
    if code == CODE_TOKEN_EXPIRED:
        raise KuroAuthError(f"库街区 token 已失效（code=220，msg={payload.get('msg') or ''}）")
    if code == CODE_NO_ROLE:
        raise GameNotBoundError("该库街区账号还没有绑定鸣潮角色，进游戏后建号即可自动生效")
    if code != CODE_OK:
        raise KuroQueryError(
            f"库街区接口 {path} 返回错误码 {code}：{payload.get('msg') or ''}"
        )

    data = payload.get("data")
    if data is None:
        raise KuroQueryError(f"库街区接口 {path} 返回缺少 data 字段")
    logger.debug("[kuro] %s 返回顶层字段：%s", path, list(data.keys())[:30] if isinstance(data, dict) else type(data))
    return data


# --------------------------------------------------------------------------- #
# 接口：绑定角色 + 小组件数据（全部只读）
# --------------------------------------------------------------------------- #


def list_roles(token: str, game_id: int = GAME_ID_WUWA) -> List[Dict[str, Any]]:
    """查询绑定角色列表：POST /gamer/role/list（gameId=3 鸣潮）。

    返回 data 数组（每项含 roleId/roleName/serverName/gameLevel）；
    没绑角色返回空数组——token 本身有效。token 无效直接抛 KuroAuthError，
    所以它同时是绑定页的校验手段。
    """
    data = request_kuro("/gamer/role/list", token, body={"gameId": game_id})
    if not isinstance(data, list):
        raise KuroQueryError("gamer/role/list 返回的不是数组")
    return data


def get_widget_data(token: str, use_refresh: bool = True) -> Dict[str, Any]:
    """鸣潮小组件数据：POST /gamer/widget/game3/refresh（getData 同构，refresh 更准）。

    一次拿全：energyData（结晶波片 cur/total/refreshTimeStamp）、
    storeEnergyData（结晶单质，溢出存储）、livenessData（每日活跃）、
    weeklyData（战歌重奏）、hasSignIn（月签状态，只读展示）、roleId/roleName。

    注意：库洛服务器时间偶发不准——调用方做相对时间推算时以 data.serverTime 为准。
    """
    path = "/gamer/widget/game3/refresh" if use_refresh else "/gamer/widget/game3/getData"
    data = request_kuro(path, token, body={"gameId": GAME_ID_WUWA, "type": 2, "sizeType": 1})
    if not isinstance(data, dict):
        raise KuroQueryError("鸣潮小组件接口返回的不是字典")
    if not data.get("roleId"):
        # code 200 但没有角色：与 6001 同语义（未绑定/未设置展示角色）
        raise GameNotBoundError("该库街区账号还没有设置鸣潮角色，进游戏后建号即可自动生效")
    return data


# --------------------------------------------------------------------------- #
# 校验（绑定页粘贴 token 后的预检）
# --------------------------------------------------------------------------- #


def verify_kuro_token(token: str) -> Dict[str, Any]:
    """预检：粘贴的 token 是否有效、有没有绑定鸣潮角色。

    Returns:
        {"ok": True, "roles": [...], "user_id": ...} 或
        {"ok": False, "error": 原因}
    """
    clean = str(token or "").strip()
    if not clean:
        return {"ok": False, "error": "token 为空，请先粘贴库街区 token"}
    if "请填写" in clean:
        return {"ok": False, "error": "还是出厂占位符，请粘贴真实的库街区 token"}
    if any(ch.isspace() for ch in clean):
        return {"ok": False, "error": f"token 中间含空白字符（长度 {len(clean)}），请重新复制"}
    try:
        roles = list_roles(clean)
    except KuroQueryError as exc:
        return {"ok": False, "error": str(exc)}
    wuwa_roles = [r for r in roles if str(r.get("gameId")) == str(GAME_ID_WUWA)]
    return {"ok": True, "roles": roles, "wuwa_roles": wuwa_roles}

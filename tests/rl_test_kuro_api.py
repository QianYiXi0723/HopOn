# -*- coding: utf-8 -*-
"""库街区 API 层桩测试（纯离线，零网络——requests 全程 mock）。

覆盖：
  A1  verify 预检：空 / 占位符 / 带空白
  A2  code 220 → KuroAuthError（且 cred_kind="auth" 类属性在位）
  A3  code 6001 → GameNotBoundError
  A4  其他业务码 → KuroQueryError
  A5  code 200 → data 原样返回
  A6  HTTP 非 200 → KuroQueryError
  A7  list_roles 请求体带 gameId=3
  A8  get_widget_data 默认走 /refresh，能解析文档示例响应
  A9  widget 返回无 roleId → GameNotBoundError
  A10 APP 头齐备（devCode/source/token/User-Agent）
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import kuro_api as K       # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("ok  : " if ok else "FAIL: ") + name + (("  | " + str(detail)) if detail else ""))


class FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload, ensure_ascii=False)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeRequests:
    """记录调用参数、按脚本返回预设响应的最小 requests 替身。"""

    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        return self.resp


def patch(resp):
    fake = FakeRequests(resp)
    K._import_requests = lambda: fake
    return fake


def run():
    # ---- A1 verify 预检 ----
    for label, tok, word in [("空 token", "", "为空"), ("占位符", "请填写库街区token", "占位符"), ("带空白", "abc def", "空白")]:
        out = K.verify_kuro_token(tok)
        check(f"A1 {label}被拦", out["ok"] is False and word in out["error"], out.get("error"))

    # ---- A2 220 → AuthError ----
    patch(FakeResp(200, {"code": 220, "msg": "用户登录已过期", "success": False}))
    try:
        K.request_kuro("/gamer/role/list", "tok")
        check("A2 220 抛 AuthError", False, "没抛")
    except K.KuroAuthError as exc:
        check("A2 220 抛 AuthError", True)
        check("A2b AuthError.cred_kind=auth", exc.cred_kind == "auth")
    except Exception as exc:  # noqa: BLE001
        check("A2 220 抛 AuthError", False, repr(exc))

    # ---- A3 6001 → GameNotBound ----
    patch(FakeResp(200, {"code": 6001, "msg": "暂无数据", "success": False}))
    try:
        K.request_kuro("/x", "tok")
        check("A3 6001 抛 GameNotBound", False, "没抛")
    except K.GameNotBoundError:
        check("A3 6001 抛 GameNotBound", True)
    except Exception as exc:  # noqa: BLE001
        check("A3 6001 抛 GameNotBound", False, repr(exc))

    # ---- A4 其他业务码 ----
    patch(FakeResp(200, {"code": -10000, "msg": "参数空了"}))
    try:
        K.request_kuro("/x", "tok")
        check("A4 其他码抛 QueryError", False, "没抛")
    except K.KuroQueryError as exc:
        check("A4 其他码抛 QueryError", "-10000" in str(exc))
    except Exception as exc:  # noqa: BLE001
        check("A4 其他码抛 QueryError", False, repr(exc))

    # ---- A5 200 原样返回 ----
    patch(FakeResp(200, {"code": 200, "msg": "请求成功", "success": True, "data": {"k": 1}}))
    out = K.request_kuro("/x", "tok")
    check("A5 200 返回 data", out == {"k": 1})

    # ---- A6 HTTP 500 ----
    patch(FakeResp(502, None, text="bad gateway"))
    try:
        K.request_kuro("/x", "tok")
        check("A6 HTTP500 抛 QueryError", False, "没抛")
    except K.KuroQueryError as exc:
        check("A6 HTTP500 抛 QueryError", "502" in str(exc))

    # ---- A7 list_roles 请求体 ----
    fake = patch(FakeResp(200, {"code": 200, "data": [{"gameId": 3, "roleId": "101812955"}]}))
    roles = K.list_roles("tok")
    check("A7 list_roles 传 gameId=3", fake.calls[0]["data"] == {"gameId": 3} and len(roles) == 1)

    # ---- A8 widget refresh + 示例响应 ----
    sample = {
        "code": 200, "msg": "请求成功", "success": True,
        "data": {
            "gameId": 3, "roleId": "101812955", "roleName": "首席",
            "serverTime": 1719475326, "hasSignIn": True,
            "energyData": {"cur": 146, "total": 240, "refreshTimeStamp": 1719508373, "status": 0},
            "storeEnergyData": {"cur": 345, "total": 480},
            "livenessData": {"cur": 100, "total": 100},
            "weeklyData": {"cur": 0, "total": 3},
        },
    }
    fake = patch(FakeResp(200, sample))
    wd = K.get_widget_data("tok")
    check("A8a 默认走 /refresh", fake.calls[0]["url"].endswith("/gamer/widget/game3/refresh"))
    check("A8b 解析出体力 146/240", wd["energyData"]["cur"] == 146 and wd["energyData"]["total"] == 240)
    check("A8c 活跃度在场", wd["livenessData"]["cur"] == 100)

    # ---- A9 无 roleId ----
    patch(FakeResp(200, {"code": 200, "data": {"gameId": 3, "roleId": ""}}))
    try:
        K.get_widget_data("tok")
        check("A9 无角色抛 GameNotBound", False, "没抛")
    except K.GameNotBoundError:
        check("A9 无角色抛 GameNotBound", True)
    except Exception as exc:  # noqa: BLE001
        check("A9 无角色抛 GameNotBound", False, repr(exc))

    # ---- A10 公共头 ----
    fake = patch(FakeResp(200, {"code": 200, "data": {}}))
    K.request_kuro("/x", "mytoken")
    h = fake.calls[0]["headers"]
    check("A10 头齐备", h.get("token") == "mytoken" and h.get("source") == "android"
          and h.get("devCode") and h.get("User-Agent", "").startswith("okhttp"))
    check("A10b form 编码", h.get("Content-Type", "").startswith("application/x-www-form-urlencoded"))
    # 去空白契约在 request_kuro 层（内部函数透传），用带空白的 token 走一遍全链路验证
    fake2 = patch(FakeResp(200, {"code": 200, "data": {}}))
    K.request_kuro("/x", "  tk \n")
    check("A10c token 去空白", fake2.calls[0]["headers"]["token"] == "tk")

    failed = [n for n, ok in results if not ok]
    print(f"\n== {len(results) - len(failed)}/{len(results)} 通过 ==")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())

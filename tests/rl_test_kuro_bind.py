# -*- coding: utf-8 -*-
"""库街区绑定链路测试（D2a，纯离线）。

覆盖：
  D1  send_sms_code：成功（data.geeTest=False）/ 需要验证码 / 验证码错误 -130 / 网络炸
  D2  sdk_login：成功取 token / 验证码错误 / 缺 token 字段
  D3  write_kuro：存量配置无 wuwa 行 → 创建；有行 → 只动 token/昵称不动别家；
      enabled=false 拉起；provider=mock 切 miyoushe；DPAPI 密文落盘
  D4  手机号/验证码格式预检（_is_cn_mobile）
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import kuro_api as K   # noqa: E402
from core.config import DEFAULT_CONFIG  # noqa: E402
import login_ui                   # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("ok  : " if ok else "FAIL: ") + name + (("  | " + str(detail)) if detail else ""))


class FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


class FakeRequests:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append({"url": url, "data": data, "headers": headers})
        return self.resp


def patch(resp):
    fake = FakeRequests(resp)
    K._import_requests = lambda: fake
    return fake


def run():
    # ---- D1 send_sms_code ----
    fake = patch(FakeResp({"code": 200, "msg": "成功", "data": {"geeTest": False}}))
    K.send_sms_code("13800138000", '{"lot_number":"x"}')
    call = fake.calls[0]
    check("D1a 成功且带 geeTestData",
          call["data"]["geeTestData"] == '{"lot_number":"x"}'
          and call["headers"]["source"] == "h5"
          and "getSmsCodeForH5" in call["url"])

    patch(FakeResp({"code": 200, "msg": "成功", "data": {"geeTest": True}}))
    try:
        K.send_sms_code("13800138000", "")
        check("D1b 需验证码被拦", False, "没抛")
    except K.KuroSmsError as exc:
        check("D1b 需验证码被拦", "验证" in str(exc))

    patch(FakeResp({"code": -130, "msg": "验证码错误"}))
    try:
        K.sdk_login("13800138000", "123456")
        check("D1c -130 归类 SmsError", False, "没抛")
    except K.KuroSmsError as exc:
        check("D1c -130 归类 SmsError", "验证码" in str(exc))

    def _boom(*a, **kw):
        raise ConnectionError("reset")
    K._import_requests = lambda: type("R", (), {"post": staticmethod(_boom)})()
    try:
        K.send_sms_code("13800138000", "x")
        check("D1d 网络炸归 SmsError", False, "没抛")
    except K.KuroSmsError:
        check("D1d 网络炸归 SmsError", True)

    # ---- D2 sdk_login ----
    fake = patch(FakeResp({"code": 200, "msg": "成功", "data": {
        "token": "eyJtok", "userId": "10065669", "userName": "首席",
        "headPhotoUrl": "https://x/y.png"}}))
    data = K.sdk_login("13800138000", "123456")
    check("D2a 登录取 token", data["token"] == "eyJtok"
          and "mobile=13800138000" in fake.calls[0]["url"]
          and fake.calls[0]["headers"]["source"] == "android")

    patch(FakeResp({"code": 200, "msg": "成功", "data": {"token": ""}}))
    try:
        K.sdk_login("13800138000", "123456")
        check("D2b 缺 token 报错", False, "没抛")
    except K.KuroSmsError as exc:
        check("D2b 缺 token 报错", "token" in str(exc))

    # ---- D3 write_kuro ----
    tmpdir = Path(tempfile.mkdtemp(prefix="rl-kurobind-"))
    cfg = tmpdir / "config.json"
    login_ui.CONFIG_PATH = cfg
    base = json.loads(json.dumps(DEFAULT_CONFIG))
    # 模拟存量老配置：没有 wuwa 行
    base["accounts"] = [a for a in base["accounts"] if a.get("game") != "wuwa"]
    base["query"]["provider"] = "mock"
    cfg.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")

    saved = login_ui.write_kuro("eyJtok123", "漂泊者甲")
    raw = json.loads(cfg.read_text(encoding="utf-8"))
    wuwa = [a for a in raw["accounts"] if a.get("game") == "wuwa"]
    check("D3a 无行则建行", len(wuwa) == 1 and saved == "漂泊者甲")
    row = wuwa[0]
    from core.secure_store import decrypt_value
    check("D3b token+昵称落盘", decrypt_value(row["credential"]["token"]) == "eyJtok123"
          and row["nickname"] == "漂泊者甲" and row["enabled"] is True)
    check("D3c provider 切走 mock", raw["query"]["provider"] == "miyoushe")
    check("D3d DPAPI 密文落盘", str(row["credential"]["token"]).startswith("dpapi:v1:"))
    check("D3e 其他游戏不动", [a for a in raw["accounts"] if a.get("game") == "genshin"])

    # 战双同 token 落一份：开关不被绑定触碰（模板出厂即开，与全家族一致；
    # 只玩鸣潮的用户自行在监控抽屉关掉）
    raw_p = json.loads(cfg.read_text(encoding="utf-8"))
    pgr = [a for a in raw_p["accounts"] if a.get("game") == "pgr"]
    check("D3h pgr 行同 token 开关不动", len(pgr) == 1
          and str(pgr[0]["credential"]["token"]).startswith("dpapi:v1:")
          and pgr[0]["enabled"] is True)

    # 有行更新：只动 token/昵称，enabled=false 被拉起
    raw2 = json.loads(cfg.read_text(encoding="utf-8"))
    for a in raw2["accounts"]:
        if a.get("game") == "wuwa":
            a["enabled"] = False
            a["credential"]["token"] = "dpapi:v1:OLDCIPHER"
    cfg.write_text(json.dumps(raw2, ensure_ascii=False), encoding="utf-8")
    login_ui.write_kuro("eyJtok-new", "漂泊者乙")
    raw3 = json.loads(cfg.read_text(encoding="utf-8"))
    row3 = next(a for a in raw3["accounts"] if a.get("game") == "wuwa")
    check("D3f 更新+拉起 enabled", row3["enabled"] is True
          and str(row3["credential"]["token"]).startswith("dpapi:v1:"))
    plain = __import__("core.secure_store", fromlist=["decrypt_value"]).decrypt_value(
        row3["credential"]["token"])
    check("D3g 密文可解回明文", plain == "eyJtok-new", plain[:20])

    # ---- D4 格式预检 ----
    check("D4a 合法手机号", login_ui._is_cn_mobile("13800138000"))
    for bad in ("12345678901", "1380013800", "1380013800a", "", "23800138000"):
        if login_ui._is_cn_mobile(bad):
            check(f"D4 非法号 {bad!r} 被拦", False)
            break
    else:
        check("D4b 非法号全拦", True)

    failed = [n for n, ok in results if not ok]
    print(f"\n== {len(results) - len(failed)}/{len(results)} 通过 ==")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())

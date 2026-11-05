# -*- coding: utf-8 -*-
"""凭证 DPAPI 落盘加密测试（2026-11-05 P3）——全部读写进 %TEMP%，绝不碰仓库 config.json。

覆盖：
  S1 roundtrip：encrypt_value → decrypt_value 还原明文（本机 DPAPI 可用）
  S2 幂等：空值 / 已加密值 encrypt 不二次包裹
  S3 旧明文兼容：decrypt_config_dict 对旧明文原样通过（自动迁移语义）
  S4 save_raw_config 落盘密文：文件里无明文 cookie、有 dpapi:v1: 标记；
     load_raw_config 读回明文（读写两端同链路）
  S5 滚动备份 .bak-1 同样是密文（无明文泄漏）
  S6 跨机/损坏密文：解密失败原样返回、绝不抛异常打断配置读写
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import core.secure_store as ss  # noqa: E402
import login_ui  # noqa: E402  （仅模块导入，不起服务）

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS {name}")
    else:
        FAIL += 1
        print(f"FAIL {name}  {detail}")


def run() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="rl_secm_"))
    cookie = "ltuid=197567035; ltoken=v2_TEST_fake; cookie_token=v2_TEST_fake"
    cred = {"uid": "197567035", "server": "cn_gf01",
            "cookie": cookie, "updated_at": "2026-11-05 04:45"}

    # S1 roundtrip
    enc = ss.encrypt_value(cookie)
    check("S1a 密文带标记且无明文", enc.startswith(ss.MARK_PREFIX) and cookie not in enc,
          enc[:30])
    check("S1b 解密还原明文", ss.decrypt_value(enc) == cookie)

    # S2 幂等
    check("S2 空值不包裹", ss.encrypt_value("") == "")
    check("S2 已加密值不二次包裹", ss.encrypt_value(enc) == enc)

    # S3 旧明文兼容
    plain = {"accounts": [{"game": "genshin", "credential": dict(cred)}]}
    out = ss.decrypt_config_dict(json.loads(json.dumps(plain)))
    check("S3 旧明文原样通过", out["accounts"][0]["credential"]["cookie"] == cookie)

    # S4 save→load 全链路（读写两端同钩子）
    login_ui.CONFIG_PATH = tmp / "config.json"
    login_ui.save_raw_config(json.loads(json.dumps(plain)))
    on_disk = login_ui.CONFIG_PATH.read_text(encoding="utf-8")
    check("S4a 磁盘文件无明文 cookie", cookie not in on_disk)
    check("S4b 磁盘文件带密文标记", ss.MARK_PREFIX in on_disk)
    loaded = login_ui.load_raw_config()
    check("S4c 读端解密还原明文",
          loaded["accounts"][0]["credential"]["cookie"] == cookie,
          str(loaded["accounts"][0]["credential"])[:80])
    check("S4d 非机密字段不加密",
          loaded["accounts"][0]["credential"]["uid"] == "197567035")

    # S5 滚动备份密文（备份的是「磁盘上已加密的旧文件」→ 首存后需要再存
    # 一次才有 .bak-1 可验）
    login_ui.save_raw_config(json.loads(json.dumps(plain)))
    bak = login_ui.CONFIG_PATH.parent / "config.json.bak-1"
    check("S5a .bak-1 存在", bak.exists())
    if bak.exists():
        bak_text = bak.read_text(encoding="utf-8")
        check("S5b .bak-1 无明文 cookie 且带标记",
              cookie not in bak_text and ss.MARK_PREFIX in bak_text)

    # S6 跨机/损坏密文：不抛异常、原样返回
    bad = ss.MARK_PREFIX + "!!!not-base64!!!"
    check("S6a 坏 base64 原样返回", ss.decrypt_value(bad) == bad)
    import base64
    garbage = ss.MARK_PREFIX + base64.b64encode(b"\x00\x01\x02garbage").decode()
    check("S6b 伪 blob 原样返回", ss.decrypt_value(garbage) == garbage)


    # ---- P 组：推送渠道密钥（2026-11-06 P3 收口）----
    pushy = {"accounts": [{"game": "genshin", "credential": dict(cred)}],
             "push": {"serverchan": {"enabled": True, "sendkey": "SCT123456abcdef", "timeout": 10},
                      "qmsg": {"enabled": True, "key": "qmsgkey999", "server": "https://qmsg.zendee.cn"},
                      "bark": {"enabled": False, "device_key": "BarkDevKey", "server": "https://api.day.app"},
                      "feishu": {"enabled": False, "webhook": "", "secret": ""}}}
    login_ui.save_raw_config(json.loads(json.dumps(pushy)))
    on_disk = login_ui.CONFIG_PATH.read_text(encoding="utf-8")
    check("P1a 磁盘无明文 sendkey/key/device_key",
          all(x not in on_disk for x in ("SCT123456abcdef", "qmsgkey999", "BarkDevKey")))
    check("P1b 磁盘带 dpapi 标记（4 个机密值）", on_disk.count("dpapi:v1:") >= 4)
    loaded = login_ui.load_raw_config()
    push = loaded["push"]
    check("P2 读端解密还原推送密钥",
          push["serverchan"]["sendkey"] == "SCT123456abcdef"
          and push["qmsg"]["key"] == "qmsgkey999"
          and push["bark"]["device_key"] == "BarkDevKey")
    check("P3 空值/非机密字段不动",
          push["feishu"]["webhook"] == "" and push["serverchan"]["timeout"] == 10)

    check("R0 测试目录隔离", str(tmp) in str(login_ui.CONFIG_PATH))


if __name__ == "__main__":
    run()
    print(f"\n[结论] {PASS}/{PASS + FAIL} 通过")
    sys.exit(1 if FAIL else 0)

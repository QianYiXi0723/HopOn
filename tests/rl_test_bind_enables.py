# -*- coding: utf-8 -*-
"""绑定只自动开启本次绑定成功的游戏（2026-11-16 用户需求 B）。

统一原则：**绑定 = 开，从不关** —— 扫码流程对 enabled 只有「拉起本次绑定
成功者」一种写法，存量 enabled（含出厂关着但有凭证的 wuwa/bh3/pgr）
绝不回溯修改，要用户手动开。

沙箱纪律：
  - HOPON_USER_DIR 在任何 core.paths / login_ui import **之前**指向临时目录，
    CONFIG_PATH / USER_DIR（含 write_skland 的 logs 缓存清理）全部落在临时盘，
    绝不触碰真实 %LOCALAPPDATA%\HopOn；
  - 不调用 ensure_layout(allow_migrate=True)，只测 write_* 纯函数行为；
  - _skland_identity / _kuro_identities 打桩为空——零网络铁律。

用例：
  E1 write_mihoyo：updated（有角色、写入成功）→ enabled=True；
     未进 updated 的其余米游社游戏仍 False
  E2 write_mihoyo：absent（账号下没这游戏）→ enabled 不动，但 cookie 照写
  E3 write_mihoyo：原本 enabled=True 的游戏不在 games 列表 → 保持 True
     （凭证也不被本次触碰）
  E4 write_skland：只拉起 target，另一款不动
  E5 write_kuro：行为回归（勾选行拉起、token 刷进所有库街区行、
     未勾选的另一款不关——现实现已符合统一原则，断言防改坏）
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 沙箱开关必须在 import login_ui 之前定死（login_ui import 时即读 env 解析 USER_DIR）
_SANDBOX = tempfile.mkdtemp(prefix="rl_bind_enables_")
os.environ["HOPON_USER_DIR"] = _SANDBOX

import login_ui  # noqa: E402
from core.config import DEFAULT_CONFIG  # noqa: E402
from core.secure_store import decrypt_value  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("ok  : " if ok else "FAIL: ") + name + (("  | " + str(detail)) if detail else ""))


GAMES = ["genshin", "starrail", "zzz", "bh3", "pgr", "arknights", "endfield", "wuwa"]


def fresh_config():
    """8 款全部 enabled=False、无凭证的出厂形态（含存量没有 bh3 行的老配置）。"""
    base = json.loads(json.dumps(DEFAULT_CONFIG))
    base["accounts"] = [
        {"game": g, "nickname": g, "enabled": False, "credential": {}}
        for g in GAMES
    ]
    base.setdefault("query", {})["provider"] = "miyoushe"
    path = Path(login_ui.CONFIG_PATH)
    path.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    return path


def rows_of():
    raw = json.loads(Path(login_ui.CONFIG_PATH).read_text(encoding="utf-8"))
    return {a["game"]: a for a in raw.get("accounts", []) if isinstance(a, dict)}


def run():
    check("S0 CONFIG_PATH 落在沙箱内（不碰真实数据）",
          str(_SANDBOX) in str(login_ui.CONFIG_PATH), str(login_ui.CONFIG_PATH))

    # 零网络铁律：两个身份回填函数都会打真实接口，打桩为空（回填失败本就不挡绑定）
    login_ui._skland_identity = lambda target, token: {}
    login_ui._kuro_identities = lambda token: {}

    # ---- E1 write_mihoyo：本次绑定成功者开，其余不动 ----
    fresh_config()
    login_ui.write_mihoyo("fake_cookie", ["genshin", "starrail"], roles={
        "genshin": {"uid": "1", "nickname": "甲", "server": "cn_gf01"},
        "starrail": {"uid": "2", "nickname": "乙", "server": "prod_official_vip"},
    })
    r = rows_of()
    check("E1a genshin enabled=True", r["genshin"]["enabled"] is True,
          r["genshin"]["enabled"])
    check("E1b starrail enabled=True", r["starrail"]["enabled"] is True,
          r["starrail"]["enabled"])
    check("E1c 未绑定的其余三款米游社仍 False",
          all(r[g]["enabled"] is False for g in ("zzz", "bh3", "pgr")),
          {g: r[g]["enabled"] for g in ("zzz", "bh3", "pgr")})

    # ---- E2 write_mihoyo：absent 只存登录态，不碰开关 ----
    login_ui.write_mihoyo("fake_cookie", ["bh3"], roles={})
    r = rows_of()
    check("E2a bh3（账号下没这游戏）enabled 仍 False",
          r["bh3"]["enabled"] is False, r["bh3"]["enabled"])
    check("E2b bh3 cookie 已写入（登录态要存）",
          decrypt_value(r["bh3"]["credential"]["cookie"]) == "fake_cookie",
          str(r["bh3"]["credential"])[:48])
    check("E2c 其他游戏 enabled 不被回写",
          r["zzz"]["enabled"] is False and r["pgr"]["enabled"] is False)

    # ---- E3 write_mihoyo：从不自动关闭 ----
    path = fresh_config()
    raw = json.loads(path.read_text(encoding="utf-8"))
    for a in raw["accounts"]:
        if a["game"] == "genshin":
            a["enabled"] = True   # 预置：用户手动开过
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    login_ui.write_mihoyo("c3_cookie", ["zzz"], roles={
        "zzz": {"uid": "3", "nickname": "丙", "server": "cn_gf01"}})
    r = rows_of()
    check("E3a 已开启的 genshin 不在 games 列表 → 保持 True",
          r["genshin"]["enabled"] is True, r["genshin"]["enabled"])
    check("E3b genshin 的凭证不被本次触碰",
          "cookie" not in r["genshin"]["credential"],
          str(r["genshin"]["credential"])[:48])
    check("E3c 本次绑定的 zzz enabled=True", r["zzz"]["enabled"] is True)

    # ---- E4 write_skland：只拉起 target ----
    fresh_config()
    login_ui.write_skland("arknights", "fake_token")
    r = rows_of()
    check("E4a arknights enabled=True", r["arknights"]["enabled"] is True,
          r["arknights"]["enabled"])
    check("E4b arknights token 落盘",
          decrypt_value(r["arknights"]["credential"]["token"]) == "fake_token")
    check("E4c endfield enabled 仍 False 且无 token",
          r["endfield"]["enabled"] is False
          and "token" not in r["endfield"]["credential"],
          str(r["endfield"]["credential"])[:48])

    # ---- E5 write_kuro 行为回归（现实现已符合统一原则） ----
    fresh_config()
    login_ui.write_kuro("kuro_tok", "漂泊者", games=["wuwa"])
    r = rows_of()
    check("E5a wuwa 勾选绑定 → enabled=True", r["wuwa"]["enabled"] is True,
          r["wuwa"]["enabled"])
    check("E5b pgr 未勾选 → enabled 仍 False（从不自动关闭）",
          r["pgr"]["enabled"] is False, r["pgr"]["enabled"])
    check("E5c token 仍刷进所有库街区行（单设备登录语义）",
          decrypt_value(r["pgr"]["credential"]["token"]) == "kuro_tok"
          and decrypt_value(r["wuwa"]["credential"]["token"]) == "kuro_tok")
    login_ui.write_kuro("kuro_tok2", "指挥官", games=["pgr"])
    r = rows_of()
    check("E5d 反向绑 pgr → pgr=True、wuwa 保持 True 不被关",
          r["pgr"]["enabled"] is True and r["wuwa"]["enabled"] is True,
          {g: r[g]["enabled"] for g in ("wuwa", "pgr")})

    failed = [n for n, ok in results if not ok]
    print(f"\n== {len(results) - len(failed)}/{len(results)} 通过 ==")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())

# -*- coding: utf-8 -*-
"""2026-11-09 双修复测试（纯离线）。

Fix A 星铁实时页「暂不支持体力查询」：
  进度串带后缀（"240/240 + 备用 298"）时 _split_progress 整段 int() 炸，
  resource.current=None 被前端误判为不支持。修后取右值前导数字。

Fix B 开机自启被外部工具删除后不自愈：
  新增 app.autostart_desired 意图字段 + autostart.self_heal + 启动链接线。
  拨开关落意图；意图开而键丢/过期 → 重写；意图关 → 绝不动。

覆盖：
  C1  _split_progress 基础/后缀/溢出/垃圾输入
  C2  autostart_desired 配置默认/解析/持久化
  C3  self_heal 四分支（对齐不动/丢失重写/过期重写/意图关不动）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("ok  : " if ok else "FAIL: ") + name + (("  | " + str(detail)) if detail else ""))


def run():
    # ---- C1 _split_progress ----
    from login_ui import _split_progress

    cases = [
        ("160/200", (160, 200)),
        ("240/240 + 备用 298", (240, 240)),
        ("205/300 + 备用 298（备用已满）", (205, 300)),
        ("312/240", (312, 240)),                      # 道具破上限（无后缀）
        ("146/240 + 结晶单质 345", (146, 240)),        # 鸣潮将来的后缀
        ("4/4", (4, 4)),
        ("", (None, None)),
        (None, (None, None)),
        ("abc/def", (None, None)),
        ("12/", (12, None)),
    ]
    for text, want in cases:
        got = _split_progress(text)
        check(f"C1 {text!r}", got == want, got)

    # ---- C2 配置字段 ----
    from core.config import parse_config

    cfg = parse_config({})
    check("C2a 默认 false", cfg.autostart_desired is False)
    cfg2 = parse_config({"app": {"autostart_desired": True}})
    check("C2b 解析 true", cfg2.autostart_desired is True)
    check("C2c to_dict 回传", cfg2.to_dict()["app"]["autostart_desired"] is True)

    # ---- C3 self_heal 分支（monkeypatch 注册表三件套）----
    from core import autostart as A

    state = {"value": None, "enabled": 0}

    A.read_value = lambda: state["value"]
    A.build_command = lambda: '"C:\\x\\HopOn.exe" --tray'
    def _fake_enable():
        state["enabled"] += 1
        state["value"] = A.build_command()
        return {"ok": True, "message": "已开启"}
    A.enable = _fake_enable

    # 意图关：键丢了也不动
    state["value"] = None
    check("C3a 意图关不动", A.self_heal(False) == "" and state["enabled"] == 0)
    # 意图开 + 键丢失 → 重写
    r = A.self_heal(True)
    check("C3b 丢失自愈", "自愈重写" in r and state["enabled"] == 1, r)
    # 意图开 + 已对齐 → 不动
    state["enabled"] = 0
    check("C3c 对齐不动", A.self_heal(True) == "" and state["enabled"] == 0)
    # 意图开 + 路径过期 → 重写
    state["value"] = '"E:\\旧目录\\HopOn.exe" --tray'
    r = A.self_heal(True)
    check("C3d 过期自愈", "路径过期" in r and state["enabled"] == 1
          and state["value"] == '"C:\\x\\HopOn.exe" --tray')

    # ---- F 措辞裁决：已满就别说「即将溢出」（2026-11-09）----
    from core.models import ResourceAlert
    from core import analyzer as AN

    def _alert(cur, mx):
        return ResourceAlert(game_id="zzz", game_name="绝区零", nickname="绳匠",
                             resource="电量", current=cur, maximum=mx, threshold=200)

    check("F1 超上限", "已超出上限" in _alert(312, 240).warn_line()
          and "超出 72 点" in _alert(312, 240).warn_line())
    fw = _alert(240, 240).warn_line()
    check("F2 已满不说即将溢出", "已满" in fw and "即将溢出" not in fw
          and "停止恢复" in fw, fw)
    fn = _alert(210, 240).warn_line()
    check("F3 未满阈值内", "即将溢出" in fn and "已满" not in fn, fn)

    # 徽章判定只读 current/maximum；正文行走真 ResourceAlert
    rep = AN.DailyReport(statuses=[], resource_alerts=[_alert(240, 240)])
    t_full = rep.to_push_payload()[0]
    check("F4 全满格标题用「体力已满」", "体力已满（1）" in t_full
          and "体力将溢出" not in t_full, t_full)
    rep.resource_alerts = [_alert(210, 240)]
    t_mixed = rep.to_push_payload()[0]
    check("F5 未满格标题保留「将溢出」", "体力将溢出（1）" in t_mixed, t_mixed)

    failed = [n for n, ok in results if not ok]
    print(f"\n== {len(results) - len(failed)}/{len(results)} 通过 ==")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())

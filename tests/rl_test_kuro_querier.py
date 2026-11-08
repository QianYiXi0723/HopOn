# -*- coding: utf-8 -*-
"""鸣潮（库街区）查询层接入测试（纯离线，零网络——get_widget_data 全程 mock）。

覆盖：
  B1  路由：get_querier("wuwa", "miyoushe") 经 fallback 自动挂 KuroQuerier；
      get_querier("wuwa", "kuro") 直挂；provider=mock 仍走 MockQuerier
  B2  parse_wuwa 文档示例全量：每日活跃 100/100 done、波片 146/240 未满
      + 结晶单质后缀、战歌重奏 0/3、recover=refresh-server（服务器时差口径）
  B3  每日活跃缺失 → KuroQueryError（判定锚点护栏，绝不退化成"零任务=全完成"）
  B4  status=1（未解锁）/ total=0 跳过，不参与判定
  B5  波片满（240/240）→ done=True 且 recover=0
  B6  fetch：占位符 token → KuroAuthError；6001 → no_account（不是 error）；
      roleName 回填 nickname；GameStatus.source="kuro"
  B7  config 接入：KNOWN_GAMES / RESOURCE_TASK_NAMES / 默认阈值 / 阈值解析 /
      resource_thresholds 映射
  B8  history.GAME_ORDER 含 wuwa；mock 数据含 wuwa
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import kuro_api as K     # noqa: E402  (先于 querier 导入，便于打桩)
from core import querier as Q      # noqa: E402
from core.config import (          # noqa: E402
    KNOWN_GAMES, RESOURCE_TASK_NAMES, RESOURCE_THRESHOLD_DEFAULT,
    AppConfig, AccountConfig, parse_config,
)
from core.history import GAME_ORDER  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("ok  : " if ok else "FAIL: ") + name + (("  | " + str(detail)) if detail else ""))


DOC_SAMPLE = {
    "gameId": 3, "userId": 10065669, "roleId": "101812955", "roleName": "首席",
    "serverTime": 1719475326, "hasSignIn": True,
    "energyData": {"name": "结晶波片", "cur": 146, "total": 240,
                   "refreshTimeStamp": 1719508373, "status": 0},
    "storeEnergyData": {"name": "结晶单质", "cur": 345, "total": 480},
    "livenessData": {"name": "活跃度", "cur": 100, "total": 100, "status": 0},
    "weeklyData": {"name": "战歌重奏", "cur": 0, "total": 3, "status": 0},
}


def _acc(game="wuwa", token="tok", enabled=True):
    return AccountConfig(game=game, nickname="漂泊者", enabled=enabled,
                         credential={"token": token})


def run():
    # ---- B1 路由 ----
    q1 = Q.get_querier("wuwa", "miyoushe")
    q2 = Q.get_querier("wuwa", "kuro")
    q3 = Q.get_querier("wuwa", "mock")
    check("B1a miyoushe 自动挂 kuro", type(q1).__name__ == "KuroQuerier")
    check("B1b kuro 直挂", type(q2).__name__ == "KuroQuerier")
    check("B1c mock 不受影响", type(q3).__name__ == "MockQuerier")

    # ---- B2 文档示例全量 ----
    tasks = Q.KuroQuerier.parse_wuwa(DOC_SAMPLE)
    names = [t.name for t in tasks]
    live = next(t for t in tasks if t.name == "每日活跃")
    energy = next(t for t in tasks if t.name == "结晶波片")
    weekly = next(t for t in tasks if t.name == "战歌重奏")
    check("B2a 四项齐", names == ["每日活跃", "结晶波片", "战歌重奏"], names)
    check("B2b 活跃 100/100 done", live.done and live.progress == "100/100" and live.counted)
    check("B2c 波片 146/240 未满+单质后缀",
          not energy.done and energy.progress == "146/240 + 结晶单质 345", energy.progress)
    check("B2d 波片不计入日常（daily 口径）", energy.counted is False)
    check("B2e 战歌重奏在场", weekly.progress == "0/3")
    check("B2f ETA=refresh-server", energy.recover_seconds == 1719508373 - 1719475326,
          energy.recover_seconds)

    # ---- B3 锚点护栏 ----
    broken = {**DOC_SAMPLE, "livenessData": None}
    try:
        Q.KuroQuerier.parse_wuwa(broken)
        check("B3 缺活跃报字段异常", False, "没抛")
    except K.KuroQueryError as exc:
        check("B3 缺活跃报字段异常", "每日活跃" in str(exc))

    # ---- B4 未解锁/total=0 跳过 ----
    tasks4 = Q.KuroQuerier.parse_wuwa({
        **DOC_SAMPLE,
        "weeklyData": {"cur": 0, "total": 0, "status": 0},
        "energyData": {**DOC_SAMPLE["energyData"], "status": 1},
    })
    names4 = [t.name for t in tasks4]
    check("B4a 未解锁波片跳过", "结晶波片" not in names4, names4)
    check("B4b total=0 周常跳过", "战歌重奏" not in names4, names4)

    # ---- B5 波片满 ----
    tasks5 = Q.KuroQuerier.parse_wuwa({
        **DOC_SAMPLE,
        "energyData": {"cur": 240, "total": 240, "refreshTimeStamp": 0, "status": 0},
    })
    energy5 = next(t for t in tasks5 if t.name == "结晶波片")
    check("B5 波片满 done + 无 ETA", energy5.done and energy5.recover_seconds is None,
          (energy5.done, energy5.recover_seconds))

    # ---- B6 fetch 链 ----
    # 占位符 token
    q = Q.KuroQuerier()
    try:
        q.fetch(_acc(token="请填写库街区Token"))
        check("B6a 占位符抛 AuthError", False, "没抛")
    except K.KuroAuthError as exc:
        check("B6a 占位符抛 AuthError", "还没绑定" in str(exc))
    except Exception as exc:  # noqa: BLE001
        check("B6a 占位符抛 AuthError", False, repr(exc))

    # 6001 → no_account（fetch 内部转 GameStatus，不抛）
    # 注意：querier 是 from-import 按值引用，补丁必须打在 Q 命名空间
    orig = Q.get_widget_data
    Q.get_widget_data = lambda tok, use_refresh=True, **kw: (_ for _ in ()).throw(
        K.GameNotBoundError("还没绑定角色"))
    try:
        st = q.fetch(_acc())
        check("B6b 未绑定 → no_account", st.no_account and not st.error)
    finally:
        Q.get_widget_data = orig

    # 正常链：nickname 回填 + source
    Q.get_widget_data = lambda tok, use_refresh=True, **kw: DOC_SAMPLE
    try:
        acc = _acc()
        st = q.fetch(acc)
        check("B6c 正常查询", st.error is None and st.source == "kuro" and len(st.tasks) == 3)
        check("B6d roleName 回填昵称", acc.nickname == "首席" and st.nickname == "首席")
    finally:
        Q.get_widget_data = orig

    # ---- B7 config 接入 ----
    check("B7a KNOWN_GAMES", KNOWN_GAMES.get("wuwa") == "鸣潮")
    check("B7b RESOURCE_TASK_NAMES", RESOURCE_TASK_NAMES.get("wuwa") == "结晶波片")
    check("B7c 默认阈值", RESOURCE_THRESHOLD_DEFAULT.get("wuwa") == 200)
    cfg = parse_config({"app": {"wuwa_energy_threshold": 150}})
    check("B7d 阈值解析", cfg.wuwa_energy_threshold == 150)
    cfg2 = parse_config({})
    check("B7e 默认阈值解析", cfg2.wuwa_energy_threshold == 200)
    check("B7f resource_thresholds 含 wuwa", cfg2.resource_thresholds.get("wuwa") == 200)
    raw = cfg2.to_dict()
    check("B7g to_dict 持久化", raw["app"]["wuwa_energy_threshold"] == 200)

    # ---- B8 序与 mock ----
    check("B8a GAME_ORDER", GAME_ORDER[5] == "wuwa" and "pgr" in GAME_ORDER and "bh3" in GAME_ORDER)
    mock_st = Q.MockQuerier().fetch(_acc())
    check("B8b mock 数据在位", any(t.name == "每日活跃" for t in mock_st.tasks))

    # ---- B9 战双（pgr）路由与解析 ----
    qp = Q.get_querier("pgr", "miyoushe")
    check("B9a pgr 自动挂 kuro", type(qp).__name__ == "KuroQuerier")
    pgr_sample = {
        "roleId": "46218962", "roleName": "指挥官", "serverTime": 1719475203,
        "actionData": {"name": "血清", "cur": 145, "total": 240,
                       "refreshTimeStamp": 1719508800, "status": 0},
        "activeData": {"cur": 100, "total": 100, "status": 0},
        "dormData": {"cur": 8, "total": 8, "status": 0},
    }
    pgr_tasks = Q.KuroQuerier.parse_pgr(pgr_sample)
    names = [t.name for t in pgr_tasks]
    check("B9b 三项齐", names == ["每日活跃", "血清", "委托"], names)
    serum = next(t for t in pgr_tasks if t.name == "血清")
    check("B9c 血清 ETA", serum.recover_seconds == 1719508800 - 1719475203, serum.recover_seconds)
    live = next(t for t in pgr_tasks if t.name == "每日活跃")
    check("B9d 活跃锚点 counted", live.done and live.counted)

    # ---- B10 每日活跃未解锁 → stale 中性展示（体力照常） ----
    unlocked_sample = {
        "roleId": "105915946", "roleName": "千一夕", "serverTime": 1719475203,
        "livenessData": {"cur": 0, "total": 0, "status": 1},
        "energyData": {"cur": 240, "total": 240, "refreshTimeStamp": 0, "status": 0},
    }
    Q.get_widget_data = lambda tok, use_refresh=True, **kw: unlocked_sample
    try:
        st = Q.KuroQuerier().fetch(_acc())
        check("B10a 未解锁 → stale", st.stale is not None and "数据终端" in st.stale)
        check("B10b 体力照常在场", any(t.name == "结晶波片" for t in st.tasks))
        check("B10c 不算失败不冒充完成", st.error is None and st.finished is False)
    finally:
        Q.get_widget_data = lambda tok, use_refresh=True, **kw: DOC_SAMPLE

    failed = [n for n, ok in results if not ok]
    print(f"\n== {len(results) - len(failed)}/{len(results)} 通过 ==")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())

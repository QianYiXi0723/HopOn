# -*- coding: utf-8 -*-
"""鸣潮活动源（库洛官方公告 CDN notice.json）离线验证。

背景（2026-11-09 普查）：
    鸣潮活动时间有两套官方免鉴权源：游戏内公告 CDN（notice.json，
    毫秒时间戳+详情正文）与库洛 wiki 首页「版本活动」侧栏。v1 接
    notice.json（覆盖面更全；wiki 源高度重叠，双源去重不值）。
    奖励信息全社区无结构化源——与方舟同走「详情正文挖取」。

覆盖：
  W1 fetch 级（monkeypatch 会话，零网络）：进行中/未开始保留、已结束过滤
  W2 permanent=1、start==end 假时间、周边(tag=11)、版本日历(tag=10) 过滤
  W3 标题黑名单（已知问题等）与例外（网页活动 tag=1 保留）
  W4 重复 id 去重（activity+game 双组）
  W5 奖励挖取：星声 530 → 「星声 530」；无星声 → 奖励未公开
  W6 输出契约（game/title/start/end 格式）+ 按 end 升序
  W7 未绑定鸣潮 → 返回 []（门控）
  W8 FETCHERS/SOURCES 注册
  W9 _gacha_reward wuwa 分支：数字/纯提及/空文本三态
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import activities as A  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("ok  : " if ok else "FAIL: ") + name + (("  | " + str(detail)) if detail else ""))


NOW = datetime.now()


def ms(dt):
    return int(dt.timestamp() * 1000)


def notice_item(oid, title, start, end, *, tag=3, perm=0, group="game"):
    return {
        "id": oid, "contentPrefix": [f"https://x/cdn/{oid}/"],
        "red": 1, "permanent": perm,
        "startTimeMs": ms(start), "endTimeMs": ms(end),
        "tabTitle": {"zh-Hans": title, "en": "en-" + title[:5]},
        "tag": tag, "category": 2, "platform": [1], "channel": [],
    }


LIVE_START = NOW - timedelta(days=10)
LIVE_END = NOW + timedelta(days=7)
FIXTURE = {
    "activity": [
        notice_item("9001", "3.7版本活动日历", LIVE_START, LIVE_END, tag=10),
        notice_item("9002", "鸣潮丨手办预售将启", LIVE_START, LIVE_END, tag=11),
        notice_item("9003", "《鸣潮》玩家社区资讯", NOW + timedelta(days=1), NOW + timedelta(days=1)),
    ],
    "game": [
        notice_item("9004", "[声弦涤荡]声骸材料限时双倍活动", LIVE_START, LIVE_END, tag=5),
        notice_item("9005", "「索拉里斯回归邀约」网页活动开启", LIVE_START, LIVE_END, tag=1),
        notice_item("9006", "3.7版本已知问题及更新说明", LIVE_START, LIVE_END, tag=1),
        notice_item("9007", "《鸣潮》防沉迷系统说明", LIVE_START, LIVE_END, tag=1, perm=1),
        notice_item("9008", "已结束的旧活动", NOW - timedelta(days=9), NOW - timedelta(days=2), tag=3),
        notice_item("9004", "[声弦涤荡]声骸材料限时双倍活动", LIVE_START, LIVE_END, tag=5),  # 双组重复 id
        notice_item("9009", "「溯洄」唤取活动", NOW + timedelta(days=1), NOW + timedelta(days=14), tag=3),
    ],
}
DETAIL_TEXTS = {
    "9005": "<div>参与网页活动，最高可得530星声！</div>",
    "9009": "<div>活动期间唤取可获星声返还。</div>",
}


class FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = "{}"

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """按 URL 分发的最小会话替身；记录全部请求用于断言限额。"""

    def __init__(self):
        self.calls = []

    def get(self, url, timeout=None, **kw):
        self.calls.append(url)
        if url.endswith("notice.json"):
            return FakeResp(FIXTURE)
        for oid, text in DETAIL_TEXTS.items():
            if f"/cdn/{oid}/zh-Hans.json" in url:
                return FakeResp({"noticeId": oid, "textContent": text,
                                 "banner": "", "textTitle": ""})
        return FakeResp({}, status_code=404)


def run():
    # 门控与 mock 注入
    A._wuwa_enabled = lambda: True
    fake = FakeSession()
    A._new_session = lambda: fake

    items = A.fetch_wuwa_activities()
    titles = [it["title"] for it in items]

    # ---- W1 进行中/未开始保留、已结束过滤 ----
    check("W1a 进行中保留", "[声弦涤荡]声骸材料限时双倍活动" in titles)
    check("W1b 未开始保留", "「溯洄」唤取活动" in titles)
    check("W1c 已结束过滤", "已结束的旧活动" not in titles)

    # ---- W2 各类过滤 ----
    check("W2a 版本日历(tag=10)", "3.7版本活动日历" not in titles)
    check("W2b 周边(tag=11)", "鸣潮丨手办预售将启" not in titles)
    check("W2c 假时间(start==end)", "《鸣潮》玩家社区资讯" not in titles)
    check("W2d permanent=1", "《鸣潮》防沉迷系统说明" not in titles)

    # ---- W3 黑名单与例外 ----
    check("W3a 说明类黑名单", "3.7版本已知问题及更新说明" not in titles)
    check("W3b 网页活动(tag=1)保留", "「索拉里斯回归邀约」网页活动开启" in titles)

    # ---- W4 去重 ----
    check("W4 双组重复 id 只留一条", titles.count("[声弦涤荡]声骸材料限时双倍活动") == 1)

    # ---- W5 奖励 ----
    by_title = {it["title"]: it for it in items}
    check("W5a 星声数字挖取", by_title["「索拉里斯回归邀约」网页活动开启"]["reward"] == "星声 530",
          by_title["「索拉里斯回归邀约」网页活动开启"]["reward"])
    check("W5b 无星声标未公开", by_title["[声弦涤荡]声骸材料限时双倍活动"]["reward"] == "奖励未公开")

    # ---- W6 契约与排序 ----
    it0 = items[0]
    check("W6a 字段契约", it0["game"] == "wuwa" and it0["game_name"] == "鸣潮"
          and it0["kind"] == "act" and "remote_id" in it0)
    try:
        datetime.strptime(it0["end"], "%Y-%m-%d %H:%M:%S")
        ok_fmt = True
    except ValueError:
        ok_fmt = False
    check("W6b 时间格式", ok_fmt)
    ends = [it["end"] for it in items]
    check("W6c end 升序", ends == sorted(ends))

    # ---- W7 门控 ----
    A._wuwa_enabled = lambda: False
    check("W7 未绑定返回空", A.fetch_wuwa_activities() == [])
    A._wuwa_enabled = lambda: True

    # ---- W8 注册 ----
    check("W8 FETCHERS/SOURCES", "wuwa" in A.FETCHERS and "wuwa" in A.SOURCES)

    # ---- W9 _gacha_reward 三态 ----
    k1, r1 = A._gacha_reward("wuwa", {}, "完成可得200星声，最高额外100星声")
    check("W9a 取最大不求和", k1 is True and r1 == "星声 200", r1)
    k2, r2 = A._gacha_reward("wuwa", {}, "活动期间可获取星声")
    check("W9b 纯提及", k2 is True and r2 == "星声", r2)
    k4, r4 = A._gacha_reward("wuwa", {}, "可兑换共计星声*480、贝币*300000")
    check("W9d 币种在前", k4 is True and r4 == "星声 480", r4)
    k3, r3 = A._gacha_reward("wuwa", {}, "")
    check("W9c 空文本未知", k3 is None and r3 == "")

    failed = [n for n, ok in results if not ok]
    print(f"\n== {len(results) - len(failed)}/{len(results)} 通过 ==")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())

"""活动日历：拉取各游戏官方的「活动日历」，缓存到 data/activities.json。

数据来源（都是已登录账号的官方接口，不是公告）
------------------------------------------------
* **原神**：``POST api-takumi-record…/genshin/api/act_calendar``
  → ``act_list``（活动）+ ``fixed_act_list``（渊月螺旋等固定挑战）
* **星铁**：``GET api-takumi-record…/hkrpg/api/get_act_calender``
  （官方拼错的 calender）→ ``act_list`` + ``challenge_list``
* **绝区零**：``GET api-takumi-record…/zzz/activity_calendar``
  （参数是 ``uid`` / ``region``，不是 role_id/server）→ ``activity_list``
* **明日方舟**：鹰角官网 ``api/news`` 的 ``tab=1``，结束时间解析自活动正文

设计要点
--------
* **只认「日历」**：不再抓游戏公告。日历里每一条都有名字、起止时间、
  进度和状态，和米游社 App 里「活动日历」看到的是同一份数据。
* **每天最多拉一次**：缓存 20 小时有效；界面另有「刷新」可强制拉取。
* **不想要的直接删**：删掉的活动记在 ``data/ignored_activities.json``，
  拉取之后立刻过滤，**永远不会再进列表、也不会再触发推送**。
* **绝不拖垮主流程**：单个游戏失败只丢那一款，网络异常一律不外抛。
"""

from __future__ import annotations

import html
import json
import logging
import math
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core import ipc_lock

logger = logging.getLogger("dailycheck.activities")

from core.paths import app_root, user_root

#: 程序根：只用于定位代码/静态资源，不落用户数据
BASE_DIR = app_root()
#: 数据根（2026-11-05 数据外置）：缓存与配置落 %LOCALAPPDATA%\HopOn
USER_DIR = user_root()
CACHE_PATH = USER_DIR / "data" / "activities.json"
IGNORED_PATH = USER_DIR / "data" / "ignored_activities.json"

#: 缓存结构版本。版本不一致时下一轮强制重拉（v4 起带抽卡资源过滤与分组）。
CACHE_VERSION = 4

#: 缓存有效期：20 小时（每天最多真正请求一次）
CACHE_TTL_HOURS = 20
#: 推送提醒窗口：活动结束前 72 小时开始提醒
ALERT_WINDOW_HOURS = 72
#: 单次请求超时（秒）—— 拉不到就算了，不能卡住定时检查
REQUEST_TIMEOUT = 8
#: 缓存文件里保留多少天的「已提醒」记录
NOTIFIED_KEEP_DAYS = 7

#: 拿不到结构化日历的游戏（其余四款都已接入）
#: 全部五款游戏都有活动数据源了，暂无「仅公告、无日程」的游戏
UNSUPPORTED: Tuple[Dict[str, str], ...] = ()

#: 只留「抽卡资源」：每款游戏对应的可抽卡货币（标题/奖励名里包含即算数）
GACHA_REWARDS: Dict[str, Tuple[str, ...]] = {
    "genshin": ("原石",),
    "starrail": ("星琼",),
    "zzz": ("菲林",),
    "arknights": ("合成玉", "源石"),
    "wuwa": ("星声",),
}

#: 拿不到奖励数据时怎么办。
#: True（默认）= 保留，界面上标「奖励未公开」—— 宁可多看一条，也别漏掉大活动；
#: False = 也过滤掉 —— 列表更干净，但接口某天少个字段就会误伤。
KEEP_WHEN_REWARD_UNKNOWN = True

GENSHIN_CAL_URL = (
    "https://api-takumi-record.mihoyo.com/game_record/app/genshin/api/act_calendar"
)
STARRAIL_CAL_URL = (
    "https://api-takumi-record.mihoyo.com/game_record/app/hkrpg/api/get_act_calender"
)
ZZZ_CAL_URL = (
    "https://api-takumi-record.mihoyo.com/event/game_record_zzz/api/zzz/activity_calendar"
)

AK_NEWS_URL = "https://ak.hypergryph.com/api/news"
AK_NEWS_PAGE = "https://ak.hypergryph.com/news/%s"
#: 最多翻几条活动详情（每条一次请求，只取最近的一批）
AK_DETAIL_LIMIT = 6

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

#: 方舟正文里的活动时间，兼容：
#:   10月24日20:00~11月24日03:59 / 11月01日 12:00 - 11月15日 03:59
_DATE_WINDOW_RE = re.compile(
    r"活动时间[：:]\s*"
    r"(?:(\d{4})[年/])?(\d{1,2})[月/](\d{1,2})日?\s*(\d{1,2}:\d{2})?\s*"
    r"[~～\-—至到]+"
    r"\s*(?:(\d{4})[年/])?(\d{1,2})[月/](\d{1,2})日?\s*(\d{1,2}:\d{2})?"
)


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #


def _clean_text(text: Any) -> str:
    """去掉 HTML 标签 / 反转义 / 压平空白。"""
    raw = _HTML_TAG_RE.sub(" ", str(text or ""))
    return _WS_RE.sub(" ", html.unescape(raw)).strip()


#: 鹰角公告标题的前缀媒介标签（[活动预告]/[活动公告]/[限定寻访·庆典] 等）
_AK_TITLE_TAG_RE = re.compile(r"^[［\[][^［\］\]]{1,14}[］\]]\s*")
#: 营销尾缀（预告型标题的「即将开启」们——活动名到这就结束了）
_AK_TITLE_TAIL_RE = re.compile(r"\s*(即将开启|即将上线|即将复刻|即将开始)\s*$")


def _clean_ak_title(title: Any) -> str:
    """鹰角公告标题 → 活动名：剥前缀标签与「即将开启」类尾缀。

    tab=1 里大量条目是预告/汇总型公告（一篇覆盖多个子活动），标题长这样：
    「[活动预告]2026感谢庆典即将开启」。正文里挖到的时间窗和奖励是真实的，
    名字却不能原样上卡片。剥干净后即活动名：「2026感谢庆典」。
    """
    text = _clean_text(title)
    previous = None
    while previous != text and text:
        previous = text
        text = _AK_TITLE_TAG_RE.sub("", text).strip()
        text = _AK_TITLE_TAIL_RE.sub("", text).strip()
    return text or _clean_text(title)


@dataclass
class Activity:
    """活动日历里的一条。

    ``start`` / ``end`` 允许为空：接口偶尔不给时间，这种条目仍然展示，
    只是排在所在分组的末尾（绝不因为缺时间就把人蒙在鼓里）。
    """

    game_id: str
    game_name: str
    title: str
    start: Optional[datetime]
    end: Optional[datetime]
    kind: str = "act"          # act=活动 challenge=固定挑战（深渊/忘却之庭）
    status: str = ""           # 进行中 / 未开启 / 已完成（没给就按时间算）
    progress: str = ""         # 36/36 这种进度，没有就空
    icon: str = ""             # 活动官方图标（保留，界面已改用游戏头像）
    remote_id: str = ""        # 官方活动 id，用于生成稳定 uid
    reward: str = ""           # 抽卡资源文案：原石 420 / 星琼 500 / 菲林 900

    @property
    def uid(self) -> str:
        key = self.remote_id or self.title
        return f"{self.game_id}:{self.kind}:{key}"

    @property
    def hours_left(self) -> float:
        if self.end is None:
            return 0.0
        return max((self.end - datetime.now()).total_seconds() / 3600.0, 0.0)

    @property
    def days_left(self) -> int:
        """向上取整的天数：47 小时 -> 2 天，23 小时 -> 1 天。"""
        return max(int(math.ceil(self.hours_left / 24.0)), 1 if self.hours_left > 0 else 0)

    @property
    def not_started(self) -> bool:
        return self.start is not None and datetime.now() < self.start

    @property
    def group(self) -> str:
        """ongoing=已开启，upcoming=未开启（界面用它分两段）。"""
        return "upcoming" if self.not_started else "ongoing"

    def countdown_text(self) -> str:
        """界面上的倒计时文案：和 App 里「5天9小时」一个意思。"""
        now = datetime.now()
        if self.start is not None and now < self.start:
            return f"{_human_gap(self.start - now)}后开启"
        if self.end is None:
            return "时间待定"
        if self.end <= now:
            return "已结束"
        return f"剩 {_human_gap(self.end - now)}"

    def to_dict(self) -> Dict[str, Any]:
        hours = self.hours_left
        return {
            "id": self.uid,
            "game": self.game_id,
            "game_name": self.game_name,
            "title": self.title,
            "kind": self.kind,
            "start": _fmt_dt(self.start),
            "end": _fmt_dt(self.end),
            "hours_left": round(hours, 1),
            "days_left": self.days_left,
            "ending_soon": self.end is not None and 0 < hours <= ALERT_WINDOW_HOURS and not self.not_started,
            "not_started": self.not_started,
            "group": self.group,
            "countdown": self.countdown_text(),
            "status": self.status or ("未开启" if self.not_started else "进行中"),
            "progress": self.progress,
            "icon": self.icon,
            "reward": self.reward,
        }


def _fmt_dt(moment: Optional[datetime]) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S") if moment else ""


def _human_gap(delta: timedelta) -> str:
    """1 天 3 小时 / 5 小时 12 分 —— 粗到小时就够，别做秒级抖动。"""
    total_minutes = max(int(delta.total_seconds() // 60), 0)
    days, rem = divmod(total_minutes, 1440)
    hours, minutes = divmod(rem, 60)
    if days:
        return f"{days} 天 {hours} 小时" if hours else f"{days} 天"
    if hours:
        return f"{hours} 小时 {minutes} 分" if minutes else f"{hours} 小时"
    return f"{minutes} 分"


@dataclass
class ActivityAlert:
    """活动结束提醒（推送正文用）。"""

    game_id: str
    game_name: str
    title: str
    end: datetime
    uid: str = ""

    @property
    def days_left(self) -> int:
        hours = max((self.end - datetime.now()).total_seconds() / 3600.0, 0.0)
        if hours <= 0:
            return 0
        return max(int(math.ceil(hours / 24.0)), 1)

    def warn_line(self) -> str:
        """📅 活动提醒：原神「祝圣移涌」，还剩 2 天结束，请抓紧时间！"""
        days = self.days_left
        needs_quote = "「" not in self.title and "」" not in self.title
        shown = f"「{self.title}」" if needs_quote else self.title
        left = f"还剩 {days} 天" if days >= 1 else "今天就要"
        return f"📅 活动提醒：{self.game_name}{shown}，{left}结束，请抓紧时间！"


# --------------------------------------------------------------------------- #
# 缓存读写
# --------------------------------------------------------------------------- #


def _empty_cache() -> Dict[str, Any]:
    return {
        "version": CACHE_VERSION,
        "fetched_at": "",
        "last_attempt": "",
        "items": [],
        "notified": {},
    }


def _read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("读取 %s 失败（按空处理）：%s", path.name, exc)
        return default
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("%s JSON 损坏（按空处理）：%s", path.name, exc)
        return default
    return data if isinstance(data, dict) else default


def load_cache() -> Dict[str, Any]:
    """读缓存；文件不存在 / 损坏 / 结构不对都返回空结构，绝不抛异常。"""
    data = _read_json(CACHE_PATH, _empty_cache())
    if not isinstance(data.get("items"), list):
        data["items"] = []
    if not isinstance(data.get("notified"), dict):
        data["notified"] = {}
    return data


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    """原子写：唯一临时文件 + os.replace，并发时不会互相删掉对方的 tmp。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix="." + path.stem + "-", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:  # 清理失败不影响已经写完的文件
                pass


def save_cache(data: Dict[str, Any]) -> None:
    _atomic_write(CACHE_PATH, data)


# --------------------------------------------------------------------------- #
# 黑名单：删掉的活动记在这里，永远不再出现
# --------------------------------------------------------------------------- #


def load_ignored() -> Dict[str, Any]:
    data = _read_json(IGNORED_PATH, {"version": 1, "ids": [], "updated_at": ""})
    ids = data.get("ids")
    if not isinstance(ids, list):
        data["ids"] = []
    else:
        data["ids"] = [str(item) for item in ids if isinstance(item, str)]
    return data


def _ignored_ids() -> set:
    return set(load_ignored().get("ids") or [])


_IGNORED_WRITE_LOCK = threading.Lock()


def ignore_activity(uid: str) -> Dict[str, Any]:
    """把一条活动加入黑名单：立刻从列表消失，且不会再触发推送。"""
    target = str(uid or "").strip()
    if not target:
        raise ValueError("缺少活动 ID")
    with _IGNORED_WRITE_LOCK:
        data = load_ignored()
        ids = data.get("ids") or []
        if target not in ids:
            ids.append(target)
        data["ids"] = ids
        data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _atomic_write(IGNORED_PATH, data)
        logger.info("活动已隐藏（加入黑名单）：%s", target)
    return summary()


def restore_ignored() -> Dict[str, Any]:
    """清空黑名单（界面上的「全部恢复」用）。"""
    with _IGNORED_WRITE_LOCK:
        _atomic_write(
            IGNORED_PATH,
            {"version": 1, "ids": [], "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
        )
    return summary()


# --------------------------------------------------------------------------- #
# 时间解析
# --------------------------------------------------------------------------- #


def _parse_time(text: Any) -> Optional[datetime]:
    if not isinstance(text, str):
        return None
    text = text.strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _from_ts(value: Any) -> Optional[datetime]:
    try:
        stamp = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if stamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(stamp)
    except (OSError, ValueError, OverflowError):
        return None


def _cache_age_hours(fetched_at: str) -> Optional[float]:
    moment = _parse_time(fetched_at)
    if moment is None:
        return None
    return (datetime.now() - moment).total_seconds() / 3600.0


def _window_from_text(text: str, base: datetime) -> Optional[Tuple[datetime, datetime]]:
    """从公告正文里抠出「活动时间：A ~ B」（只给方舟用）。"""
    match = _DATE_WINDOW_RE.search(text)
    if not match:
        return None
    sy, sm, sd, st, ey, em, ed, et = match.groups()
    try:
        start_month, start_day = int(sm), int(sd)
        end_month, end_day = int(em), int(ed)
        start_year = int(sy) if sy else base.year
        if not sy and start_month < base.month - 6:
            start_year += 1
        end_year = int(ey) if ey else start_year
        if not ey and (end_month, end_day) < (start_month, start_day):
            end_year += 1
        sh, smin = (int(st[:2]), int(st[3:])) if st else (0, 0)
        eh, emin = (int(et[:2]), int(et[3:])) if et else (23, 59)
        start = datetime(start_year, start_month, start_day, sh, smin)
        end = datetime(end_year, end_month, end_day, eh, emin)
    except ValueError:
        return None
    if end <= start:
        return None
    return start, end


# --------------------------------------------------------------------------- #
# 米游社活动日历（原神 / 星铁 / 绝区零）
# --------------------------------------------------------------------------- #


def _miyoushe_credentials(game_id: str):
    """取某个游戏已绑定账号的 (ltuid, ltoken, uid, server, cookie)。"""
    from core import mihoyo_api  # 惰性导入，避免没配账号时拖慢启动
    from core.config import load_config

    config = load_config(USER_DIR / "config.json", auto_create=False)
    for account in config.accounts:
        if account.game == game_id and account.enabled:
            return mihoyo_api.extract_credentials(account)
    raise RuntimeError(f"{game_id} 还没有绑定米游社账号，无法读取活动日历")


def _miyoushe_session():
    import requests

    session = requests.Session()
    session.trust_env = False
    return session


def _miyoushe_get(url: str, params: Dict[str, Any], creds) -> Dict[str, Any]:
    from core import mihoyo_api

    ltuid, ltoken, uid, server, cookie = creds
    headers = mihoyo_api.build_headers(
        ltuid, ltoken, query=params, cookie=cookie, with_device=False
    )
    session = _miyoushe_session()
    response = session.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    retcode = payload.get("retcode")
    if retcode not in (0, None):
        raise RuntimeError(
            f"活动日历接口返回 retcode={retcode}：{payload.get('message')}"
        )
    return payload.get("data") or {}


def _miyoushe_post(url: str, body: Dict[str, Any], creds) -> Dict[str, Any]:
    from core import mihoyo_api

    ltuid, ltoken, uid, server, cookie = creds
    text = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    headers = mihoyo_api.build_headers(ltuid, ltoken, query={}, cookie=cookie, with_device=False)
    headers["DS"] = mihoyo_api.build_ds(query={}, body=text)
    headers["Content-Type"] = "application/json"
    session = _miyoushe_session()
    response = session.post(url, data=text.encode("utf-8"), headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    retcode = payload.get("retcode")
    if retcode not in (0, None):
        raise RuntimeError(
            f"活动日历接口返回 retcode={retcode}：{payload.get('message')}"
        )
    return payload.get("data") or {}


def _pick_time(info: Any, prefix: str = "") -> Tuple[Optional[datetime], Optional[datetime]]:
    """从 time_info / 顶层字段里取 (start, end)，时间戳优先。"""
    if not isinstance(info, dict):
        return None, None
    start = _from_ts(info.get(f"{prefix}start_ts")) or _parse_time(info.get(f"{prefix}start_time"))
    end = _from_ts(info.get(f"{prefix}end_ts")) or _parse_time(info.get(f"{prefix}end_time"))
    return start, end


def _reward_icon(rewards: Any) -> str:
    if isinstance(rewards, list):
        for item in rewards:
            if isinstance(item, dict) and item.get("icon"):
                return str(item["icon"])
    return ""


def _reward_items(raw: Dict[str, Any]) -> List[Tuple[str, int]]:
    """把 reward_list 拆成 [(名字, 数量)]，坏数据直接丢掉。"""
    out: List[Tuple[str, int]] = []
    rewards = raw.get("reward_list")
    if not isinstance(rewards, list):
        return out
    for item in rewards:
        if not isinstance(item, dict):
            continue
        name = _clean_text(item.get("name"))
        if not name:
            continue
        try:
            num = int(item.get("num") or 0)
        except (TypeError, ValueError):
            num = 0
        out.append((name, max(num, 0)))
    return out


def _gacha_reward(game_id: str, raw: Dict[str, Any], text: str = "") -> Tuple[Optional[bool], str]:
    """这条活动给不给抽卡资源？

    返回 ``(keep, label)``：
    * ``keep is True``  → 命中抽卡货币，label 是可以显示的数量文案；
    * ``keep is False`` → 明确没有抽卡资源，过滤掉；
    * ``keep is None``  → **拿不到奖励数据**，按兜底保留（宁可多看一条，也别漏大活动）。

    绝区零的接口没有 reward_list，只有 ``monochrome_cnt``（菲林池），
    0 就是「这期不送菲林」；字段缺失/不是数字才算拿不到。
    """
    keywords = GACHA_REWARDS.get(game_id, ())
    unknown: Optional[bool] = None if KEEP_WHEN_REWARD_UNKNOWN else False

    if game_id == "zzz":
        raw_count = raw.get("monochrome_cnt")
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            return unknown, ""
        if count <= 0:
            return False, ""
        return True, f"菲林 {count}"

    if game_id == "arknights":
        # 方舟没有结构化奖励，只能在活动正文里找「合成玉 / 源石」。
        # 2026-11-05：正文里的数字**不能无脑相加**——同一篇公告里混着三类：
        #   ① 累计签到第 N 日：合成玉*200        ← 真·活动赠送，要
        #   ② 许愿签档位：可出现的合成玉数量为 200/300/400/500/600/800  ← 随机，不要
        #   ③ 商店组合包：至纯源石*90、十连寻访凭证*1            ← 要花钱，不要
        # 全加会算出「源石 502 / 合成玉 11200」这种胡话（把礼包售价也算了），
        # 所以只认签到句，且同一个数值只记一次（正文常把同一句话重复两遍）。
        if not text:
            return unknown, ""
        return _arknights_signin_reward(text, keywords)

    if game_id == "wuwa":
        # 鸣潮没有结构化奖励字段，详情正文（HTML）里找「星声」。
        # 同方舟教训：正文数字不能乱加——「最高可得530星声」这类取单值，
        # 多处提及取最大（每处都是"可获得"口径），绝不求和。
        if not text:
            return unknown, ""
        # 鸣潮两种口径并存：「530星声」（数字在前）与「星声*480」（币种在前）
        counts = [int(m.group(1)) for m in re.finditer(r"(\d{2,6})\s*星声", text)]
        counts += [
            int(m.group(1))
            for m in re.finditer(r"星声\s*[×x*]\s*(\d{2,6})", text)
        ]
        if counts:
            return True, f"星声 {max(counts)}"
        if "星声" in text:
            return True, "星声"
        return unknown, ""

    items = _reward_items(raw)
    if not items:
        return unknown, ""  # 奖励字段缺失或为空

    hit = ""
    total = 0
    for name, num in items:
        for keyword in keywords:
            if keyword in name:
                hit = keyword
                total += num
    if not hit:
        return False, ""
    return True, (f"{hit} {total}" if total > 0 else hit)


#: 方舟正文里的「累计签到第 N 日：... 合成玉*200 ...」——只取这类句子里的奖励。
#: 句子边界用「累计签到第」到下一个「累计签到第」或段落末，避免跨句误抓。
_AK_SIGNIN_SPLIT_RE = re.compile(r"累计签到第[一二三四五六七八九十百\d]+日")
#: 奖励条目固定写法「名字*数量」，名字里不含 * 、空白受控
_AK_REWARD_ITEM_RE = re.compile(r"([^\s*、，,：:；;（）()【】\[\]]{1,12})\s*[*x×]\s*(\d+)")


def _arknights_signin_reward(
    text: str, keywords: Tuple[str, ...]
) -> Tuple[Optional[bool], str]:
    """从方舟活动正文里只挑「累计签到」段的抽卡资源奖励。

    返回语义同 :func:`_gacha_reward`。为什么只认签到段：
    ``_gacha_reward`` 的注释里列了三类数字，只有签到是"活动白送"，
    许愿签是随机档位、组合包要花钱——混进来就是错数据。

    **正文会重复**：鹰角公告的接口字段同时带纯文本版和 HTML 转义版，
    同一组签到会出现两遍（有时三遍）。按 ``(日次, 名称, 数量)`` 去重挡不住，
    因为第二遍的日次又从「第一日」重新数。所以这里按**一组签到的内容指纹**
    去重：把一组内所有命中项拼成字符串，同一指纹只算一次。
    """
    # 用 finditer 而不是 split：split 会把分隔符（「累计签到第N日」）**吃掉**，
    # 每段只剩下「：奖励内容」，日次信息就丢了（曾因此把每一段都当成新组）。
    marks = list(_AK_SIGNIN_SPLIT_RE.finditer(text))
    if not marks:
        return False, ""      # 没有签到段 → 明确没有签到类奖励

    groups: List[Dict[str, int]] = []
    current: Dict[str, int] = {}
    last_day = 0
    seen_fingerprints: set = set()

    for idx, mark in enumerate(marks):
        day_no = _ak_day_number(mark.group(0))
        # 段内容 = 本标记之后、下一个标记之前
        start = mark.end()
        end = marks[idx + 1].start() if idx + 1 < len(marks) else len(text)
        piece = text[start:end]

        # 每段只取「紧接着的那一小句」——后面可能还粘着许愿签档位表、
        # 组合包清单等无关内容，遇到这些标记就截断。
        segment = piece.split("。")[0]
        segment = segment.split("\n")[0]
        for marker in ("许愿签", "组合包内容", "售卖时间", "礼包"):
            cut = segment.find(marker)
            if cut >= 0:
                segment = segment[:cut]

        # 日次不再严格递增 = 换了一组新的签到（如「第十日」之后又见「第一日」）
        if current and day_no <= last_day:
            fp = tuple(sorted(current.items()))
            if fp and fp not in seen_fingerprints:
                seen_fingerprints.add(fp)
                groups.append(current)
            current = {}
        last_day = day_no

        for name, num in _AK_REWARD_ITEM_RE.findall(segment):
            hit = next((k for k in keywords if k in name), "")
            if not hit:
                continue
            key = "源石" if "源石" in name else hit
            current[key] = current.get(key, 0) + int(num)

    if current:
        fp = tuple(sorted(current.items()))
        if fp and fp not in seen_fingerprints:
            seen_fingerprints.add(fp)
            groups.append(current)

    if not groups:
        return False, ""
    # 一篇公告常同时挂多个签到活动（纯文本版 + HTML 版还会各来一遍）。
    # 报「总量」会重复计，所以取抽卡资源合计最多的那一组作为这篇的收益。
    best = max(groups, key=lambda g: sum(g.values()))
    parts = [f"{name} {cnt}" for name, cnt in best.items()]
    return True, "、".join(parts)


def _ak_day_number(head: str) -> int:
    """把「累计签到第三日」的汉字/数字日次转成 int；认不出就当 0。"""
    m = re.search(r"第([一二三四五六七八九十百\d]+)日", head)
    if not m:
        return 0
    raw = m.group(1)
    if raw.isdigit():
        return int(raw)
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    if raw == "十":
        return 10
    if raw.startswith("十"):
        return 10 + digits.get(raw[1:], 0)
    if "十" in raw:
        tens, _, ones = raw.partition("十")
        return digits.get(tens, 0) * 10 + (digits.get(ones, 0) if ones else 0)
    return digits.get(raw, 0)


def fetch_genshin_activities() -> List[Dict[str, Any]]:
    """原神 act_calendar：活动 + 固定挑战（渊月螺旋）。"""
    creds = _miyoushe_credentials("genshin")
    _ltuid, _ltoken, uid, server, _cookie = creds
    data = _miyoushe_post(GENSHIN_CAL_URL, {"role_id": uid, "server": server}, creds)

    now = datetime.now()
    result: List[Dict[str, Any]] = []
    for raw in data.get("act_list") or []:
        if not isinstance(raw, dict):
            continue
        start, end = _pick_time({
            "start_ts": raw.get("start_timestamp"), "end_ts": raw.get("end_timestamp"),
            "start_time": (raw.get("start_time") or {}).get("year") and _fmt_cn(raw.get("start_time")),
            "end_time": (raw.get("end_time") or {}).get("year") and _fmt_cn(raw.get("end_time")),
        })
        title = str(raw.get("name") or "").strip()
        if not title or not start or not end or end <= now:
            continue
        finished = bool(raw.get("is_finished"))
        keep, reward = _gacha_reward("genshin", raw)
        result.append({
            "game": "genshin", "game_name": "原神", "kind": "act", "title": title,
            "start": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end": end.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "已完成" if finished else "",
            "progress": "",
            "icon": _reward_icon(raw.get("reward_list")),
            "remote_id": str(raw.get("id") or ""),
            "reward": reward, "reward_ok": keep is not False,
        })

    for raw in data.get("fixed_act_list") or []:
        if not isinstance(raw, dict):
            continue
        start, end = _pick_time({
            "start_ts": raw.get("start_timestamp"), "end_ts": raw.get("end_timestamp"),
        })
        title = str(raw.get("name") or "").strip()
        if not title or not start or not end or end <= now:
            continue
        progress = ""
        tower = raw.get("tower_detail") or {}
        if isinstance(tower, dict):
            got, total = tower.get("star_num"), tower.get("total_star_num")
            if total:
                progress = f"{got or 0}/{total}"
        keep, reward = _gacha_reward("genshin", raw)
        result.append({
            "game": "genshin", "game_name": "原神", "kind": "challenge", "title": title,
            "start": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end": end.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "", "progress": progress,
            "icon": _reward_icon(raw.get("reward_list")),
            "remote_id": str(raw.get("id") or ""),
            "reward": reward, "reward_ok": keep is not False,
        })
    result.sort(key=lambda item: item["end"])
    return result


def _fmt_cn(node: Any) -> str:
    """{"year":2026,"month":11,"day":2,"hour":3,"minute":59,"second":0} -> 字符串。"""
    try:
        return "%04d-%02d-%02d %02d:%02d:%02d" % (
            int(node["year"]), int(node["month"]), int(node["day"]),
            int(node.get("hour", 0)), int(node.get("minute", 0)), int(node.get("second", 0)),
        )
    except (KeyError, TypeError, ValueError):
        return ""


def fetch_starrail_activities() -> List[Dict[str, Any]]:
    """星铁 get_act_calender：活动 + 挑战（忘却之庭等）。"""
    creds = _miyoushe_credentials("starrail")
    _ltuid, _ltoken, uid, server, _cookie = creds
    data = _miyoushe_get(STARRAIL_CAL_URL, {"role_id": uid, "server": server}, creds)

    now = datetime.now()
    result: List[Dict[str, Any]] = []
    for raw in data.get("act_list") or []:
        if not isinstance(raw, dict):
            continue
        start, end = _pick_time(raw.get("time_info"))
        title = str(raw.get("name") or "").strip()
        if not title or not start or not end or end <= now:
            continue
        status_map = {
            "OtherActStatusUnFinish": "",
            "OtherActStatusFinish": "已完成",
            "DoubleRewardActStatusUnopened": "未开启",
            "OtherActStatusUnopened": "未开启",
        }
        progress = ""
        total, current = raw.get("total_progress"), raw.get("current_progress")
        if isinstance(total, int) and total > 0:
            progress = f"{current or 0}/{total}"
        keep, reward = _gacha_reward("starrail", raw)
        result.append({
            "game": "starrail", "game_name": "崩坏：星穹铁道", "kind": "act", "title": title,
            "start": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end": end.strftime("%Y-%m-%d %H:%M:%S"),
            "status": status_map.get(str(raw.get("act_status") or ""), ""),
            "progress": progress,
            "icon": _reward_icon(raw.get("reward_list")),
            "remote_id": str(raw.get("id") or ""),
            "reward": reward, "reward_ok": keep is not False,
        })

    for raw in data.get("challenge_list") or []:
        if not isinstance(raw, dict):
            continue
        start, end = _pick_time(raw.get("time_info"))
        title = str(raw.get("name_mi18n") or raw.get("name") or "").strip()
        if not title or not start or not end or end <= now:
            continue
        progress = ""
        total, current = raw.get("total_progress"), raw.get("current_progress")
        if isinstance(total, int) and total > 0:
            progress = f"{current or 0}/{total}"
        keep, reward = _gacha_reward("starrail", raw)
        result.append({
            "game": "starrail", "game_name": "崩坏：星穹铁道", "kind": "challenge", "title": title,
            "start": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end": end.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "", "progress": progress,
            "icon": "",
            "remote_id": str(raw.get("group_id") or raw.get("challenge_type") or title),
            "reward": reward, "reward_ok": keep is not False,
        })
    result.sort(key=lambda item: item["end"])
    return result


def fetch_zzz_activities() -> List[Dict[str, Any]]:
    """绝区零 activity_calendar：参数是 uid / region，不是 role_id / server。"""
    creds = _miyoushe_credentials("zzz")
    _ltuid, _ltoken, uid, server, _cookie = creds
    data = _miyoushe_get(ZZZ_CAL_URL, {"uid": uid, "region": server}, creds)

    now = datetime.now()
    result: List[Dict[str, Any]] = []
    for raw in data.get("activity_list") or []:
        if not isinstance(raw, dict):
            continue
        start, end = _from_ts(raw.get("start_ts")), _from_ts(raw.get("end_ts"))
        title = str(raw.get("name") or "").strip()
        if not title or not start or not end or end <= now:
            continue
        progress = ""
        total, got = raw.get("monochrome_cnt"), raw.get("monochrome_got_cnt")
        if isinstance(total, int) and total > 0:
            progress = f"{got or 0}/{total}"
        state = str(raw.get("state") or "")
        status = "已完成" if state == "STATE_FINISHED" else ""
        keep, reward = _gacha_reward("zzz", raw)
        result.append({
            "game": "zzz", "game_name": "绝区零", "kind": "act", "title": title,
            "start": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end": end.strftime("%Y-%m-%d %H:%M:%S"),
            "status": status, "progress": progress,
            "icon": "",
            "remote_id": str(raw.get("activity_id") or ""),
            "reward": reward, "reward_ok": keep is not False,
        })
    result.sort(key=lambda item: item["end"])
    return result


# --------------------------------------------------------------------------- #
# 明日方舟（鹰角官网）
# --------------------------------------------------------------------------- #


def _new_session():
    """建一个直连会话：本机系统代理会拦部分外网，trust_env=False 更稳。"""
    import requests

    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": BROWSER_UA})
    return session


def _fetch_detail_window(session, url: str, base: datetime) -> Tuple[Optional[Tuple[datetime, datetime]], str]:
    """抓公告正文，返回 (活动时间窗, 纯文本正文)。

    正文同时用来找「合成玉 / 源石」——一次请求办两件事，别为了奖励再抓一遍。
    时间窗拿不到就是 None（不猜）。
    """
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - 单条详情失败不影响其它活动
        logger.debug("读取活动正文失败 %s：%s", url, exc)
        return None, ""
    body = _clean_text(response.text)
    return _window_from_text(body, base), body


def fetch_arknights_activities() -> List[Dict[str, Any]]:
    """明日方舟：鹰角官网 news 接口（tab=1 是活动），起止时间解析自正文。"""
    session = _new_session()
    response = session.get(
        AK_NEWS_URL,
        params={"lang": "zh-cn", "page": 1, "pageSize": 20},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") not in (0, None):
        raise RuntimeError(f"官网新闻接口返回 code={payload.get('code')}")

    now = datetime.now()
    result: List[Dict[str, Any]] = []
    checked = 0
    for item in (payload.get("data") or {}).get("list") or []:
        if not isinstance(item, dict) or str(item.get("tab")) != "1":
            continue  # tab: 0=公告 1=活动 2=制作组通讯
        title = _clean_ak_title(item.get("title"))
        cid = str(item.get("cid") or "").strip()
        if not title or not cid:
            continue
        if checked >= AK_DETAIL_LIMIT:
            break
        checked += 1
        published = now
        stamp = item.get("displayTime")
        if isinstance(stamp, (int, float)) and stamp > 0:
            try:
                published = datetime.fromtimestamp(int(stamp))
            except (OSError, ValueError, OverflowError):
                published = now
        window, body = _fetch_detail_window(session, AK_NEWS_PAGE % cid, published)
        if not window:
            logger.debug("方舟活动正文里没有明确的起止时间，跳过：%s", title)
            continue
        start, end = window
        if end <= now:
            continue
        keep, reward = _gacha_reward("arknights", {}, body)
        result.append({
            "game": "arknights", "game_name": "明日方舟", "kind": "act", "title": title,
            "start": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end": end.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "", "progress": "", "icon": "",
            "remote_id": cid,
            "reward": reward, "reward_ok": keep is not False,
        })
    result.sort(key=lambda item: item["end"])
    return result


#: 终末地：官方没有活动日历接口（公告不带截止时间），社区的做法是吃
#: fz.wiki「活动」文章——编辑者维护的结构化日程（open/close 直出），
#: 零启发式，比方舟的正文正则干净得多。数据质量依赖 wiki 维护。
ENDFIELD_WIKI_API = "https://api.fz.wiki/api/v1/articles/by-title"
_ENDFIELD_ITEM_TYPE = "endfieldCardActivityIndex__activities"


def _parse_wiki_time(text: Any) -> Optional[datetime]:
    """fz.wiki 时刻格式："2026/7/16 7:00:00"（月/日/时不补零）。"""
    try:
        return datetime.strptime(str(text).strip(), "%Y/%m/%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def _endfield_activity_nodes(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从文章修订里挖 endfieldCardActivityIndex 的活动节点。

    上游有两种历史形态：legacy 把字段直接挂在 attrs 上，current 挪进了
    attrs.data——用 node.type 或 attrs.itemType 识别后统一解包 attrs.data。
    """
    content = (((payload.get("revision") or {}).get("contentJson") or {}).get("content")) or []
    index_node = next(
        (n for n in content
         if isinstance(n, dict) and n.get("type") == "endfieldCardActivityIndex"),
        None,
    )
    children = (index_node or {}).get("content") or []
    nodes: List[Dict[str, Any]] = []
    for child in children:
        if not isinstance(child, dict):
            continue
        attrs = child.get("attrs") or {}
        if (child.get("type") != _ENDFIELD_ITEM_TYPE
                and attrs.get("itemType") != _ENDFIELD_ITEM_TYPE):
            continue
        data = attrs.get("data")
        node = data if isinstance(data, dict) else attrs
        if isinstance(node, dict) and node.get("name"):
            nodes.append(node)
    return nodes


def fetch_endfield_activities() -> List[Dict[str, Any]]:
    """终末地：fz.wiki「活动」日程 → 活动列表（已结束的跳过）。"""
    session = _new_session()
    response = session.get(
        ENDFIELD_WIKI_API,
        params={"ns": "0", "title": "活动", "withRevision": "1"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()

    now = datetime.now()
    result: List[Dict[str, Any]] = []
    for act in _endfield_activity_nodes(payload):
        calendar_range = act.get("calendarRange") or {}
        window = (calendar_range
                  if isinstance(calendar_range, dict) and calendar_range.get("open")
                  else (act.get("timeRanges") or [{}])[0])
        if not isinstance(window, dict) or not window.get("open"):
            continue
        start = _parse_wiki_time(window.get("open"))
        end = _parse_wiki_time(window.get("close"))
        if start is None:
            continue
        if end is not None and end <= now:
            continue  # 已结束：不展示、也不提醒
        if end is None:
            continue  # 常驻类（无截止时间）：没有"结束"可提醒，进卡只会永远占位
        title = _clean_text(act.get("calendarTitle") or act.get("name"))
        if not title:
            continue
        # 2026-11-05：fz.wiki 的活动节点**没有任何奖励字段**（只有 activityId/
        # calendarTitle/tags/timeRanges 这些），挖不到「送多少」。所以这一格不
        # 留空、也不编数字，改放**活动类型标签**（限时活动/限时签到/趣味活动），
        # 与其它四款游戏的「原石 420」占同一个位置，视觉上对齐。
        tag = _clean_text(act.get("calendarTag") or "")
        if not tag:
            tags = act.get("tags")
            if isinstance(tags, list) and tags:
                tag = _clean_text(str(tags[0]))
        result.append({
            "game": "endfield", "game_name": "终末地", "kind": "act", "title": title,
            "start": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end": end.strftime("%Y-%m-%d %H:%M:%S") if end else "",
            "status": "", "progress": "", "icon": "",
            "remote_id": str(act.get("activityId") or title),
            "reward": tag, "reward_ok": True,
        })
    result.sort(key=lambda item: item["end"])
    return result


# --------------------------------------------------------------------------- #
# 鸣潮：库洛官方游戏内公告 CDN（免鉴权、结构化毫秒时间戳）。
# notice.json 含 game（游戏内公告全量）与 activity（精选）两组，每条带
# startTimeMs/endTimeMs；详情正文在 ``{contentPrefix}zh-Hans.json`` 的
# textContent（HTML 富文本，奖励写在文字里）。奖励信息全社区无结构化源
# （2026-11-09 普查：game-schedule/game-cal 都只有标题+时间）——与方舟
# 「正文挖取」同路径。库洛 wiki 首页接口（版本活动侧栏）作交叉验证源，
# v1 不接：与 notice.json 高度重叠，双源去重复杂度不值。
# --------------------------------------------------------------------------- #
WUWA_NOTICE_URL = (
    "https://aki-gm-resources-back.aki-game.com/gamenotice/"
    "G152/76402e5b20be2c39f095a152090afddc/notice.json"
)
#: 详情正文最多抓多少条（20h 一次，控请求数；过滤后通常 ≤10）
WUWA_DETAIL_LIMIT = 12
#: 纯说明类标题（版本预告/已知问题这类没有"活动可参与"语义的）。
#: 注意别把「XX活动说明」误伤——那是真活动的说明页（带真实截止时间）。
WUWA_TITLE_BLOCKLIST = (
    "已知问题", "更新说明", "更新维护预告", "防沉迷", "反馈途径",
    "公平运营", "资源分级", "先行体验", "玩家社区资讯", "签到工具",
    "版本内容说明", "正式上线说明", "客户端资源",
)


def _wuwa_enabled() -> bool:
    """绑定了鸣潮账号且巡检开关开着才抓——没玩鸣潮的用户不该收到其活动提醒。

    活动数据本身免鉴权，但「接不接」跟账号绑定走（与其它游戏的活动
    出现节奏一致：绑定即有，关开关即消失）。
    """
    from core.config import load_config

    try:
        config = load_config(USER_DIR / "config.json", auto_create=False)
    except Exception:  # noqa: BLE001 - 配置读不到就别抓
        return False
    return any(a.game == "wuwa" and a.enabled for a in config.accounts)


def _wuwa_detail_text(session: Any, prefixes: Any) -> str:
    """取公告详情正文（contentPrefix 是多 CDN 候选列表，取第一个成功的）。"""
    if not isinstance(prefixes, list):
        return ""
    for prefix in prefixes:
        if not isinstance(prefix, str) or not prefix:
            continue
        try:
            resp = session.get(
                prefix.rstrip("/") + "/zh-Hans.json", timeout=REQUEST_TIMEOUT
            )
            if resp.status_code != 200:
                continue
            payload = resp.json()
            text = payload.get("textContent")
            if isinstance(text, str) and text:
                return text
        except Exception:  # noqa: BLE001 - 单条详情失败不影响整体
            continue
    return ""


def fetch_wuwa_activities() -> List[Dict[str, Any]]:
    """鸣潮：官方公告 CDN → 活动列表（已结束/常驻/说明类跳过）。"""
    if not _wuwa_enabled():
        return []
    session = _new_session()
    response = session.get(WUWA_NOTICE_URL, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()

    now = datetime.now()
    seen_ids: set = set()
    detail_checked = 0
    result: List[Dict[str, Any]] = []
    for group in ("activity", "game"):
        for it in payload.get(group) or []:
            if not isinstance(it, dict):
                continue
            rid = str(it.get("id") or "")
            if not rid or rid in seen_ids:
                continue
            seen_ids.add(rid)
            if it.get("permanent") == 1:
                continue  # 常驻说明（防沉迷/公平运营这类，一年期时间戳）
            try:
                start = datetime.fromtimestamp(int(it.get("startTimeMs")) / 1000)
                end = datetime.fromtimestamp(int(it.get("endTimeMs")) / 1000)
            except (TypeError, ValueError, OSError, OverflowError):
                continue
            if end <= now:
                continue  # 已结束：不展示、也不提醒
            if start == end:
                continue  # 假时间占位（社区资讯/签到工具那批）
            tt = it.get("tabTitle") or {}
            title = _clean_text(tt.get("zh-Hans") if isinstance(tt, dict) else tt)
            if not title:
                continue
            if it.get("tag") == 11 or it.get("tag") == 10:
                continue  # 周边预售 / 版本活动日历（meta 条目，非活动）
            if any(bad in title for bad in WUWA_TITLE_BLOCKLIST):
                continue
            # 奖励挖取：详情正文找「星声 N」；拿不到就按兜底保留
            reward, reward_ok = "奖励未公开", True
            if detail_checked < WUWA_DETAIL_LIMIT:
                detail_checked += 1
                body = _wuwa_detail_text(session, it.get("contentPrefix"))
                if body:
                    # 正文是 HTML：「530</span>星声」这种数字与币种被标签隔断，
                    # 剥标签后再挖，否则数字全丢、只剩纯提及
                    keep, reward = _gacha_reward(
                        "wuwa", {}, re.sub(r"<[^>]+>", " ", body))
                    reward_ok = keep is not False
            result.append({
                "game": "wuwa", "game_name": "鸣潮", "kind": "act", "title": title,
                "start": start.strftime("%Y-%m-%d %H:%M:%S"),
                "end": end.strftime("%Y-%m-%d %H:%M:%S"),
                "status": "", "progress": "", "icon": "",
                "remote_id": rid,
                "reward": reward, "reward_ok": reward_ok,
            })
    result.sort(key=lambda item: item["end"])
    return result


# --------------------------------------------------------------------------- #
# 战双：库街区 wiki 首页「热门活动」侧栏（Wiki_type=2，免鉴权）。
# countDown.dateRange 直出 [开始, 结束] 字符串——与鸣潮 notice.json 同级的
# 结构化源。jacket-sikaha/game-schedule 的 getPunishingEvent 同款端点。
# --------------------------------------------------------------------------- #
PGR_WIKI_API = "https://api.kurobbs.com/wiki/core/homepage/getPage"
PGR_WIKI_TYPE = "2"          # 战双=2、鸣潮=9
PGR_TARGET_TITLE = "热门活动"


def fetch_pgr_activities() -> List[Dict[str, Any]]:
    """战双：wiki 首页侧栏 → 活动列表（已结束的跳过）。"""
    if not _pgr_enabled():
        return []
    session = _new_session()
    response = session.post(
        PGR_WIKI_API,
        headers={"Wiki_type": PGR_WIKI_TYPE},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    mods = (((payload.get("data") or {}).get("contentJson")) or {}).get("sideModules") or []
    target = next((m for m in mods if m.get("title") == PGR_TARGET_TITLE), None)
    if not target:
        return []

    now = datetime.now()
    result: List[Dict[str, Any]] = []
    for it in target.get("content") or []:
        if not isinstance(it, dict):
            continue
        cd = it.get("countDown") or {}
        rng = cd.get("dateRange") or []
        if len(rng) != 2:
            continue  # 无倒计时的常驻入口
        start = _parse_kuro_time(rng[0])
        end = _parse_kuro_time(rng[1])
        if start is None or end is None or end <= now:
            continue
        title = _clean_text(it.get("title"))
        if not title:
            continue
        entry_id = str((it.get("linkConfig") or {}).get("entryId") or "")
        result.append({
            "game": "pgr", "game_name": "战双帕弥什", "kind": "act", "title": title,
            "start": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end": end.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "", "progress": "", "icon": "",
            "remote_id": entry_id or title,
            "reward": "", "reward_ok": True,   # wiki 侧栏无奖励字段（未公开口径）
        })
    result.sort(key=lambda item: item["end"])
    return result


def _pgr_enabled() -> bool:
    """绑定了战双（库街区）账号且巡检开关开着才抓。"""
    from core.config import load_config

    try:
        config = load_config(USER_DIR / "config.json", auto_create=False)
    except Exception:  # noqa: BLE001
        return False
    return any(a.game == "pgr" and a.enabled for a in config.accounts)


def _parse_kuro_time(text: Any) -> Optional[datetime]:
    """库洛 wiki 时刻格式："2026-09-30 11:00"（分钟精度）。"""
    raw = str(text or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except (ValueError, TypeError):
            continue
    return None


#: 每个抓取器对应的来源说明，写进缓存的 source 字段方便排查
SOURCES: Dict[str, str] = {
    "genshin": "miyoushe act_calendar",
    "starrail": "miyoushe get_act_calender",
    "zzz": "miyoushe activity_calendar",
    "arknights": "ak.hypergryph.com news(tab=1)+正文",
    "endfield": "fz.wiki 活动（wikiCardActivityIndex 结构化日程）",
    "wuwa": "库洛官方公告 CDN notice.json（毫秒时间戳+详情正文挖奖励）",
    "pgr": "库街区 wiki 首页热门活动侧栏（countDown.dateRange）",
}

#: 新增游戏时在这里挂一个 fetch 函数即可。
FETCHERS = {
    "genshin": fetch_genshin_activities,
    "starrail": fetch_starrail_activities,
    "zzz": fetch_zzz_activities,
    "arknights": fetch_arknights_activities,
    "endfield": fetch_endfield_activities,
    "wuwa": fetch_wuwa_activities,
    "pgr": fetch_pgr_activities,
}


def _merge_notified_and_save(cache: Dict[str, Any]) -> None:
    """落盘前重读磁盘合并 notified，再原子写。

    refresh 的网络抓取花数秒，期间守护进程可能已 mark_notified（跨进程无锁）——
    直接 save_cache(进锁前的旧快照) 会把去重标记整份回滚，同一活动重复推送。
    合并以磁盘为准（磁盘上的标记更新），本地旧快照只补磁盘没有的键。
    2026-10-31：合并与落盘整体置于跨进程文件锁内（core/ipc_lock）——
    mark_notified 同锁内「锁内重读 → 只改 notified → 落盘」，items 不再可能回滚。
    """
    with ipc_lock.blocking(str(CACHE_PATH) + ".lock"):
        fresh = load_cache()
        merged = {**(cache.get("notified") or {}), **(fresh.get("notified") or {})}
        cache["notified"] = _prune_notified(merged)
        save_cache(cache)


def refresh(force: bool = False) -> Dict[str, Any]:
    """刷新缓存（默认 20 小时内不重复请求）。失败保留旧数据，只记 last_error。"""
    cache = load_cache()
    age = _cache_age_hours(cache.get("fetched_at", ""))
    if not force and cache.get("version") == CACHE_VERSION and age is not None and age < CACHE_TTL_HOURS:
        return summary()

    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    collected: List[Dict[str, Any]] = []
    errors: List[str] = []
    for game_id, fetcher in FETCHERS.items():
        try:
            collected.extend(fetcher())
        except Exception as exc:  # noqa: BLE001 - 单个游戏失败不能影响其它游戏
            logger.warning("拉取 %s 活动日历失败：%s", game_id, exc)
            errors.append(f"{game_id}: {exc}")

    cache["last_attempt"] = now_text
    if errors and not collected:
        cache["last_error"] = "；".join(errors)
        _merge_notified_and_save(cache)
        logger.warning("活动缓存未更新（沿用旧数据）：%s", cache["last_error"])
        return summary()

    # 只留给抽卡资源的活动。拿不到奖励数据的（reward_ok=True）一律保留，
    # 宁可多看一条，也不能因为接口少个字段就把大活动藏了。
    kept: List[Dict[str, Any]] = []
    stats: Dict[str, Dict[str, int]] = {}
    for item in collected:
        bucket = stats.setdefault(str(item.get("game") or ""), {"total": 0, "kept": 0})
        bucket["total"] += 1
        if item.pop("reward_ok", True) is False:
            continue
        bucket["kept"] += 1
        kept.append(item)
    dropped = len(collected) - len(kept)

    # 黑名单只做「读时过滤」：缓存里保留全量，
    # 这样点「恢复全部」能立刻回来，不用等下一轮重新拉接口。
    cache["version"] = CACHE_VERSION
    cache["items"] = kept
    cache["stats"] = stats
    cache["filtered_count"] = dropped
    cache["fetched_at"] = now_text
    cache["source"] = dict(SOURCES)
    if errors:
        cache["last_error"] = "部分游戏拉取失败：" + "；".join(errors)
    else:
        cache.pop("last_error", None)
    _merge_notified_and_save(cache)
    ignored = _ignored_ids()
    hidden = sum(
        1 for item in kept
        if f"{item['game']}:{item.get('kind', 'act')}:{item.get('remote_id') or item['title']}" in ignored
    )
    logger.info(
        "活动日历已更新：%d 个带抽卡资源（过滤掉 %d 个材料活动，%d 个在黑名单里不展示）",
        len(kept), dropped, hidden,
    )
    return summary()


def ensure_fresh() -> Dict[str, Any]:
    """定时检查每轮调用：缓存过期才真的去拉，异常一律吞掉。"""
    try:
        return refresh(force=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("检查活动缓存失败（不影响本轮提醒）：%s", exc)
        return summary()


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #


#: 排序时给「时间缺失」的占位值：保证它们落在所在分组的最后面
_MAX_TIME = datetime.max


def _activity_sort_key(act: Activity) -> Tuple[int, datetime, datetime]:
    """第一组（进行中）按结束时间升序，第二组（未开启）按开启时间升序。"""
    if act.group == "upcoming":
        return (1, act.start or _MAX_TIME, act.end or _MAX_TIME)
    return (0, act.end or _MAX_TIME, act.start or _MAX_TIME)


def ongoing() -> List[Activity]:
    """还没结束的活动，已按界面顺序排好。

    顺序 = 进行中（结束时间由近到远）→ 未开启（开启时间由近到远）；
    已结束的直接丢掉，时间缺失的排在所在分组最后。
    """
    cache = load_cache()
    now = datetime.now()
    ignored = _ignored_ids()
    result: List[Activity] = []
    for item in cache.get("items") or []:
        if not isinstance(item, dict):
            continue
        start = _parse_time(item.get("start"))
        end = _parse_time(item.get("end"))
        if end is not None and end <= now:
            continue  # 已结束：不展示、也不提醒
        activity = Activity(
            game_id=str(item.get("game") or ""),
            game_name=str(item.get("game_name") or ""),
            title=str(item.get("title") or ""),
            start=start,
            end=end,
            kind=str(item.get("kind") or "act"),
            status=str(item.get("status") or ""),
            progress=str(item.get("progress") or ""),
            icon=str(item.get("icon") or ""),
            remote_id=str(item.get("remote_id") or ""),
            reward=str(item.get("reward") or ""),
        )
        if activity.uid in ignored:
            continue  # 黑名单兜底：即使缓存里还在，也不让它出现
        result.append(activity)
    result.sort(key=_activity_sort_key)
    return result


def pending_alerts(
    hours: int = ALERT_WINDOW_HOURS,
    *,
    today: Optional[datetime] = None,
) -> List[ActivityAlert]:
    """剩余 <= hours 且已开始的活动，今天还没提醒过的。"""
    # 「今天」按 04:00 游戏日界算（game_moment 唯一入口）——用真实日期的话，
    # 跨零点补发/深夜手动巡检会拿到空去重集，同一活动几小时内推两条。
    from core.history import game_moment

    day_key = (today or game_moment()).strftime("%Y-%m-%d")
    cache = load_cache()
    raw_notified = cache.get("notified", {}).get(day_key, [])
    notified = set(raw_notified) if isinstance(raw_notified, list) else set()
    alerts: List[ActivityAlert] = []
    for act in ongoing():
        if act.not_started:
            continue
        left = act.hours_left
        if not (0 < left <= hours):
            continue
        if act.uid in notified:
            continue
        alerts.append(ActivityAlert(
            game_id=act.game_id, game_name=act.game_name, title=act.title,
            end=act.end, uid=act.uid,
        ))
    return alerts


@dataclass
class ActivityStartAlert:
    """活动开始提醒（推送正文用）——「别错过」与结束提醒的止损互补。

    去重与结束提醒同表不同名：uid 带 ``:start`` 后缀，同一活动
    「开始推一次、临结束再推一次」互不挤占。
    """

    game_id: str
    game_name: str
    title: str
    start: datetime
    uid: str = ""

    def warn_line(self) -> str:
        """🆕 活动上新：原神「XX」，现已开始，快去看看！"""
        needs_quote = "「" not in self.title and "」" not in self.title
        shown = f"「{self.title}」" if needs_quote else self.title
        return f"🆕 活动上新：{self.game_name}{shown} 现已开始，快去看看！"


def pending_start_alerts(*, window_hours: int = 24) -> List[ActivityStartAlert]:
    """刚开始的活动提醒——活动开始后第一个巡检窗口就会推。

    触发窗口：start 落在最近 window_hours 小时内（巡检在活动开始后 30 分钟
    到半天不等，窗口太窄会漏）。去重是**终身制**的：uid（带 :start 后缀）
    一旦记过就永远不再推——结束提醒的「每天可重复」语义不适用于开始提醒。
    """
    cache = load_cache()
    notified_all = set()
    for day_list in (cache.get("notified") or {}).values():
        if isinstance(day_list, list):
            notified_all.update(day_list)

    now = datetime.now()
    window_start = now - timedelta(hours=window_hours)
    alerts: List[ActivityStartAlert] = []
    for act in ongoing():
        if act.start is None or act.not_started or act.start < window_start:
            continue
        uid = act.uid + ":start"
        if uid in notified_all:
            continue
        alerts.append(ActivityStartAlert(
            game_id=act.game_id, game_name=act.game_name, title=act.title,
            start=act.start, uid=uid,
        ))
    return alerts


def mark_notified(alerts: Iterable[ActivityAlert], *, today: Optional[datetime] = None) -> None:
    """把今天已经推送过的活动记下来，避免同一天每轮检查都催一遍。"""
    # 与 pending_alerts 同口径：04:00 游戏日界（见其注释）
    from core.history import game_moment

    day_key = (today or game_moment()).strftime("%Y-%m-%d")
    ids = [alert.uid for alert in alerts if alert.uid]
    if not ids:
        return
    # 跨进程锁：daemon（mark_notified）与控制台（refresh）写同一缓存文件，
    # 线程锁只防进程内。锁内重读 → 只改 notified → 落盘，items 零回滚窗口
    with _IGNORED_WRITE_LOCK, ipc_lock.blocking(str(CACHE_PATH) + ".lock"):
        cache = load_cache()
        notified = cache.get("notified") or {}
        if not isinstance(notified, dict):
            notified = {}
        raw_current = notified.get(day_key)
        current = list(raw_current) if isinstance(raw_current, list) else []
        for uid in ids:
            if uid not in current:
                current.append(uid)
        notified[day_key] = current
        cache["notified"] = _prune_notified(notified)
        try:
            save_cache(cache)
        except OSError as exc:
            logger.warning("记录活动提醒失败（下次可能重复提醒）：%s", exc)


def _prune_notified(notified: Dict[str, Any]) -> Dict[str, Any]:
    cutoff = datetime.now() - timedelta(days=NOTIFIED_KEEP_DAYS)
    kept: Dict[str, Any] = {}
    for key, value in notified.items():
        moment = _parse_time(str(key) + " 00:00:00")
        if moment is None or moment >= cutoff:
            kept[key] = value
    return kept


# --------------------------------------------------------------------------- #
# 结束推送·三段梯度（2026-11-11 第一队列 #9，用户裁定：三段独立、-1 关闭）
# --------------------------------------------------------------------------- #

#: 三段默认档（小时）——动作节点分层：规划(72h)→行动(24h)→召回(6h)。
#: 运行时以 AppConfig.activity_deadline_tiers 为准（main 每次显式传入）；
#: 本常量仅供独立调用/测试兜底，与 core/config 的默认档保持同值。
DEADLINE_TIERS_DEFAULT = [72, 24, 6]


@dataclass
class ActivityTierAlert:
    """活动结束·三段梯度提醒（推送正文用）。

    每档**终身只推一次**；迟过越线（停机跨过整档窗口）的档在下次巡检当场
    补发一次——「只早不迟：迟到走补发」的用户裁决。旧「每日可重复」结束
    提醒由三段取代：每活动最多三条，防轰炸从源头收敛。
    文案按动作节点分层（模仿航班值机/证书到期阶梯）：规划=安排时间、
    行动=今天该打、召回=马上就没了。
    """

    game_id: str
    game_name: str
    title: str
    end: datetime
    tier: int              # 档位序号 1..3（对应 tiers 列表下标 +1）
    threshold: int         # 该档阈值（小时）
    hours_left: float
    uid: str = ""

    def _shown(self) -> str:
        needs_quote = "「" not in self.title and "」" not in self.title
        return f"「{self.title}」" if needs_quote else self.title

    def _left_text(self) -> str:
        hours = max(self.hours_left, 0.0)
        if hours >= 48:
            days = max(int(math.ceil(hours / 24.0)), 1)
            return f"{days} 天"
        return f"{int(hours)}h{int(hours * 60) % 60:02d}m"

    def warn_line(self) -> str:
        left = self._left_text()
        if self.tier == 1:
            return (f"🗓️ 活动规划：{self.game_name}{self._shown()} 约 {left}后结束"
                    f"（第 1 档·规划），安排好时间")
        if self.tier == 2:
            return (f"⏳ 活动行动：{self.game_name}{self._shown()} 约 {left}后结束"
                    f"（第 2 档·行动），今天该打了")
        return (f"🔔 最后冲刺：{self.game_name}{self._shown()} 仅剩约 {left}"
                f"（第 3 档·冲刺），想拿奖励抓紧了")


def pending_deadline_alerts(
    tiers: Optional[List[int]] = None,
) -> List[ActivityTierAlert]:
    """三段梯度结束提醒：越线即推、每档终身一次，迟过的档当场补发。

    「只早不迟」落地：目标时刻 = 结束前 threshold 小时，巡检在越线后的
    第一个 tick 落地（最迟≈轮询周期）；-1 = 该档关闭（用户裁决）。
    去重键 = ``uid:档位``，存缓存 ``tier_fired`` 终身账本（prune 同 notified）。
    """
    if tiers is None:
        tiers = list(DEADLINE_TIERS_DEFAULT)
    cache = load_cache()
    raw_fired = cache.get("tier_fired")
    fired = raw_fired if isinstance(raw_fired, dict) else {}
    alerts: List[ActivityTierAlert] = []
    for act in ongoing():
        if act.not_started or act.end is None:
            continue
        left = act.hours_left
        if left <= 0:
            continue
        for idx, threshold in enumerate(tiers[:3], start=1):
            if not isinstance(threshold, int) or threshold < 0:
                continue  # -1 / 非法 = 该档不提醒
            if left > threshold:
                continue
            key = f"{act.uid}:{idx}"
            if fired.get(key):
                continue
            alerts.append(ActivityTierAlert(
                game_id=act.game_id, game_name=act.game_name, title=act.title,
                end=act.end, tier=idx, threshold=threshold,
                hours_left=left, uid=act.uid,
            ))
    return alerts


def mark_tier_notified(alerts: Iterable[ActivityTierAlert]) -> None:
    """把已推的（活动, 档位）记进终身账本 tier_fired——锁纪律同 mark_notified。"""
    ids = [f"{a.uid}:{a.tier}" for a in alerts if a.uid]
    if not ids:
        return
    with _IGNORED_WRITE_LOCK, ipc_lock.blocking(str(CACHE_PATH) + ".lock"):
        cache = load_cache()
        raw = cache.get("tier_fired")
        fired = raw if isinstance(raw, dict) else {}
        stamp = datetime.now().strftime("%Y-%m-%d")
        for key in ids:
            fired[key] = stamp
        cache["tier_fired"] = _prune_tier_fired(fired)
        try:
            save_cache(cache)
        except OSError as exc:
            logger.warning("记录梯度提醒失败（下次可能重复提醒）：%s", exc)


def _prune_tier_fired(fired: Dict[str, Any]) -> Dict[str, Any]:
    cutoff = datetime.now() - timedelta(days=NOTIFIED_KEEP_DAYS)
    kept: Dict[str, Any] = {}
    for key, value in fired.items():
        moment = _parse_time(str(value) + " 00:00:00")
        if moment is None or moment >= cutoff:
            kept[key] = value
    return kept


def summary(
    *,
    window_hours: Optional[int] = None,
    tiers: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """给界面/接口用的汇总：活动（已分好组）+ 暂不支持的游戏 + 缓存状态。"""
    cache = load_cache()
    fetched_at = str(cache.get("fetched_at") or "")
    age = _cache_age_hours(fetched_at)
    _acts = ongoing()
    activities = [act.to_dict() for act in _acts]
    # 三段与英雄卡关联（2026-11-11 用户裁定）：每张卡带 tiers_set（已设档位）
    # 与 tiers_fired（已推档位），前端据此渲染段位标记与打勾
    raw_fired = cache.get("tier_fired")
    fired_map = raw_fired if isinstance(raw_fired, dict) else {}
    tiers_set = [int(t) for t in (tiers if tiers is not None else DEADLINE_TIERS_DEFAULT)][:3]
    for act, item in zip(_acts, activities):
        item["tiers_set"] = tiers_set
        item["tiers_fired"] = [
            i for i in (1, 2, 3) if fired_map.get(f"{act.uid}:{i}")
        ]
    # 「即将结束」徽章口径跟用户设置的提醒天数走（2026-11-07 用户裁决）：
    # 传入 window_hours 时按它重算 ending_soon，不依赖缓存时定格的 72h 固定值
    window = window_hours if (window_hours and window_hours > 0) else ALERT_WINDOW_HOURS
    if window != ALERT_WINDOW_HOURS:
        for act in activities:
            hours = act.get("hours_left")
            act["ending_soon"] = bool(
                act.get("end") is not None
                and isinstance(hours, (int, float)) and 0 < hours <= window
                and not act.get("not_started"))
    soon = [item for item in activities if item.get("ending_soon")]
    ignored = load_ignored()
    upcoming = [item for item in activities if item.get("group") == "upcoming"]
    return {
        "ok": True,
        "tiers_set": tiers_set,
        "has_data": bool(fetched_at),
        "fetched_at": fetched_at,
        "stale": (age is None) or (age >= CACHE_TTL_HOURS),
        "age_hours": round(age, 1) if age is not None else None,
        "last_error": str(cache.get("last_error") or ""),
        "window_hours": window,
        "items": activities,
        "unsupported": [dict(item) for item in UNSUPPORTED],
        "soon_count": len(soon),
        "ongoing_count": len(activities) - len(upcoming),
        "upcoming_count": len(upcoming),
        "filtered_count": int(cache.get("filtered_count") or 0),
        "stats": cache.get("stats") or {},
        "ignored_count": len(ignored.get("ids") or []),
    }

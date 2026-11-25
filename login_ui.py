#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图形化扫码登录界面（本地 Web 服务，只监听 127.0.0.1）。

选哪些游戏就登录哪些：
  - 米游社：一次扫码，勾选的游戏同时更新（当然可以只勾一款）
  - 森空岛：每款需要单独扫码，勾几款就依次扫几次

为什么用网页而不是 tkinter：
  - 二维码要足够大才能扫得稳，浏览器缩放/居中天然合适；
  - 只需要标准库 + 已有的 qrcode / pillow，不新增任何依赖；
  - 手机扫码时电脑屏幕不用凑近看。

用法：
    python login_ui.py                  # 启动并自动打开浏览器
    python login_ui.py --port 8800      # 指定端口
    python login_ui.py --no-browser     # 不自动开浏览器

登录成功后直接写 config.json，只动被勾选的游戏。
"""

from __future__ import annotations

import argparse
import base64
import functools
import json
import logging
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urlparse

# engine 发行版布局锚点修复，判据与 core.paths.app_root 一致（见彼处注释）
_BASE = Path(__file__).resolve().parent
BASE_DIR = _BASE.parent if _BASE.name == "engine" else _BASE
# sys.path 保持「代码=本脚本所在树」：engine 发行版导入 engine\ 下的模块。
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

# 数据根与程序根分离（2026-11-05 数据外置）：config/data/logs 落
# %LOCALAPPDATA%\HopOn。ensure_layout() 幂等，顺带完成旧布局迁移。
from core.paths import ensure_layout as _ensure_layout, user_root as _user_root  # noqa: E402

try:
    _LAYOUT = _ensure_layout(allow_migrate=True)
    # 旧版自启键清理（2026-11-06）：改名前注册的 ResinLoop Run 键若残留，
    # 开机会新旧两套同时拉起抢 8765——首启无条件移除（幂等、失败忽略）
    try:
        from core.autostart import cleanup_legacy_autostart

        cleanup_legacy_autostart()
    except Exception:  # noqa: BLE001 - 清理失败绝不挡启动
        pass
except Exception as _exc:  # noqa: BLE001 - 布局失败也要让控制台起得来
    print("[paths] 布局初始化失败，退回程序目录：", _exc)
    _LAYOUT = {}

USER_DIR = _user_root()
CONFIG_PATH = USER_DIR / "config.json"

# 开机自启自愈（2026-11-09）：Run 键被安全软件清理时按用户意图重写，
# 与 daemon.py 启动链同款；失败绝不挡控制台启动。
try:
    import json as _json

    from core.autostart import self_heal as _autostart_heal

    with open(CONFIG_PATH, "r", encoding="utf-8") as _f:
        _autostart_heal(bool((_json.load(_f).get("app") or {}).get("autostart_desired")))
except Exception:  # noqa: BLE001 - 自愈失败绝不挡启动
    pass

#: 静态资源（游戏头像等），只允许读这个目录下的图片
STATIC_DIR = _BASE / "static"
STATIC_TYPES: Dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

#: 页面里引用位图图标的实体（游戏 + 平台胶囊；顺序即 fallback 顺序）。
#: 全部进 mtime 缓存戳：换任何一张图，页面 URL 立即失效旧缓存
ICON_GAMES = ("genshin", "starrail", "zzz", "arknights", "endfield",
              "miyoushe", "skland")
#: 图标目录（/static/icons/... 对应的真实路径）
ICON_DIR = STATIC_DIR / "icons"


def _icon_stamp() -> str:
    """图标缓存戳：取四张图最新的 mtime。

    换一张图，时间戳就变，页面里的 URL 变成 `...png?v=1793xxxx`，
    浏览器/WebView2 当成新资源重新下载 —— 这是治「换了图还显示旧图」的唯一可靠办法。
    """
    stamp = 0
    for game_id in ICON_GAMES:
        try:
            stamp = max(stamp, int((ICON_DIR / f"{game_id}.png").stat().st_mtime))
        except OSError:
            continue
    return str(stamp)


def _render_page() -> str:
    """把模板里的 __ICON_STAMP__ 换成当前缓存戳。每次请求都算，不缓存。

    页面本体走 get_page()：ui/ 三个文件任一改动都会在下一次请求时自动生效。
    """
    return get_page().replace("__ICON_STAMP__", _icon_stamp())

logger = logging.getLogger("dailycheck.loginui")

#: 米游社四款游戏：id -> 显示名
MIHOYO_GAMES: Dict[str, str] = {
    "genshin": "原神",
    "starrail": "崩坏：星穹铁道",
    "zzz": "绝区零",
    "bh3": "崩坏3",
}

#: 森空岛两款游戏：id -> 显示名
SKLAND_TARGETS: Dict[str, str] = {
    "arknights": "明日方舟",
    "endfield": "明日方舟：终末地",
}

#: 错过时间点后的补发窗口（分钟）：0 = 关闭补发
CATCH_UP_MINUTES_DEFAULT = 180
CATCH_UP_MINUTES_MAX = 720


# --------------------------------------------------------------------------- #
# 会话状态
# --------------------------------------------------------------------------- #


class Session:
    """一次登录任务的完整状态。

    state 取值：creating / waiting / scanned / writing / done / error
    step / total 只表示「已写入几款 / 勾了几款」，两条平台都是一次扫码全部写入。
    """

    def __init__(self, provider: str, targets: List[str]) -> None:
        self.sid = uuid.uuid4().hex
        self.provider = provider
        self.targets = list(targets)
        self.total = len(self.targets)
        self.step = 0            # 已完成的款数
        self.current = ""        # 正在登录的那一款
        self.state = "creating"
        self.message = "正在准备…"
        self.qr = ""
        self.detail: Dict[str, Any] = {}
        self.results: List[Dict[str, Any]] = []
        self.created_at = time.time()
        self._lock = threading.Lock()

    def labels(self) -> List[str]:
        table = MIHOYO_GAMES if self.provider == "mihoyo" else SKLAND_TARGETS
        return [table.get(t, t) for t in self.targets]

    def update(self, state: Optional[str] = None, message: Optional[str] = None,
               **detail: Any) -> None:
        with self._lock:
            if state:
                self.state = state
            if message:
                self.message = message
            if detail:
                self.detail.update(detail)

    def set_qr(self, png_b64: str) -> None:
        with self._lock:
            self.qr = png_b64

    def add_result(self, game: str, label: str, ok: bool, note: str = "",
                   skipped: bool = False) -> None:
        """记录单款游戏的写入结果。

        skipped=True 表示"没写入是正常的"（账号下没这款游戏），前端画灰色 `–`，
        不算失败也不算成功 —— 别让正常跳过显示成红 ✗ 吓人。
        """
        with self._lock:
            self.results.append(
                {"game": game, "label": label, "ok": ok, "note": note,
                 "skipped": skipped}
            )

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            table = MIHOYO_GAMES if self.provider == "mihoyo" else SKLAND_TARGETS
            return {
                "sid": self.sid,
                "provider": self.provider,
                "targets": list(self.targets),
                "labels": [table.get(t, t) for t in self.targets],
                "step": self.step,
                "total": self.total,
                "current": self.current,
                "currentLabel": table.get(self.current, self.current),
                "state": self.state,
                "message": self.message,
                "qr": self.qr,
                "detail": dict(self.detail),
                "results": list(self.results),
            }


SESSIONS: Dict[str, Session] = {}
SESSIONS_LOCK = threading.Lock()


def _put(session: Session) -> None:
    with SESSIONS_LOCK:
        cutoff = time.time() - 600
        for sid in [k for k, v in SESSIONS.items() if v.created_at < cutoff]:
            SESSIONS.pop(sid, None)
        SESSIONS[session.sid] = session


def _get(sid: str) -> Optional[Session]:
    with SESSIONS_LOCK:
        return SESSIONS.get(sid)


# --------------------------------------------------------------------------- #
# 二维码
# --------------------------------------------------------------------------- #

# 设备信息.apk 公网下载页（2026-11-14 用户裁决：软件公开发行，二维码指向
# Gitee Releases——免梯、手机直开；APK 是跨版本通用独立工具，各版本资产都有）
GITEE_RELEASES_URL = "https://gitee.com/qianyixi/HopOn/releases"



def qr_png_base64(text: str) -> str:
    """把文本渲染成 PNG 二维码并转 base64；缺依赖时返回空串（前端会降级提示）。"""
    try:
        import qrcode  # noqa: PLC0415 - 可选依赖
    except ImportError:
        logger.warning("未安装 qrcode，无法渲染二维码")
        return ""
    try:
        qr = qrcode.QRCode(border=2, box_size=8, error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#111111", back_color="#ffffff")
        buf = BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as exc:  # noqa: BLE001
        logger.warning("二维码渲染失败：%s", exc)
        return ""


# --------------------------------------------------------------------------- #
# 配置读写
# --------------------------------------------------------------------------- #


def ensure_config() -> None:
    """没有 config.json 就按默认值生成一份（幂等）。

    只为让「下载后直接扫码」少一步：不用先手动跑 python main.py --init。
    """
    if CONFIG_PATH.exists():
        return
    from core.config import create_default_config  # 复用同一份默认值，避免两处漂移

    create_default_config(CONFIG_PATH)
    logger.info("未找到 config.json，已按默认值生成：%s", CONFIG_PATH)


def load_raw_config() -> Dict[str, Any]:
    ensure_config()
    # 凭证字段落盘是 DPAPI 密文（core.secure_store），读端统一在此解密；
    # 旧明文原样通过（自动迁移），解密失败返回密文由认证层引导重扫
    from core.secure_store import decrypt_config_dict

    return decrypt_config_dict(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))


def _attach_manual(payload: Dict[str, Any], *, month_mode: bool) -> None:
    """报表挂手动项行（2026-11-14 用户裁决：自定义日常进七日/月报表）。

    周报：payload["manual"] = dailies.report_rows 行（前端热力图追加行）。
    月报：手动项并成伪游戏塞进 games（game="cd:<id>"）并逐日注入格子
    {known, finished}——选单/月历/统计三处前端零改动复用。
    """
    try:
        from core import dailies as _dl

        items = (load_raw_config().get("app") or {}).get("custom_dailies") or []

        def _is_daily(it: Dict[str, Any]) -> bool:
            """报表只收「每天推送」的手动项（2026-11-14 用户裁决）：周计划
            非全勾的项大部分日子是空白格，进报表只会占一行挤高度。"""
            days = it.get("days") if (
                isinstance(it.get("days"), list) and len(it.get("days")) == 7
                and all(isinstance(x, bool) for x in it.get("days"))) else [True] * 7
            return all(days)

        items = [it for it in items
                 if isinstance(it, dict) and it.get("enabled", True) and _is_daily(it)]
        date_keys = [str(row.get("date") or "")
                     for row in (payload.get("days") or []) if row.get("date")]
        rows = _dl.report_rows(items, CONFIG_PATH.parent, date_keys)
        # KPI「今日完成」并入手动项（2026-11-14 用户裁决）：planned=今日计划数
        # （每天项恒计、周计划项只在其计划日计），done=其中已打卡数
        st = custom_dailies_status()
        planned = sum(1 for it in st.get("items", [])
                      if it.get("enabled", True) and it.get("planned_today"))
        done = sum(1 for it in st.get("items", [])
                   if it.get("enabled", True) and it.get("planned_today")
                   and it.get("done_today"))
        payload["manual_today"] = {"planned": planned, "done": done}
        if month_mode:
            for row in rows:
                gid = "cd:" + (row["id"] or row["name"])
                done = sum(1 for c in row["days"] if c["state"] == "ok")
                miss = sum(1 for c in row["days"] if c["state"] == "miss")
                nodata = sum(1 for c in row["days"] if c["state"] == "na")
                payload.setdefault("games", []).append({
                    "game": gid, "game_name": row["name"],
                    "done": done, "missed": miss, "nodata": nodata,
                })
                by_date = {c["date"]: c["state"] for c in row["days"]}
                for day in (payload.get("days") or []):
                    st = by_date.get(str(day.get("date") or ""))
                    day.setdefault("games", {})[gid] = {
                        "known": st in ("ok", "miss"),
                        "finished": st == "ok",
                        "checks": 1 if st in ("ok", "miss") else 0,
                    }
        else:
            payload["manual"] = rows
    except Exception:  # noqa: BLE001 - 手动项读不到不影响报表主面
        logger.exception("报表挂手动项失败，忽略")


def _enabled_game_ids() -> List[str]:
    """当前 config.json 里 enabled=true 的游戏 id（页面要列游戏的唯一来源）。

    报表、日志的"有哪些游戏"全部从这里派生，**任何地方都不再写死游戏清单**，
    这样将来加第七款游戏时只要注册一次就到处可见。读不到配置就返回空列表，
    不让一个坏配置把整个页面拖垮。
    """
    try:
        raw = load_raw_config()
    except Exception:  # noqa: BLE001 - 同上
        return []
    games: List[str] = []
    for item in (raw.get("accounts") or []):
        if not isinstance(item, dict):
            continue
        game = str(item.get("game") or "").strip()
        if game and item.get("enabled", True) and game not in games:
            games.append(game)
    return games


def _app_version() -> str:
    """程序版本号。唯一来源是 core/__init__.py 的 __version__，界面左下角也读它。"""
    try:
        from core import __version__ as version
    except Exception:  # noqa: BLE001 - 版本号读不到不该拖垮页面
        return ""
    return str(version or "")


def push_status() -> Dict[str, Any]:
    """当前推送通道的配置状态。

    扫码只解决「能查到数据」，收不收得到通知取决于推送通道是否填了 key。
    页面据此提示，避免出现「扫码成功却始终收不到提醒」的困惑。
    """
    try:
        raw = load_raw_config()
    except Exception:  # noqa: BLE001 - 读不到配置也要能开页面
        return {"channels": [], "ready": [], "configured": False, "version": _app_version()}

    push = raw.get("push") or {}
    channels = [str(c).lower() for c in (push.get("channels") or [])]
    ready: List[str] = []
    for name in channels:
        node = push.get(name) or {}
        if not isinstance(node, dict):
            continue
        if name == "bark" and str(node.get("device_key") or "").strip():
            ready.append(name)
        elif name == "serverchan" and str(node.get("sendkey") or "").strip():
            ready.append(name)
        elif name == "qmsg" and str(node.get("key") or "").strip():
            ready.append(name)
        elif name in ("feishu", "dingtalk") and str(node.get("webhook") or "").strip():
            ready.append(name)
    # 演示模式：出厂配置 provider=mock（还没扫过码）→ 前端显示欢迎横幅
    provider = str((raw.get("query") or {}).get("provider") or "").strip().lower()
    return {
        "channels": channels,
        "ready": ready,
        "configured": bool(ready),
        "always_notify": bool(push.get("always_notify", False)),
        "demo_mode": provider == "mock",
        # 界面左下角显示的版本号从这里取，避免 HTML 里再写死一个数字
        "version": _app_version(),
    }


def _is_cn_mobile(mobile: str) -> bool:
    """国内手机号格式：1[3-9] 开头 11 位数字。"""
    import re as _re

    return bool(_re.fullmatch(r"1[3-9]\d{9}", mobile or ""))


def _game_display_map() -> Dict[str, str]:
    """游戏 id -> 中文名，唯一来源是 core.config.KNOWN_GAMES。"""
    try:
        from core.config import KNOWN_GAMES

        return dict(KNOWN_GAMES)
    except Exception:  # noqa: BLE001
        return {}


def _enabled_account_info() -> List[Dict[str, str]]:
    """当前参与检查的账号：id / 中文名 / 昵称（页面展示用）。"""
    try:
        raw = load_raw_config()
    except Exception:  # noqa: BLE001
        return []
    names = _game_display_map()
    items: List[Dict[str, str]] = []
    seen: set = set()
    for node in (raw.get("accounts") or []):
        if not isinstance(node, dict):
            continue
        game = str(node.get("game") or "").strip()
        if not game or game in seen:
            continue
        seen.add(game)
        if not node.get("enabled", True):
            continue
        items.append({
            "id": game,
            "name": names.get(game, game),
            "nickname": str(node.get("nickname") or ""),
        })
    return items


def _cred_filled(value: object) -> bool:
    """凭证字段是否真的填了。判定单源在 core.config.cred_filled（P6-5 合流）：
    非空，且不是出厂模板的「请填写…」占位符——否则"还没绑定"会显示成"已绑定"。"""
    from core.config import cred_filled

    return cred_filled(value)


def accounts_status() -> Dict[str, Any]:
    """每个游戏账号的当前状态：是否参与巡检（enabled）、是否已有登录凭证。

    这是「账号绑定」页复选框的唯一事实来源：勾选 = 参与每日巡检并推送，
    与扫码登录（凭证更新）是两件独立的事。
    """
    names = _game_display_map()
    items: List[Dict[str, Any]] = []
    try:
        raw = load_raw_config()
    except Exception:  # noqa: BLE001
        return {"ok": False, "items": []}
    for node in (raw.get("accounts") or []):
        if not isinstance(node, dict):
            continue
        game = str(node.get("game") or "").strip()
        if not game:
            continue
        cred = node.get("credential") if isinstance(node.get("credential"), dict) else {}
        has_cred = _cred_filled(cred.get("cookie")) or _cred_filled(cred.get("token"))
        items.append({
            "id": game,
            "name": names.get(game, game),
            "nickname": str(node.get("nickname") or ""),
            "enabled": bool(node.get("enabled", True)),
            "has_credential": has_cred,
            "credential_updated_at": str(cred.get("updated_at") or ""),
        })
    return {"ok": True, "items": items}


#: config.json 是「读 → 改 → 写」的共享文件。界面上两个开关几乎同时保存时，
#: 后写的一方会拿着旧副本把前一次的改动覆盖掉。这把锁把整段临界区串起来。
#: （必须放在 set_account_enabled 之前：装饰器在 def 执行时就要用到这个名字。）
_CONFIG_WRITE_LOCK = threading.Lock()


def _serialized_write(func):
    """让同一个进程内的配置写入排队执行（HTTP 服务是多线程的）。"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _CONFIG_WRITE_LOCK:
            return func(*args, **kwargs)

    return wrapper


#: 出厂占位昵称。这几款游戏的绑定流程早期只写 token，没回填真实昵称，
#: config 里一直是这些默认值——气泡/提示里直接念出来像是玩家的真名
#: （「已关闭提醒：博士」），2026-11-09 用户实测指出。读出时一律降级成游戏名。
PLACEHOLDER_NICKNAMES = frozenset({"博士", "管理员", "舰长", "指挥官", "漂泊者"})

#: 尾部的「#1234」标签（明日方舟昵称格式 Name#0642）。用户裁决：
#: 界面上只留名字，编号是平台内部的区分后缀，念出来/看起来都像噪音。
_NICK_TAG_RE = re.compile(r"\s*#\d+\s*$")


def clean_nickname(nick: Any) -> str:
    """去掉昵称尾部的 #数字 标签（KiloDusk#0642 -> KiloDusk）。"""
    return _NICK_TAG_RE.sub("", str(nick or "")).strip()


def _display_nickname(node: Dict[str, Any], game: str,
                      names: Optional[Dict[str, str]] = None) -> str:
    """账号行的可读称呼：真实昵称 > 游戏显示名（占位昵称不算真名）。"""
    nick = clean_nickname((node or {}).get("nickname"))
    if nick and nick not in PLACEHOLDER_NICKNAMES:
        return nick
    table = names if names is not None else {}
    return table.get(game, game)


def _toggle_label(node: Dict[str, Any], game: str,
                  names: Optional[Dict[str, str]] = None) -> str:
    """开关监控气泡里的称呼：游戏名在前，真实昵称括注。

    2026-11-11 用户实测指出：只念昵称（KiloDusk）看不出切的是哪款游戏，
    游戏标识必须出现。占位昵称/空昵称时括注省略，只报游戏名。
    """
    game_name = (names or {}).get(game, game)
    nick = _display_nickname(node, game, names)
    if nick == game_name:
        return game_name
    return f"{game_name}（{nick}）"


@_serialized_write
def set_account_enabled(games: List[str], enabled: bool) -> Dict[str, Any]:
    """把指定游戏写进/移出每日巡检（config accounts[].enabled）。

    只动 enabled，绝不碰 credential —— 提醒开关和登录凭证彻底解耦。
    """
    if not games:
        raise ValueError("没有指定游戏")
    raw = load_raw_config()
    names = _game_display_map()
    present = {
        str(a.get("game") or "").strip(): a
        for a in (raw.get("accounts") or [])
        if isinstance(a, dict)
    }
    changed: List[str] = []
    game_names: List[str] = []
    for game in games:
        node = present.get(game)
        if node is None:
            continue
        node["enabled"] = bool(enabled)
        changed.append(_toggle_label(node, game, names))
        game_names.append(names.get(game, game))
    if not changed:
        raise ValueError("config.json 里找不到这些游戏节点")
    save_raw_config(raw)
    # 批量开关（≥3 款，只有「全选/取消全选」走得到）：逐款带昵称括注太长，
    # 气泡降成纯游戏名清单——单款/双款仍保持「游戏名（昵称）」明细
    labels = game_names if len(changed) >= 3 else changed
    if enabled:
        message = "已开启提醒：" + "、".join(labels)
    else:
        message = "已关闭提醒：" + "、".join(labels)
    return {"ok": True, "enabled": bool(enabled), "games": labels, "message": message}


# --------------------------------------------------------------------------- #
# 体力溢出阈值（/api/settings）：用户在 UI 改完直接写 config.app
# --------------------------------------------------------------------------- #

#: 前端字段名 -> (写到 config.app 的 JSON 键, 默认值)。跟 core/config 字段名保持一致。
_THRESHOLD_FIELDS: Tuple[Tuple[str, str, int], ...] = (
    # 顺序与 core.history.GAME_ORDER（平台分组序）一致：米哈游→鹰角→库街区，
    # 与游戏概览/信息查询同口径（2026-11-11 用户裁决）
    ("genshin",   "genshin_resin_threshold",    160),
    ("starrail",  "starrail_stamina_threshold", 240),
    ("zzz",       "zzz_energy_threshold",       200),
    ("bh3",       "bh3_stamina_threshold",       90),
    ("arknights", "arknights_ap_threshold",     135),
    ("endfield",  "endfield_sanity_threshold",  300),
    ("wuwa",      "wuwa_energy_threshold",      200),
    ("pgr",       "pgr_serum_threshold",        200),
)


def _resource_thresholds() -> Dict[str, int]:
    """读当前 config.json 里的三个阈值，缺字段按默认值返回；0 = 已关闭。"""
    try:
        raw = load_raw_config()
    except Exception:  # noqa: BLE001
        return {key: default for key, _jk, default in _THRESHOLD_FIELDS}
    app = raw.get("app") if isinstance(raw.get("app"), dict) else {}
    out: Dict[str, int] = {}
    for key, json_key, default in _THRESHOLD_FIELDS:
        value = app.get(json_key)
        if value is None or (isinstance(value, str) and not value.strip()):
            out[key] = default
        else:
            try:
                out[key] = int(value)
            except (TypeError, ValueError):
                out[key] = default
    return out


def _normalize_threshold(raw: Any, label: str, default: int) -> int:
    """阈值校验：空 -> 默认；非法 -> 抛 ValueError；0=关闭；最大 1000。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{label} 必须是 0（关闭）或正整数，收到：{raw!r}")
    if value < 0:
        return 0
    return min(value, 1000)


def _resource_alert_enabled() -> bool:
    """体力溢出提醒总开关；读不到配置时按开启处理（保持旧行为）。"""
    try:
        raw = load_raw_config()
    except Exception:  # noqa: BLE001
        return True
    app = raw.get("app") if isinstance(raw.get("app"), dict) else {}
    try:
        return _normalize_bool(app.get("resource_alert_enabled"), True)
    except ValueError:
        return True


def thresholds_status() -> Dict[str, Any]:
    """返回当前阈值 + 默认值 + 上限 + 总开关（前端占位符、提示用）。"""
    return {
        "ok": True,
        "enabled": _resource_alert_enabled(),
        "values": _resource_thresholds(),
        "defaults": {key: default for key, _jk, default in _THRESHOLD_FIELDS},
        "max": 1000,
    }


@_serialized_write
def save_thresholds(payload: Dict[str, Any]) -> Dict[str, Any]:
    """保存三个阈值。校验不过抛 ValueError（接口返 400，不落盘）。"""
    from core.config import parse_config

    normalized: Dict[str, int] = {}
    problems: List[str] = []
    for key, _json_key, default in _THRESHOLD_FIELDS:
        raw = payload.get(key) if isinstance(payload, dict) else None
        try:
            normalized[key] = _normalize_threshold(raw, key, default)
        except ValueError as exc:
            problems.append(str(exc))
    if problems:
        raise ValueError("；".join(problems))

    # 总开关（可选）：不传就保持原样，避免只改阈值时把开关状态覆盖掉
    enabled = None
    if isinstance(payload, dict) and payload.get("enabled") is not None:
        enabled = _normalize_bool(payload.get("enabled"), True)

    raw = load_raw_config()
    if not isinstance(raw.get("app"), dict):
        raw["app"] = {}
    if enabled is not None:
        raw["app"]["resource_alert_enabled"] = enabled
    raw["app"]["genshin_resin_threshold"]    = normalized["genshin"]
    raw["app"]["starrail_stamina_threshold"] = normalized["starrail"]
    raw["app"]["zzz_energy_threshold"]       = normalized["zzz"]
    raw["app"]["arknights_ap_threshold"]     = normalized["arknights"]
    raw["app"]["endfield_sanity_threshold"]  = normalized["endfield"]

    # 用 core.config 自己的规则做全量复检 —— 校验不过就抛异常，文件保持原样
    parse_config(raw, source=CONFIG_PATH)
    save_raw_config(raw)
    logger.info(
        "体力溢出阈值已更新：原粹树脂=%d / 开拓力=%d / 电量=%d / 方舟理智=%d / 终末地理智=%d",
        normalized["genshin"], normalized["starrail"],
        normalized["zzz"], normalized["arknights"], normalized["endfield"],
    )

    result = thresholds_status()
    off = [k for k, v in normalized.items() if v == 0]
    if not result["enabled"]:
        result["message"] = "已保存阈值，但提醒总开关是关闭的（判定已跳过）"
    elif off:
        result["message"] = f"已保存（{', '.join(off)} 已关闭）"
    else:
        result["message"] = "已保存，下次检查自动生效"
    return result


#: 滚动备份份数：config.json.bak-1（最新）~ .bak-3（最老），写入前轮转
CONFIG_BACKUP_KEEP = 3
_CONFIG_BAK_LOCK = threading.Lock()


def _backup_config_before_write() -> None:
    """写入前把当前 config.json 滚动备份为 .bak-1/.bak-2/.bak-3。

    原子写防的是「写坏一半」，防不了「写错内容」——上层把配置改坏再保存时，
    靠这三份快照回退。备份失败绝不阻断保存本身，只记 WARNING。
    用独立锁而非 _CONFIG_WRITE_LOCK：调用方有的持锁有的不持，
    后者可重入加锁会死锁；备份只读 config.json、不碰它本身，独立串行化即可。
    """
    try:
        if not CONFIG_PATH.exists():
            return
        with _CONFIG_BAK_LOCK:
            for i in range(CONFIG_BACKUP_KEEP - 1, 0, -1):
                src = CONFIG_PATH.parent / f"{CONFIG_PATH.name}.bak-{i}"
                dst = CONFIG_PATH.parent / f"{CONFIG_PATH.name}.bak-{i + 1}"
                if src.exists():
                    os.replace(src, dst)   # 旧的往后挪，最老的一份被顶掉
            shutil.copy2(CONFIG_PATH,
                         CONFIG_PATH.parent / f"{CONFIG_PATH.name}.bak-1")
    except OSError as exc:
        logger.warning("config.json 滚动备份失败（不影响本次保存）：%s", exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("config.json 滚动备份出现未预期错误（%s）", exc)


def save_raw_config(raw: Dict[str, Any]) -> None:
    """写回 config.json：先序列化到临时文件并复检，再原子替换。

    直接 write_text 覆盖有个隐患——写到一半断电/被杀，config.json 就成半截
    JSON，之后所有命令都读不了配置。这里改成「临时文件 + os.replace」：
    同一分区内的 replace 是原子操作，失败时原文件完好无损。

    临时文件名用 mkstemp 保证唯一：界面上两个开关几乎同时保存时，
    固定文件名会让两个线程互相删掉对方的临时文件（先写的那次拿不到
    自己的 tmp，finally 清理直接抛错，于是配置写进去了、接口却返回 500）。
    """
    _backup_config_before_write()   # 滚动备份（防"写错内容"，原子写只防"写坏一半"）
    # 机密字段（cookie/token）DPAPI 加密后落盘（P3）：内存态明文 → 磁盘密文；
    # .bak 滚动备份复制的是磁盘旧文件，同样已是密文
    from core.secure_store import encrypt_config_dict

    text = json.dumps(encrypt_config_dict(raw), ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=".config-", suffix=".tmp", dir=str(CONFIG_PATH.parent)
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        json.loads(tmp.read_text(encoding="utf-8"))  # 复检：确保落盘内容可解析
        os.replace(tmp, CONFIG_PATH)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:  # 清理失败不影响已完成的保存
                pass



# --------------------------------------------------------------------------- #
# 通知绑定（push 节点读写）
# --------------------------------------------------------------------------- #

#: 通知绑定页管理的两个通道：name -> 显示名 / config.json 里的 Key 字段名
PUSH_CHANNELS: Dict[str, Dict[str, str]] = {
    "serverchan": {"label": "Server酱", "field": "sendkey"},
    "qmsg": {"label": "Qmsg 酱", "field": "key"},
}

#: 保存动作 -> 提示文案
ACTION_TEXT: Dict[str, str] = {
    "keep": "保持不变",
    "set": "已更新",
    "clear": "已删除",
    "unchanged": "未提交",
}


def mask_key(value: str, head: int = 4, tail: int = 3) -> str:
    """把 Key 打成 SCT4*******c41 这种掩码，仅用于页面回显。

    中间固定 7 个星号，不按真实长度铺星号——长度本身也是信息。
    掩码是「是否改动」的判定依据：前端原样回传掩码，后端就知道用户没动过。
    """
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= head + tail:
        return "*" * 8
    return value[:head] + "*" * 7 + value[-tail:]


def _push_node(raw: Dict[str, Any]) -> Dict[str, Any]:
    """取出 push 节点；类型不对时**不静默覆盖**，交由调用方决定。"""
    push = raw.get("push")
    return push if isinstance(push, dict) else {}


def push_detail() -> Dict[str, Any]:
    """页面可安全展示的推送配置：只有掩码，绝不含明文 Key。"""
    try:
        raw = load_raw_config()
    except Exception as exc:  # noqa: BLE001 - 读不到也要能开页面
        return {"ok": False, "message": f"读取 config.json 失败：{exc}"}

    push = _push_node(raw)
    channels = [
        str(c).strip().lower() for c in (push.get("channels") or []) if str(c).strip()
    ]

    items: Dict[str, Any] = {}
    for name, meta in PUSH_CHANNELS.items():
        node = push.get(name)
        node = node if isinstance(node, dict) else {}
        key = str(node.get(meta["field"]) or "").strip()
        items[name] = {
            "label": meta["label"],
            "field": meta["field"],
            "enabled": bool(node.get("enabled", True)),
            "has_key": bool(key),
            "masked": mask_key(key),
            "in_channels": name in channels,
        }

    return {
        "ok": True,
        "channels": channels,
        "items": items,
        "always_notify": bool(push.get("always_notify", False)),
        "quiet_enabled": bool(push.get("quiet_enabled", False)),
        "quiet_start": str(push.get("quiet_start", "23:00")),
        "quiet_end": str(push.get("quiet_end", "08:00")),
        "configured": any(i["has_key"] and i["in_channels"] for i in items.values()),
    }


def _resolve_key(submitted: str, existing: str) -> Tuple[str, str]:
    """判断这次提交对某个 Key 的意图：(keep | set | clear, 最终值)。

    - 空串        -> clear（清空 = 删除，并退出 channels）
    - 等于当前掩码 -> keep（用户没动输入框，绝不能覆盖原值）
    - 其它         -> set
    """
    text = (submitted or "").strip()
    if not text:
        return "clear", ""
    if existing and text == mask_key(existing):
        return "keep", existing
    if "*" in text:
        raise ValueError("输入内容里有 * 号，看起来是掩码而不是真实 Key，请粘贴完整 Key")
    return "set", text


def update_push_config(raw: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    """把表单值合并进 raw["push"]（原地修改），返回动作、是否真有改动、新 channels。

    只碰这些位置，其余字段（timeout / server / bark / feishu…）原样保留：
        push.channels
        push.serverchan.{enabled, sendkey}
        push.qmsg.{enabled, key}
    """
    push = raw.get("push")
    if not isinstance(push, dict):
        push = {}
        raw["push"] = push

    before = json.loads(json.dumps(push))  # 深拷贝一份，用于判断到底改没改

    # 平安通知：全部日常完成时是否也发一条（默认关闭 = 无事不打扰）
    if "always_notify" in payload:
        push["always_notify"] = bool(payload.get("always_notify"))

    # 免打扰时段：窗口内推送顺延（条件会在时段结束后自然重现，天然补推）
    if "quiet_enabled" in payload:
        push["quiet_enabled"] = bool(payload.get("quiet_enabled"))
    for field in ("quiet_start", "quiet_end"):
        if field in payload:
            value = str(payload.get(field) or "").strip()
            if value and not _TIME_RE.match(value):
                raise ValueError(f"「{value}」不是合法时间，格式应为 HH:MM（如 23:00）")
            push[field] = value or ("23:00" if field == "quiet_start" else "08:00")

    old_channels = [
        str(c).strip().lower() for c in (push.get("channels") or []) if str(c).strip()
    ]

    actions: Dict[str, str] = {}
    qualifying: Dict[str, bool] = {}

    for name, meta in PUSH_CHANNELS.items():
        field = meta["field"]
        item = payload.get(name)

        # 前端没提交这个通道：完全不动它
        if not isinstance(item, dict):
            node = push.get(name)
            node = node if isinstance(node, dict) else {}
            key = str(node.get(field) or "").strip()
            qualifying[name] = (
                bool(key) and bool(node.get("enabled", True)) and name in old_channels
            )
            actions[name] = "unchanged"
            continue

        node = push.get(name)
        if not isinstance(node, dict):
            node = {}
            push[name] = node

        existing = str(node.get(field) or "").strip()

        if "enabled" in item:
            node["enabled"] = bool(item.get("enabled"))
        enabled = bool(node.get("enabled", True))

        if "key" in item:
            action, value = _resolve_key(str(item.get("key") or ""), existing)
        else:
            action, value = "keep", existing

        if action == "clear":
            node[field] = ""
        elif action == "set":
            node[field] = value
        # keep：一个字节都不改，原 Key 原样留着

        final_key = str(node.get(field) or "").strip()
        qualifying[name] = enabled and bool(final_key)
        actions[name] = action

    # 重建 channels：其它通道保持原顺序；本次管理的两个通道按「启用且有 Key」决定去留
    final: List[str] = []
    for name in old_channels:
        if name in PUSH_CHANNELS:
            if qualifying.get(name) and name not in final:
                final.append(name)
        elif name not in final:
            final.append(name)
    for name in PUSH_CHANNELS:
        if qualifying.get(name) and name not in final:
            final.append(name)
    push["channels"] = final

    return {
        "actions": actions,
        "changed": json.loads(json.dumps(push)) != before,
        "channels": list(final),
    }


def run_test_push(payload: Dict[str, Any]) -> Dict[str, Any]:
    """用**当前表单值**发一条测试消息。

    只在内存里的配置副本上套用表单值，**不写盘**：想留下来必须先点「保存配置」。
    这样用户可以先试通再决定保存，也不会因为一次误操作改动 config.json。
    """
    try:
        from core.config import PushConfig
        from core.notifier import build_notifiers, push_all
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"推送模块加载失败：{exc}"}

    try:
        raw = json.loads(json.dumps(load_raw_config()))  # 深拷贝：与磁盘完全隔离
        update_push_config(raw, payload)
        push_cfg = PushConfig.from_dict(raw.get("push") or {})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"配置校验未通过：{exc}"}

    try:
        notifiers = build_notifiers(push_cfg)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"构造推送器失败：{exc}"}

    ready = [n for n in notifiers if n.ready]
    if not ready:
        selected = [
            PUSH_CHANNELS[n.name]["label"] for n in notifiers if n.name in PUSH_CHANNELS
        ]
        tail = f"（当前选中：{'、'.join(selected)}）" if selected else ""
        return {
            "ok": False,
            "message": "没有可用的推送通道：请勾选「启用」并填入 Key" + tail,
        }

    now = time.strftime("%m-%d %H:%M")
    body = (
        "收到这条说明推送通道工作正常。\n"
        f"发送时间：{now}\n"
        "来源：登录界面 · 通知绑定 · 测试推送"
    )
    results = push_all(ready, "HopOn · 测试推送", body)
    rows = [
        {
            "channel": r.channel,
            "label": PUSH_CHANNELS.get(r.channel, {}).get("label", r.channel),
            "ok": bool(r.ok),
            "skipped": bool(r.skipped),
            "message": r.message,
        }
        for r in results
    ]
    logger.info(
        "测试推送：%s",
        "；".join(f"{x['label']}={'成功' if x['ok'] else '失败'}" for x in rows),
    )
    return {"ok": True, "results": rows}


def push_preview_payload() -> Dict[str, Any]:
    """GET /api/push_preview 的响应体：与手机实发同一条组装路径的标题/正文。

    快照位置由 core.history 掌握（DATA_DIR / REPORT_SNAPSHOT_NAME，现场拼接，
    测试重定向 DATA_DIR 即整体隔离）；文案组装全部在 core.analyzer
    （push_preview_from_snapshot），本函数只管把快照从盘上读出来。
    只读盘、不联网、不推送、不写 push_history。
    """
    from core import history as history_mod
    from core.analyzer import push_preview_from_snapshot

    snap: Optional[Dict[str, Any]] = None
    snap_path = history_mod.DATA_DIR / history_mod.REPORT_SNAPSHOT_NAME
    try:
        loaded = json.loads(snap_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            snap = loaded
    except FileNotFoundError:
        pass   # 还没跑过巡检：交给 analyzer 返回「等待第一次巡检」
    except Exception as exc:  # noqa: BLE001 - 快照坏了当没巡检过，页面不许 500
        logger.warning("读取巡检快照失败，预览降级为空态：%s", exc)
    # 手动项实态并入预览（2026-11-13）：未完成行 + 已完成并入 [已完成] 汇总，
    # 与实发 main.py 1.7 段同一单源（merged_push_parts）
    manual_undone: List[str] = []
    manual_finished: List[str] = []
    try:
        from core import dailies as _cd

        _items = [it for it in ((load_raw_config().get("app") or {})
                                .get("custom_dailies") or [])
                  if isinstance(it, dict)]
        for _it in _items:
            _it.setdefault("reset", _cd.DEFAULT_RESET)
        manual_undone, manual_finished = _cd.merged_push_parts(
            _items, CONFIG_PATH.parent)
    except Exception as exc:  # noqa: BLE001 - 手动项读不到不影响预览主面
        logger.warning("推送预览并入手动项失败：%s", exc)
    # 徽章单源（2026-11-18 用户实证：勾选框 DOM 状态≠实发通道，已停用的
    # Server酱 仍被列上）。实发语义 = channels 成员且 key 在位，名字用
    # PUSH_CHANNELS 注册表（与通知绑定卡同源）。读失败降级空表——页面显示
    # 「未启用推送渠道」，绝不拖垮预览主体。
    ready_labels: List[str] = []
    try:
        push = _push_node(load_raw_config())
        for _name in (push.get("channels") or []):
            _key = str(_name).strip().lower()
            meta = PUSH_CHANNELS.get(_key)
            if not meta:
                continue
            node = push.get(_key)
            node = node if isinstance(node, dict) else {}
            if str(node.get(meta["field"]) or "").strip():
                ready_labels.append(meta["label"])
    except Exception as exc:  # noqa: BLE001 - 通道读不到不影响预览主面
        logger.warning("推送预览读取通道列表失败：%s", exc)
        ready_labels = []
    return {"ok": True, "channels": ready_labels, **push_preview_from_snapshot(
        snap, manual_undone=manual_undone, manual_finished=manual_finished)}


# --------------------------------------------------------------------------- #
# 设备伪装
# --------------------------------------------------------------------------- #

#: 指纹缓存：换设备档案后必须删掉，否则会继续用旧机型的 device_fp
_DEVICE_CACHE_PATH = USER_DIR / "logs" / "device_fp.json"

#: APK 下载服务信息（main() 启动后填充）
TOOLS_SERVER: Optional[Dict[str, Any]] = None

#: 启动失败的原因。空串表示没失败（要么没试过，要么成功了）
TOOLS_ERROR: str = ""

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _mask_oaid(value: str) -> str:
    text = (value or "").strip()
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}****{text[-4:]}"


def apk_info(*, retry: bool = True) -> Dict[str, Any]:
    """设备信息.apk 的公网下载信息 + 二维码（2026-11-14 用户裁决：软件公开发
    行，二维码必须指向公网——原局域网 0.0.0.0:8790 方案废弃）。

    URL = Gitee Releases 页（免梯、手机直开；设备信息.apk 是跨版本通用的
    独立工具，各版本资产里都有）。本地 tools/ 有 APK 时顺带报大小。
    ``retry`` 参数保留兼容旧签名，公网方案无服务可重启，忽略。
    """
    apk_path = BASE_DIR / "tools" / "设备信息.apk"
    size_text = ""
    if apk_path.exists():
        size_text = _human_size(apk_path.stat().st_size)
    return {
        "available": True,
        "name": "设备信息.apk",
        "size_text": size_text,
        "url": GITEE_RELEASES_URL,
        "qr": qr_png_base64(GITEE_RELEASES_URL),
    }


def device_status() -> Dict[str, Any]:
    """当前生效的设备档案（OAID 打码）与相关状态。"""
    from core import mihoyo_api

    profile = mihoyo_api.load_device_profile()  # 读盘，换完立刻反映
    try:
        proxy = str((load_raw_config().get("query") or {}).get("proxy") or "")
    except Exception:  # noqa: BLE001 - 配置读不到不影响设备页其它内容
        proxy = ""
    return {
        "ok": True,
        "custom": mihoyo_api.PROFILE_OVERRIDE_PATH.exists(),
        "label": mihoyo_api.identity_label(),
        "proxy": proxy,
        "profile": {
            "brand": profile.get("brand", ""),
            "model": profile.get("model", ""),
            "os_version": profile.get("os_version", ""),
            "sdk_version": profile.get("sdk_version", ""),
            "device": profile.get("device", ""),
            "device_info": profile.get("device_info", ""),
            "oaid": _mask_oaid(profile.get("oaid", "")),
        },
        "ua": mihoyo_api.build_user_agent(profile),
        "cache_exists": _DEVICE_CACHE_PATH.exists(),
        # retry=False：设备页每次加载都会走到这里，不能顺手把绑 0.0.0.0 的服务拉起来
        "apk": apk_info(retry=False),
    }


def _extract_json_object(text: str) -> Dict[str, Any]:
    """从粘贴内容里截出 JSON 对象。

    小白常常连着"设备信息"几个字一起复制，严格 json.loads 会失败，
    这里按首尾花括号截取，容错更高。
    """
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("没找到 JSON 内容（应当以 { 开头、} 结尾）")
    try:
        data = json.loads(text[start : end + 1])
    except ValueError as exc:
        raise ValueError(f"JSON 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("粘贴的内容不是 JSON 对象")
    return data


def save_device_text(raw_text: str) -> Dict[str, Any]:
    """解析用户粘贴的手机档案 -> 写入覆盖文件 -> 清指纹缓存。"""
    from core import mihoyo_api

    text = (raw_text or "").strip()
    if not text:
        raise ValueError("请先粘贴「设备信息」App 导出的内容")

    data = _extract_json_object(text)
    mihoyo_api.save_device_profile(data)   # 校验 + 落盘
    mihoyo_api.reload_device_profile()     # 让 UA 等缓存立刻跟上
    removed = mihoyo_api.clear_device_cache()

    result = device_status()
    result["removed_cache"] = removed
    result["message"] = "设备指纹已替换，请重新扫码"
    logger.info("设备档案已替换为 %s（UA 已同步）", result["profile"]["model"])
    return result


def reset_device_text() -> Dict[str, Any]:
    """恢复默认设备档案并清缓存。"""
    from core import mihoyo_api

    mihoyo_api.clear_device_profile()
    mihoyo_api.reload_device_profile()
    removed = mihoyo_api.clear_device_cache()

    result = device_status()
    result["removed_cache"] = removed
    result["message"] = "已切回随机设备，请重新扫码"
    return result


@_serialized_write
def save_query_proxy(raw_value: Any) -> Dict[str, Any]:
    """校验并写入 config.query.proxy（空串 = 直连）。仅本地读写，不做连通性测试。

    消费链：下一轮检查 run_check → _apply_mihoyo_query_config → mihoyo_api.set_proxy，
    getFp 与 dailyNote 均经此出口。
    """
    value = str(raw_value or "").strip()
    if value and not re.match(r"^(https?|socks5h?)://[^\s]+$", value, re.IGNORECASE):
        raise ValueError("代理格式不对：应以 http://、https:// 或 socks5:// 开头，且不含空格")
    raw = load_raw_config()
    query = raw.get("query")
    if not isinstance(query, dict):
        query = {}
        raw["query"] = query
    query["proxy"] = value

    from core.config import parse_config

    parse_config(raw, source=CONFIG_PATH)
    save_raw_config(raw)
    message = "已保存：米游社查询将经代理出口" if value else "已保存：查询直连"
    logger.info("查询代理已更新：%s", value or "(直连)")
    return {"ok": True, "proxy": value, "message": message}


def shuffle_device() -> Dict[str, Any]:
    """随机换一台设备：换机型 + 换 OAID，旧指纹缓存一并作废。"""
    from core import mihoyo_api

    mihoyo_api.shuffle_device_profile()   # 内部已清 device_fp 缓存
    mihoyo_api.reload_device_profile()    # 让 UA 等缓存立刻跟上

    result = device_status()
    result["message"] = "已换一台设备，下次查询会用新指纹"
    logger.info(
        "设备已切换为 %s（%s）",
        result["profile"]["model"],
        result.get("label") or "未知",
    )
    return result


# --------------------------------------------------------------------------- #
# 定时与守护进程
# --------------------------------------------------------------------------- #


def _normalize_times(times: Any) -> List[str]:
    if not isinstance(times, list):
        raise ValueError("times 必须是数组")
    result: List[str] = []
    for item in times:
        text = str(item).strip()
        if not _TIME_RE.match(text):
            raise ValueError(f"「{text}」不是合法时间，格式应为 HH:MM（如 08:30）")
        hour, minute = text.split(":")
        text = f"{int(hour):02d}:{minute}"   # 9:00 → 09:00：core 的存储校验只收 2 位小时
        if text not in result:
            result.append(text)
    if not result:
        raise ValueError("至少要保留一个时间点")
    if len(result) > 12:
        raise ValueError("时间点最多 12 个")
    return sorted(result)


def daemon_status() -> Dict[str, Any]:
    try:
        import daemon as daemon_mod

        return daemon_mod.status()
    except Exception as exc:  # noqa: BLE001 - 状态读不到不能影响页面
        logger.warning("读取守护进程状态失败：%s", exc)
        return {"running": False, "error": str(exc), "check_times": [], "next_run": ""}


def _normalize_bool(raw: Any, default: bool) -> bool:
    """解析总开关：接受 true/false、1/0、on/off；留空走默认值。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    text = str(raw).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"开关值必须是 true 或 false，收到：{raw!r}")


def schedule_status() -> Dict[str, Any]:
    from core.config import next_check_time, _parse_bg_refresh_overrides

    raw = load_raw_config()
    app = raw.get("app") if isinstance(raw.get("app"), dict) else {}
    times = [str(item) for item in (app.get("check_times") or [])]
    target = next_check_time(times)
    # 今天还剩的时间点（cron-job.org「next runs」范式）：只认服务器时钟，
    # 前端严禁自行取 Date.now() 判断先后 —— 字段由这里算好直接给
    now_hm = (datetime.now().hour, datetime.now().minute)
    today_next = []
    for t in times:
        try:
            a, b = str(t).split(":")[:2]
            if (int(a), int(b)) >= now_hm:
                today_next.append(str(t))
        except (ValueError, IndexError):
            continue
    today_next.sort(key=lambda s: tuple(int(x) for x in s.split(":")[:2]))
    return {
        "ok": True,
        "times": times,
        "today_next": today_next,
        "catch_up_minutes": _normalize_catch_up(app.get("catch_up_minutes")),
        "catch_up_enabled": _normalize_bool(app.get("catch_up_enabled"), True),
        "resource_alert_enabled": _normalize_bool(app.get("resource_alert_enabled"), True),
        "bg_refresh_enabled": _normalize_bool(app.get("bg_refresh_enabled"), True),
        "bg_refresh_overrides": _parse_bg_refresh_overrides(
            app.get("bg_refresh_overrides")),
        "activity_alert_enabled": _normalize_bool(app.get("activity_alert_enabled"), True),
        "activity_alert_days": _normalize_alert_days(app.get("activity_alert_days")),
        "activity_deadline_tiers": _normalize_deadline_tiers(
            app.get("activity_deadline_tiers")),
        "next_run": target.strftime("%Y-%m-%d %H:%M") if target else "",
        "daemon": daemon_status(),
        # 页面上要列账号就取这里，避免把游戏清单写死在多份 HTML / JS 里
        "enabled_games": _enabled_game_ids(),
        "accounts_info": _enabled_account_info(),
    }


#: 底部运行日志的噪音规则表：这些 INFO 行是程序生命周期/机械动作，
#: 对用户没有可行动的信息。黑名单式（宁可少滤不可误滤），可增删；
#: WARNING/ERROR 永远不算噪音（哪怕来自机械动作——出问题就该被看见）。
_LOG_NOISE_PATTERNS = (
    "守护进程启动", "守护进程已退出", "守护进程停止",
    "轮询线程已启动", "轮询线程已退出",
    "检测到停止标记", "托盘图标已创建",
    "时间点：", "补发窗口：", "日志：",
    "测试模式：",
    "未覆盖游戏", "自动改用",
    "强制重新换取森空岛凭证",
    "窗口已关闭", "控制台服务已关闭", "桌面控制台退出",
    "config_mock",
)


def _classify_log_block(text: str) -> Dict[str, Any]:
    """给一块日志定级并标记是否为噪音块。首行的 [LEVEL] 决定级别。"""
    first_line = text.split("\n", 1)[0]
    if re.search(r"\[(ERROR|CRITICAL)\s*\]", first_line):
        level = "error"
    elif re.search(r"\[WARNING\s*\]", first_line):
        level = "warn"
    else:
        level = "info"
    noise = level == "info" and any(p in text for p in _LOG_NOISE_PATTERNS)
    return {"level": level, "noise": noise}


def log_tail(n: int = 200, useful: bool = False, log_dir: Optional[Path] = None) -> Dict[str, Any]:
    """读取运行日志最后 n 块，给底部日志控制台。

    双源按时间戳合并：dailycheck.log（守护进程：巡检/推送）+ desktop.log
    （控制台进程：配置保存/扫码登录/守护进程启停等一切界面操作）——
    只读单源时另一个进程做的事在日志里完全不可见（日志分家老问题）。
    零点切分后当天 dailycheck.log 可能为空或不存在，回退最近的 .YYYY-MM-DD。
    无时间戳的续行（traceback 等）并入上一块，保证多行堆栈排序后仍完整。
    key 在 notifier 源头已脱敏，可直接展示。

    ``useful=True``（底部收起条专用）：从窗口里挑**最新一条非噪音块**——
    收起条的价值在"一眼看到最近发生的有用的事"，而最新任意行大概率是
    守护进程生命周期噪音（实测噪音占比 ~75-80%）。全窗口都是噪音时
    回退最新块，保证永远有内容。
    """
    # 日志目录可注入（2026-11-05 数据外置改造）：默认读用户数据根的 logs\，
    # 测试传自己的临时目录，避免读到真实运行日志里的噪音干扰断言。
    log_dir = log_dir or (USER_DIR / "logs")
    ts_pat = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

    def _pick(path: Path) -> Path:
        if path.exists():
            return path
        candidates = sorted(log_dir.glob(path.name + ".*"))
        return candidates[-1] if candidates else path

    def _blocks(path: Path, limit: int) -> list:
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError as exc:  # noqa: BLE001 - 单源读取失败降级为空，不拖垮另一源
            logger.warning("读取 %s 失败：%s", path.name, exc)
            return []
        blocks: list = []
        # 预读 4 倍余量：traceback 续行会摊薄块数，按行数截断再收敛到块数
        for raw in lines[-(limit * 4):]:
            line = raw.rstrip("\r\n")
            m = ts_pat.match(line)
            if m:
                blocks.append((m.group(1), line))
            elif blocks:
                ts, text = blocks[-1]
                blocks[-1] = (ts, text + "\n" + line)
        return blocks[-limit:]

    try:
        merged = _blocks(_pick(log_dir / "dailycheck.log"), n) + \
                 _blocks(_pick(log_dir / "desktop.log"), n)
        merged.sort(key=lambda item: item[0])
        window = merged[-n:]
        events = []
        for ts, text in window:
            info = _classify_log_block(text)
            events.append({"ts": ts, "level": info["level"],
                           "noise": info["noise"], "text": text})
        if useful:
            picked = [e for e in events if not e["noise"]][-1:] or events[-1:]
            return {"ok": True, "lines": [e["text"] for e in picked],
                    "events": picked}
        return {"ok": True, "lines": [e["text"] for e in events], "events": events}
    except Exception as exc:  # noqa: BLE001 - 前端要的是 200 + 错误文案，不是 500
        return {"ok": False, "message": f"读取日志失败：{exc}"}


def _normalize_catch_up(raw: Any) -> int:
    """补发窗口（分钟）：0 = 关闭；没配这个字段时按 180 分钟处理。

    非数字会直接报错（交给接口返回 400），不静默吞掉——避免用户以为保存成功了。
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return CATCH_UP_MINUTES_DEFAULT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(
            f"补发窗口必须是 0（关闭）~ {CATCH_UP_MINUTES_MAX} 之间的整数，收到：{raw!r}"
        )
    if value < 0:
        return 0
    return min(value, CATCH_UP_MINUTES_MAX)


#: 活动结束提前提醒天数：1~30，默认 3（与 core.config 保持一致）
ACTIVITY_ALERT_DAYS_DEFAULT = 3
ACTIVITY_ALERT_DAYS_MAX = 30


def _normalize_alert_days(raw: Any) -> int:
    """提前提醒天数：留空/非法走默认 3，越界收敛到 1~30（读取路径不报错）。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return ACTIVITY_ALERT_DAYS_DEFAULT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return ACTIVITY_ALERT_DAYS_DEFAULT
    return max(1, min(value, ACTIVITY_ALERT_DAYS_MAX))


@_serialized_write
def save_catch_up(raw_minutes: Any, enabled: Any = None) -> Dict[str, Any]:
    """保存补发窗口（可选同时改总开关）。改完不用重启守护进程。"""
    value = _normalize_catch_up(raw_minutes)
    raw = load_raw_config()
    if not isinstance(raw.get("app"), dict):
        raw["app"] = {}
    raw["app"]["catch_up_minutes"] = value
    if enabled is not None:
        raw["app"]["catch_up_enabled"] = _normalize_bool(enabled, True)

    from core.config import parse_config

    parse_config(raw, source=CONFIG_PATH)
    save_raw_config(raw)
    logger.info("补发窗口已更新：%d 分钟（总开关=%s）", value,
                "开" if raw["app"].get("catch_up_enabled", True) else "关")
    result = schedule_status()
    if not result["catch_up_enabled"]:
        result["message"] = "已关闭补发（补发窗口的数值已保留）"
    elif value == 0:
        result["message"] = "已关闭补发（补发窗口为 0）"
    else:
        result["message"] = f"错过时间点后 {value} 分钟内会补发"
    return result


@_serialized_write
def save_bg_refresh(enabled: Any = None, overrides: Any = None) -> Dict[str, Any]:
    """保存后台静默刷新：总开关 + 每款游戏覆盖项（enabled/interval）。

    overrides 经 _parse_bg_refresh_overrides 消毒后才落盘（未知游戏/越界
    间隔整项丢弃）——弹窗警示在客户端做，服务端只认合法值。
    """
    raw = load_raw_config()
    if not isinstance(raw.get("app"), dict):
        raw["app"] = {}
    if enabled is not None:
        raw["app"]["bg_refresh_enabled"] = _normalize_bool(enabled, True)
    if overrides is not None:
        from core.config import _parse_bg_refresh_overrides

        raw["app"]["bg_refresh_overrides"] = _parse_bg_refresh_overrides(overrides)

    from core.config import parse_config

    parse_config(raw, source=CONFIG_PATH)
    save_raw_config(raw)
    logger.info("后台静默刷新已保存（总开关=%s，覆盖 %d 款）",
                "开" if raw["app"].get("bg_refresh_enabled", True) else "关",
                len(raw["app"].get("bg_refresh_overrides") or {}))
    result = schedule_status()
    result["message"] = "已保存"
    return result


def custom_dailies_status() -> Dict[str, Any]:
    """手动打卡清单 + 今日完成态（抽屉/报表/托盘共用数据源）。"""
    from core import dailies

    raw = load_raw_config()
    items = (raw.get("app") or {}).get("custom_dailies") or []
    user_dir = CONFIG_PATH.parent
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        it = dict(it)
        it.setdefault("reset", dailies.DEFAULT_RESET)
        days_raw = it.get("days")
        if (not isinstance(days_raw, list) or len(days_raw) != 7
                or not all(isinstance(x, bool) for x in days_raw)):
            days_raw = [True] * 7
        _done = dailies.is_done(it, user_dir)
        # 今日是否在周计划内（2026-11-13 用户裁决：非计划日的概览卡不显示）
        # 逐项 reset 时刻口径由后端统一算——前端不自己按 Date.now 猜星期
        _planned = dailies._planned_weekday(it, dailies._period_date(it))
        # 推送效果诚实口径（2026-11-13）：未完成=[未完成] 行；已完成=并入
        # [已完成] 汇总行（不独立成行）——预览与实发同语义
        if _done:
            _push_text = f"[已完成] {it.get('name') or ''}（并入推送的完成汇总行）"
        else:
            _push_text = dailies.render_message(it)
        out.append({
            "planned_today": _planned,
            "id": str(it.get("id") or ""),
            "name": str(it.get("name") or ""),
            "mode": str(it.get("mode") or "merged"),
            "time": str(it.get("time") or ""),
            "times": dailies._times(it),   # 多时刻（2026-11-25）：空=用 time 单时刻
            "reset": it["reset"],
            "template": str(it.get("template") or ""),
            "enabled": bool(it.get("enabled", True)),
            "days": list(days_raw),
            "done_today": _done,
            "streak": dailies.streak_of(it, user_dir),
            "push_text": _push_text,
        })
    return {"ok": True, "items": out,
            "default_template": dailies.DEFAULT_TEMPLATE}


@_serialized_write
def custom_dailies_update(payload: Dict[str, Any]) -> Dict[str, Any]:
    """手动打卡增删改/今日勾选（写 config + 完成账本，双写一致）。"""
    import uuid as _uuid

    from core import dailies

    action = str(payload.get("action") or "")
    user_dir = CONFIG_PATH.parent
    raw = load_raw_config()
    if not isinstance(raw.get("app"), dict):
        raw["app"] = {}
    items = [it for it in (raw["app"].get("custom_dailies") or [])
             if isinstance(it, dict)]

    if action == "toggle_done":
        item_id = str(payload.get("id") or "")
        hit = next((it for it in items if str(it.get("id")) == item_id), None)
        if hit is None:
            raise ValueError("项不存在")
        hit = dict(hit)
        hit.setdefault("reset", dailies.DEFAULT_RESET)
        # 报表长按补录（2026-11-14 用户裁决）：payload.date = 历史日历日
        pdate = None
        date_raw = str(payload.get("date") or "").strip()
        if date_raw:
            try:
                from datetime import datetime as _dt

                pdate = _dt.strptime(date_raw, "%Y-%m-%d").date()
            except ValueError:
                raise ValueError("日期格式不对：" + date_raw)
        undo = bool(payload.get("undo"))
        dailies.mark_done(hit, user_dir, undo=undo, period_date=pdate)
        result = custom_dailies_status()
        if pdate is not None:
            result["message"] = (("已补录 " + date_raw) if not undo
                                 else ("已撤销 " + date_raw + " 的打卡"))
        else:
            result["message"] = ("已勾掉今日（次日 04:00 自动重开）"
                                 if not undo else "已恢复今日待办")
        return result

    if action == "del":
        item_id = str(payload.get("id") or "")
        items = [it for it in items if str(it.get("id")) != item_id]
        dailies.forget(item_id, user_dir)
        raw["app"]["custom_dailies"] = items
        from core.config import parse_config

        parse_config(raw, source=CONFIG_PATH)
        save_raw_config(raw)
        result = custom_dailies_status()
        result["message"] = "已删除"
        return result

    name = str(payload.get("name") or "").strip()[:20]
    if not name:
        raise ValueError("名称不能为空")
    mode = str(payload.get("mode") or "merged")
    if mode not in ("merged", "standalone"):
        mode = "merged"
    time_s = str(payload.get("time") or "").strip()
    # 多时刻（2026-11-25）：standalone 可用 times[]（≤5 合法 HH:MM 去重），
    # 或退回落单 time；merged 清空。
    times_list: List[str] = []
    raw_times = payload.get("times")
    if mode == "standalone" and isinstance(raw_times, list):
        for t in raw_times:
            ts = str(t).strip()
            if re.match(r"^\d{1,2}:\d{2}$", ts) and ts not in times_list:
                times_list.append(ts)
            if len(times_list) >= 5:
                break
    if mode == "standalone" and not times_list and not re.match(r"^\d{1,2}:\d{2}$", time_s):
        raise ValueError("独立时刻需要有效的 HH:MM")
    if mode == "merged":
        time_s = ""
    reset_s = str(payload.get("reset") or dailies.DEFAULT_RESET).strip()
    if not re.match(r"^\d{1,2}:\d{2}$", reset_s):
        reset_s = dailies.DEFAULT_RESET
    template = str(payload.get("template") or "").strip()[:100]
    enabled = bool(payload.get("enabled", True))
    days_raw = payload.get("days")
    if (not isinstance(days_raw, list) or len(days_raw) != 7
            or not all(isinstance(x, bool) for x in days_raw)):
        days_raw = [True] * 7   # 缺省 = 每天
    days = [bool(x) for x in days_raw]
    if not any(days):
        raise ValueError("至少勾选一个重复日")

    if action == "add":
        from datetime import datetime as _dt2

        items.append({"id": f"cd-{_uuid.uuid4().hex[:8]}", "name": name,
                      "mode": mode, "time": time_s, "times": times_list, "reset": reset_s,
                      "template": template, "enabled": enabled, "days": days,
                      # 创建时间（2026-11-18）：报表「开始记录前」地板的锚点——
                      # 新建项的过去画 ⋯ 而不是 ✗ 漏打
                      "created_at": _dt2.now().strftime("%Y-%m-%d %H:%M:%S")})
    elif action == "update":
        item_id = str(payload.get("id") or "")
        hit = next((it for it in items if str(it.get("id")) == item_id), None)
        if hit is None:
            raise ValueError("要编辑的项不存在")
        hit.update({"name": name, "mode": mode, "time": time_s, "times": times_list,
                    "reset": reset_s, "template": template, "enabled": enabled, "days": days})
    else:
        raise ValueError(f"未知 action：{action}")

    raw["app"]["custom_dailies"] = items
    from core.config import parse_config

    parse_config(raw, source=CONFIG_PATH)
    save_raw_config(raw)
    result = custom_dailies_status()
    result["message"] = "已添加" if action == "add" else "已保存"
    return result


def _normalize_deadline_tiers(raw: Any) -> List[int]:
    """三段阈值的展示口径：非法/缺失回默认档（保存侧校验走 parse_config）。"""
    from core.config import ACTIVITY_DEADLINE_TIERS_DEFAULT

    try:
        values = list(raw) if raw is not None else []
        if len(values) != 3:
            return list(ACTIVITY_DEADLINE_TIERS_DEFAULT)
        out = [int(v) for v in values]
        if any(v != -1 and not (1 <= v <= 2160) for v in out):
            return list(ACTIVITY_DEADLINE_TIERS_DEFAULT)
        return out
    except Exception:  # noqa: BLE001
        return list(ACTIVITY_DEADLINE_TIERS_DEFAULT)


@_serialized_write
def save_activity_alert(enabled: Any = None, days: Any = None,
                        tiers: Any = None) -> Dict[str, Any]:
    """活动结束提醒开关/提前天数/三段阈值（config.app.activity_alert_*）。

    enabled / days / tiers 任传其一或都传；只改部分字段时其余保持原值。
    tiers = [档1, 档2, 档3]（小时，-1 = 该档关闭）；非法值由 parse_config 报错。
    """
    raw = load_raw_config()
    if not isinstance(raw.get("app"), dict):
        raw["app"] = {}
    changed_switch = enabled is not None
    changed_days = days is not None and str(days).strip() != ""
    changed_tiers = tiers is not None
    if not changed_switch and not changed_days and not changed_tiers:
        raise ValueError("没有要保存的内容")

    if changed_switch:
        raw["app"]["activity_alert_enabled"] = _normalize_bool(enabled, True)
    if changed_days:
        raw["app"]["activity_alert_days"] = _normalize_alert_days(days)
    if changed_tiers:
        raw["app"]["activity_deadline_tiers"] = tiers  # 合法性由 parse_config 把关
    value = _normalize_alert_days(raw["app"].get("activity_alert_days"))
    switch_on = _normalize_bool(raw["app"].get("activity_alert_enabled"), True)

    from core.config import parse_config

    parse_config(raw, source=CONFIG_PATH)
    save_raw_config(raw)
    tiers_now = _normalize_deadline_tiers(raw["app"].get("activity_deadline_tiers"))
    logger.info(
        "活动提醒：开关=%s，徽章提前 %d 天，三段=%s",
        "开" if switch_on else "关", value, tiers_now,
    )
    result = schedule_status()
    if changed_tiers:
        result["message"] = (
            f"结束推送三段已保存：{tiers_now[0]}h/{tiers_now[1]}h/{tiers_now[2]}h"
            "（-1 = 关闭）"
        )
    elif changed_days:
        result["message"] = f"活动结束前 {value} 天开始提醒"
    elif switch_on:
        result["message"] = f"已开启活动结束提醒（结束前 {value} 天开始提醒）"
    else:
        result["message"] = "已关闭活动结束提醒"
    return result


# --------------------------------------------------------------------------- #
# 信息查询：实时体力 / 活动缓存
# --------------------------------------------------------------------------- #

#: 实时查询结果缓存（秒）。切页签不该每次都重新打 5 个游戏的接口。
_LIVE_TTL_SECONDS = 45
_LIVE_CACHE: Dict[str, Any] = {"at": 0.0, "payload": None}
#: 打开页面自动刷新（refresh=auto）的年龄阈值（分钟）：磁盘缓存比这新就
#: 秒开缓存，比这旧才真查——"每次打开都强刷"实测让 F5 也要白等 ~14 秒。
_LIVE_AUTO_REFRESH_MINUTES = 10
#: force 刷新的并发闸：collect_statuses 一轮几十秒，两个「刷新」同时点会双打
#: 五游戏接口（风控面翻倍）并交错写同一磁盘缓存。持锁做二次新鲜度判定，
#: 等锁期间别人刷完就直接吃他的结果。
_LIVE_LOCK = threading.Lock()

#: 更新安装单飞闸（2026-11-23 对照 Velopack 全局锁文件同款）：install 端点
#: 从「收到请求」到「引擎自杀」有 1.5 秒+ 窗口，UI 连点/重试会 POST 两次——
#: 两次 spawn 两个 Updater 并发覆盖同一安装根。锁持有到引擎自杀为止（进程
#: 死亡锁自然消失）；错误路径显式释放。
_INSTALL_LOCK = threading.Lock()
#: 磁盘缓存：服务重启后也能秒回上一次的结果，页面冷启动绝不该现场打接口
_LIVE_DISK_CACHE = USER_DIR / "data" / "live_cache.json"
#: 同一款游戏两次真实查询的最小间隔（秒）。2026-11-16 用户实测：F5 与连续切
#: 监控开关会在一分钟内对同一 gid 打多次私有接口，只靠 45 秒进程缓存挡不住。
#: 与 45 秒 TTL 叠加而非替换：0-45s 吃进程缓存，45-90s 走间隔护栏回放磁盘缓存，
#: ≥90s 才放行真查。force 也不例外（refresh=1 ≠ 不限频）。
_LIVE_MIN_INTERVAL_SECONDS = 90
#: gid -> 上次真实发起查询的 time.time()（进程内，不落盘——落盘会引入新的文件锁问题）
_LIVE_LAST_QUERY: Dict[str, float] = {}


def _live_disk_read() -> Optional[Dict[str, Any]]:
    """读上一次成功查询落盘的结果；没有或坏了返回 None。"""
    try:
        data = json.loads(_LIVE_DISK_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("games"):
        return None
    return data


def _live_disk_write(payload: Dict[str, Any]) -> None:
    """成功查询后原子落盘；写失败只影响下次秒开，不影响本次返回。

    临时文件用 mkstemp 唯一名：并发刷新时固定 tmp 名会被两个线程交错写、
    replace 撞对方句柄（Windows PermissionError），缓存半截/未更新。
    """
    try:
        _LIVE_DISK_CACHE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".live_cache-", suffix=".tmp", dir=str(_LIVE_DISK_CACHE.parent)
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False))
        os.replace(tmp_name, _LIVE_DISK_CACHE)
    except OSError:
        logger.exception("实时查询磁盘缓存写入失败")


def _split_progress(text: Any) -> Tuple[Optional[int], Optional[int]]:
    """把 "160/200" 拆成 (160, 200)；解析不了返回 (None, None)。

    进度串允许带说明后缀（v2.4.7 起星铁体力是 "240/240 + 备用 298"，
    鸣潮将是 "146/240 + 结晶单质 345"）——只取斜杠两侧的**前导数字**，
    右值遇到第一个非数字字符即停。此前整段 int() 会 ValueError 返回
    (None, None)，实时页资源格被误判成「暂不支持体力查询」。
    """
    if not isinstance(text, str) or "/" not in text:
        return None, None
    left, right = text.split("/", 1)

    def _leading_int(chunk: str) -> Optional[int]:
        digits = ""
        for ch in chunk.strip():
            if ch.isdigit():
                digits += ch
            else:
                break
        return int(digits) if digits else None

    try:
        current = int(left.strip())
    except ValueError:
        return None, None
    return current, _leading_int(right)


def _filter_live_games(payload: Dict[str, Any], enabled: Optional[set]) -> Dict[str, Any]:
    """缓存回放按当前启用账号过滤 games —— 未勾选的游戏不从历史缓存里漏出来。

    缓存写入时的启用集合可能与当前不同（用户中途取消勾选）；
    过滤只影响本响应的 games 列表，不改动缓存文件本身。
    enabled 为 None（配置读不出）时原样返回并已记 warning，不静默。

    新勾选的游戏在旧缓存里没有查询结果：给一张「尚未查询」占位卡而不是
    让它凭空消失（占位符兜底纪律）。秒开铁律不破 —— 占位不现场打接口，
    用户点「刷新」或等下一轮巡检才真正查询；占位标记 placeholder=True，
    前端据此渲染中性卡，且绝不落入缓存文件。"""
    if enabled is None:
        return payload
    out = dict(payload)
    names = _game_display_map()
    games = []
    for g in (payload.get("games") or []):
        if not isinstance(g, dict):
            continue
        g = dict(g)
        # 展示名唯一权威=KNOWN_GAMES（2026-11-13 用户裁决）：live 缓存烙印
        # 的旧名（改名前的快照）不回放，未登记游戏才回落缓存名
        if g.get("game"):
            g["game_name"] = names.get(g["game"], g.get("game_name") or g["game"])
        if g.get("game") in enabled:
            games.append(g)
    present = {g.get("game") for g in games}
    missing = enabled - present
    if missing:
        for gid in sorted(missing):
            games.append({
                "game": gid,
                "game_name": names.get(gid, gid),
                "placeholder": True,
            })
    out["games"] = games
    return out


#: 实时查询风控退避（2026-11-01）：命中米游社风控（1034 需人机验证 / 10041 拒绝）的
#: 游戏，冷却 N 小时内不再对其发起实时查询 —— 每次撞墙都在给风控模型喂信号，
#: 越刷标记越深。只管控制台「刷新」这条实时路径；定时守护进程轮次不退避
#: （每日 3 轮是本工具的权威数据源，跳过会让历史/热力图开天窗）。
_LIVE_RISK_COOLDOWN_HOURS = 6
_LIVE_RISK_FILE = USER_DIR / "data" / "live_risk_backoff.json"
_LIVE_RISK_RE = re.compile(r"retcode\s*=?\s*(1034|10041)\b")


def _live_risk_read() -> Dict[str, Dict[str, Any]]:
    """读风控退避账本；没有/坏了/结构不对返回空账本（不抛）。"""
    try:
        data = json.loads(_LIVE_RISK_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def _live_risk_write(book: Dict[str, Dict[str, Any]]) -> None:
    """原子写回账本；写失败只影响冷却记忆，不影响本次返回。"""
    try:
        _LIVE_RISK_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".risk-", suffix=".tmp", dir=str(_LIVE_RISK_FILE.parent)
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(book, ensure_ascii=False))
        os.replace(tmp_name, _LIVE_RISK_FILE)
    except OSError:
        logger.exception("风控退避账本写入失败")


def _live_risk_active() -> Dict[str, Dict[str, Any]]:
    """当前仍在冷却期的 {game_id: 记录}；过期条目顺手清理写回。"""
    book = _live_risk_read()
    now = time.time()
    active: Dict[str, Dict[str, Any]] = {}
    expired = False
    for gid, rec in book.items():
        try:
            until = float(rec.get("until") or 0)
        except (TypeError, ValueError):
            expired = True
            continue
        if until > now:
            active[gid] = rec
        else:
            expired = True
    if expired:
        _live_risk_write(active)
    return active


def _live_risk_record(errors: List[Tuple[str, Any]]) -> None:
    """把本轮实时查询命中风控的游戏记入账本（until=now+6h，原子写）。

    errors = [(game_id, error_text), ...]；retcode 命中风控族才记账，
    其他失败（网络超时等）不退避 —— 它们不值得跳过后续查询。
    """
    book = _live_risk_read()
    now = time.time()
    hits: List[Tuple[str, int]] = []
    for gid, err in errors:
        m = _LIVE_RISK_RE.search(str(err or ""))
        if not m:
            continue
        retcode = int(m.group(1))
        book[gid] = {
            "until": now + _LIVE_RISK_COOLDOWN_HOURS * 3600,
            "retcode": retcode,
            "recorded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        hits.append((gid, retcode))
    if not hits:
        return
    _live_risk_write(book)
    names = _game_display_map()
    for gid, retcode in hits:
        logger.warning(
            "%s 实时查询命中风控 retcode=%d，进入 %d 小时冷却（期间控制台不再对该游戏发起实时查询）",
            names.get(gid, gid), retcode, _LIVE_RISK_COOLDOWN_HOURS,
        )


def probe_bindings(games: List[str]) -> Dict[str, Any]:
    """绑定完成当场探针（2026-11-19 #78）：对新绑定的游戏各发一次只读查询。

    「绑完当场试一次，行不行马上告诉你」——验证是独立的后续一步，失败也
    不回滚绑定。只展示不计账：不写 cred_health / history / push_history，
    不碰 live 的 90 秒间隔账本与进程/磁盘缓存（直调 collect_statuses，
    querier 生产入口）；风控冷却账本只**纯读**（_live_risk_read——注意
    _live_risk_active 会顺手清理写回，不能用）用于「冷却中」展示，冷却中
    的游戏本轮不发查询，如实标记让用户稍后手动重验。
    """
    from dataclasses import replace as _dc_replace

    from core.config import KNOWN_GAMES, load_config
    from main import collect_statuses

    cfg = load_config(CONFIG_PATH)
    if cfg.query.provider == "mock":
        return {"ok": False, "error": "演示模式（出厂态），没有可验证的真实凭证"}
    # 白名单过滤 + 去重保序（前端传什么不信任什么）
    wanted: List[str] = []
    for g in games or []:
        if g in KNOWN_GAMES and g not in wanted:
            wanted.append(g)
    now_ts = time.time()
    cooled = {
        gid: rec for gid, rec in _live_risk_read().items()
        if float(rec.get("until") or 0) > now_ts
    }
    picked = [a for a in cfg.accounts
              if a.game in wanted and a.game not in cooled]
    statuses = collect_statuses(_dc_replace(cfg, accounts=picked)) if picked else []
    by_game = {s.game_id: s for s in statuses}
    names = _game_display_map()
    out: List[Dict[str, Any]] = []
    for g in wanted:
        entry: Dict[str, Any] = {"game": g, "game_name": names.get(g, g)}
        if g in cooled:
            entry.update(
                cooled=True, cooled_until=datetime.fromtimestamp(
                    float(cooled[g].get("until") or 0)).strftime("%H:%M"))
            out.append(entry)
            continue
        s = by_game.get(g)
        if not any(a.game == g for a in cfg.accounts):
            entry.update(ok=False, error="config.json 里没有该游戏的账号节点")
        elif s is None:
            # collect_statuses 丢行（账号被禁用 / mihoyo_enabled 关）：
            # 不算凭证失败，如实说原因，别让用户误以为凭证坏了
            entry.update(ok=False, error="该游戏本轮未参与查询（账号被禁用或查询开关关闭）")
        else:
            entry.update(ok=bool(s.ok), error=s.error or "",
                         stale=s.stale or "", no_account=s.no_account or "",
                         nickname=s.nickname or "")
        out.append(entry)
    return {"ok": True, "games": out,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


def _annotate_live_cool(
    payload: Dict[str, Any], book: Optional[Dict[str, Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """给 games 里处于冷却期的游戏打 cooled 标记 + 剩余分钟数。

    缓存回放与新查询两条路都要过这里 —— 页面秒开（吃缓存）也必须显示
    「风控冷却中」而不是上次失败的红叉。冷却已过期的条目顺手摘掉旧标记
    （缓存文件里可能残留上一次冷却期的 cooled 标记）；不改缓存文件本身。
    """
    active = book if book is not None else _live_risk_active()
    if not active and not any(
        isinstance(g, dict) and g.get("cooled") for g in (payload.get("games") or [])
    ):
        return payload
    out = dict(payload)
    games: List[Any] = []
    for g in (payload.get("games") or []):
        if not isinstance(g, dict):
            games.append(g)
            continue
        rec = active.get(g.get("game"))
        if rec is not None:
            g = dict(g)
            try:
                until = float(rec.get("until") or 0)
            except (TypeError, ValueError):
                until = time.time()
            g["cooled"] = True
            g["cooldown_left_minutes"] = max(1, int(round((until - time.time()) / 60)))
        elif g.get("cooled"):
            g = dict(g)
            g.pop("cooled", None)
            g.pop("cooldown_left_minutes", None)
        games.append(g)
    out["games"] = games
    return out


def live_status(force: bool = False, family: Optional[str] = None,
                only_games: Optional[List[str]] = None) -> Dict[str, Any]:
    """返回每款游戏的体力 / 理智看板。

    三级缓存：进程内 45 秒 → 磁盘 data/live_cache.json → 现场查询。
    默认（页面加载）只回缓存，绝不现场打接口 —— 秒开是铁律；
    真正的实时查询只发生在「刷新」按钮（force=1），日常数据由定时守护进程落盘。

    family（2026-11-13 后台静默刷新）：mihoyo/kuro/skland——daemon 按游戏族
    间隔只刷本族（森空岛系 180 分，不跟米游社 60 分走）。家族刷新走合并写：
    本族条目替换、他族条目原样保留，缓存不因局部刷新丢数据。

    状态切换不漏旧数据：配置读取在最前 —— 演示模式（provider=mock）在所有
    缓存路径之前短路（切换后任何时代的旧缓存都不会漏出来）；缓存回放按
    当前启用集合过滤（未勾选的游戏不从历史缓存里漏出来）。
    """
    try:
        from core.config import load_config

        cfg = load_config(CONFIG_PATH)
        enabled = {acc.game for acc in cfg.enabled_accounts}
        demo_mode = cfg.query.provider == "mock"
    except Exception as exc:  # noqa: BLE001 - 配置读不出：不做过滤/演示判定，按正常路径走
        logger.warning("live_status 读取配置失败（%s），缓存回放不过滤", exc)
        cfg = None
        enabled = None
        demo_mode = False

    # 演示模式（出厂 provider=mock）：拒绝展示模拟假数据，也绝不让假数据进缓存 ——
    # 首次体验应该是引导扫码，而不是"博士 全部完成"这种会让用户以为软件出错的假面板。
    # 放在所有缓存路径之前：provider 切换后，任何时代写入的旧缓存都不会漏出来。
    if demo_mode:
        return {
            "ok": True,
            "cached": False,
            "demo": True,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "games": [],
        }

    # 风控退避：读当前冷却账本；命中的游戏本轮不再发起实时查询（cfg 过滤账号子集）
    cooled = _live_risk_active()
    if cooled and cfg is not None:
        kept = [a for a in cfg.accounts if a.game not in cooled]
        if len(kept) != len(cfg.accounts):
            skipped_names = "、".join(sorted(
                {_game_display_map().get(a.game, a.game)
                 for a in cfg.accounts if a.game in cooled}
            ))
            logger.warning(
                "实时查询风控冷却中，本轮跳过 %d 款游戏：%s（%d 小时冷却，到点自动恢复）",
                len(cfg.accounts) - len(kept), skipped_names, _LIVE_RISK_COOLDOWN_HOURS,
            )
            from dataclasses import replace as _dc_replace

            cfg = _dc_replace(cfg, accounts=kept)

    cached = _LIVE_CACHE.get("payload")
    if (not force) and cached is not None and (time.time() - float(_LIVE_CACHE.get("at") or 0)) < _LIVE_TTL_SECONDS:
        payload = dict(cached)
        payload["cached"] = True
        return _annotate_live_cool(_filter_live_games(payload, enabled), cooled)

    if not force:
        # 进程缓存不在有效期：直接回上一次落盘的结果（标 stale，前端提示可手动刷新）。
        disk = _live_disk_read()
        if disk is not None:
            payload = dict(disk)
            payload["cached"] = True
            payload["stale"] = True
            return _annotate_live_cool(_filter_live_games(payload, enabled), cooled)

    if cfg is None:
        # 配置读不出（上面已 warning）：没有可靠的游戏清单，返回空面板兜底
        return {"ok": True, "cached": False, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "games": []}

    from core.config import RESOURCE_TASK_NAMES
    from main import collect_statuses

    # 家族/点名刷新（2026-11-13 后台静默刷新）：只查指定集合内启用的游戏；
    # 集合为空就不打任何接口（daemon 侧也有同款判断，这里是兜底）
    target_games: Optional[set] = None
    if only_games:
        from core.config import KNOWN_GAMES as _KG

        target_games = {g for g in only_games if g in _KG} & (enabled or set())
    elif family in ("mihoyo", "kuro", "skland"):
        from core.config import MIHOYO_GAMES, KURO_GAMES, SKLAND_GAMES

        fmap = {"mihoyo": MIHOYO_GAMES, "kuro": KURO_GAMES, "skland": SKLAND_GAMES}
        target_games = set(fmap[family]) & (enabled or set())
    if target_games is not None:
        sub_accounts = [a for a in cfg.accounts if a.game in target_games]
        if not sub_accounts:
            return {"ok": True, "cached": True, "generated_at":
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "games": []}
        from dataclasses import replace as _dc_replace

        cfg = _dc_replace(cfg, accounts=sub_accounts)

    with _LIVE_LOCK:
        # 等锁期间别的线程可能刚完成刷新：二次判定进程缓存新鲜度，
        # 新鲜就吃它的结果——这就是并发「刷新」不去重会双打接口的原因所在
        cached = _LIVE_CACHE.get("payload")
        if cached is not None and (time.time() - float(_LIVE_CACHE.get("at") or 0)) < _LIVE_TTL_SECONDS:
            payload = dict(cached)
            payload["cached"] = True
            return _annotate_live_cool(_filter_live_games(payload, enabled), cooled)

        # 90 秒最短间隔护栏：距上次真实发起查询不足间隔的 gid 本轮不查，
        # 磁盘缓存原样回放；全部被限频就直接回磁盘缓存（ok 恒为 true）。
        # 风控冷却（1034/10041 账本）是另一条独立护栏，两者互不掺和。
        now_ts = time.time()
        skip_gids = {
            a.game for a in cfg.accounts
            if now_ts - float(_LIVE_LAST_QUERY.get(a.game, 0.0)) < _LIVE_MIN_INTERVAL_SECONDS
        }
        throttled_gids: List[str] = []
        if skip_gids:
            kept_accounts = [a for a in cfg.accounts if a.game not in skip_gids]
            throttled_gids = sorted(skip_gids)
            if not kept_accounts:
                disk_all = _live_disk_read()
                if disk_all is not None:
                    payload = dict(disk_all)
                    payload["throttled"] = True
                    logger.info(
                        "实时查询 90 秒间隔护栏：本轮 %d 款游戏全部在间隔内，回放磁盘缓存",
                        len(skip_gids))
                    return payload
                logger.info(
                    "实时查询 90 秒间隔护栏：本轮 %d 款游戏全部在间隔内且无磁盘缓存",
                    len(skip_gids))
                return {"ok": True, "cached": True, "throttled": True,
                        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "games": []}
            from dataclasses import replace as _dc_replace

            cfg = _dc_replace(cfg, accounts=kept_accounts)

        statuses = collect_statuses(cfg)

        # 间隔记账：真实发起过查询的 gid 记 now（成功失败都算——护栏保护的是
        # 「打接口」这个动作本身）；必须在 _LIVE_LOCK 内、写缓存之前
        sent_at = time.time()
        for _acc in cfg.accounts:
            _LIVE_LAST_QUERY[_acc.game] = sent_at

        # 风控记账：本轮命中的 1034/10041 写入冷却账本（6 小时内不再实时查询该游戏）
        _live_risk_record([(s.game_id, s.error) for s in statuses if not s.ok and s.error])

        games: List[Dict[str, Any]] = []
        for status in statuses:
            want = RESOURCE_TASK_NAMES.get(status.game_id)
            resource: Optional[Dict[str, Any]] = None
            for task in status.tasks:
                if task.name != want:
                    continue
                current, maximum = _split_progress(task.progress)
                percent = None
                if current is not None and maximum:
                    # 道具可把体力顶破上限（current > max）：进度条钳 100%，
                    # 数字原样透传（2026-11-07 用户裁决：三态全用真实值）
                    percent = max(0, min(100, round(current * 100 / maximum)))
                resource = {
                    "name": task.name,
                    "progress": task.progress,
                    "current": current,
                    "maximum": maximum,
                    "percent": percent,
                    # 距满秒数（API 恢复字段经 normalize_recover_seconds 统一化；无则 None）
                    "recover": task.recover_seconds,
                }
                break
            games.append({
                "game": status.game_id,
                "game_name": status.game_name,
                "nickname": status.nickname,
                "ok": status.ok,
                "finished": status.finished,
                "error": status.error or "",
                # 非空 = 数据待更新（如当天没进过游戏，接口只给上次登录快照）；
                # 此时 ok 仍为 True、finished 必为 False，前端画中性提示而不是"失败"
                "stale": status.stale or "",
                # 非空 = 这个账号下没这款游戏的角色（勾选并扫码了但没玩过）；
                # 同样不是失败，前端画灰色「未创建角色」，建号后自动生效
                "no_account": status.no_account or "",
                "resource": resource,
                # 判定任务明细（不含"仅展示"的资源项）：供迷你卡显示"委托 0/4"级进度
                "tasks": [
                    {"name": t.name, "done": bool(t.done), "progress": t.progress or ""}
                    for t in status.counted_tasks
                ],
            })

        # 冷却中的游戏本轮没查：从磁盘缓存回放上次结果（标 cooled），没有就诚实占位
        if cooled:
            disk = _live_disk_read()
            disk_games = (
                {g.get("game"): g for g in (disk.get("games") or []) if isinstance(g, dict)}
                if disk else {}
            )
            names = _game_display_map()
            present = {g.get("game") for g in games}
            for gid in sorted(cooled):
                if gid in present:
                    continue
                old = disk_games.get(gid)
                entry = dict(old) if isinstance(old, dict) else {
                    "game": gid, "game_name": names.get(gid, gid), "placeholder": True,
                }
                entry["cooled"] = True
                games.append(entry)

        # 间隔护栏内的游戏：磁盘缓存原样回放（不设 cooled/cooldown——这不是风控，
        # 前端按正常数据渲染）；磁盘也没有就给占位条目，绝不缺游戏
        if throttled_gids:
            disk_int = _live_disk_read()
            disk_games_int = (
                {g.get("game"): g for g in (disk_int.get("games") or []) if isinstance(g, dict)}
                if disk_int else {}
            )
            names_int = _game_display_map()
            present_int = {g.get("game") for g in games}
            for gid in throttled_gids:
                if gid in present_int:
                    continue
                old_int = disk_games_int.get(gid)
                games.append(dict(old_int) if isinstance(old_int, dict) else {
                    "game": gid, "game_name": names_int.get(gid, gid), "placeholder": True,
                })

        # 点名刷新合并写：集合内条目替换、集合外条目原样保留——局部刷新不丢缓存
        if target_games is not None:
            disk = _live_disk_read() or {}
            games.extend(g for g in (disk.get("games") or [])
                         if isinstance(g, dict) and g.get("game") not in target_games)

        # 排序统一按 GAME_ORDER 平台分组序（与游戏概览/报表同口径）——
        # config 账号行是历史追加序（崩3/战双在尾部），不排两页会不一致
        from core.history import GAME_ORDER as _GAME_ORDER
        _order = {g: i for i, g in enumerate(_GAME_ORDER)}
        games.sort(key=lambda item: _order.get(str(item.get("game")), len(_order)))
        payload = {
            "ok": True,
            "cached": False,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "games": games,
        }
        # 进程/磁盘缓存存不带 throttled 的基线（限频标记只属于本次响应，
        # 落进缓存会让 45 秒 TTL 消费者读到误导字段）
        _LIVE_CACHE["at"] = time.time()
        _LIVE_CACHE["payload"] = payload
        _live_disk_write(payload)
        if throttled_gids:
            payload = dict(payload)
            payload["throttled"] = True
        return _annotate_live_cool(payload)


@_serialized_write
def save_schedule(times: Any) -> Dict[str, Any]:
    normalized = _normalize_times(times)
    raw = load_raw_config()
    if not isinstance(raw.get("app"), dict):
        raw["app"] = {}
    raw["app"]["check_times"] = normalized

    # 写盘前用真正的配置解析器复检，保证不会把 config.json 写坏
    from core.config import parse_config

    parse_config(raw, source=CONFIG_PATH)
    save_raw_config(raw)

    result = schedule_status()
    result["message"] = f"已保存 {len(normalized)} 个时间点"
    logger.info("时间点已更新：%s", "、".join(normalized))
    return result


#: 后加游戏的默认昵称（补行用；与 DEFAULT_CONFIG 模板一致）
_MISSING_ACCOUNT_NICKNAMES = {"bh3": "舰长", "pgr": "指挥官", "wuwa": "漂泊者"}


@_serialized_write
def write_mihoyo(
    cookie: str,
    games: List[str],
    roles: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[List[str], List[str]]:
    """把 Cookie 写进**选中的**游戏节点，其余游戏原样不动。

    roles 是扫码后从角色列表接口拿到的 ``{game_id: {uid, server, nickname}}``；
    有角色的游戏顺手回填 uid / server（修掉模板占位 UID 100000000），
    没角色的游戏只写登录态、不回填 —— 查询层会按"未创建角色"提示，
    玩家日后建号**无需重新扫码**。返回 (已写入, 未创建角色) 两个 id 列表。

    2026-11-16 统一原则：绑定只把本次绑定成功的游戏开启监控（enabled=True），
    **从不自动关闭任何游戏**——不在 games 列表 / absent 的行，enabled 原样保留
    （存量 wuwa/bh3/pgr 关着但有凭证，就是靠这条保证不被扫码流程回溯改动）。
    """
    raw = load_raw_config()
    wanted = set(games)
    roles = roles or {}
    updated: List[str] = []
    absent: List[str] = []
    # 存量配置没有的后加游戏（如崩坏3）：**先按出厂模板补行、再进写循环**——
    # 补行若发生在写循环之后，勾选的游戏永远拿不到 Cookie（只勾崩3时更会
    # 触发「没有写入任何游戏」误报；2026-11-09 用户实测无法绑定崩3的根因）
    present = {a.get("game") for a in raw.get("accounts", []) if isinstance(a, dict)}
    missing = wanted - present
    if missing:
        for game in sorted(missing):
            raw.setdefault("accounts", []).append({
                "game": game,
                "nickname": _MISSING_ACCOUNT_NICKNAMES.get(game, game),
                "enabled": True,
                "credential": {},
            })
        logger.info("补建缺失账号节点：%s（存量配置迁移）", ", ".join(sorted(missing)))
    for account in raw.get("accounts", []):
        game = account.get("game")
        if game not in wanted:
            continue
        cred = account.setdefault("credential", {})
        cred["cookie"] = cookie
        cred["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        # 清掉可能残留的分字段，避免覆盖整串 cookie
        cred.pop("ltuid", None)
        cred.pop("ltoken", None)
        role = roles.get(game)
        if role:
            cred["uid"] = str(role.get("uid") or cred.get("uid") or "")
            if role.get("server"):
                cred["server"] = role["server"]
            if role.get("nickname"):
                account["nickname"] = role["nickname"]
            # 2026-11-16 统一原则：只拉起本次绑定成功（进了 updated）的游戏；
            # absent 与不在 games 列表的行不进这个分支，enabled 原样保留
            account["enabled"] = True
            updated.append(str(account.get("nickname") or MIHOYO_GAMES.get(game, game)))
        else:
            absent.append(game)
    if not updated and not absent:
        raise RuntimeError("没有写入任何游戏，请检查 config.json 的 accounts")

    query = raw.setdefault("query", {})
    query["mihoyo_enabled"] = True
    if str(query.get("provider", "")).strip().lower() == "mock":
        query["provider"] = "miyoushe"
    save_raw_config(raw)
    return updated, absent


@_serialized_write
def write_skland(target: str, token: str) -> str:
    """只更新目标游戏的 token，不动另一款。

    2026-11-09：顺手回填真实昵称与角色 ID——此前只写 token，config 里昵称
    一直停在出厂占位（博士/管理员），用户看到的气泡就成了「已开启提醒：博士」。
    """
    raw = load_raw_config()
    hit = False
    for account in raw.get("accounts", []):
        if account.get("game") == target:
            cred = account.setdefault("credential", {})
            cred["token"] = token
            cred["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            # 2026-11-16 统一原则：只拉起本次绑定的 target，另一款永不自动关闭
            account["enabled"] = True
            hit = True
            break
    if not hit:
        raise RuntimeError(f"config.json 里没有 {target} 账号节点")

    # 真实身份（尽力而为：拉不到不影响绑定成功，只是昵称停回退到游戏名）
    identity = _skland_identity(target, token)
    if identity:
        for account in raw.get("accounts", []):
            if account.get("game") != target:
                continue
            cred = account.setdefault("credential", {})
            if identity.get("uid"):
                cred["uid"] = identity["uid"]
            if identity.get("server"):
                cred["server"] = identity["server"]
            if identity.get("nickname"):
                account["nickname"] = identity["nickname"]
            break

    query = raw.setdefault("query", {})
    if str(query.get("provider", "")).strip().lower() == "mock":
        query["provider"] = "miyoushe"
    save_raw_config(raw)

    # 清掉旧 cred 缓存，强制下次用新 token 重新换取
    (USER_DIR / "logs" / f"skland_cred_{target}.json").unlink(missing_ok=True)
    return SKLAND_TARGETS.get(target, target)


def _skland_identity(target: str, token: str) -> Dict[str, str]:
    """从森空岛绑定列表里取该游戏的真实昵称 + 角色 ID。

    返回 {} 表示拿不到（接口异常 / 没绑角色）——调用方按"没读到"处理。
    明日方舟的 ID 是绑定里的 uid；终末地是角色 roleId（serverId 一并存，
    查询层要用 roleId+serverId 拼 sk-game-role 头）。
    """
    try:
        from core.skland_api import get_skland_credentials, list_players

        cred = get_skland_credentials({"token": token}, game=target)
        payload = list_players(cred["cred"], cred["token"])
    except Exception as exc:  # noqa: BLE001 - 回填失败绝不能挡住绑定
        logger.info("森空岛 %s 昵称回填失败（不影响绑定）：%s", target, exc)
        return {}

    game_code = {"arknights": "arknights", "endfield": "endfield"}.get(target, target)
    for item in (payload.get("list") or []):
        if not isinstance(item, dict) or item.get("appCode") != game_code:
            continue
        bindings = [b for b in (item.get("bindingList") or []) if isinstance(b, dict)]
        if not bindings:
            return {}
        binding = bindings[0]
        role = binding.get("defaultRole")
        role = role if isinstance(role, dict) else {}
        if target == "endfield":
            # 终末地昵称在角色上（binding.nickName 常为空串），ID 用 roleId
            nick = clean_nickname(role.get("nickname") or binding.get("nickName"))
            return {
                "nickname": nick,
                "uid": str(role.get("roleId") or "").strip(),
                "server": str(role.get("serverId") or "").strip(),
            }
        return {
            "nickname": clean_nickname(binding.get("nickName")),
            "uid": str(binding.get("uid") or "").strip(),
        }
    return {}


def _kuro_identities(token: str) -> Dict[str, Dict[str, str]]:
    """库街区 token -> {game: {uid: roleId, nickname: roleName}}。

    一个 token 覆盖鸣潮(gameId=3)与战双(gameId=2)，两款角色各自独立。
    任何一款拉不到就跳过那款，绝不影响绑定（绑定的核心是写 token）。
    """
    out: Dict[str, Dict[str, str]] = {}
    try:
        from core.kuro_api import list_roles
    except Exception:  # noqa: BLE001
        return out
    for game, game_id in (("wuwa", 3), ("pgr", 2)):
        try:
            roles = list_roles(token, game_id)
        except Exception as exc:  # noqa: BLE001 - 单款失败不挡绑定
            logger.info("库街区 %s 角色信息拉取失败（不影响绑定）：%s", game, exc)
            continue
        for role in roles or []:
            if not isinstance(role, dict):
                continue
            role_id = str(role.get("roleId") or "").strip()
            if not role_id:
                continue
            out[game] = {
                "uid": role_id,
                "nickname": clean_nickname(role.get("roleName")),
            }
            break
    return out


@_serialized_write
def write_kuro(token: str, nickname: str = "", games: Optional[List[str]] = None) -> str:
    """写入库街区 token。games=None 时两款全写（兼容旧调用）；
    传入白名单（选卡弹窗勾选）则只写勾选的游戏——绑定即开启对应开关。

    与 write_skland 的差异：**可创建账号行**——森空岛上线时全量注入过账号
    节点，鸣潮/战双是后加游戏，存量 config.json 里没有，找不到就按出厂
    模板形态补行。
    """
    raw = load_raw_config()
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M")
    # 库街区角色身份（game -> {uid, nickname}）：一个 token 两款游戏各有角色，
    # 此前只写 token，config 里昵称停在占位（指挥官/漂泊者）。
    identities = _kuro_identities(token)

    def _upsert(game: str, nickname_default: str, enabled: bool) -> str:
        hit = None
        for account in raw.get("accounts", []):
            if isinstance(account, dict) and account.get("game") == game:
                hit = account
                break
        if hit is None:
            hit = {
                "game": game,
                "nickname": nickname_default,
                "enabled": enabled,
                "credential": {},
            }
            raw.setdefault("accounts", []).append(hit)
            logger.info("%s 账号节点不存在，已创建（存量配置迁移）", game)
        cred = hit.setdefault("credential", {})
        cred["token"] = token
        cred["updated_at"] = now_text
        identity = identities.get(game) or {}
        if identity.get("uid"):
            cred["uid"] = identity["uid"]          # 库街区角色 ID（roleId）
        real_name = str(identity.get("nickname") or "").strip()
        if real_name:
            hit["nickname"] = real_name            # 真实角色名优先于传入/占位
        elif nickname:
            hit["nickname"] = nickname
        # 2026-11-16 注记：此处已符合统一原则（绑定=只开本次勾选、从不自动关闭），勿改
        if enabled and not hit.get("enabled", True):
            hit["enabled"] = True   # 绑定了就参与巡检；不想查可以再关开关
        return str(hit.get("nickname") or nickname_default)

    targets = [g for g in (games or ["wuwa", "pgr"]) if g in ("wuwa", "pgr")]
    if not targets:
        targets = ["wuwa", "pgr"]
    wuwa_name = ""
    # token 刷新覆盖**所有已存在的库街区行**（wuwa/pgr）：库街区单设备登录，
    # 每次 sdkLogin 的新 token 会让旧 token 全部失效——只写勾选的游戏会把
    # 另一款的旧 token 留成死凭证（2026-11-09 用户先绑鸣潮再绑战双实测）。
    # 勾选（targets）只决定：激活开关 + 不存在的行要不要创建。
    for game in ("wuwa", "pgr"):
        existing = any(
            isinstance(a, dict) and a.get("game") == game
            for a in raw.get("accounts", [])
        )
        if existing:
            _upsert(
                game,
                "指挥官" if game == "pgr" else (nickname or "漂泊者"),
                enabled=game in targets,
            )
        elif game in targets:
            _upsert(
                game,
                "指挥官" if game == "pgr" else (nickname or "漂泊者"),
                enabled=True,
            )
        if game == "wuwa":
            wuwa_name = next(
                (str(a.get("nickname") or "漂泊者")
                 for a in raw.get("accounts", [])
                 if isinstance(a, dict) and a.get("game") == "wuwa"),
                "漂泊者",
            )

    query = raw.setdefault("query", {})
    if str(query.get("provider", "")).strip().lower() == "mock":
        query["provider"] = "miyoushe"
    save_raw_config(raw)
    return wuwa_name


# --------------------------------------------------------------------------- #
# 登录流程（后台线程）
# --------------------------------------------------------------------------- #


def run_mihoyo(session: Session) -> None:
    """一次扫码，更新所有被勾选的米游社游戏。"""
    from core.mihoyo_api import bind_device, fetch_game_roles, identity_device_id
    from core.mihoyo_login import (
        MiyousheLoginError,
        build_cookie_string,
        create_qr,
        poll_confirm,
    )

    games = list(session.targets)

    def on_status(status: str) -> None:
        if status == "Scanned":
            session.update("scanned", "已扫码，请在手机上点击「确认登录」")

    try:
        device_id = identity_device_id()  # 全局唯一安装级设备：扫码与查询共用
        session.update("creating", f"正在申请二维码（将更新 {len(games)} 款游戏）…")
        qr = create_qr(device_id)
        session.set_qr(qr_png_base64(qr["url"]))
        session.update("waiting", "请用手机「米游社」App 扫码")

        result = poll_confirm(device_id, qr["ticket"], timeout=180, on_status=on_status)

        session.update("writing", "扫码成功，正在绑定设备并写入配置…")
        cookie, creds = build_cookie_string(result["cookies"])

        # 拉一次角色列表：回填 uid/server/昵称 + 识别"账号下没这款游戏"。
        # 这步失败**不阻断登录** —— 降级成旧行为（只写 cookie），界面说明一声。
        roles: Optional[Dict[str, Dict[str, Any]]] = None
        roles_note = ""
        try:
            roles = fetch_game_roles(creds["ltuid"], "", cookie=cookie)
        except Exception as exc:  # noqa: BLE001 - 角色列表只是增强，失败不挡登录
            logger.warning("角色列表拉取失败（跳过 UID 回填）：%s", exc)
            roles_note = "没能自动读取角色列表，uid 保持原样；查询报「缺 UID」时手动填一次"

        # 只重签被勾选游戏的指纹，其余游戏保持原设备身份
        bind_device(device_id, games)
        updated_ids, absent_ids = write_mihoyo(cookie, games, roles=roles)

        labels = [MIHOYO_GAMES.get(g, g) for g in games]
        for game, label in zip(games, labels):
            if game in absent_ids:
                # 没写入角色 = 这个米游社账号没玩过它：正常跳过，不算失败
                session.add_result(
                    game, label, True,
                    "账号下没有这款游戏，已保存登录态，建号后自动生效",
                    skipped=True,
                )
            else:
                session.add_result(game, label, True)
        with session._lock:   # snapshot() 在锁里读 step，写侧同锁防读改写交错
            session.step = len(updated_ids)
        updated = [MIHOYO_GAMES.get(g, g) for g in updated_ids]

        # exe 用户没有 main.py：验证入口写 UI 路径（信息查询页刷新），两端通用
        note = "设备指纹已绑定到这些游戏；到「信息查询」页点「刷新」即可验证"
        if roles_note:
            note = roles_note + "；" + note
        if absent_ids:
            names = "、".join(MIHOYO_GAMES.get(g, g) for g in absent_ids)
            note = f"{names} 未创建角色（已保存登录态，建号后自动生效）；{note}"
        session.update(
            "done",
            f"登录成功，已更新 {len(updated)} 款游戏"
            + (f"，{len(absent_ids)} 款未创建角色" if absent_ids else ""),
            ltuid=creds["ltuid"],
            games=updated,
            note=note,
        )
        logger.info(
            "米游社登录成功，ltuid=%s，写入 %s，未创建角色 %s",
            creds["ltuid"], "、".join(updated_ids) or "-", "、".join(absent_ids) or "-",
        )
    except MiyousheLoginError as exc:
        session.update("error", str(exc))
        logger.warning("米游社登录失败：%s", exc)
    except Exception as exc:  # noqa: BLE001
        session.update("error", f"登录异常：{exc}")
        logger.exception("米游社登录未预期异常")


def _skland_binds_game(token: str, game: str) -> Optional[bool]:
    """该鹰角通行证账号是否绑定了这款游戏。True/False；确认不了返回 None。

    用查询同款的 binding 接口只读确认一次；write_skland 刚清过 cred 缓存，
    这次换取的 cred 会正常写入缓存，查询时直接复用，没有额外成本。
    网络失败**绝不阻断登录**：返回 None，调用方按"未知"降级处理。
    """
    from core.skland_api import get_skland_credentials, list_players

    try:
        credential = get_skland_credentials({"token": token}, game)
        players = list_players(credential["cred"], credential["token"])
    except Exception as exc:  # noqa: BLE001 - 绑定确认只是增强，失败不挡登录
        logger.warning("[skland] %s 绑定状态确认失败（按未知处理）：%s", game, exc)
        return None
    for app in players.get("list") or []:
        if isinstance(app, dict) and app.get("appCode") == game:
            bindings = app.get("bindingList")
            return bool(isinstance(bindings, list) and bindings)
    return False


def run_skland(session: Session) -> None:
    """一次扫码，更新所有被勾选的森空岛游戏（与米游社同一口径）。"""
    from core.hypergryph_login import (
        HypergryphLoginError,
        create_qr,
        exchange_short_token,
        poll_confirm,
    )
    from core.skland_api import verify_ark_token

    targets = list(session.targets)
    labels = [SKLAND_TARGETS.get(t, t) for t in targets]
    joined = "、".join(labels)

    def on_status(status: Any) -> None:
        if status == 101:
            session.update("scanned", "已扫码，请在手机上点击「确认」")

    try:
        session.set_qr("")
        session.update("creating", f"正在申请二维码（将更新 {len(targets)} 款游戏）…")
        qr = create_qr()
        session.set_qr(qr_png_base64(qr["scan_url"]))
        session.update("waiting", f"请用「森空岛」App 扫码，将更新：{joined}")

        scan_code = poll_confirm(qr["scan_id"], timeout=180, on_status=on_status)

        session.update("writing", "正在验证 Token…")
        token = exchange_short_token(scan_code)
        check = verify_ark_token(token)
        if not check.get("ok"):
            raise HypergryphLoginError(
                f"Token 校验未通过：{check.get('message') or check}"
            )
    except HypergryphLoginError as exc:
        session.update("error", f"登录失败：{exc}")
        logger.warning("森空岛登录失败：%s", exc)
        return
    except Exception as exc:  # noqa: BLE001
        session.update("error", f"登录异常：{exc}")
        logger.exception("森空岛登录未预期异常")
        return

    written: List[str] = []
    failed: List[str] = []
    for target, label in zip(targets, labels):
        try:
            name = write_skland(target, token)
        except Exception as exc:  # noqa: BLE001
            session.add_result(target, label, False, str(exc))
            failed.append(label)
            logger.warning("%s 写入失败：%s", label, exc)
            continue

        bound = _skland_binds_game(token, target)
        if bound is False:
            # 没绑这款游戏 = 这个鹰角账号没玩过它：正常跳过，不算失败。
            # 登录态已写入，玩家建号后查询自动生效，无需重新扫码。
            session.add_result(
                target, name, True,
                "账号下没有这款游戏，已保存登录态，建号后自动生效",
                skipped=True,
            )
            logger.info("%s 未创建角色（登录态已写入，跳过）", name)
            continue
        if bound is None:
            session.add_result(
                target, name, True,
                "绑定状态没能确认（网络原因），稍后查询时再看",
            )
        else:
            session.add_result(target, name, True)
        written.append(name)
        with session._lock:   # 同 snapshot() 的读锁口径
            session.step += 1
        logger.info("%s 写入成功（一次扫码）", name)

    if not written:
        session.update("error", "写入失败：" + "、".join(failed))
        return

    note = "各游戏的 cred 缓存已清除，下次运行自动换新"
    if failed:
        note = "未写入：" + "、".join(failed) + "；" + note
    session.update(
        "done",
        f"{len(written)} 款游戏已更新",
        games=written,
        note=note,
    )


# --------------------------------------------------------------------------- #
# 页面
# --------------------------------------------------------------------------- #

#: 前端三件套（ui/index.html + ui/style.css + ui/app.js）：拼装成单页 HTML。
#: 占位符 __STYLE__ / __APP_JS__ 在 _assemble_page() 里填充；
#: __ICON_STAMP__（图标缓存戳）在每次请求时由 _render_page() 替换。
#: 不再是导入时一次性拼装 —— get_page() 按文件指纹缓存，ui/ 改了自动重载。
#: 拆成独立文件的原因：5800 行 Python 里的 HTML 字符串已不可维护（第三阶段架构项）。
_UI_DIR = _BASE / "ui"
_UI_FILES = ("index.html", "style.css", "app.js")

#: 页面缓存。key = 三个文件的 (mtime_ns, size) 指纹，值 = 拼好的单页 HTML。
#: ui/ 里任一文件在磁盘上发生变化，下一次请求就会自动重建，无需重启进程。
_PAGE_LOCK = threading.Lock()
_PAGE_CACHE: Dict[str, Any] = {"stamp": None, "html": ""}


def _assemble_page() -> str:
    """读取 ui/ 三件套拼成单页 HTML（占位符 __STYLE__ / __APP_JS__ 在此填充）。"""
    html = (_UI_DIR / "index.html").read_text(encoding="utf-8")
    return html.replace(
        "__STYLE__", (_UI_DIR / "style.css").read_text(encoding="utf-8")
    ).replace(
        "__APP_JS__", (_UI_DIR / "app.js").read_text(encoding="utf-8")
    )


def get_page() -> str:
    """取当前页面；ui/ 文件有更新则自动重建（前端热重载）。

    首次读取失败会照旧抛 RuntimeError（ui/ 不完整）；此后遭遇读取异常时
    降级返回上一份已加载的页面，避免临时性 IO 抖动让整个控制台 500。
    """
    stamp = None
    try:
        stamp = tuple(
            (f, int((_UI_DIR / f).stat().st_mtime_ns), (_UI_DIR / f).stat().st_size)
            for f in _UI_FILES
        )
    except OSError:
        pass

    with _PAGE_LOCK:
        cached = _PAGE_CACHE["html"]
        if stamp is None:
            if not cached:
                raise RuntimeError("前端三件套读取失败（ui/ 目录不完整？）")
            return cached
        if _PAGE_CACHE["stamp"] != stamp or not cached:
            try:
                _PAGE_CACHE["html"] = _assemble_page()
                _PAGE_CACHE["stamp"] = stamp
            except (OSError, ValueError) as exc:
                # 半写瞬间的 ui/*.js 会以 UnicodeDecodeError（ValueError 子类）
                # 冒出来，只捕 OSError 挡不住——降级必须覆盖两种
                if not cached:
                    raise RuntimeError(
                        f"前端三件套读取失败（ui/ 目录不完整？）：{exc}"
                    ) from exc
                logger.warning("重载前端资源失败，继续使用上一版：%s", exc)
        return _PAGE_CACHE["html"]


# --------------------------------------------------------------------------- #
# HTTP 服务
# --------------------------------------------------------------------------- #

#: 无参只读端点路由表：统一走「异常 → 200 + ok/人话」信封。
#: 2026-10-31 审计：这七个端点此前裸奔，config 读盘抖动会炸穿 do_GET——
#: 3 秒轮询的 /api/schedule 首当其冲（前端每轮刷「连接失败」）。
#: 复杂端点（/api/history、/api/live、/api/start、/api/state 等有自定义
#: 查询参数/会话语义/专用错误文案的）不进此表，保留各自分支。
def _api_autostart() -> Dict[str, Any]:
    from core import autostart

    return autostart.status()


def _api_toasts() -> Dict[str, Any]:
    """取走页内未读通知（消费型队列：取走即标已读）。"""
    from core.toasts import take_unread

    return {"ok": True, "items": take_unread()}


#: 托盘一行一字简称（2026-11-11 用户裁定：崩原星绝舟终鸣战 ↔ 八游戏）
TRAY_GAME_SHORT = {
    "bh3": "崩", "genshin": "原", "starrail": "星", "zzz": "绝",
    "arknights": "舟", "endfield": "终", "wuwa": "鸣", "pgr": "战",
}


def tray_summary_status() -> Dict[str, Any]:
    """托盘摘要（2026-11-11 第一队列 #2）：C# 壳托盘菜单与角标的数据源。

    语义全部在 Python 侧算好，壳只渲染不做计算——「约 Xh 满」的倒计时
    起点只认 Python 端（心跳纪律），壳侧零时间推算。只读 live_cache 磁盘
    快照，绝不现场查询：托盘菜单要秒开，也绝不因看一眼托盘给游戏接口
    添请求（风控安全）。未绑定/无账号的游戏直接不渲染——微软托盘指南：
    不适用项移除而非置灰。text 用 \\n 分行，壳侧 split 即用。

    v2（2026-11-11 用户反馈三连）：①待办在标题里点名——不开控制台就知
    道待办是什么；②游戏名换一字简称（崩原星绝舟终鸣战，用户映射）；
    ③行统一为「简 值/上限」对齐。
    """
    lines: List[str] = []
    pending_descs: List[str] = []
    disk = _live_disk_read()
    try:
        import daemon as daemon_mod

        daemon_ok = bool((daemon_mod.status() or {}).get("running"))
    except Exception:  # noqa: BLE001 - 守护状态读不到按未运行渲染（琥珀提醒）
        daemon_ok = False
    if not daemon_ok:
        pending_descs.append("守护未运行")

    # 手动打卡：待办点名 + 托盘可勾行数据源（id|name|done 单行编码，C# 拆分）
    manual_rows: List[str] = []
    raw = load_raw_config()
    try:
        from core import dailies as _dl

        _items = (raw.get("app") or {}).get("custom_dailies") or []
        for _it in _items:
            if not isinstance(_it, dict) or not _it.get("enabled", True):
                continue
            _id = str(_it.get("id") or "")
            _name = str(_it.get("name") or "?").replace("|", " ").replace("\n", " ")
            _itd = dict(_it)
            _itd.setdefault("reset", _dl.DEFAULT_RESET)
            # 非计划日的项整个不显示（2026-11-13 用户裁决：与概览卡同口径，
            # 还没到星期四就不显示）——也不进待办点名
            _planned = _dl._planned_weekday(_itd, _dl._period_date(_itd))
            if not _planned:
                continue
            _done = _dl.is_done(_itd, CONFIG_PATH.parent)
            manual_rows.append(f"{_id}|{_name}|{1 if _done else 0}")
            if not _done:
                pending_descs.append(f"{_name} 未清")
    except Exception:  # noqa: BLE001 - 手动打卡读不到不影响托盘主面
        pass

    # 启用集过滤（2026-11-12 实锤：live 缓存可能含已停用游戏的旧数据——
    # 崩3 已停用仍泄漏进托盘；缓存缺失的启用游戏也要有行——方舟/终末地隐身实锤）
    enabled_games = {str(a.get("game")) for a in (raw.get("accounts") or [])
                     if a.get("enabled")}
    cached_games: Dict[str, Dict[str, Any]] = {}
    if disk and disk.get("games"):
        for g in disk.get("games") or []:
            if isinstance(g, dict) and g.get("game"):
                cached_games[str(g["game"])] = g

    if disk and disk.get("games"):
        try:
            gen = datetime.strptime(
                str(disk.get("generated_at") or ""), "%Y-%m-%d %H:%M:%S")
            age = max(0, int((datetime.now() - gen).total_seconds()))
        except ValueError:
            age = None
        stale_tag = ""
        if age is not None and age >= 7200:
            stale_tag = f"（数据 {age // 3600}h 前）"
        from core.history import GAME_ORDER as _GAME_ORDER
        ordered_ids = ([g for g in _GAME_ORDER
                        if g in cached_games or g in enabled_games]
                       + [g for g in cached_games if g not in _GAME_ORDER])
        for gid in ordered_ids:
            g = cached_games.get(gid)
            if g is None:
                # 启用但缓存缺失（刚勾选还没查过）：占位行，等下次巡检/刷新进缓存
                short = TRAY_GAME_SHORT.get(gid) or gid[:1]
                lines.append(f"{short}　  —/  — · 待巡检")
                continue
            if not g.get("enabled", True) or gid not in enabled_games:
                continue  # 已停用：不渲染（移除而非置灰）
            if g.get("no_account"):
                continue  # 未绑定：不渲染（移除而非置灰）
            key = str(g.get("game") or "")
            short = TRAY_GAME_SHORT.get(key) or str(g.get("game_name") or "?")[:1]
            if g.get("error"):
                lines.append(f"{short}　查询失败：{str(g.get('error'))[:18]}")
                continue
            res = g.get("resource") or {}
            cur, mx = res.get("current"), res.get("maximum")
            if not isinstance(cur, int) or not isinstance(mx, int) or mx <= 0:
                continue
            if cur >= mx:
                pending_descs.append(f"{short} 已满")
                # 排版纪律（2026-11-11 用户反馈：托盘行要排列整齐）：定宽三段式——
                # 简称 + 全角空格（2026-11-12 用户反馈：名字与数据再拉开）+
                # 值/上限各右对齐 3 位 + 「 · 」+ 12 半角宽尾列。
                # 2026-11-12 再裁（用户）：尾列「约 X 满」与「已满」同尾字，真已满
                # 不显眼——已满行加 ★ 前缀成为唯一带符号的行，未满行去「满」尾字。
                lines.append(f"{short}　{cur:>3}/{mx:>3} · ★ 已满，可清{stale_tag}")
                continue
            recover = res.get("recover")
            tail = ""
            if isinstance(recover, (int, float)) and recover > 0:
                eff = max(0, int(recover) - (age or 0))
                if eff > 0:
                    h, m = eff // 3600, (eff % 3600) // 60
                    # 「剩」字每行重复=零信息填充（2026-11-12 用户裁），裸排时间；
                    # 列位固定（「 · 」后必是回满倒计时），扫读习惯一次建立
                    tail = f"{min(h, 99):>2}h{m:02d}"
                    if eff <= 7200:
                        hm = f"{h}h{m:02d}m" if h else f"{m}m"
                        pending_descs.append(f"{short} 将满({hm})")
            if tail:
                lines.append(f"{short}　{cur:>3}/{mx:>3} · {tail}{stale_tag}")
            else:
                lines.append(f"{short}　{cur:>3}/{mx:>3}{stale_tag}")

    if pending_descs:
        # 标题只点名第一条（2026-11-14 用户裁决：托盘菜单宽度由最长行决定，
        # 全量拼进标题把菜单撑成宽条；完整明细本来就在下面的逐行里）
        n = len(pending_descs)
        headline = f"待办 {n} 项：{pending_descs[0]}" + (f" 等 {n - 1} 项" if n > 1 else "")
    elif not lines:
        headline = "尚无巡检数据"
    else:
        headline = "全部正常"
    if len(headline) > 50:  # NotifyIcon.Text 上限 63 字符，标题留余量
        headline = headline[:49] + "…"
    return {
        "ok": True,
        "headline": headline,
        "pending": bool(pending_descs),
        "daemon_ok": daemon_ok,
        "manual": "\n".join(manual_rows),
        "text": "\n".join(lines[:12]),
        "generated_at": str((disk or {}).get("generated_at") or ""),
    }


def _diag_collect_secrets() -> List[str]:
    """从真实配置提取敏感值清单（口径对齐 _build_release.collect_secrets）。

    刻意用「真实值扫描」而不是模式猜测：打包门禁已实证这套清单能拦住
    带密文件出库。诊断包导出前拿它做 fail-closed 终检。
    """
    found: set = set()

    def _add(value: object) -> None:
        text = str(value or "").strip()
        if len(text) >= 8:
            found.add(text)

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 配置读不到就没什么可扫的，空清单
        data = {}
    for node in (data.get("push") or {}).values():
        if isinstance(node, dict):
            for field in ("key", "sendkey", "device_key", "webhook", "secret"):
                _add(node.get(field))
    for account in data.get("accounts") or []:
        cred = account.get("credential") or {}
        _add(cred.get("uid"))
        _add(cred.get("token"))
        for part in str(cred.get("cookie") or "").replace(";", " ").split():
            if "=" in part:
                _add(part.split("=", 1)[1])
    return [t for t in found if t]


def _diag_body(path: Path) -> str:
    """读更新链状态文件；不存在或空时给明确占位，不裸空（2026-11-24 规范审查）。

    诊断包里裸 0 字节文件看起来像采集失败；更新成功后 download_state.json
    本就为空，应如实标注「本次无数据」而非让人误判。
    """
    if not path.exists():
        return "[本次无数据] 文件不存在"
    body = path.read_text(encoding="utf-8")
    return body if body.strip() else "[本次无数据] 文件为空"


def _manifest_line(arc_name: str, text: str, desc: str) -> str:
    """MANIFEST 单行：报字节数，与 ZIP 内 encode('utf-8') 落盘口径一致。

    此前用 len(text) 报字符数，中文 1 字符=3 字节，声明与实际对不上，
    核包时会误判「文件被改过」（2026-11-24 规范审查缺陷1）。
    """
    return f"  {arc_name}  {len(text.encode('utf-8'))} 字节  — {desc}"


def diag_export(target_dir: object = None) -> Dict[str, Any]:
    """一键导出脱敏诊断包（2026-11-11 第一队列 #3）。

    模仿 sosreport/must-gather 的「收集-脱敏-附单」三段式 + CouchMode 的
    内容透明化（MANIFEST 逐文件说明）+ AppProfileSafe 的时间戳命名。
    安全三闸：配置值脱敏 → 真实密钥值全量扫描（命中即中止，fail-closed，
    打包门禁同款）→ 逐文件非空自检（hyperwhisper 0 字节事故教训）。
    落桌面，绝不静默上传。
    """
    import zipfile
    import platform
    import sys as _sys
    from datetime import timedelta as _td

    install_root = Path(__file__).resolve().parent.parent
    banned = _diag_collect_secrets()
    entries: List[List[Any]] = []  # [zip内名, 文本, manifest说明]
    warnings: List[str] = []

    # 1) 环境元数据
    env_rows = [
        f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"HopOn 版本: {_try_current_version()}",
        f"Python: {_sys.version.split()[0]}",
        f"系统: {platform.platform()}",
        f"数据根: {USER_DIR}",
        f"安装根: {install_root}",
    ]
    try:
        import daemon as daemon_mod

        st = daemon_mod.status() or {}
        env_rows.append(
            f"守护进程: pid={st.get('pid')} running={st.get('running')}"
            f" heartbeat={st.get('heartbeat_age')}")
    except Exception as exc:  # noqa: BLE001
        env_rows.append(f"守护进程: 状态读取失败（{exc}）")
    disk = _live_disk_read()
    env_rows.append(f"live 快照: {str((disk or {}).get('generated_at') or '无')}")
    entries.append(["env.txt", "\n".join(env_rows) + "\n", "版本/系统/守护进程状态"])

    # 2) 近 7 天日志（时间窗裁剪，不是全量）
    cutoff = (datetime.now() - _td(days=7)).strftime("%Y-%m-%d")
    log_targets = [
        ("dailycheck.log", USER_DIR / "logs" / "dailycheck.log", "引擎巡检日志"),
        ("shell.log", install_root / "logs" / "shell.log", "C# 壳日志"),
        # engine.log 实际落点=数据根（shell/engine_app.py: LOGS_DIR=_user_root()/logs），
        # 2026-11-20 修复：此前误按安装根收集，朋友机诊断包里恒 0 字节
        ("engine.log", USER_DIR / "logs" / "engine.log", "引擎启动日志（数据根）"),
    ]
    for arc_name, path, desc in log_targets:
        text = ""
        try:
            if path.exists():
                rows = path.read_text(encoding="utf-8", errors="ignore").splitlines()
                keep: List[str] = []
                in_window = False
                for row in rows:
                    if len(row) >= 19 and row[:4].isdigit() and row[4] == "-":
                        in_window = row[:10] >= cutoff
                    if in_window:
                        keep.append(row)
                text = "\n".join(keep[-1200:])
        except OSError as exc:
            text = f"[读取失败: {exc}]"
        if not text:
            warnings.append(f"{arc_name}: 窗口内无内容")
        entries.append([f"logs/{arc_name}", text + ("\n" if text else ""),
                        f"{desc}（近 7 天，最多 1200 行）"])
    # Windows 崩溃事件（2026-11-22 朋友机引擎静默死亡取证）：Event Viewer
    # 的 Application 日志 EventID 1000（应用错误）/1001（WER 报告）由系统
    # 自动记录崩溃的故障模块与异常码——引擎静默死亡（pythonw 无 stderr、
    # 崩溃不留日志）的死因取证主通道。wevtutil 系统自带零依赖；采集失败
    # 只占位，不影响诊断包其余部分。
    try:
        import subprocess

        _ev = subprocess.run(
            ["wevtutil", "qe", "Application",
             "/q:*[System[(EventID=1000) or (EventID=1001)]]",
             "/c:40", "/rd:true", "/f:text"],
            capture_output=True, timeout=30,
            encoding="utf-8", errors="replace")  # 取证关键字段（EventID/P1）均为 ASCII；中文装饰字段乱码不影响分析
        ev_text = (_ev.stdout or "").strip()
        if not ev_text and _ev.stderr:
            ev_text = f"[wevtutil 输出: {_ev.stderr.strip()[:200]}]"
        entries.append(["events_app_error.txt", ev_text + "\n",
                        "Windows 应用崩溃事件（EventID 1000/1001，近 40 条）"])
    except Exception as exc:  # noqa: BLE001 - 取证失败不影响诊断包其余部分
        entries.append(["events_app_error.txt", f"[采集失败: {exc}]\n",
                        "Windows 应用崩溃事件（采集失败）"])

    # 2b) 更新链状态原文（2026-11-22 朋友机「点立即安装没反应」取证盲区修复）：
    # download_state.json 的 message 字段=上次下载/验签失败的报错原文，
    # update_check.json 的 latest/current=检查更新视角——这两个文件的内容
    # 是判定「一键更新卡在哪一步」的决定性证据，此前诊断包只有键名没有内容。
    for arc_name, data_file in (("download_state.json", USER_DIR / "data" / "download_state.json"),
                                ("update_check.json", USER_DIR / "data" / "update_check.json")):
        try:
            entries.append([arc_name, _diag_body(data_file), f"更新链状态原文（{data_file.name}）"])
        except OSError as exc:
            entries.append([arc_name, f"[读取失败: {exc}]", "更新链状态原文（读取失败）"])

    # 3) 配置快照（值脱敏）
    def _mask(node: Any) -> Any:
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if re.search(r"key|token|secret|cookie|password|webhook|cred",
                             str(k), re.IGNORECASE) and v:
                    out[k] = "***MASKED***"
                else:
                    out[k] = _mask(v)
            return out
        if isinstance(node, list):
            return [_mask(x) for x in node]
        return node

    cfg_text = ""
    try:
        cfg_text = json.dumps(_mask(json.loads(
            CONFIG_PATH.read_text(encoding="utf-8"))), ensure_ascii=False, indent=1)
    except Exception as exc:  # noqa: BLE001
        cfg_text = f"[配置读取失败: {exc}]"
    entries.append(["config_sanitized.json", cfg_text + "\n",
                    "配置快照（敏感字段已置 MASKED，结构保留）"])

    # 4) 数据摘要（只摘要不打包全量）
    data_dir = USER_DIR / "data"
    sum_rows = [f"数据目录: {data_dir}", ""]
    try:
        for f in sorted(data_dir.glob("*.json")):
            note = ""
            try:
                blob = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(blob, list):
                    note = f"{len(blob)} 条"
                elif isinstance(blob, dict):
                    note = "键: " + ", ".join(list(blob.keys())[:8])
            except Exception:  # noqa: BLE001
                note = "（非 JSON 或读取失败）"
            sum_rows.append(f"{f.name}  {f.stat().st_size} 字节  {note}")
    except OSError as exc:
        sum_rows.append(f"数据目录读取失败: {exc}")
    entries.append(["data_summary.txt", "\n".join(sum_rows) + "\n",
                    "数据文件名/大小/条数摘要（不含内容）"])

    # 安全终检 ①：真实密钥值扫描（fail-closed）
    for arc_name, text, _desc in entries:
        for token in banned:
            if token in text:
                logger.warning("诊断包导出中止：%s 命中敏感值（不记录值本身）", arc_name)
                return {"ok": False,
                        "message": "检测到敏感值泄漏风险，已中止导出。请反馈给开发者排查脱敏清单。"}
    # 安全终检 ②：掩码后仍出现长 token 形状 = 脱敏清单有漏
    for arc_name, text, _desc in entries:
        if re.search(r"eyJ[A-Za-z0-9_-]{20,}", text):
            return {"ok": False, "message": "检测到疑似令牌形状的内容，已中止导出。"}

    # MANIFEST（透明化：逐文件说明 + 行数）
    man_rows = ["HopOn 诊断包内容清单", ""]
    for arc_name, text, desc in entries:
        man_rows.append(_manifest_line(arc_name, text, desc))
    if warnings:
        man_rows.append("")
        man_rows.append("警告: " + "; ".join(warnings))
    entries.insert(0, ["MANIFEST.txt", "\n".join(man_rows) + "\n", "本包内容清单"])

    # 逐文件非空自检（MANIFEST 除外）
    empty = [name for name, text, _d in entries
             if name != "MANIFEST.txt" and len(text.strip()) == 0]
    if len(empty) == len([e for e in entries if e[0] != "MANIFEST.txt"]):
        return {"ok": False, "message": "诊断包内容全为空，已中止导出（请检查日志目录）。"}

    # 落盘：用户所选目录 > 桌面 > 数据目录兜底（块B 2026-11-24）
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    chosen = str(target_dir).strip() if isinstance(target_dir, str) else ""
    if chosen and os.path.isdir(chosen):
        target_dir = Path(chosen)
    else:
        target_dir = Path.home() / "Desktop"
        if not target_dir.is_dir():
            target_dir = USER_DIR / "diagnostics"
            target_dir.mkdir(parents=True, exist_ok=True)
    zip_path = target_dir / f"HopOn_Diagnostics_{stamp}.zip"
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for arc_name, text, _desc in entries:
                zf.writestr(arc_name, text.encode("utf-8"))
    except OSError as exc:
        return {"ok": False, "message": f"诊断包写入失败：{exc}"}
    logger.info("诊断包已导出：%s（%d 个文件，%d 字节）",
                zip_path, len(entries), zip_path.stat().st_size)
    return {"ok": True, "path": str(zip_path), "files": [e[0] for e in entries],
            "empty": empty, "warnings": warnings,
            "message": f"诊断包已导出：{zip_path}"}


def _diag_reveal_target(path: object) -> Optional[str]:
    """把「打开所在文件夹」请求里的 path 收敛成可安全定位的诊断包 zip。

    安全边界（2026-11-24 块A）：前端 path 可能来自 URL/误传/注入，绝不能
    拿 explorer 去定位任意文件。只接受真实存在的、按本工具命名规则生成的
    诊断包 zip（HopOn_Diagnostics_YYYYMMDD_HHMMSS.zip）；其余一律 None。
    """
    import re as _re

    if not isinstance(path, str) or not path:
        return None
    name = os.path.basename(path)
    if not _re.fullmatch(r"HopOn_Diagnostics_\d{8}_\d{6}\.zip", name):
        return None
    try:
        candidate = Path(path)
        if not candidate.is_file():
            return None
    except (OSError, ValueError):
        return None
    return str(candidate)


def _diag_pick_folder(initial_dir: object, runner=None) -> Optional[str]:
    """弹 Windows 原生「选择文件夹」对话框，返回用户选中的目录；取消返回 None。

    引擎便携 Python 无 tkinter，改用系统自带 PowerShell 的 FolderBrowserDialog：
    - 零新依赖、不写临时脚本、三运行模式通用；
    - runner(cmd, timeout)->(stdout, stderr) 可注入测试（默认真跑 PowerShell）。
    初始目录做单引号转义（PowerShell 里 ' 用 '' 表示），防路径注入。
    """
    import subprocess as _sp

    if runner is None:
        def runner(cmd, timeout):
            proc = _sp.run(cmd, capture_output=True, text=True, timeout=timeout)
            return proc.stdout, proc.stderr

    initial = str(initial_dir or "").strip()
    if not initial or not os.path.isdir(initial):
        initial = str(Path.home())
    safe = initial.replace("'", "''")
    # 纯 ASCII 命令体：中文放 Python 侧输出，避免 PS 5.1 无 BOM 的 GBK 解码坑。
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
        "$d.SelectedPath='%s';"
        "if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){"
        "Write-Output ('PICKED|'+$d.SelectedPath)}" % safe
    )
    ps_path = str(Path(os.environ.get("WINDIR", r"C:\Windows")) /
                  "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
    cmd = [ps_path, "-NoProfile", "-STA", "-Command", ps]
    try:
        out, _err = runner(cmd, 300)  # 用户最多 5 分钟做选择
    except Exception:  # noqa: BLE001 - 超时/未找到/对话框异常都当取消
        return None
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("PICKED|"):
            picked = line[len("PICKED|"):].strip()
            return picked or None
    return None


def _try_current_version() -> str:
    try:
        from core.update import current_version

        return current_version()
    except Exception:  # noqa: BLE001 - 版本读不到不挡导出
        return "?"


def av_check() -> Dict[str, Any]:
    """杀软拦截自检（2026-11-11 第一队列 #4）：判「是不是被杀软办了」。

    三信号（案例模仿：文件消失=业界通用第一信号；Defender 检测记录=
    MSFT_MpThreatDetection 的 Resources 字段含被隔离路径；心跳=行为信号）：
      A 关键文件存在性——被隔离 = 文件从磁盘消失（零权限、跨杀软通用）
      B Defender 检测记录——WMI 非提权尝试，读不到就如实说，绝不装懂
      C 守护心跳——文件都在但守护未运行 → 只能标「疑似」
    绝不自动改杀软设置（只读自检）；处置步骤与上报入口由前端静态指引承载。
    """
    import hashlib
    import subprocess as _sp

    _NOWIN = 0x08000000 if os.name == "nt" else 0
    install_root = Path(__file__).resolve().parent.parent
    targets = [
        ("HopOn.exe", install_root / "HopOn.exe"),
        ("engine\\python\\pythonw.exe",
         install_root / "engine" / "python" / "pythonw.exe"),
        ("engine\\daemon.py", install_root / "engine" / "daemon.py"),
        ("engine\\app.py", install_root / "engine" / "app.py"),
    ]
    missing = [name for name, p in targets if not p.exists()]

    defender_hits: List[str] = []
    defender_note = "未读取到与 HopOn 相关的检测记录"
    try:
        proc = _sp.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance -Namespace root\\Microsoft\\Windows\\Defender "
             "-ClassName MSFT_MpThreatDetection | "
             "Select-Object -ExpandProperty Resources"],
            capture_output=True, timeout=10,
            creationflags=_NOWIN,
        )
        text = proc.stdout.decode("utf-8", errors="ignore")
        root_low = str(install_root).lower()
        for row in text.splitlines():
            row = row.strip()
            if row and ("hopon" in row.lower() or root_low in row.lower()):
                defender_hits.append(row[:120])
    except Exception as exc:  # noqa: BLE001 - 无 Defender / 权限拒绝 / 超时
        defender_note = f"无法读取检测记录（{exc.__class__.__name__}，可能非 Defender 或权限不足）"

    sha = ""
    exe = install_root / "HopOn.exe"
    if exe.exists():
        try:
            h = hashlib.sha256()
            with open(exe, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            sha = h.hexdigest()
        except OSError:
            sha = ""

    daemon_running = False
    try:
        import daemon as daemon_mod

        daemon_running = bool((daemon_mod.status() or {}).get("running"))
    except Exception:  # noqa: BLE001
        daemon_running = False

    if missing or defender_hits:
        verdict = "blocked"
        parts = []
        if missing:
            parts.append(f"缺失 {len(missing)} 个关键文件（{'、'.join(missing)}）")
        if defender_hits:
            parts.append("Defender 检测记录命中 HopOn 路径")
        message = "疑似被杀软拦截：" + "；".join(parts) + "。按下方步骤加白名单/恢复。"
    elif not daemon_running:
        verdict = "suspect"
        message = "关键文件都在，但守护进程未运行——可能是被拦截，也可能是其他原因（看运行日志）。"
    else:
        verdict = "clear"
        message = "未发现拦截迹象。本检查只覆盖能读到的信号，仅供参考。"
    return {
        "ok": True,
        "verdict": verdict,
        "message": message,
        "missing": missing,
        "defender_hits": defender_hits,
        "defender_note": defender_note,
        "daemon_running": daemon_running,
        "sha256": sha,
        "install_root": str(install_root),
        "releases_url": "https://github.com/QianYiXi0723/HopOn/releases",
    }


_GET_SIMPLE_ROUTES: Dict[str, Any] = {
    "/api/accounts": accounts_status,
    "/api/env": push_status,
    "/api/push": push_detail,
    "/api/device": device_status,
    "/api/toasts": _api_toasts,
    "/api/schedule": schedule_status,
    "/api/settings": thresholds_status,
    "/api/autostart": _api_autostart,
    "/api/tray_summary": tray_summary_status,
    "/api/diag_export": diag_export,
    "/api/av_check": av_check,
    "/api/custom_dailies": custom_dailies_status,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "DailyCheckLogin/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # 访问日志写 engine.log
        # 2026-11-22 朋友机取证盲区修复：每请求一行进 engine.log——此前静音
        # 导致「页面 POST 是否到达引擎」永远无法判定（连发四轮修不全）。
        # /api/env GET 排除（探活 5 秒一次，纯噪音）。
        try:
            line = fmt % args
            if "GET /api/env" in line:
                return
            logger.info("HTTP %s", line)
        except Exception:  # noqa: BLE001 - 日志失败不影响请求处理
            pass

    def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _serve_static(self, path: str) -> None:
        """只吐 static/ 下的图片，别的路径一律 404（顺手挡住 ../ 穿越）。"""
        rel = path[len("/static/"):]
        target = (STATIC_DIR / rel).resolve()
        try:
            target.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        ctype = STATIC_TYPES.get(target.suffix.lower())
        if ctype is None or not target.is_file():
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        try:
            body = target.read_bytes()
        except OSError:
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        # 不缓存：图标是本地文件，重下一遍的成本远低于「换了图还看旧图」的排查成本
        self._send(200, body, ctype, cache="no-store")

    def _json(self, payload: Dict[str, Any], code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path in ("/", "/index.html"):
            self._send(200, _render_page().encode("utf-8"), "text/html; charset=utf-8")
            return

        if parsed.path == "/api/mobile/status":
            from core.mobile_gateway import get_active_gateway
            self._json({"ok": True, "enabled": get_active_gateway() is not None})
            return

        if parsed.path == "/api/mobile/pair-qr":
            # 本机 UI 取配对二维码（地址+一次性码都在码里，用户不用手填）。
            # 仅本机可访问：主控制台只绑 127.0.0.1，且此处不额外开放局域网。
            from core.mobile_gateway import get_active_gateway, lan_ip
            gw = get_active_gateway()
            if gw is None:
                self._json({"ok": False, "message": "手机访问未开启"}, 404)
                return
            self._json({"ok": True, **gw.issue_pair_qr(lan_ip())})
            return

        if parsed.path == "/api/start":
            provider = (query.get("provider") or [""])[0]
            raw = (query.get("targets") or [""])[0]
            targets = [t.strip() for t in raw.split(",") if t.strip()]

            if provider == "mihoyo":
                bad = [t for t in targets if t not in MIHOYO_GAMES]
                if not targets or bad:
                    self._json(
                        {"ok": False, "message": f"无效的游戏参数：{', '.join(bad) or '空'}"},
                        400,
                    )
                    return
                session = Session("mihoyo", targets)
                _put(session)
                threading.Thread(target=run_mihoyo, args=(session,), daemon=True).start()
                self._json({"ok": True, "sid": session.sid, "labels": session.labels()})
                return

            if provider == "skland":
                bad = [t for t in targets if t not in SKLAND_TARGETS]
                if not targets or bad:
                    self._json(
                        {"ok": False, "message": f"无效的游戏参数：{', '.join(bad) or '空'}"},
                        400,
                    )
                    return
                session = Session("skland", targets)
                _put(session)
                threading.Thread(target=run_skland, args=(session,), daemon=True).start()
                self._json({"ok": True, "sid": session.sid, "labels": session.labels()})
                return

            self._json({"ok": False, "message": "未知的 provider"}, 400)
            return

        # ---- 无参只读端点：路由表 + 统一错误信封（异常必 200+人话，绝不 500）----
        simple = _GET_SIMPLE_ROUTES.get(parsed.path)
        if simple is not None:
            try:
                self._json(simple())
            except Exception:  # noqa: BLE001 - 前端 3 秒轮询这些端点，500 会刷屏
                logger.exception("GET %s 失败", parsed.path)
                self._json({
                    "ok": False,
                    "message": "读取失败：服务端内部错误（详见运行日志）",
                })
            return

        if parsed.path == "/api/history":
            try:
                days = int((query.get("days") or ["7"])[0])
            except (TypeError, ValueError):
                days = 7
            from core import history

            # 列 = 仅当前启用账号 —— 未勾选的游戏从报表/热力图/KPI 全部消失
            # （数据保留在 history.json，重新勾选即原样恢复）。
            enabled_games = _enabled_game_ids()

            # 任何异常都必须变成"200 + 空报表"，绝不能 500：
            # 前端 fetch 拿到 500 时 r.json() 会失败，弹的是一句让人摸不着头脑的
            # "无法读取历史记录"红字，而真实原因只是 data/history.json 还没生成。
            # 月报下钻（2026-11-06）：?month=YYYY-MM → 整月格子 + 当月统计
            month_q = (query.get("month") or [""])[0]
            if month_q:
                try:
                    payload = history.month_grid(month_q, enabled_games=enabled_games)
                except Exception:  # noqa: BLE001
                    logger.exception("读取月报失败，返回空月历")
                    payload = {"ok": False, "message": "读取月报失败（详见运行日志）",
                               "days": [], "games": []}
                _attach_manual(payload, month_mode=True)
                self._json(payload)
                return
            try:
                payload = history.summary(days=days, enabled_games=enabled_games)
            except Exception:  # noqa: BLE001
                logger.exception("读取历史记录失败，返回空报表")
                payload = history.empty_payload(days=days)
                payload["message"] = "读取历史记录失败，已显示空报表（详见 logs/dailycheck.log）"
            _attach_manual(payload, month_mode=False)
            self._json(payload)
            return

        if parsed.path == "/api/state":
            sid = (query.get("sid") or [""])[0]
            session = _get(sid)
            if session is None:
                self._json({"state": "error", "message": "会话已失效，请重新开始"}, 404)
                return
            self._json(session.snapshot())
            return

        if parsed.path == "/api/update":
            # 检查更新（**只查不装**，2026-11-05）。refresh=1 强制走网络，
            # 默认读 6 小时缓存——开控制台不该每次都去打 GitHub。
            # 任何失败都返回 200 + ok:False，不打扰用户。
            force = (query.get("refresh") or ["0"])[0].lower() in ("1", "true", "yes")
            try:
                from core import update as _update

                if force:
                    self._json(_update.check(force=True).to_dict())
                else:
                    cached = _update.peek()
                    self._json(cached or _update.check().to_dict())
            except Exception as exc:  # noqa: BLE001 - 查更新失败不该 500
                logger.debug("检查更新失败：%s", exc)
                self._json({"ok": False, "message": "检查更新失败：可稍后重试"})
            return

        if parsed.path == "/api/update/download":
            # 站内下载进度查询（GET）：下载在引擎后台线程跑（Gitee 主通道
            # 实测 1.9MB/s），前端轮询状态；发起=POST /api/update/download，
            # 取消=POST /api/update/download/cancel。
            from core import update as _update

            self._json(_update.download_status())
            return

        if parsed.path == "/api/update/result":
            # 一次性更新结果（2026-11-23 重做）：UI 首次加载时消费，替代前端
            # localStorage rl-updating 标记。独立分支（非 _GET_SIMPLE_ROUTES
            # ——那是无参只读轮询端点，消费型 GET 放进去会被首轮轮询吞掉）。
            # 读后即清（take_update_result），幂等：拿不到返回 present=False，
            # 前端不报错、下次加载再试（BUG A 教训）。
            from core import update as _update

            try:
                result = _update.take_update_result()
                self._json({"ok": True, "present": result is not None,
                            "result": result, "current": _update.current_version()})
            except Exception as exc:  # noqa: BLE001
                self._json({"ok": False, "message": str(exc)[:150]})
            return

        if parsed.path == "/api/logtail":
            try:
                n = int((query.get("n") or ["200"])[0])
            except (TypeError, ValueError):
                n = 200
            useful = (query.get("useful") or ["0"])[0].lower() in ("1", "true", "yes")
            self._json(log_tail(max(10, min(n, 500)), useful=useful))
            return

        if parsed.path == "/api/push_history":
            # 最近推送（GET）：读 data/push_history.json（notifier 每次真实发送都落一条），
            # 不再解析日志 —— desktop.log / dailycheck.log 分家曾让前端永远筛不到
            try:
                from core.notifier import load_push_history

                self._json({"ok": True, "items": load_push_history(8)})
            except Exception as exc:  # noqa: BLE001
                self._json({"ok": False, "items": [], "message": str(exc)})
            return

        if parsed.path == "/api/push_preview":
            # 推送预览（GET）：读巡检快照离线重建，组装与手机实发共用
            # core.analyzer.to_push_payload 同一条路径 —— 文案只在一处维护
            try:
                self._json(push_preview_payload())
            except Exception as exc:  # noqa: BLE001
                logger.exception("生成推送预览失败")
                from core.analyzer import (
                    WAITING_FIRST_CHECK_BODY,
                    WAITING_FIRST_CHECK_TITLE,
                )

                self._json({
                    "ok": False,
                    "state": "waiting_first_check",
                    "title": WAITING_FIRST_CHECK_TITLE,
                    "body": WAITING_FIRST_CHECK_BODY,
                    "message": f"生成预览失败：{exc}",
                })
            return

        if parsed.path == "/api/activities/ignored":
            # 已删除活动明细（2026-11-18 用户裁决「查看详情」弹窗数据源）
            from core import activities

            try:
                self._json(activities.ignored_detail())
            except Exception as exc:  # noqa: BLE001
                logger.exception("读取已删除活动失败")
                self._json({"ok": False, "message": f"读取失败：{exc}"}, 500)
            return

        if parsed.path == "/api/activities":
            from core import activities

            try:
                # 「即将结束」窗口随用户设置走（app.activity_alert_days 天）：
                # 徽章 ending_soon 在 summary 内按它重算，与推送提醒同口径
                # （2026-11-07 用户裁决：徽章不再固定 72h）
                try:
                    raw_cfg = load_raw_config()
                    days = _normalize_alert_days(
                        (raw_cfg.get("app") or {}).get("activity_alert_days"))
                except Exception:  # noqa: BLE001 - 读不到配置按默认窗口
                    days = _normalize_alert_days(None)
                try:
                    tiers = _normalize_deadline_tiers(
                        (raw_cfg.get("app") or {}).get("activity_deadline_tiers"))
                except Exception:  # noqa: BLE001 - 读不到配置按默认档
                    tiers = _normalize_deadline_tiers(None)
                payload = activities.summary(window_hours=days * 24, tiers=tiers)
                # 未勾选的游戏：活动卡与「暂不支持」提示也从所有展示面消失
                # （活动缓存仍全量抓取，重新勾选即恢复；过滤只影响本响应）
                enabled = set(_enabled_game_ids())
                payload["items"] = [it for it in (payload.get("items") or [])
                                    if it.get("game") in enabled]
                payload["unsupported"] = [u for u in (payload.get("unsupported") or [])
                                          if u.get("game") in enabled]
                self._json(payload)
            except Exception as exc:  # noqa: BLE001 - 页面读不到也要给人话
                logger.exception("读取活动缓存失败")
                self._json({"ok": False, "message": f"读取活动缓存失败：{exc}"}, 500)
            return

        if parsed.path == "/api/live":
            # refresh=1 强制重新查询（默认走 45 秒进程内缓存）；
            # refresh=auto（打开页面自动刷新）：由服务端按磁盘缓存年龄决定——
            # 比 _LIVE_AUTO_REFRESH_MINUTES 新就秒开缓存，过旧才真查。
            # 时钟判定放在服务端（缓存里的 generated_at），前端不做时间假设。
            refresh_val = (query.get("refresh") or ["0"])[0].lower()
            force = refresh_val in ("1", "true", "yes")
            if refresh_val == "auto":
                disk = _live_disk_read()
                gen_dt = None
                if disk is not None:
                    try:
                        gen_dt = datetime.strptime(
                            str(disk.get("generated_at") or ""),
                            "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        gen_dt = None
                force = (disk is None or gen_dt is None or
                         (datetime.now() - gen_dt).total_seconds()
                         > _LIVE_AUTO_REFRESH_MINUTES * 60)
            try:
                family = (query.get("family") or [""])[0].lower()
                games_param = (query.get("games") or [""])[0]
                game_ids = ([g.strip() for g in games_param.split(",") if g.strip()]
                            if games_param else None)
                self._json(live_status(force=force, family=family or None,
                                       only_games=game_ids))
            except Exception as exc:  # noqa: BLE001
                logger.exception("实时查询失败")
                self._json({"ok": False, "message": f"实时查询失败：{exc}", "games": []}, 500)
            return

        if parsed.path.startswith("/static/"):
            self._serve_static(parsed.path)
            return

        self._send(404, b"not found", "text/plain; charset=utf-8")

    # ------------------------------ POST ------------------------------ #

    def _origin_ok(self) -> bool:
        """POST 只接受本页面发起的请求。

        页面与服务同源，浏览器一定会带 Origin；带了且不是本机就直接拒。
        没带 Origin 的（curl / 脚本）放行 —— 它们本来就能直接改文件。
        """
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        try:
            host = (urlparse(origin).hostname or "").lower()
        except ValueError:
            return False
        return host in ("127.0.0.1", "localhost", "::1")

    def _read_json_body(self) -> Optional[Dict[str, Any]]:
        """读取并解析 JSON 请求体；任何异常都已写好响应，返回 None。"""
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            # 强制 JSON 顺带挡掉跨站表单：它只能发 text/plain 或 form-urlencoded
            self._json({"ok": False, "message": "Content-Type 必须是 application/json"}, 415)
            return None

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            self._json({"ok": False, "message": "请求体为空"}, 400)
            return None
        if length > 64 * 1024:
            self._json({"ok": False, "message": "请求体过大"}, 413)
            return None

        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._json({"ok": False, "message": f"JSON 解析失败：{exc}"}, 400)
            return None
        if not isinstance(data, dict):
            self._json({"ok": False, "message": "请求体必须是 JSON 对象"}, 400)
            return None
        return data

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        if not self._origin_ok():
            self._json({"ok": False, "message": "拒绝来自其它站点的请求"}, 403)
            return

        path = urlparse(self.path).path

        if path == "/api/push":
            payload = self._read_json_body()
            if payload is not None:
                self._handle_save_push(payload)
            return

        if path in ("/api/update/download", "/api/update/download/cancel"):
            # 站内下载发起/取消（2026-11-15）：进度走 GET /api/update/download。
            from core import update as _update

            if path.endswith("/cancel"):
                self._json(_update.cancel_download())
            else:
                payload = self._read_json_body()
                if payload is None:
                    return
                self._json(_update.start_download(
                    payload.get("version") or "", payload.get("asset") or ""))
            return

        if path == "/api/accounts/enabled":
            payload = self._read_json_body()
            if payload is not None:
                try:
                    games = payload.get("games")
                    if not isinstance(games, list) or not games:
                        one = str(payload.get("game") or "").strip()
                        games = [one] if one else []
                    games = [str(g).strip() for g in games if str(g).strip()]
                    self._json(set_account_enabled(games, _normalize_bool(
                        payload.get("enabled"), True)))
                except ValueError as exc:
                    self._json({"ok": False, "message": str(exc)}, 400)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("切换巡检提醒失败")
                    self._json({"ok": False, "message": f"设置失败：{exc}"}, 500)
            return

        if path == "/api/test_push":
            payload = self._read_json_body()
            if payload is not None:
                result = run_test_push(payload)
                self._json(result, 200 if result.get("ok") else 400)
            return

        if path == "/api/device":
            payload = self._read_json_body()
            if payload is not None:
                try:
                    self._json(save_device_text(str(payload.get("text") or "")))
                except ValueError as exc:
                    self._json({"ok": False, "message": str(exc)}, 400)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("保存设备档案失败")
                    self._json({"ok": False, "message": f"保存失败：{exc}"}, 500)
            return

        if path == "/api/device/reset":
            payload = self._read_json_body()
            if payload is not None:
                try:
                    self._json(reset_device_text())
                except Exception as exc:  # noqa: BLE001
                    logger.exception("恢复默认设备档案失败")
                    self._json({"ok": False, "message": f"恢复失败：{exc}"}, 500)
            return

        if path == "/api/query/proxy":
            payload = self._read_json_body()
            if payload is not None:
                try:
                    self._json(save_query_proxy(payload.get("proxy")))
                except ValueError as exc:
                    self._json({"ok": False, "message": str(exc)}, 400)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("保存查询代理失败")
                    self._json({"ok": False, "message": "保存失败，请查看运行日志"}, 500)
            return

        if path == "/api/device/shuffle":
            payload = self._read_json_body()
            if payload is not None:
                try:
                    self._json(shuffle_device())
                except Exception as exc:  # noqa: BLE001
                    logger.exception("换设备失败")
                    self._json({"ok": False, "message": f"换设备失败：{exc}"}, 500)
            return

        if path == "/api/schedule":
            payload = self._read_json_body()
            if payload is not None:
                try:
                    self._json(save_schedule(payload.get("times")))
                except ValueError as exc:
                    self._json({"ok": False, "message": str(exc)}, 400)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("保存时间点失败")
                    self._json({"ok": False, "message": f"保存失败：{exc}"}, 500)
            return

        if path == "/api/bg_refresh":
            payload = self._read_json_body()
            if payload is not None:
                try:
                    self._json(save_bg_refresh(payload.get("enabled"),
                                               payload.get("overrides")))
                except Exception as exc:  # noqa: BLE001
                    logger.exception("保存后台刷新开关失败")
                    self._json({"ok": False, "message": f"保存失败：{exc}"}, 500)
            return

        if path == "/api/catch_up":
            payload = self._read_json_body()
            if payload is not None:
                try:
                    self._json(save_catch_up(payload.get("minutes"), payload.get("enabled")))
                except ValueError as exc:
                    self._json({"ok": False, "message": str(exc)}, 400)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("保存补发窗口失败")
                    self._json({"ok": False, "message": f"保存失败：{exc}"}, 500)
            return

        if path == "/api/activity_alert":
            payload = self._read_json_body()
            if payload is not None:
                try:
                    self._json(save_activity_alert(
                        payload.get("enabled"), payload.get("days"),
                        payload.get("tiers")))
                except ValueError as exc:
                    self._json({"ok": False, "message": str(exc)}, 400)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("保存活动提醒开关失败")
                    self._json({"ok": False, "message": f"保存失败：{exc}"}, 500)
            return

        if path == "/api/custom_dailies":
            payload = self._read_json_body()
            if payload is not None:
                try:
                    self._json(custom_dailies_update(payload))
                except ValueError as exc:
                    self._json({"ok": False, "message": str(exc)}, 400)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("保存手动打卡失败")
                    self._json({"ok": False, "message": f"保存失败：{exc}"}, 500)
            return

        if path == "/api/activities/delete":
            from core import activities

            payload = self._read_json_body()
            if payload is not None:
                try:
                    self._json(activities.ignore_activity(str(payload.get("id") or "")))
                except ValueError as exc:
                    self._json({"ok": False, "message": str(exc)}, 400)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("删除活动失败")
                    self._json({"ok": False, "message": f"删除失败：{exc}"}, 500)
            return

        if path == "/api/activities/restore":
            from core import activities

            payload = self._read_json_body()
            if payload is not None:
                try:
                    # uids=None=全部恢复（旧口径）；给列表=单个/勾选批量恢复
                    # （2026-11-18 用户裁决：已删除活动支持单独恢复）
                    uids = payload.get("uids") if isinstance(payload, dict) else None
                    if uids is not None and not isinstance(uids, list):
                        uids = None
                    self._json(activities.restore_ignored(uids))
                except Exception as exc:  # noqa: BLE001
                    logger.exception("恢复活动失败")
                    self._json({"ok": False, "message": f"恢复失败：{exc}"}, 500)
            return

        if path == "/api/activities/refresh":
            from core import activities

            payload = self._read_json_body()
            if payload is not None:
                try:
                    self._json(activities.refresh(force=True))
                except Exception as exc:  # noqa: BLE001
                    logger.exception("刷新活动缓存失败")
                    self._json({"ok": False, "message": f"刷新失败：{exc}"}, 500)
            return

        if path == "/api/history_override":
            payload = self._read_json_body()
            try:
                from core import history as _hist

                date_key = str((payload or {}).get("date") or "")
                game = str((payload or {}).get("game") or "")
                action = str((payload or {}).get("action") or "")
                if not date_key or not game or action not in ("done", "undone", "clear"):
                    raise ValueError("参数不完整")
                if game not in _hist.GAME_ORDER:
                    raise ValueError("未知游戏：" + game)
                if action == "done":
                    _hist.set_override(date_key, game, True)
                    msg = "已补录 " + game + " " + date_key + " 为完成"
                elif action == "undone":
                    _hist.set_override(date_key, game, False)
                    msg = "已将 " + game + " " + date_key + " 标记为未完成"
                else:
                    _hist.clear_override(date_key, game)
                    msg = "已撤销 " + game + " " + date_key + " 的补录（恢复巡检记录）"
                self._json({"ok": True, "message": msg})
            except ValueError as exc:
                self._json({"ok": False, "message": str(exc)}, 400)
            except Exception as exc:  # noqa: BLE001
                logger.exception("保存报表补录失败")
                self._json({"ok": False, "message": f"保存失败：{exc}"}, 500)
            return

        if path == "/api/settings":
            payload = self._read_json_body()
            if payload is not None:
                try:
                    self._json(save_thresholds(payload))
                except ValueError as exc:
                    self._json({"ok": False, "message": str(exc)}, 400)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("保存阈值失败")
                    self._json({"ok": False, "message": f"保存失败：{exc}"}, 500)
            return

        if path == "/api/mobile/toggle":
            payload = self._read_json_body()
            if payload is None:
                return
            try:
                from core.mobile_gateway import start_gateway, stop_gateway
                enable = bool(payload.get("enable"))
                result = start_gateway() if enable else stop_gateway()
                # 写 config 让下次启动保持该状态（沿用本模块串行写锁）。
                with _CONFIG_WRITE_LOCK:
                    raw = load_raw_config()
                    app = raw.setdefault("app", {})
                    app["mobile_gateway"] = enable
                    save_raw_config(raw)
                self._json(result)
            except Exception as exc:  # noqa: BLE001
                logger.exception("切换手机访问失败")
                self._json({"ok": False, "message": f"切换失败：{exc}"}, 500)
            return

        if path == "/api/autostart":
            payload = self._read_json_body()
            if payload is not None:
                try:
                    from core import autostart

                    result = autostart.apply(payload.get("enable"))
                    # 意图持久化（2026-11-09）：拨开关 = 记下用户要什么。
                    # 之后 Run 键被安全软件删掉时，启动链按它自愈重写。
                    want = payload.get("enable")
                    desired = want is True or str(want).strip().lower() in ("1", "true", "on", "yes")
                    try:
                        with _CONFIG_WRITE_LOCK:
                            raw = load_raw_config()
                            app_node = raw.get("app")
                            if isinstance(app_node, dict):
                                app_node["autostart_desired"] = bool(desired)
                                save_raw_config(raw)
                    except Exception:  # noqa: BLE001 - 意图落盘失败不影响本次开关
                        logger.warning("autostart_desired 意图落盘失败（不影响开关本身）")
                    self._json(result)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("切换开机自启失败")
                    self._json({"ok": False, "message": f"操作失败：{exc}"}, 500)
            return

        if path == "/api/kuro/verify":
            # token 粘贴模式预检：只读校验，不落盘
            payload = self._read_json_body()
            if payload is not None:
                try:
                    from core.kuro_api import verify_kuro_token

                    self._json(verify_kuro_token(str(payload.get("token") or "")))
                except Exception as exc:  # noqa: BLE001
                    logger.exception("库街区 token 校验失败")
                    self._json({"ok": False, "error": f"校验失败：{exc}"}, 500)
            return

        if path == "/api/probe_binding":
            # 绑定完成当场探针（#78）：只读验证刚写入的凭证；只展示不计账，
            # 不写 cred_health/history/push_history/live 缓存（见 probe_bindings）
            payload = self._read_json_body()
            if payload is not None:
                games = payload.get("games")
                try:
                    self._json(probe_bindings(
                        games if isinstance(games, list) else []))
                except Exception as exc:  # noqa: BLE001
                    logger.exception("当场探针失败")
                    self._json({"ok": False, "error": f"探针失败：{exc}"}, 500)
            return

        if path == "/api/kuro/save":
            # token 粘贴模式保存：校验通过才落盘（DPAPI 由 save_raw_config 透明处理）
            payload = self._read_json_body()
            if payload is not None:
                try:
                    from core.kuro_api import verify_kuro_token

                    token = str(payload.get("token") or "").strip()
                    check = verify_kuro_token(token)
                    if not check.get("ok"):
                        self._json({"ok": False, "error": check.get("error") or "token 校验未通过"})
                        return
                    roles = check.get("wuwa_roles") or []
                    nickname = str(roles[0].get("roleName") or "") if roles else ""
                    saved_name = write_kuro(
                        token, nickname,
                        games=payload.get("games") if isinstance(payload.get("games"), list) else None)
                    self._json({"ok": True, "nickname": saved_name,
                                "message": f"已绑定库街区账号：{saved_name}"})
                except Exception as exc:  # noqa: BLE001
                    logger.exception("库街区 token 保存失败")
                    self._json({"ok": False, "error": f"保存失败：{exc}"}, 500)
            return

        if path == "/api/kuro/sms/send":
            # 短信模式第一步：前端极验弹窗通过后，带 validate 结果发码
            payload = self._read_json_body()
            if payload is not None:
                try:
                    from core.kuro_api import KuroSmsError, send_sms_code

                    mobile = str(payload.get("mobile") or "").strip()
                    if not _is_cn_mobile(mobile):
                        self._json({"ok": False, "error": "手机号格式不对（仅支持国内号段）"})
                        return
                    send_sms_code(mobile, str(payload.get("gee_test_data") or ""))
                    self._json({"ok": True, "message": "验证码已发送，注意查收短信"})
                except KuroSmsError as exc:
                    self._json({"ok": False, "error": str(exc)})
                except Exception as exc:  # noqa: BLE001
                    logger.exception("库街区发送验证码失败")
                    self._json({"ok": False, "error": f"发送失败：{exc}"}, 500)
            return

        if path == "/api/kuro/sms/login":
            # 短信模式第二步：验证码登录 → 落盘。
            # 注意 sdkLogin 是 APP 端登录，会顶掉手机库街区 App 的登录态（单设备策略）
            payload = self._read_json_body()
            if payload is not None:
                try:
                    from core.kuro_api import KuroSmsError, list_roles, sdk_login

                    mobile = str(payload.get("mobile") or "").strip()
                    code = str(payload.get("code") or "").strip()
                    if not _is_cn_mobile(mobile):
                        self._json({"ok": False, "error": "手机号格式不对（仅支持国内号段）"})
                        return
                    if not (len(code) == 6 and code.isdigit()):
                        self._json({"ok": False, "error": "验证码应为 6 位数字"})
                        return
                    data = sdk_login(mobile, code)
                    token = str(data.get("token") or "").strip()
                    try:
                        roles = list_roles(token)
                        wuwa_roles = [r for r in roles if str(r.get("gameId")) == "3"]
                        nickname = str((wuwa_roles or roles or [{}])[0].get("roleName") or "")
                    except Exception:  # noqa: BLE001 - 拉角色失败不挡绑定
                        nickname = str(data.get("userName") or "")
                    saved_name = write_kuro(
                        token, nickname,
                        games=payload.get("games") if isinstance(payload.get("games"), list) else None)
                    self._json({"ok": True, "nickname": saved_name,
                                "message": f"已绑定库街区账号：{saved_name}"
                                           "（注意：手机上的库街区 App 已被顶下线）"})
                except KuroSmsError as exc:
                    self._json({"ok": False, "error": str(exc)})
                except Exception as exc:  # noqa: BLE001
                    logger.exception("库街区短信登录失败")
                    self._json({"ok": False, "error": f"登录失败：{exc}"}, 500)
            return

        if path == "/api/diag/open-folder":
            # 块A（2026-11-24）：在资源管理器中定位刚导出的诊断包 zip。
            # path 经 _diag_reveal_target 安全收敛，只接受本工具生成的诊断包。
            payload = self._read_json_body()
            if payload is None:
                return
            target = _diag_reveal_target(payload.get("path"))
            if target is None:
                self._json({"ok": False, "error": "路径无效或文件不存在"}, 400)
                return
            try:
                import subprocess

                subprocess.Popen(["explorer", "/select,", target])
                self._json({"ok": True, "message": "已在资源管理器中定位诊断包"})
            except Exception as exc:  # noqa: BLE001
                self._json({"ok": False, "error": f"打开失败：{exc}"}, 500)
            return

        if path == "/api/diag/pick-folder":
            # 块B（2026-11-24）：弹原生「选择文件夹」对话框，返回选中目录。
            # 对话框会阻塞本请求线程，但 HTTP 是 ThreadingHTTPServer，不挡其它请求。
            payload = self._read_json_body()
            if payload is None:
                return
            picked = _diag_pick_folder(payload.get("initial_dir"))
            self._json({"ok": bool(picked), "dir": picked or ""})
            return

        if path == "/api/diag_export_ex":
            # 块B：导出到用户所选目录（零参 GET /api/diag_export 保持原行为）
            payload = self._read_json_body()
            if payload is None:
                return
            self._json(diag_export(payload.get("dir")))
            return

        if path == "/api/tools/open-apk-folder":
            # 资源管理器打开并选中设备信息.apk——扫码不通时的兜底获取途径
            try:
                apk = BASE_DIR / "tools" / "设备信息.apk"
                if not apk.exists():
                    self._json({"ok": False, "error": "apk 不在安装目录（发行包不内置，已改为按需拉取）"}, 404)
                    return
                import subprocess

                subprocess.Popen(["explorer", "/select,", str(apk)])
                self._json({"ok": True, "message": "已在资源管理器中定位 设备信息.apk，用微信/QQ 发给手机即可"})
            except Exception as exc:  # noqa: BLE001
                self._json({"ok": False, "error": f"打开失败：{exc}"}, 500)
            return

        if path == "/api/update/install":
            # 一键静默安装（2026-11-15，#20 第二步）：前置=站内下载 done 且
            # sha_ok——不满足拒绝。VERYSILENT 无 UI；/CLOSEAPPLICATIONS+
            # /RESTARTAPPLICATIONS 命令行覆盖 iss 指令：安装器自动关闭运行中的
            # HopOn（文件锁）、装完经 Restart Manager 重启壳——壳拉起全家，
            # 新版本上线。响应发出后本进程即将被关闭，写完就 return。
            # 单飞闸两层（2026-11-23 对照 Velopack 全局锁；初版只靠进程内锁
            # 是想当然——成功路径锁在端点返回时就释放了，拦不住自杀窗口）：
            #   a) pending.install_started 时间戳（15 秒内拒绝）——覆盖
            #      「响应已返回、引擎还活 1.5 秒才自杀」的危险窗口；
            #   b) 进程内锁——防两个请求同时在端点里处理。
            _install_reject = False
            try:
                from core import update as _update_probe

                _pp = _update_probe.pending_path()
                if _pp.exists():
                    _raw = json.loads(_pp.read_text(encoding="utf-8"))
                    if _raw.get("install_started") and time.time() - float(
                            _raw.get("install_started_ts") or 0) < 15:
                        _install_reject = True
            except (OSError, ValueError, TypeError):
                pass
            if _install_reject:
                self._json({"ok": False, "error": "更新已在进行中，请稍候几秒"}, 429)
                return
            if not _INSTALL_LOCK.acquire(blocking=False):
                self._json({"ok": False, "error": "更新已在进行中，请稍候几秒"}, 429)
                return
            _install_locked = True
            try:
                from core import update as _update

                st = _update.download_status()
                if st.get("stage") == "staging":
                    # 运行期解压中（2026-11-22 staged 改造）：不拒绝任务本身，
                    # 让前端稍后重试——此时 staging 半成品绝不能被 apply
                    self._json({"ok": False,
                                "error": "正在解压准备更新，请几秒后再点安装"}, 400)
                    return
                if st.get("stage") not in ("done", "staged") or not st.get("sha_ok"):
                    self._json({"ok": False, "error": "还没有校验通过的安装包，请先完成站内下载"}, 400)
                    return
                # 防降级（官方验收：本地版本更高时不安装）：包名解析版本须新于当前
                pkg = str(st.get("file") or "")
                pkg_ver = _update._parse(str(st.get("version") or ""))
                if pkg_ver <= _update._parse(_update.current_version()):
                    self._json({"ok": False,
                                "error": "安装包版本（%s）不高于当前版本，已拒绝（防降级）"
                                         % (".".join(str(x) for x in pkg_ver))}, 400)
                    return
                if not os.path.isfile(pkg):
                    self._json({"ok": False, "error": "安装包文件不存在"}, 404)
                    return
                # 一键安装（2026-11-16 三改）：改为启动独立 HopOnUpdater.exe。
                # 为什么不再跑 Setup.exe：Inno 靠 RestartManager 关运行中进程，
                # 而引擎 pythonw 被壳挂在 Job Object 里内核层面关不掉 →
                # 安装器 Abort → 整体回滚（2026-11-16 19:35 实弹 rc=5）。
                # Updater 自己不在替换集合里，可先等本进程退出再换文件，
                # 两阶段替换 + 完整备份，失败能回滚。
                import subprocess

                # 目标目录=**当前引擎的安装根**（2026-11-20 修正，Velopack
                # CurrentBinaryDir 语义）：更新目标=正在运行的这份程序所在
                # 目录，运行时解析——自定义目录安装（如 D:\临时\HopOn）原地
                # 更新，标准用户=标准目录，全部正确。此前硬算标准位置
                # %LOCALAPPDATA%\Programs\HopOn，对自定义目录用户会把新版
                # 装到 C 盘、旧版还在原地（装出双份，朋友机场景）。
                # HOPON_INSTALL_DIR 沙箱钩子（2026-11-19）保留最高优先级：
                # 全新电脑 E2E 把安装根指进沙箱（实测事故）。
                # 2026-11-22 staged 改造：解析收敛到 core.paths.install_root()
                # 单源——staging 解压与安装目标必须同源（此前本端点内联读
                # HOPON_INSTALL_DIR，而 app_root() 认 HOPON_APP_DIR，不同源）。
                # 开发机天然保护：git 工作树根没有 HopOnUpdater.exe →
                # 下方「更新器缺失」拒绝，工作树不会被覆盖。
                from core.paths import install_root as _app_root

                _install_dir = _app_root()

                # Updater 与主程序同级。用 core.paths.app_root() 而非
                # Path(sys.executable).parent.parent —— 后者只在安装版成立
                # （…\HopOn\engine\python\python.exe → …\HopOn），工作树下
                # 会算成 engine\ 少了根（2026-11-16 实测 → 误报「更新器缺失」）
                _updater = _app_root() / "HopOnUpdater.exe"
                if not _updater.is_file():
                    self._json({"ok": False,
                                "error": "更新器缺失（HopOnUpdater.exe 不在安装目录），"
                                         "请手动运行安装包完成更新"}, 400)
                    return

                try:
                    # spawn 前写权威状态（phase=applying）：壳与 Updater 共认的
                    # 唯一事实源。staged 态刷新它（staging 期已写 phase=staged），
                    # done 态补写——否则壳的 Engine_Exited 感知不到更新退出，
                    # 会把旧引擎拉回来（脏竞态）。
                    # 单飞字段保留（15s 内第二次 install 一律 429）。
                    _update.write_update_state(
                        phase="applying", version=st.get("version"),
                        asset=st.get("asset"), pkg=pkg,
                        staged_at=st.get("staged_at") or _update._now_text(),
                        src_pid=os.getpid(), files=st.get("files"),
                        install_started=True, install_started_ts=time.time(),
                    )
                except OSError as exc:
                    # Z34：权威文件是壳感知与单飞判据的事实源，写失败还硬闯
                    # spawn=壳复活旧引擎+双 POST 无保护并发——拒绝安装
                    self._json({"ok": False,
                                "error": f"更新状态写入失败（磁盘或权限）：{exc}"}, 500)
                    return
                try:
                    # 关键：Updater 必须**脱离壳的 Job Object**。
                    # 壳把引擎（连带引擎派生的一切）挂进 KILL_ON_JOB_CLOSE 的 Job，
                    # 而 Updater 是引擎起的子进程 → 继承该 Job。这样 Updater 去
                    # 杀壳时会被 Job 连带杀死（2026-11-16 真机实测：日志停在
                    # 「结束占用进程」后无任何输出、backup 未清理）。
                    # CREATE_BREAKAWAY_FROM_JOB = 0x01000000 让它自成一体。
                    # staged 态（2026-11-22）：staging 已由引擎运行期解压就绪，
                    # --staged 让 Updater 跳过解压直接覆盖（黑幕只剩纯覆盖秒级）；
                    # staging 不完整时 Updater 自动回退从 zip 解压（双保险）。
                    _cmd = [str(_updater),
                            "--pid", str(os.getpid()),
                            "--root", str(_install_dir),
                            "--pkg", pkg,
                            "--restart"]
                    if st.get("stage") == "staged":
                        _cmd.append("--staged")
                    try:
                        subprocess.Popen(
                            _cmd,
                            creationflags=0x00000008 | 0x01000000)  # DETACHED_PROCESS | CREATE_BREAKAWAY_FROM_JOB
                    except OSError:
                        # Job 不许 breakaway（组策略/沙箱环境）→ 降级不带 flag：
                        # Updater 的 DetachFromJob() 会自己从 Job 脱离（双保险
                        # 设计，Velopack 同思路——杀壳动作绝不能因 breakaway
                        # 被拒而整个安装失败）
                        subprocess.Popen(_cmd, creationflags=0x00000008)  # DETACHED_PROCESS
                except Exception as exc:  # noqa: BLE001
                    logger.exception("更新器启动失败")
                    self._json({"ok": False, "error": f"更新器启动失败：{exc}"}, 500)
                    return

                logger.info("已启动更新器：%s（root=%s pkg=%s）——本进程即将退出，"
                            "由更新器等待退出后替换文件并重启", _updater, _install_dir, pkg)
                self._json({"ok": True,
                            "message": "更新已启动：应用将关闭，替换完成后自动重启新版"})
                # 立刻退出让 Updater 的 WaitPidGone 通过（它轮询本进程 PID）；
                # 先睡 1.5s 保证 HTTP 响应真的送达客户端再走。
                def _exit_soon():
                    import time as _t

                    _t.sleep(1.5)
                    try:
                        import daemon as _d

                        _d.stop_daemon()   # 停守护（旗标+等退出，≤3s）
                    except Exception:  # noqa: BLE001
                        pass
                    os._exit(0)

                threading.Thread(target=_exit_soon, daemon=True).start()
                # 成功路径不释放锁——交给引擎自杀（进程死亡）释放，覆盖整个
                # 1.5 秒+ 窗口；错误路径由 finally 释放。
                _install_locked = False
            except Exception as exc:  # noqa: BLE001
                logger.exception("静默安装发起失败")
                self._json({"ok": False, "error": f"安装启动失败：{exc}"}, 500)
            finally:
                if _install_locked:
                    _INSTALL_LOCK.release()
            return

        if path == "/api/update/open-folder":
            # 资源管理器定位已下载并校验通过的安装包（站内下载完成态）
            try:
                from core import update as _update

                st = _update.download_status()
                file_path = str(st.get("file") or "")
                # 2026-11-23 逐行审 D14：staged 改造后 done 会升级为 staged
                # （staged 的 file 同在）——旧条件只认 done，staged 态点
                # 「打开文件夹」误报 404
                if st.get("stage") not in ("done", "staged") or not st.get("sha_ok") or not file_path:
                    self._json({"ok": False, "error": "还没有校验通过的安装包"}, 404)
                    return
                if not os.path.isfile(file_path):
                    self._json({"ok": False, "error": "安装包文件不存在（可能已被移动）"}, 404)
                    return
                import subprocess

                subprocess.Popen(["explorer", "/select,", file_path])
                self._json({"ok": True, "message": "已在资源管理器中定位安装包，双击即可安装"})
            except Exception as exc:  # noqa: BLE001
                self._json({"ok": False, "error": f"打开失败：{exc}"}, 500)
            return


        if path == "/api/daemon/quiet-run":
            # 关机前落盘（2026-11-06）：壳「彻底退出」/系统关机前调用。写
            # .quiet_run 旗标，daemon 0.5s 内接手跑一轮 quiet_push 检查
            # （写历史、不推送）后自行删旗标；本端点阻塞等旗标消失——
            # 壳侧超时更短，超时也会照常走收尸，数据由在途检查兜底
            payload = self._read_json_body()
            if payload is None:
                return
            try:
                import daemon as daemon_mod

                flag = daemon_mod.QUIET_RUN_FLAG
                if not daemon_status().get("running"):
                    self._json({"ok": False,
                                "message": "守护进程未运行，跳过关机前落盘"})
                    return
                flag.write_text(
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n",
                    encoding="utf-8")
                deadline = time.time() + 25
                while time.time() < deadline and flag.exists():
                    time.sleep(0.3)
                done = not flag.exists()
                self._json({
                    "ok": done,
                    "message": ("关机前落盘完成" if done
                                else "落盘超时（引擎侧可能在收尾，数据由在途检查兜底）"),
                })
            except Exception as exc:  # noqa: BLE001 - 退出路径绝不 500 卡壳
                logger.warning("关机前落盘失败：%s", exc)
                self._json({"ok": False, "message": f"关机前落盘失败：{exc}"})
            return

        if path in ("/api/daemon/start", "/api/daemon/stop"):
            payload = self._read_json_body()
            if payload is None:
                return
            try:
                import daemon as daemon_mod

                result = (
                    daemon_mod.start_daemon()
                    if path.endswith("start")
                    else daemon_mod.stop_daemon()
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("守护进程控制失败")
                result = {"ok": False, "message": f"操作失败：{exc}"}
            status_payload = daemon_status()
            result["daemon"] = status_payload
            self._json(result, 200 if result.get("ok") else 400)
            return

        if path == "/api/daemon/run-now":
            payload = self._read_json_body()
            if payload is None:
                return
            try:
                import daemon as daemon_mod

                if not daemon_status().get("running"):
                    start = daemon_mod.start_daemon()
                    if not start.get("ok"):
                        self._json({"ok": False,
                                    "message": start.get("message") or "守护进程未能启动，巡检未执行"},
                                   400)
                        return
                daemon_mod.RUN_NOW_FLAG.write_text(
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n", encoding="utf-8")
                self._json({"ok": True, "message": "已开始巡检"})
            except Exception as exc:  # noqa: BLE001
                logger.exception("立即巡检请求失败")
                self._json({"ok": False, "message": "巡检请求执行失败，请查看运行日志"}, 500)
            return

        self._json({"ok": False, "message": "not found"}, 404)

    @_serialized_write
    def _handle_save_push(self, payload: Dict[str, Any]) -> None:
        try:
            raw = load_raw_config()
            result = update_push_config(raw, payload)
        except ValueError as exc:  # 掩码误填等输入问题
            self._json({"ok": False, "message": str(exc)}, 400)
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("读取推送配置失败")
            self._json({"ok": False, "message": f"读取配置失败：{exc}"}, 500)
            return

        actions = result["actions"]

        # 一个字都没改就别碰文件：原始 config.json 可能是别的工具生成的，
        # 重新序列化会改变排版，没必要冒这个险。
        if not result["changed"]:
            detail = push_detail()
            detail["actions"] = actions
            detail["changed"] = False
            detail["message"] = "没有任何改动，config.json 未被写入"
            self._json(detail)
            return

        # 写盘前用真正的配置解析器复检：结构不对就一个字都不写
        try:
            from core.config import PushConfig

            PushConfig.from_dict(raw.get("push") or {})
        except Exception as exc:  # noqa: BLE001
            logger.warning("推送配置校验未通过，已放弃写入：%s", exc)
            self._json(
                {"ok": False, "message": f"推送配置校验未通过，未写入：{exc}"}, 400
            )
            return

        try:
            save_raw_config(raw)
        except Exception as exc:  # noqa: BLE001
            logger.exception("写入 config.json 失败")
            self._json({"ok": False, "message": f"写入失败：{exc}"}, 500)
            return

        detail = push_detail()  # 重新从磁盘读，确认真的生效
        detail["actions"] = actions
        detail["changed"] = True
        parts = [
            f"{PUSH_CHANNELS[name]['label']}{ACTION_TEXT[action]}"
            for name, action in actions.items()
            if action in ("set", "clear")
        ]
        detail["message"] = "保存成功" + (f"（{'，'.join(parts)}）" if parts else "")
        logger.info("推送配置已保存：%s", detail["message"])
        self._json(detail)



def pick_port(preferred: int, tries: int = 12) -> int:
    """从 preferred 开始找一个真正空闲的端口。

    注意：这里**不能**加 SO_REUSEADDR。Windows 的语义与 Unix 不同 ——
    它允许与"已监听"的端口重复绑定，探测会误判为空闲，结果两个进程
    同时监听同一端口，请求随机落到其中一个，表现为会话莫名失效。
    """
    for offset in range(tries):
        port = preferred + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"{preferred}~{preferred + tries - 1} 端口都被占用")


class LocalHTTPServer(ThreadingHTTPServer):
    """只服务本机；Windows 上禁用 allow_reuse_address（原因同 pick_port）。"""

    daemon_threads = True
    allow_reuse_address = os.name != "nt"


def serve(
    port: int = 8765,
    *,
    lan_tools: bool = True,
    tries: int = 12,
) -> Dict[str, Any]:
    """启动控制台 HTTP 服务并立刻返回（服务跑在后台线程，不阻塞调用方）。

    桌面窗口（app.py）和命令行入口共用这一份实现，避免两处端口 / 初始化
    逻辑漂移。返回 ``{"server", "port", "url", "thread", "tools"}``，
    关闭时调用 :func:`stop_server`。
    """
    global LAN_TOOLS_ENABLED

    ensure_config()
    # 刻意不在这里预热 APK 下载服务：它必须绑 0.0.0.0，会让 Windows 防火墙在
    # 每次出现新路径时问一次。等用户点「生成下载二维码」再由 /api/apk/restart 懒启动。
    LAN_TOOLS_ENABLED = lan_tools

    actual = pick_port(port, tries)
    server = LocalHTTPServer(("127.0.0.1", actual), Handler)
    thread = threading.Thread(
        target=server.serve_forever, name="console-http", daemon=True
    )
    thread.start()
    logger.info("控制台已监听 127.0.0.1:%d", actual)
    # staged 改造启动对账（2026-11-23 自审）：盘上 done 态但 staging 失效
    # 时后台补解压，UI 快链自动恢复——否则 done 永远不会变 staged。
    try:
        from core import update as _upd_boot

        _upd_boot.reconcile_on_boot()
    except Exception:  # noqa: BLE001 - 对账失败不挡启动
        pass
    # 手机访问网关（2026-11-25，M1）：默认关闭，仅 config.app.mobile_gateway=True 时
    # 才在独立端口 8766 监听。不复用主控制台 handler——它有 /api/push、/api/daemon/*
    # 等敏感端点，绝不能暴露到局域网（见旧 tools_server.py 删除原因）。
    gw = None
    try:
        if bool((load_raw_config().get("app") or {}).get("mobile_gateway")):
            from core.mobile_gateway import start_gateway, get_active_gateway
            start_gateway()
            gw = get_active_gateway()
            logger.info("手机访问已开启：独立端口 8766（默认关闭）")
    except Exception:  # noqa: BLE001 - 网关失败不挡主控制台
        logger.warning("手机访问网关启动失败", exc_info=True)
    return {
        "server": server,
        "port": actual,
        "url": f"http://127.0.0.1:{actual}/",
        "thread": thread,
        "tools": TOOLS_SERVER,
        "mobile_gateway": gw,
    }


def stop_server(info: Optional[Dict[str, Any]]) -> None:
    """关闭 :func:`serve` 起出来的服务（幂等，可重复调用）。"""
    server = (info or {}).get("server")
    if server is None:
        return
    try:
        server.shutdown()
    except Exception:  # noqa: BLE001
        logger.debug("关闭 HTTP 服务时出错（已忽略）", exc_info=True)
    try:
        server.server_close()
    except Exception:  # noqa: BLE001
        logger.debug("释放监听端口时出错（已忽略）", exc_info=True)
    gw = (info or {}).get("mobile_gateway")
    if gw is not None:
        try:
            gw.stop()
        except Exception:  # noqa: BLE001
            logger.debug("关闭手机网关时出错（已忽略）", exc_info=True)


def console_alive(port: int = 8765, timeout: float = 1.5) -> bool:
    """探测本机某端口上是否已经跑着本控制台（用于复用已开着的实例）。"""
    import urllib.request

    url = f"http://127.0.0.1:{port}/api/env"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        return isinstance(data, dict) and "channels" in data
    except Exception:  # noqa: BLE001
        return False


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="HopOn · 本地控制台")
    parser.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument(
        "--no-lan-tools",
        action="store_true",
        help="不启动 APK 局域网下载服务（手机就没法扫码下载了）",
    )
    args = parser.parse_args(argv)

    if sys.stdout is None or sys.stderr is None:
        # pythonw.exe 下 std 句柄是 None：print / logging 都会炸。
        # 终端 banner 改投 devnull，进程照常静默驻留（日志走 logs/ 文件）。
        try:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
            sys.stderr = open(os.devnull, "w", encoding="utf-8")
        except OSError:
            pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        info = serve(args.port, lan_tools=not args.no_lan_tools)
    except Exception as exc:  # noqa: BLE001
        print(f"[错误] 控制台启动失败：{exc}")
        return 1

    print("=" * 58)
    print("  HopOn · 控制台已启动")
    print(f"  控制台  ：{info['url']}")
    if info["tools"]:
        print(f"  APK 下载：{info['tools']['url']}（手机连同一 WiFi 可访问）")
    else:
        print("  APK 下载：按需启动 —— 界面「设备」页点「生成下载二维码」才监听局域网")
    print("  关闭本窗口只停控制台，定时提醒由守护进程负责")
    print("=" * 58)

    if not args.no_browser:
        threading.Thread(
            target=lambda: (time.sleep(0.6), webbrowser.open(info["url"])),
            daemon=True,
        ).start()

    waiter = threading.Event()
    try:
        while info["thread"].is_alive():
            waiter.wait(0.5)
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        stop_server(info)
    return 0


if __name__ == "__main__":
    sys.exit(main())

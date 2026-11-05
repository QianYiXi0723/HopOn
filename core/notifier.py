"""推送层。

职责：把提醒文本推到手机。本阶段只实现 Bark（GET /<device_key>/<title>/<body>），
并负责"要不要推"的测试模式判断：

    auto          有未完成项才推
    force_alert   无论结果如何都推（验证链路是否打通）
    force_silent  一律不推，只跑流程和写日志（不打扰自己 / 调代码时用）

设计成 BaseNotifier 抽象，后续加 Telegram、企业微信、Server 酱只需新增子类并
在 build_notifiers() 里挂一行。

requests 采用惰性导入：没装依赖时脚本仍能跑完查询与日志，只在真正推送时给出明确提示。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import tempfile
import threading

from core import ipc_lock
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode

from .config import (
    TEST_MODE_FORCE_ALERT,
    TEST_MODE_FORCE_SILENT,
    BarkConfig,
    DingTalkConfig,
    FeishuConfig,
    PushConfig,
    QmsgConfig,
    ServerChanConfig,
)
from .models import PushResult

logger = logging.getLogger("dailycheck.notifier")


# --------------------------------------------------------------------------- #
# 推送历史（data/push_history.json）
# --------------------------------------------------------------------------- #

from core.paths import app_root, user_root

BASE_DIR = app_root()  # 程序根：定位代码
USER_DIR = user_root()  # 数据根：落用户数据
PUSH_HISTORY_PATH = USER_DIR / "data" / "push_history.json"
#: 最多保留多少条（一次检查最多几个渠道，50 条足够回看一周以上）
PUSH_HISTORY_KEEP = 50
_PUSH_WRITE_LOCK = threading.Lock()


def record_push_history(
    channel: str, ok: bool, message: str = "", at: Optional[datetime] = None,
    title: str = "", body: str = "",
) -> None:
    """每次**真实**推送（成功/失败都算）追加一条结构化记录。

    「最近推送」直接读这份 json，不再解析纯文本日志 ——
    console 与 daemon 的推送日志分别落在 desktop.log / dailycheck.log，
    按文件捞日志的前端永远筛不中，这就是"最近没有推送记录"bug 的根源。
    title 是本次推送的标题、body 是正文预览（截 300 字符），前端拿它们当主要内容；
    老记录 / 日志补录两者皆无，前端回退到 message。
    """
    entry = {
        "ts": (at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
        "channel": str(channel or ""),
        "ok": bool(ok),
        "message": str(message or "")[:160],
        "title": str(title or "")[:160],
        "body": str(body or "")[:300],
    }
    # 跨进程锁：推送可能来自 daemon（定时）与控制台（测试按钮）两个进程，
    # 读改写 push_history.json 必须跨进程互斥（线程锁只防进程内）
    with _PUSH_WRITE_LOCK, ipc_lock.blocking(str(PUSH_HISTORY_PATH) + ".lock"):
        try:
            raw = PUSH_HISTORY_PATH.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                data = {}
        except FileNotFoundError:
            data = {}
        except Exception as exc:  # noqa: BLE001 - 文件坏了就重建，绝不影响推送本身
            logger.warning("推送历史损坏，重建（%s）", exc)
            data = {}
        items = data.get("items")
        if not isinstance(items, list):
            items = []
        items.append(entry)
        data = {"version": 1, "items": items[-PUSH_HISTORY_KEEP:]}
        try:
            PUSH_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(data, ensure_ascii=False, indent=2)
            json.loads(text)  # 复检：写出去的必须是合法 JSON
            fd, tmp = tempfile.mkstemp(dir=str(PUSH_HISTORY_PATH.parent),
                                       prefix=".pushhist-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.write("\n")
            os.replace(tmp, PUSH_HISTORY_PATH)
        except OSError as exc:
            logger.warning("写推送历史失败（%s）", exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("写推送历史出现未预期错误（%s）", exc)


def load_push_history(limit: int = 8) -> List[Dict[str, Any]]:
    """给 /api/push_history 用的最近 limit 条（最新在前）。"""
    try:
        raw = PUSH_HISTORY_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            items = []
    except FileNotFoundError:
        return []
    except Exception as exc:  # noqa: BLE001
        logger.warning("读推送历史失败（%s）", exc)
        return []
    clean = [it for it in items if isinstance(it, dict)]
    return list(reversed(clean[-max(1, int(limit)):]))


def backfill_push_history_from_log(log_dir: Optional[Path] = None) -> int:
    """一次性迁移：把既有日志里的推送终态捞进 json，返回补录条数。

    只认终态行（推送成功 / 推送失败 / 请求异常 / HTTP n / 接口返回失败），
    "推送中…"这类过程行不算。补录完成后，运行时**不再碰任何日志文件**。
    """
    import re

    log_dir = log_dir or USER_DIR / "logs"
    pattern = re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?\[(?P<ch>bark|serverchan|qmsg|dingtalk|feishu)\]"
        r".*?(推送成功|推送失败|请求异常|HTTP \d{3}|接口返回失败|未预期异常)",
        re.IGNORECASE,
    )
    seen: set = set()
    found: List[Dict[str, Any]] = []
    for log_file in sorted(log_dir.glob("*.log*")):
        try:
            lines = log_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            m = pattern.match(line)
            if not m:
                continue
            ok = "推送成功" in line
            tail = line.split(f"[{m.group('ch')}] ", 1)[-1]
            key = (m.group("ts"), m.group("ch"), ok)
            if key in seen:
                continue
            seen.add(key)
            found.append({"ts": m.group("ts"), "channel": m.group("ch"),
                          "ok": ok, "message": tail[:160]})
    found.sort(key=lambda it: it["ts"])
    for it in found:
        record_push_history(it["channel"], it["ok"], it["message"],
                            at=datetime.strptime(it["ts"], "%Y-%m-%d %H:%M:%S"))
    return len(found)


class PushError(Exception):
    """推送过程中的可预期失败（缺依赖 / 配置不全 / 接口报错）。"""


def _import_requests():
    """惰性导入 requests，缺失时给出可执行的修复提示。"""
    try:
        import requests  # noqa: PLC0415 - 故意延迟导入
    except ImportError as exc:  # pragma: no cover - 取决于运行环境
        raise PushError(
            "缺少依赖 requests，请先执行：pip install -r requirements.txt"
        ) from exc
    return requests


def mask_secret(value: str, keep: int = 4) -> str:
    """日志脱敏：device_key 只留前几位。"""
    if not value:
        return "(空)"
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "*" * (len(value) - keep)


# --------------------------------------------------------------------------- #
# 推送决策
# --------------------------------------------------------------------------- #


def in_quiet_window(now_minutes: int, start: str, end: str) -> bool:
    """免打扰判定，支持跨零点（23:00-08:00 = now>=23:00 或 now<08:00）。

    时间串非法（没配/写错）一律返回 False——配置坏了宁可多打扰也不静默。
    """
    def minutes(text: str) -> int:
        parts = str(text).strip().split(":")
        try:
            return int(parts[0]) * 60 + int(parts[1])
        except (ValueError, IndexError):
            return -1
    s, e = minutes(start), minutes(end)
    if s < 0 or e < 0:
        return False
    if s <= e:
        return s <= now_minutes < e
    return now_minutes >= s or now_minutes < e


def should_push(
    test_mode: str,
    need_alert: bool,
    unfinished_count: int = 0,
    always_notify: bool = False,
    overflow_count: int = 0,
    activity_count: int = 0,
    quiet_now: bool = False,
    quiet_window: str = "",
) -> Tuple[bool, str]:
    """根据测试模式与检查结论决定是否推送，返回 (是否推送, 原因)。

    ``always_notify`` 打开后，全部完成也发一条「今日全部完成」的平安通知，
    免得用户以为守护进程坏了 —— 默认关闭，保持原来的安静行为。

    ``overflow_count`` > 0 表示体力 / 树脂 / 电量达到预警阈值。即便日常全部
    完成也会推（体力溢出是有时限的损失，跟"没做日常"一样值得催一下）。

    ``activity_count`` > 0 表示有活动提醒待送达（结束止损 / 活动上新）。
    同一活动每天只提醒一次（去重由 ``core.activities`` 负责）。

    ``quiet_now`` = 免打扰时段中：推送一律顺延。触发条件（未完成/溢出/
    活动结束）在时段结束后自然重现，明早自动补推，无需持久化。
    """
    if test_mode == TEST_MODE_FORCE_SILENT:
        return False, "测试模式=强制静默，本次只写日志"
    if test_mode == TEST_MODE_FORCE_ALERT:
        return True, "测试模式=强制提醒，无论结果都推送"
    if quiet_now:
        return False, f"免打扰时段（{quiet_window}），推送顺延至时段结束"
    if need_alert:
        return True, f"自动模式：{unfinished_count} 个游戏存在未完成项"
    if overflow_count > 0:
        return True, f"自动模式：{overflow_count} 项体力达到预警阈值，即将溢出"
    if activity_count > 0:
        return True, f"自动模式：{activity_count} 个活动即将结束（剩 <=72 小时）"
    if always_notify:
        return True, "自动模式：全部完成，按设置发送平安通知"
    return False, "自动模式：全部日常已完成，保持安静"


# --------------------------------------------------------------------------- #
# Notifier
# --------------------------------------------------------------------------- #


class BaseNotifier:
    """推送通道基类。

    send() 是模板方法，收口五渠道重复的九步管线（禁用→配置不全→dry-run→
    构造请求→网络→HTTP 码→JSON→成功判据→成功）；子类只声明差异：
      - _missing_config()：配置不全时的人话提示
      - _secrets()：敏感值清单（异常文本里的 Key 会被替换成掩码——
        旧实现把 str(exc) 原样塞进 message，GET 型渠道的 Key 就在 URL
        path 上，断网时测试推送会把明文 Key 推到网页日志台）
      - _build_request()：构造 (method, url, requests_kwargs)
      - _judge()：响应 JSON 的成功判据
    """

    name = "base"

    @property
    def enabled(self) -> bool:
        """配置里是否启用。"""
        return False

    @property
    def ready(self) -> bool:
        """配置是否填全、可以真正发送。"""
        return False

    # -- 子类钩子 ------------------------------------------------------- #

    def _missing_config(self) -> Optional[str]:
        """配置不全时返回人话提示；就绪返回 None。"""
        return None

    def _secrets(self) -> List[str]:
        """本通道的敏感值清单。"""
        return []

    def _build_request(
        self, title: str, body: str
    ) -> Tuple[str, str, Dict[str, Any]]:
        """构造请求，返回 (method, url, requests_kwargs)。"""
        raise NotImplementedError

    def _judge(self, payload: Dict[str, Any]) -> Tuple[bool, str]:
        """解析响应 JSON，返回 (是否成功, 失败原因)。"""
        raise NotImplementedError

    # -- 公共管线 ------------------------------------------------------- #

    def _mask_error(self, exc: Exception) -> str:
        """把异常文本里出现的本通道敏感值替换成掩码。"""
        text = str(exc)
        for secret in self._secrets():
            if secret and secret in text:
                text = text.replace(secret, mask_secret(secret))
        return text

    def send(self, title: str, body: str, dry_run: bool = False) -> PushResult:
        if not self.enabled:
            return PushResult(self.name, ok=False, message="配置中已禁用", skipped=True)
        missing = self._missing_config()
        if missing:
            return PushResult(self.name, ok=False, message=missing, skipped=True)
        if dry_run:
            logger.info(
                "[%s] dry-run：跳过真实请求（标题=%s）", self.name, title
            )
            return PushResult(self.name, ok=False, message="dry-run 未发送", skipped=True)

        requests = _import_requests()
        try:
            method, url, kwargs = self._build_request(title, body)
            logger.info(
                "[%s] 推送中…（标题=%s，正文 %d 字）", self.name, title, len(body)
            )
            if method == "GET":
                response = requests.get(url, **kwargs)
            else:
                response = requests.post(url, **kwargs)
        except Exception as exc:  # noqa: BLE001 - 网络异常、超时、DNS 统一收口
            masked = self._mask_error(exc)
            logger.warning("[%s] 请求异常：%s", self.name, masked)
            return PushResult(self.name, ok=False, message=f"请求异常：{masked}")

        if response.status_code != 200:
            logger.warning(
                "[%s] HTTP %s：%s", self.name, response.status_code, response.text[:200]
            )
            return PushResult(
                self.name,
                ok=False,
                message=f"推送服务暂时没有响应（HTTP {response.status_code}），请稍后再试",
            )

        try:
            payload = response.json()
        except ValueError:
            logger.warning("[%s] 响应非 JSON：%s", self.name, response.text[:200])
            return PushResult(self.name, ok=False, message="响应不是合法 JSON")

        ok, reason = self._judge(payload)
        if not ok:
            logger.warning("[%s] 接口返回失败：%s", self.name, reason)
            return PushResult(self.name, ok=False, message=f"接口返回失败：{reason}")

        logger.info("[%s] 推送成功", self.name)
        return PushResult(self.name, ok=True, message="推送成功")


class BarkNotifier(BaseNotifier):
    """Bark（iOS）推送，GET 方式，正文走 URL path。"""

    name = "bark"

    def __init__(self, config: BarkConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def ready(self) -> bool:
        return self.config.ready

    def build_url(self, title: str, body: str) -> str:
        """拼 GET 地址。标题/正文逐段 quote(safe="")，防止正文里的 / 和换行破坏 path。"""
        cfg = self.config
        segments = [
            cfg.server.rstrip("/"),
            quote(cfg.device_key, safe=""),
            quote(title, safe=""),
            quote(body, safe=""),
        ]
        return "/".join(segments)

    def masked_url(self) -> str:
        """日志用地址，隐藏 device_key。"""
        return f"{self.config.server.rstrip('/')}/****/"

    def build_params(self) -> Dict[str, Any]:
        cfg = self.config
        params: Dict[str, Any] = {"group": cfg.group, "level": cfg.level}
        if cfg.sound:
            params["sound"] = cfg.sound
        if cfg.icon:
            params["icon"] = cfg.icon
        return {key: value for key, value in params.items() if value not in ("", None)}

    def _missing_config(self) -> Optional[str]:
        if not self.config.device_key.strip():
            return "未配置 device_key，请在 config.json 的 push.bark.device_key 填入 Bark Key"
        return None

    def _secrets(self) -> List[str]:
        return [self.config.device_key]

    def _build_request(
        self, title: str, body: str
    ) -> Tuple[str, str, Dict[str, Any]]:
        logger.info(
            "[bark] GET %s …（正文 %d 字，key=%s）",
            self.masked_url(),
            len(body),
            mask_secret(self.config.device_key),
        )
        return "GET", self.build_url(title, body), {
            "params": self.build_params(),
            "timeout": self.config.timeout,
        }

    def _judge(self, payload: Dict[str, Any]) -> Tuple[bool, str]:
        if str(payload.get("code")) != "200":
            message = payload.get("message") or str(payload)
            return False, message
        return True, ""


# --------------------------------------------------------------------------- #
# Server 酱
# --------------------------------------------------------------------------- #


class ServerChanNotifier(BaseNotifier):
    """Server 酱 Turbo（sctapi.ftqq.com）：GET {server}/{sendkey}.send，title + desp（Markdown）。

    成功判定：HTTP 200 且返回 JSON code == 0。
    """

    name = "serverchan"

    def __init__(self, config: ServerChanConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def ready(self) -> bool:
        return self.config.ready

    def _missing_config(self) -> Optional[str]:
        if not self.config.sendkey.strip():
            return "未配置 sendkey，请在 config.json 的 push.serverchan.sendkey 填入 Server 酱 SendKey"
        return None

    def _secrets(self) -> List[str]:
        return [self.config.sendkey]

    def _build_request(
        self, title: str, body: str
    ) -> Tuple[str, str, Dict[str, Any]]:
        # sendkey 可能自带 ".send" 后缀，也兼容裸 key
        key = self.config.sendkey.strip()
        if not key.endswith(".send"):
            key += ".send"
        url = f"https://sctapi.ftqq.com/{quote(key, safe='')}"
        logger.info(
            "[serverchan] 推送中…（标题=%s，key=%s）", title, mask_secret(self.config.sendkey)
        )
        return "GET", url, {
            "params": {"title": title, "desp": body},
            "timeout": self.config.timeout,
        }

    def _judge(self, payload: Dict[str, Any]) -> Tuple[bool, str]:
        if payload.get("code") != 0:
            message = payload.get("message") or str(payload)
            return False, message
        return True, ""


# --------------------------------------------------------------------------- #
# Qmsg 酱（QQ）
# --------------------------------------------------------------------------- #


class QmsgNotifier(BaseNotifier):
    """Qmsg 酱（qmsg.zendee.cn）：GET {server}/send/{key}?msg=<标题换行正文>。

    **一次请求 = 一条消息**：标题与正文用单个 \\n 拼成一段纯文本，走 `msg` 参数一次发出，
    QQ 里只弹一条气泡，不会拆成"标题一条、正文一条"。正文本身不含 Markdown，无需转换。

    成功判定：返回 JSON `success == true`（旧版同时带 `code == 0`，两者都认）。
    接口限制：
      - 内容里的 URL、IPv4、长数字串会被判违规（`success=false`，`reason` 说明原因）；
      - 单条上限 1800 字符，超出截断（正常提醒远小于此）。
    """

    name = "qmsg"
    MAX_LEN = 1800

    def __init__(self, config: QmsgConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def ready(self) -> bool:
        return self.config.ready

    @staticmethod
    def merge_text(title: str, body: str) -> str:
        """把标题与正文合并成一条纯文本：`标题\\n正文`。

        只做规范化，不加任何装饰或分段标记：
          1. CRLF / CR 统一成 \\n，避免 QQ 里出现方块或意外换行；
          2. 逐行去掉首尾空白（Qmsg 会原样显示连续空格）；
          3. 连续空行压成一行，长气泡里不留大段空白。
        """
        raw = f"{title}\n{body}".replace("\r\n", "\n").replace("\r", "\n")
        merged: List[str] = []
        for line in raw.split("\n"):
            line = line.strip()
            if not line and (not merged or not merged[-1]):
                continue
            merged.append(line)
        return "\n".join(merged).strip()

    def _missing_config(self) -> Optional[str]:
        if not self.config.key.strip():
            return "未配置 key，请在 config.json 的 push.qmsg.key 填入 Qmsg 酱 Key"
        return None

    def _secrets(self) -> List[str]:
        return [self.config.key]

    def _build_request(
        self, title: str, body: str
    ) -> Tuple[str, str, Dict[str, Any]]:
        text = self.merge_text(title, body)
        if len(text) > self.MAX_LEN:
            text = text[: self.MAX_LEN - 3].rstrip() + "..."
        url = f"{self.config.server}/send/{quote(self.config.key.strip(), safe='')}"
        logger.info("[qmsg] 推送中…（标题=%s，key=%s）", title, mask_secret(self.config.key))
        return "GET", url, {"params": {"msg": text}, "timeout": self.config.timeout}

    def _judge(self, payload: Dict[str, Any]) -> Tuple[bool, str]:
        # 旧版返回 {"success":true,"reason":"操作成功","code":0}
        if "success" in payload:
            ok = bool(payload.get("success"))
        else:
            ok = payload.get("code") == 0
        if not ok:
            reason = payload.get("reason") or payload.get("msg") or str(payload)
            return False, reason
        return True, ""


# --------------------------------------------------------------------------- #
# 飞书
# --------------------------------------------------------------------------- #


def _feishu_sign(secret: str, timestamp: int) -> str:
    """飞书自定义机器人加签：base64(hmac_sha256(secret, "{timestamp}\\n{secret}"))。"""
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


class FeishuNotifier(BaseNotifier):
    """飞书自定义机器人 webhook：POST {"msg_type":"text","content":{"text": title\nbody}}。

    配置了 secret 时启用官方加签（timestamp + sign 放在请求体顶层）。
    成功判定：HTTP 200 且返回 JSON code == 0（新版）或 StatusCode == 0（旧版）。
    """

    name = "feishu"

    def __init__(self, config: FeishuConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def ready(self) -> bool:
        return self.config.ready

    def _missing_config(self) -> Optional[str]:
        if not self.config.webhook.strip():
            return "未配置 webhook，请在 config.json 的 push.feishu.webhook 填入机器人 Webhook 地址"
        return None

    def _secrets(self) -> List[str]:
        return [self.config.webhook, self.config.secret]

    def _build_request(
        self, title: str, body: str
    ) -> Tuple[str, str, Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "msg_type": "text",
            "content": {"text": f"{title}\n{body}"},
        }
        if self.config.secret:
            payload["timestamp"] = str(int(time.time()))
            payload["sign"] = _feishu_sign(self.config.secret, int(time.time()))

        logger.info(
            "[feishu] 推送中…（标题=%s，webhook=%s）",
            title, mask_secret(self.config.webhook, keep=8),
        )
        return "POST", self.config.webhook, {"json": payload, "timeout": self.config.timeout}

    def _judge(self, payload: Dict[str, Any]) -> Tuple[bool, str]:
        code = payload.get("code", payload.get("StatusCode"))
        if code not in (0, "0", None):
            message = payload.get("msg") or str(payload)
            return False, message
        if code is None:
            # 既无 code 又无 StatusCode，按字段缺失处理，不静默当成功
            return False, f"响应缺少成功码字段：{str(payload)[:120]}"
        return True, ""


# --------------------------------------------------------------------------- #
# 钉钉
# --------------------------------------------------------------------------- #


def _dingtalk_sign(secret: str, timestamp_ms: int) -> str:
    """钉钉机器人加签：urlencode(base64(hmac_sha256(secret, "{timestamp}\\n{secret}")))。"""
    string_to_sign = f"{timestamp_ms}\n{secret}"
    digest = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    return quote(base64.b64encode(digest).decode("utf-8"), safe="")


class DingTalkNotifier(BaseNotifier):
    """钉钉自定义机器人 webhook：POST {"msgtype":"text","text":{"content": title\nbody}}。

    配置了 secret 时在 webhook URL 上追加 timestamp + sign（官方加签协议）。
    成功判定：HTTP 200 且返回 JSON errcode == 0。
    """

    name = "dingtalk"

    def __init__(self, config: DingTalkConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def ready(self) -> bool:
        return self.config.ready

    def _build_url(self) -> str:
        url = self.config.webhook
        if self.config.secret:
            ts = int(time.time() * 1000)
            sign = _dingtalk_sign(self.config.secret, ts)
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}timestamp={ts}&sign={sign}"
        return url

    def _missing_config(self) -> Optional[str]:
        if not self.config.webhook.strip():
            return "未配置 webhook，请在 config.json 的 push.dingtalk.webhook 填入机器人 Webhook 地址"
        return None

    def _secrets(self) -> List[str]:
        return [self.config.webhook]

    def _build_request(
        self, title: str, body: str
    ) -> Tuple[str, str, Dict[str, Any]]:
        payload = {"msgtype": "text", "text": {"content": f"{title}\n{body}"}}
        logger.info(
            "[dingtalk] 推送中…（标题=%s，webhook=%s）", title, mask_secret(self.config.webhook, keep=8)
        )
        return "POST", self._build_url(), {"json": payload, "timeout": self.config.timeout}

    def _judge(self, payload: Dict[str, Any]) -> Tuple[bool, str]:
        if payload.get("errcode") != 0:
            message = payload.get("errmsg") or str(payload)
            return False, message
        return True, ""


class ToastNotifier(BaseNotifier):
    """页内通知渠道：推送同步投递到打开着的控制台页面（toast 气泡）。

    不走网络管线（覆写 send）：直接写 data/toasts.json 环形队列，
    前端轮询 /api/toasts 消费（取走即标已读）。无配置、常开——
    这是「浏览器就是控制台」的最后一块：daemon 的推送，开着页面的人
    在页面上同步看得到。
    """

    name = "toast"
    #: 页内投递不是远程推送，不进 push_history.json（那是「发到手机」的台账）
    records_history = False

    @property
    def enabled(self) -> bool:
        return True

    @property
    def ready(self) -> bool:
        return True

    def send(self, title: str, body: str, dry_run: bool = False) -> PushResult:
        if dry_run:
            return PushResult(self.name, ok=False, message="dry-run 未发送", skipped=True)
        try:
            from core.toasts import add_toasts

            add_toasts(title, body)
        except Exception as exc:  # noqa: BLE001 - 投递失败不影响其它通道
            logger.exception("[toast] 投递失败")
            return PushResult(self.name, ok=False, message=f"投递失败：{exc}")
        return PushResult(self.name, ok=True, message="已投递到页内通知")


# --------------------------------------------------------------------------- #
# 工厂
# --------------------------------------------------------------------------- #


def build_notifiers(push_config: PushConfig) -> List[BaseNotifier]:
    """按 push.channels 构造推送器列表；未选中的通道不实例化。

    toast（页内通知）不占 channels 名额、常开：它是投给打开着的控制台
    页面的本地镜像，不依赖任何远程服务配置。
    """
    notifiers: List[BaseNotifier] = [ToastNotifier()]
    channels = [item.lower() for item in push_config.channels]
    known = {"bark", "serverchan", "qmsg", "feishu", "dingtalk"}
    if "bark" in channels:
        notifiers.append(BarkNotifier(push_config.bark))
    if "serverchan" in channels:
        notifiers.append(ServerChanNotifier(push_config.serverchan))
    if "qmsg" in channels:
        notifiers.append(QmsgNotifier(push_config.qmsg))
    if "feishu" in channels:
        notifiers.append(FeishuNotifier(push_config.feishu))
    if "dingtalk" in channels:
        notifiers.append(DingTalkNotifier(push_config.dingtalk))
    unknown = sorted(set(channels) - known)
    if unknown:
        logger.warning(
            "忽略未实现的推送通道：%s（支持：bark / serverchan / qmsg / feishu / dingtalk）",
            "、".join(unknown),
        )
    return notifiers


def push_all(
    notifiers: List[BaseNotifier],
    title: str,
    body: str,
    dry_run: bool = False,
) -> List[PushResult]:
    """依次推送到所有通道，单通道失败不影响其它通道，全部结果返回给调用方记日志。"""
    results: List[PushResult] = []
    for notifier in notifiers:
        try:
            result = notifier.send(title, body, dry_run=dry_run)
        except PushError as exc:
            logger.error("[%s] 推送失败：%s", notifier.name, exc)
            result = PushResult(notifier.name, ok=False, message=str(exc))
        except Exception as exc:  # 兜底，别让脚本因为推送崩掉
            logger.exception("[%s] 未预期异常", notifier.name)
            result = PushResult(notifier.name, ok=False, message=f"未预期异常：{exc}")
        results.append(result)
        # toast 是投给控制台页面的本地镜像，不进「发到手机」的台账
        if not dry_run and getattr(notifier, "records_history", True):
            record_push_history(result.channel, result.ok, result.message,
                                title=title, body=body)
    return results

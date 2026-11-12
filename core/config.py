"""配置模块。

职责：把 config.json 变成强类型的 AppConfig 对象，并提供保存 / 加载 / 默认值
生成能力。所有字段缺失都走默认值，字段非法则抛 ConfigError（不静默吞掉），
保证下游模块拿到的配置一定可用。

覆盖内容：账号凭证、推送配置（本阶段仅 Bark）、每日检查时间、测试模式、日志与查询设置。
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

TEST_MODE_AUTO = "auto"
TEST_MODE_FORCE_ALERT = "force_alert"
TEST_MODE_FORCE_SILENT = "force_silent"

VALID_TEST_MODES: Tuple[str, ...] = (
    TEST_MODE_AUTO,
    TEST_MODE_FORCE_ALERT,
    TEST_MODE_FORCE_SILENT,
)

TEST_MODE_LABELS: Dict[str, str] = {
    TEST_MODE_AUTO: "自动判断（有未完成项才推送）",
    TEST_MODE_FORCE_ALERT: "强制提醒（无论结果都推送）",
    TEST_MODE_FORCE_SILENT: "强制静默（只跑流程和日志，不推送）",
}

#: 内置游戏标识 -> 展示名。新增游戏只需在这里登记 + 在 querier 里注册查询器。
KNOWN_GAMES: Dict[str, str] = {
    "genshin": "原神",
    "starrail": "崩坏：星穹铁道",
    "zzz": "绝区零",
    "arknights": "明日方舟",
    "endfield": "明日方舟：终末地",
    "wuwa": "鸣潮",
    "pgr": "战双帕弥什",
    "bh3": "崩坏3",
}

#: 米游社数据源覆盖的游戏（query.mihoyo_enabled=False 时跳过它们）
MIHOYO_GAMES: Tuple[str, ...] = ("genshin", "starrail", "zzz", "bh3")
#: 库街区系（鸣潮/战双，kurobbs API——风控容忍度三家里最高）
KURO_GAMES: Tuple[str, ...] = ("wuwa", "pgr")
#: 森空岛系（明日方舟/终末地，skland API——数美风控，高频轮询会致 token 失效）
SKLAND_GAMES: Tuple[str, ...] = ("arknights", "endfield")
#: 后台静默刷新间隔（分钟）：米游社/库街区 60（现役项目 1-2h 惯例的保守端），
#: 森空岛 180（无先例档位，用户裁决+自停护栏兜底）
BG_REFRESH_INTERVALS: Dict[str, int] = {"mihoyo": 60, "kuro": 60, "skland": 180}

#: 错过时间点后的补发窗口（分钟）。0 = 关掉补发，最大 720 分钟（12 小时）。
#: 例如 180：21:30 那轮因为休眠没跑到，只要在 23:59 前醒过来，就会立刻补跑一次。
CATCH_UP_MINUTES_DEFAULT = 180
CATCH_UP_MINUTES_MAX = 720

#: 「睡过头补发」「体力溢出提醒」的总开关。
#: 关掉后各自的时间 / 阈值参数会原样保留，重新打开即恢复，不用重新填一遍。
CATCH_UP_ENABLED_DEFAULT = True
RESOURCE_ALERT_ENABLED_DEFAULT = True
#: 「活动结束提醒」总开关：活动结束前 N 天开始在推送末尾追加提醒（默认开）
ACTIVITY_ALERT_ENABLED_DEFAULT = True
#: 活动结束提前提醒天数：1~30，默认 3 天（约等于旧的 72 小时窗口）
ACTIVITY_ALERT_DAYS_DEFAULT = 3
ACTIVITY_ALERT_DAYS_MAX = 30

#: 体力溢出预警阈值：当前值 >= 阈值就强制推一条（0 = 关闭该游戏的预警）。
#: 判定完全复用定时检查那一次查询的结果，**不额外发任何请求**。
RESOURCE_THRESHOLD_DEFAULT: Dict[str, int] = {
    "genshin": 160,    # 原粹树脂，上限 200
    "starrail": 240,   # 开拓力，上限 240
    "zzz": 200,        # 电量，上限 240
    "arknights": 135,  # 理智，上限随账号等级（常见 135~205）
    "endfield": 300,   # 理智（curStamina），上限实测 350
    "wuwa": 200,       # 结晶波片，上限 240（溢出转存结晶单质，另算）
    "pgr": 200,        # 血清，上限 240
    "bh3": 90,         # 琥珀能量，上限随等级（常见 120）
}

#: 各游戏"体力/树脂/电量/理智"在任务列表里的显示名，用于从查询结果里认出那一项。
#: 值与 core/querier.py 里 TaskItem 的 name 必须一致。
#: 明日方舟与终末地的体力都叫「理智」，靠 game_id 区分，不冲突。
RESOURCE_TASK_NAMES: Dict[str, str] = {
    "genshin": "原粹树脂",
    "starrail": "开拓力",
    "zzz": "电量",
    "arknights": "理智",
    "endfield": "理智",
    "wuwa": "结晶波片",
    "pgr": "血清",
    "bh3": "体力",
}

#: 阈值允许的最大值（体力上限普遍在 200~240 之间，留点余量）
RESOURCE_THRESHOLD_MAX = 1000


class ConfigError(Exception):
    """配置文件缺失 / 字段非法时抛出，由 main 统一转成友好提示。"""


# --------------------------------------------------------------------------- #
# 默认配置（首次运行据此生成 config.json，也是配置示例的单一来源）
# --------------------------------------------------------------------------- #

DEFAULT_CONFIG: Dict[str, Any] = {
    "app": {
        "test_mode": TEST_MODE_AUTO,
        "check_times": ["08:30", "20:30"],
        # 日界前落盘（2026-11-05）：04:00 是游戏日界，跨过去当天进度就被游戏
        # 清零、再也取不回来。所以日界前补一次「只落盘、不推送」的巡检，
        # 把当天最终状态冻进历史。默认开启——这是修 bug，不是可选项。
        "dayend_refresh_enabled": True,
        "dayend_refresh_at": "03:50",
        # 开机自启的用户意图：true = 用户要自启。注册表 Run 键被外部工具
        # （安全软件「开机加速」类）删掉时，启动链按此意图自愈重写；
        # 用户手动关闭开关时它落 false，自愈绝不违背用户意愿。
        "autostart_desired": False,
        "catch_up_minutes": CATCH_UP_MINUTES_DEFAULT,
        "catch_up_enabled": CATCH_UP_ENABLED_DEFAULT,
        "resource_alert_enabled": RESOURCE_ALERT_ENABLED_DEFAULT,
        "activity_alert_enabled": ACTIVITY_ALERT_ENABLED_DEFAULT,
        "genshin_resin_threshold": RESOURCE_THRESHOLD_DEFAULT["genshin"],
        "starrail_stamina_threshold": RESOURCE_THRESHOLD_DEFAULT["starrail"],
        "zzz_energy_threshold": RESOURCE_THRESHOLD_DEFAULT["zzz"],
        "arknights_ap_threshold": RESOURCE_THRESHOLD_DEFAULT["arknights"],
        "endfield_sanity_threshold": RESOURCE_THRESHOLD_DEFAULT["endfield"],
        "wuwa_energy_threshold": RESOURCE_THRESHOLD_DEFAULT["wuwa"],
        "pgr_serum_threshold": RESOURCE_THRESHOLD_DEFAULT["pgr"],
        "bh3_stamina_threshold": RESOURCE_THRESHOLD_DEFAULT["bh3"],
    },
    "accounts": [
        {
            "game": "genshin",
            "nickname": "旅行者",
            "enabled": True,
            "credential": {
                "uid": "100000000",
                "server": "cn_gf01",
                "cookie": "ltuid=请填写; ltoken=请填写",
            },
        },
        {
            "game": "starrail",
            "nickname": "开拓者",
            "enabled": True,
            "credential": {
                "uid": "100000000",
                "server": "prod_gf_cn",
                "cookie": "ltuid=请填写; ltoken=请填写",
            },
        },
        {
            "game": "zzz",
            "nickname": "绳匠",
            "enabled": True,
            "credential": {
                "uid": "100000000",
                "server": "prod_gf_cn",
                "cookie": "ltuid=请填写; ltoken=请填写",
            },
        },
        {
            "game": "bh3",
            "nickname": "舰长",
            "enabled": True,
            "credential": {
                "cookie": "ltuid=请填写; ltoken=请填写",
            },
        },
        {
            "game": "arknights",
            "nickname": "博士",
            "enabled": True,
            "credential": {
                # 鹰角通行证 Token：脚本会自动换取森空岛 cred 并缓存
                "token": "请填写鹰角通行证Token",
            },
        },
        {
            "game": "endfield",
            "nickname": "管理员",
            "enabled": True,
            "credential": {
                "token": "请填写鹰角通行证Token",
            },
        },
        {
            "game": "wuwa",
            "nickname": "漂泊者",
            "enabled": True,
            "credential": {
                # 库街区 Token：网页版登录后从 Cookie 的 user_token 取
                "token": "请填写库街区Token",
            },
        },
        {
            "game": "pgr",
            "nickname": "指挥官",
            "enabled": True,
            "credential": {
                # 同一张库街区 Token（绑定鸣潮时自动写入）
                "token": "请填写库街区Token",
            },
        },
    ],
    "push": {
        "channels": ["bark"],
        "always_notify": False,
        "bark": {
            "enabled": True,
            "server": "https://api.day.app",
            "device_key": "",
            "group": "游戏日常",
            "sound": "birdsong",
            "icon": "",
            "level": "active",
            "timeout": 10,
        },
        "serverchan": {
            "enabled": True,
            "sendkey": "",
            "timeout": 10,
        },
        "qmsg": {
            "enabled": True,
            "key": "",
            "server": "https://qmsg.zendee.cn",
            "timeout": 10,
        },
        "feishu": {
            "enabled": True,
            "webhook": "",
            "secret": "",
            "timeout": 10,
        },
        "dingtalk": {
            "enabled": True,
            "webhook": "",
            "secret": "",
            "timeout": 10,
        },
    },
    "query": {
        "provider": "mock",
        "timeout": 10,
        "mihoyo_enabled": True,
        "proxy": "",
        "mihoyo_device_mode": "auto",
        "mihoyo_task_scope": "daily",
    },
    "logging": {
        "dir": "logs",
        "level": "INFO",
        "keep_days": 14,
    },
}


# --------------------------------------------------------------------------- #
# 配置对象
# --------------------------------------------------------------------------- #


def cred_filled(value: object) -> bool:
    """凭证字段是否真的填了：非空，且不是出厂模板的「请填写…」占位符。

    占位符也算非空字符串，直接 bool() 会把"还没绑定"判成"已绑定"——
    调用方（凭证健康展示 / mock 门卫 / 周报守卫）就会各说各话。
    全项目凭证「是否已绑定」的唯一判定入口（2026-11-05 P6-5 合流）。
    """
    v = str(value or "").strip()
    return bool(v) and "请填写" not in v


@dataclass
class AccountConfig:
    """一个游戏账号的配置。credential 本阶段不参与真实请求，仅做结构占位。"""

    game: str
    nickname: str = ""
    enabled: bool = True
    credential: Dict[str, str] = field(default_factory=dict)

    @property
    def game_name(self) -> str:
        return KNOWN_GAMES.get(self.game, self.game)

    @classmethod
    def from_dict(cls, data: Any, index: int) -> "AccountConfig":
        if not isinstance(data, dict):
            raise ConfigError(f"accounts[{index}] 必须是 JSON 对象")
        game = str(data.get("game", "")).strip()
        if not game:
            raise ConfigError(f"accounts[{index}] 缺少 game 字段")
        credential = data.get("credential") or {}
        if not isinstance(credential, dict):
            raise ConfigError(f"accounts[{index}].credential 必须是 JSON 对象")
        return cls(
            game=game,
            nickname=str(data.get("nickname", "")),
            enabled=_coerce_bool(data.get("enabled", True)),
            credential={str(k): str(v) for k, v in credential.items()},
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "game": self.game,
            "nickname": self.nickname,
            "enabled": self.enabled,
            "credential": dict(self.credential),
        }


@dataclass
class BarkConfig:
    """Bark 推送配置。device_key 为空时视为"未配置"，推送阶段优雅跳过。"""

    enabled: bool = True
    server: str = "https://api.day.app"
    device_key: str = ""
    group: str = "游戏日常"
    sound: str = "birdsong"
    icon: str = ""
    level: str = "active"
    timeout: int = 10

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self.device_key.strip())

    @classmethod
    def from_dict(cls, data: Any) -> "BarkConfig":
        if data is None:
            return cls(enabled=False)
        if not isinstance(data, dict):
            raise ConfigError("push.bark 必须是 JSON 对象")
        return cls(
            enabled=_coerce_bool(data.get("enabled", True)),
            server=str(data.get("server") or "https://api.day.app").rstrip("/"),
            device_key=str(data.get("device_key") or "").strip(),
            group=str(data.get("group") or "游戏日常"),
            sound=str(data.get("sound") or ""),
            icon=str(data.get("icon") or ""),
            level=str(data.get("level") or "active"),
            timeout=int(data.get("timeout") or 10),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "server": self.server,
            "device_key": self.device_key,
            "group": self.group,
            "sound": self.sound,
            "icon": self.icon,
            "level": self.level,
            "timeout": self.timeout,
        }


@dataclass
class ServerChanConfig:
    """Server 酱（sctapi.ftqq.com）推送配置。sendkey 为空时视为"未配置"。"""

    enabled: bool = True
    sendkey: str = ""
    timeout: int = 10

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self.sendkey.strip())

    @classmethod
    def from_dict(cls, data: Any) -> "ServerChanConfig":
        if data is None:
            return cls(enabled=False)
        if not isinstance(data, dict):
            raise ConfigError("push.serverchan 必须是 JSON 对象")
        return cls(
            enabled=_coerce_bool(data.get("enabled", True)),
            sendkey=str(data.get("sendkey") or "").strip(),
            timeout=int(data.get("timeout") or 10),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"enabled": self.enabled, "sendkey": self.sendkey, "timeout": self.timeout}


@dataclass
class QmsgConfig:
    """Qmsg 酱（qmsg.zendee.cn）推送配置：把消息发到 QQ。

    接口：GET {server}/send/{key}?msg=<内容>（旧版口子，改版后另有 /v3/send）。
    成功判定：返回 JSON `success == true`（旧版同时带 `code == 0`）。

    key 为 Qmsg 控制台里那串 Key；server 一般不用改，留空则用官方地址。
    """

    enabled: bool = True
    key: str = ""
    server: str = "https://qmsg.zendee.cn"
    timeout: int = 10

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self.key.strip())

    @classmethod
    def from_dict(cls, data: Any) -> "QmsgConfig":
        if data is None:
            return cls(enabled=False)
        if not isinstance(data, dict):
            raise ConfigError("push.qmsg 必须是 JSON 对象")
        return cls(
            enabled=_coerce_bool(data.get("enabled", True)),
            key=str(data.get("key") or "").strip(),
            server=str(data.get("server") or "https://qmsg.zendee.cn").strip().rstrip("/"),
            timeout=int(data.get("timeout") or 10),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "key": self.key,
            "server": self.server,
            "timeout": self.timeout,
        }


@dataclass
class FeishuConfig:
    """飞书自定义机器人 webhook 推送配置。secret 为加签密钥（可选）。"""

    enabled: bool = True
    webhook: str = ""
    secret: str = ""
    timeout: int = 10

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self.webhook.strip())

    @classmethod
    def from_dict(cls, data: Any) -> "FeishuConfig":
        if data is None:
            return cls(enabled=False)
        if not isinstance(data, dict):
            raise ConfigError("push.feishu 必须是 JSON 对象")
        return cls(
            enabled=_coerce_bool(data.get("enabled", True)),
            webhook=str(data.get("webhook") or "").strip(),
            secret=str(data.get("secret") or "").strip(),
            timeout=int(data.get("timeout") or 10),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "webhook": self.webhook,
            "secret": self.secret,
            "timeout": self.timeout,
        }


@dataclass
class DingTalkConfig:
    """钉钉自定义机器人 webhook 推送配置。secret 为加签密钥（可选）。"""

    enabled: bool = True
    webhook: str = ""
    secret: str = ""
    timeout: int = 10

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self.webhook.strip())

    @classmethod
    def from_dict(cls, data: Any) -> "DingTalkConfig":
        if data is None:
            return cls(enabled=False)
        if not isinstance(data, dict):
            raise ConfigError("push.dingtalk 必须是 JSON 对象")
        return cls(
            enabled=_coerce_bool(data.get("enabled", True)),
            webhook=str(data.get("webhook") or "").strip(),
            secret=str(data.get("secret") or "").strip(),
            timeout=int(data.get("timeout") or 10),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "webhook": self.webhook,
            "secret": self.secret,
            "timeout": self.timeout,
        }


@dataclass
class PushConfig:
    """推送配置容器。按 push.channels 决定实际启用哪几个通道。"""

    channels: List[str] = field(default_factory=lambda: ["bark"])
    #: 全部日常已完成时是否也发一条「今日全部完成」的平安通知
    always_notify: bool = False
    bark: BarkConfig = field(default_factory=BarkConfig)
    serverchan: ServerChanConfig = field(default_factory=ServerChanConfig)
    qmsg: QmsgConfig = field(default_factory=QmsgConfig)
    feishu: FeishuConfig = field(default_factory=FeishuConfig)
    dingtalk: DingTalkConfig = field(default_factory=DingTalkConfig)
    #: 免打扰时段（HH:MM，可跨零点）：窗口内推送顺延到时段结束
    quiet_enabled: bool = False
    quiet_start: str = "23:00"
    quiet_end: str = "08:00"

    @staticmethod
    def _quiet_time(raw: Any, fallback: str, label: str) -> str:
        """免打扰时间校验：HH:MM（小时可 1 位）；缺省回落 fallback，非法才 ConfigError。

        11-03 之前写的 config 都没有 quiet_* 键——缺省必须是合法态，
        否则存量配置每次 parse 都炸（2026-11-05 实测 live_status 每分钟刷
        「push.quiet_start = None 非法」警告）。
        """
        text = str(raw if raw is not None else "").strip()
        if not text:
            return fallback
        if not re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", text):
            raise ConfigError(f"push.{label} = {raw!r} 非法，格式应为 HH:MM（如 23:00）")
        hour, minute = text.split(":")
        return f"{int(hour):02d}:{minute}"

    @classmethod
    def from_dict(cls, data: Any) -> "PushConfig":
        if data is None:
            return cls(channels=[], bark=BarkConfig(enabled=False),
                       serverchan=ServerChanConfig(enabled=False),
                       qmsg=QmsgConfig(enabled=False),
                       feishu=FeishuConfig(enabled=False),
                       dingtalk=DingTalkConfig(enabled=False))
        if not isinstance(data, dict):
            raise ConfigError("push 必须是 JSON 对象")
        channels = data.get("channels") or ["bark"]
        if not isinstance(channels, list):
            raise ConfigError("push.channels 必须是数组")
        # 兼容两种写法：push.qmsg.key（推荐）与平铺的 push.qmsg_key
        qmsg_data = data.get("qmsg")
        if qmsg_data is None and str(data.get("qmsg_key") or "").strip():
            qmsg_data = {"key": data.get("qmsg_key")}
        return cls(
            channels=[str(item).strip().lower() for item in channels if str(item).strip()],
            always_notify=bool(data.get("always_notify", False)),
            bark=BarkConfig.from_dict(data.get("bark")),
            serverchan=ServerChanConfig.from_dict(data.get("serverchan")),
            qmsg=QmsgConfig.from_dict(qmsg_data),
            feishu=FeishuConfig.from_dict(data.get("feishu")),
            dingtalk=DingTalkConfig.from_dict(data.get("dingtalk")),
            quiet_enabled=_coerce_bool(data.get("quiet_enabled", False)),
            quiet_start=cls._quiet_time(data.get("quiet_start"), "23:00", "quiet_start"),
            quiet_end=cls._quiet_time(data.get("quiet_end"), "08:00", "quiet_end"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "channels": list(self.channels),
            "always_notify": bool(self.always_notify),
            "bark": self.bark.to_dict(),
            "serverchan": self.serverchan.to_dict(),
            "qmsg": self.qmsg.to_dict(),
            "feishu": self.feishu.to_dict(),
            "dingtalk": self.dingtalk.to_dict(),
            "quiet_enabled": self.quiet_enabled,
            "quiet_start": self.quiet_start,
            "quiet_end": self.quiet_end,
        }


@dataclass
class QueryConfig:
    """查询层配置。provider=mock 时全部走写死的模拟数据。

    mihoyo_enabled=False 时跳过全部米游社游戏（原神/崩铁/绝区零），
    仅保留森空岛系（明日方舟/终末地），用于米游社持续风控时临时止血。

    proxy:
        米游社请求（getFp / deviceLogin / saveDevice / dailyNote）统一走的代理，
        例如 "http://127.0.0.1:7890"。留空则直连（或沿用系统 HTTP(S)_PROXY）。
        因为 getFp 是按出口 IP 签发指纹的，换 IP 必须让全部请求一起换。

    mihoyo_device_mode:
        - "auto"（默认）：先按 GsCore 原生方式请求（带持久化设备指纹），
          命中 5003/10041/1034/10035 时追加 x-rpc-challenge-* 头重试一次。
        - "fp"：始终带完整设备头（device_id + device_fp + name/model）。
        - "gs"：完全不参与设备注册，只发基础头（最小化指纹暴露，排障用）。

    mihoyo_task_scope:
        - "daily"（默认）：只把「每日委托 / 每日实训 / 每日活跃度」算进完成度；
          树脂、开拓力、电量、探索派遣等资源数据只在控制台"仅展示"，不进未完成、
          不触发推送。
        - "full"：资源类也算完成度（树脂满/体力满/无进行中派遣才算完成）。
    """

    provider: str = "mock"
    timeout: int = 10
    mihoyo_enabled: bool = True
    proxy: str = ""
    mihoyo_device_mode: str = "auto"
    mihoyo_task_scope: str = "daily"

    #: 合法的设备模式取值（配置校验用）
    DEVICE_MODES = ("auto", "fp", "gs")
    #: 合法的任务口径
    TASK_SCOPES = ("daily", "full")

    @classmethod
    def from_dict(cls, data: Any) -> "QueryConfig":
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise ConfigError("query 必须是 JSON 对象")
        mode = str(data.get("mihoyo_device_mode") or "auto").strip().lower()
        if mode not in cls.DEVICE_MODES:
            raise ConfigError(
                f"query.mihoyo_device_mode 只能是 {'/'.join(cls.DEVICE_MODES)}，实际为 {mode!r}"
            )
        scope = str(data.get("mihoyo_task_scope") or "daily").strip().lower()
        if scope not in cls.TASK_SCOPES:
            raise ConfigError(
                f"query.mihoyo_task_scope 只能是 {'/'.join(cls.TASK_SCOPES)}，实际为 {scope!r}"
            )
        return cls(
            provider=str(data.get("provider") or "mock").strip().lower(),
            timeout=int(data.get("timeout") or 10),
            mihoyo_enabled=_coerce_bool(data.get("mihoyo_enabled", True)),
            proxy=str(data.get("proxy") or "").strip(),
            mihoyo_device_mode=mode,
            mihoyo_task_scope=scope,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "timeout": self.timeout,
            "mihoyo_enabled": self.mihoyo_enabled,
            "proxy": self.proxy,
            "mihoyo_device_mode": self.mihoyo_device_mode,
            "mihoyo_task_scope": self.mihoyo_task_scope,
        }


@dataclass
class LoggingConfig:
    """日志配置。dir 为相对路径时，由 main 按项目根目录解析。"""

    dir: str = "logs"
    level: str = "INFO"
    keep_days: int = 14

    @classmethod
    def from_dict(cls, data: Any) -> "LoggingConfig":
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise ConfigError("logging 必须是 JSON 对象")
        return cls(
            dir=str(data.get("dir") or "logs"),
            level=str(data.get("level") or "INFO").upper(),
            keep_days=int(data.get("keep_days") or 14),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"dir": self.dir, "level": self.level, "keep_days": self.keep_days}


@dataclass
class CustomDaily:
    """手动打卡项：无 API 游戏的日常（2026-11-12）。

    mode=merged 随班次推送末尾；standalone 按独立时刻推（只早不迟）。
    template 为消息模板（空=默认文案），变量 {name}/{time}，Python 端渲染。
    reset 为该项的重置时刻（勾掉的「今日」到点自动重开）——默认 04:00
    与游戏日常重置同频，但每项可自定义（如健身房用 00:00）。
    """

    id: str
    name: str
    mode: str = "merged"
    time: str = ""
    reset: str = "04:00"
    template: str = ""
    enabled: bool = True
    #: 周计划：七元布尔 [一,二,三,四,五,六,日]（索引 0=周一），全 True=每天
    days: List[bool] = field(default_factory=lambda: [True] * 7)


@dataclass
class AppConfig:
    """完整配置对象，main 只依赖它。"""

    test_mode: str = TEST_MODE_AUTO
    check_times: List[str] = field(default_factory=lambda: ["08:30", "20:30"])
    # 睡过头 / 关机错过时间点后，醒来立刻补跑一次；0 = 关闭补发
    catch_up_minutes: int = CATCH_UP_MINUTES_DEFAULT
    # 补发总开关：false 时补发窗口整体失效（catch_up_minutes 原样保留）
    catch_up_enabled: bool = CATCH_UP_ENABLED_DEFAULT
    # 开机自启的用户意图（自愈依据）：注册表 Run 键被外部工具删掉时，
    # 启动链按它重写；手动关开关时落 false。见 autostart.self_heal。
    autostart_desired: bool = False
    pgr_serum_threshold: int = RESOURCE_THRESHOLD_DEFAULT["pgr"]
    bh3_stamina_threshold: int = RESOURCE_THRESHOLD_DEFAULT["bh3"]
    # 体力溢出提醒总开关：false 时完全跳过判定（各阈值原样保留）
    resource_alert_enabled: bool = RESOURCE_ALERT_ENABLED_DEFAULT
    # 后台静默刷新：定时巡查之外按游戏族间隔自动更新实时面板（不写巡检历史）
    bg_refresh_enabled: bool = True
    # 每款游戏的覆盖项 {game_id: {enabled: bool, interval: 分钟}}；
    # 未覆盖的游戏用游戏族默认档（米/库 60、森 180）
    bg_refresh_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # 活动结束提醒总开关：false 时不读活动缓存、不追加任何活动警告
    activity_alert_enabled: bool = ACTIVITY_ALERT_ENABLED_DEFAULT
    # 活动结束提前提醒天数：结束前 N 天开始提醒（1~30，默认 3）——
    # 三段梯度上线（2026-11-11）后只控「即将结束」徽章显示窗，推送走三段
    activity_alert_days: int = ACTIVITY_ALERT_DAYS_DEFAULT
    # 结束推送三段阈值（小时）：规划/行动/召回三档独立，-1 = 该档不提醒
    activity_deadline_tiers: List[int] = field(
        default_factory=lambda: list(ACTIVITY_DEADLINE_TIERS_DEFAULT))
    # 手动打卡项（无 API 游戏的日常，2026-11-12）
    custom_dailies: List[CustomDaily] = field(default_factory=list)
    # 体力溢出预警：当前值 >= 阈值则强制推送（0 = 关闭）。只看已有查询结果，不额外请求。
    genshin_resin_threshold: int = RESOURCE_THRESHOLD_DEFAULT["genshin"]
    starrail_stamina_threshold: int = RESOURCE_THRESHOLD_DEFAULT["starrail"]
    zzz_energy_threshold: int = RESOURCE_THRESHOLD_DEFAULT["zzz"]
    arknights_ap_threshold: int = RESOURCE_THRESHOLD_DEFAULT["arknights"]
    endfield_sanity_threshold: int = RESOURCE_THRESHOLD_DEFAULT["endfield"]
    wuwa_energy_threshold: int = RESOURCE_THRESHOLD_DEFAULT["wuwa"]
    accounts: List[AccountConfig] = field(default_factory=list)
    push: PushConfig = field(default_factory=PushConfig)
    query: QueryConfig = field(default_factory=QueryConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    config_path: Optional[Path] = None

    @property
    def enabled_accounts(self) -> List[AccountConfig]:
        return [account for account in self.accounts if account.enabled]

    @property
    def disabled_accounts(self) -> List[AccountConfig]:
        """被 enabled=false 关掉的账号 —— 它们不会被查询，也不会进报表。"""
        return [account for account in self.accounts if not account.enabled]

    @property
    def enabled_game_ids(self) -> List[str]:
        """当前启用的游戏标识列表（保持配置里的顺序）。

        报表、日志、界面要列游戏时一律从这里取，**不要在别处写死游戏清单**。
        """
        return [account.game for account in self.accounts if account.enabled]

    @property
    def test_mode_label(self) -> str:
        return TEST_MODE_LABELS.get(self.test_mode, self.test_mode)

    @property
    def resource_thresholds(self) -> Dict[str, int]:
        """{game_id: 阈值}，交给 analyzer 判定体力溢出。

        总开关关掉时统一返回 0（= 每个游戏都关闭）——analyzer 里阈值 <= 0 即跳过，
        这样「关闭提醒」只需要经过一个入口，不用在判定函数里再加分支。
        """
        if not self.resource_alert_enabled:
            return {game: 0 for game in RESOURCE_THRESHOLD_DEFAULT}
        return {
            "genshin": self.genshin_resin_threshold,
            "starrail": self.starrail_stamina_threshold,
            "zzz": self.zzz_energy_threshold,
            "arknights": self.arknights_ap_threshold,
            "endfield": self.endfield_sanity_threshold,
            "wuwa": self.wuwa_energy_threshold,
            "pgr": self.pgr_serum_threshold,
            "bh3": self.bh3_stamina_threshold,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "app": {
                "test_mode": self.test_mode,
                "check_times": list(self.check_times),
                "catch_up_minutes": int(self.catch_up_minutes),
                "catch_up_enabled": bool(self.catch_up_enabled),
                "autostart_desired": bool(self.autostart_desired),
                "resource_alert_enabled": bool(self.resource_alert_enabled),
                "bg_refresh_enabled": bool(self.bg_refresh_enabled),
                "bg_refresh_overrides": {
                    k: dict(v) for k, v in self.bg_refresh_overrides.items()},
                "activity_alert_enabled": bool(self.activity_alert_enabled),
                "activity_alert_days": int(self.activity_alert_days),
                "activity_deadline_tiers": list(self.activity_deadline_tiers),
                "custom_dailies": [
                    dict(id=d.id, name=d.name, mode=d.mode, time=d.time,
                         reset=d.reset, template=d.template, enabled=bool(d.enabled),
                         days=[bool(x) for x in d.days])
                    for d in self.custom_dailies
                ],
                "genshin_resin_threshold": int(self.genshin_resin_threshold),
                "starrail_stamina_threshold": int(self.starrail_stamina_threshold),
                "zzz_energy_threshold": int(self.zzz_energy_threshold),
                "arknights_ap_threshold": int(self.arknights_ap_threshold),
                "endfield_sanity_threshold": int(self.endfield_sanity_threshold),
                "wuwa_energy_threshold": int(self.wuwa_energy_threshold),
                "pgr_serum_threshold": int(self.pgr_serum_threshold),
                "bh3_stamina_threshold": int(self.bh3_stamina_threshold),
            },
            "accounts": [account.to_dict() for account in self.accounts],
            "push": self.push.to_dict(),
            "query": self.query.to_dict(),
            "logging": self.logging.to_dict(),
        }


# --------------------------------------------------------------------------- #
# 加载 / 保存
# --------------------------------------------------------------------------- #


def _parse_check_times(raw: Any) -> List[str]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError("app.check_times 必须是非空数组，例如 [\"08:30\", \"20:30\"]")
    result: List[str] = []
    for item in raw:
        text = str(item).strip()
        if not TIME_PATTERN.match(text):
            raise ConfigError(f"app.check_times 中的 \"{text}\" 不是合法的 HH:MM 时间")
        if text not in result:
            result.append(text)
    return sorted(result)


def _parse_bool(raw: Any, label: str, default: bool = True) -> bool:
    """解析总开关：接受 true/false、1/0、on/off、yes/no；留空走默认值。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    text = str(raw).strip().lower()
    if text in ("1", "true", "yes", "on", "开", "开启"):
        return True
    if text in ("0", "false", "no", "off", "关", "关闭"):
        return False
    raise ConfigError(f"app.{label} = {raw!r} 非法，请填 true 或 false")


def _coerce_bool(raw: Any, default: bool = True) -> bool:
    """宽松布尔解析（账号/渠道 enabled 等逐项字段）：与 _parse_bool 同样的字面量
    集合，但认不出的值回落 default 而不是拒整份配置。专治手写
    "enabled": "false" 被 bool() 当成 True——字符串非空恒真。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    text = str(raw).strip().lower()
    if text in ("1", "true", "yes", "on", "开", "开启"):
        return True
    if text in ("0", "false", "no", "off", "关", "关闭"):
        return False
    return default


def _parse_catch_up_minutes(raw: Any) -> int:
    """解析补发窗口（分钟）：留空/非法走默认值，0 或负数表示关闭补发。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return CATCH_UP_MINUTES_DEFAULT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ConfigError(
            f"app.catch_up_minutes = {raw!r} 非法，请填 0（关闭）~ {CATCH_UP_MINUTES_MAX} 之间的整数"
        )
    if value < 0:
        return 0
    return min(value, CATCH_UP_MINUTES_MAX)


def _parse_activity_days(raw: Any) -> int:
    """解析活动提前提醒天数：留空 -> 默认 3；非法 -> 报错；越界收敛到 1~30。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return ACTIVITY_ALERT_DAYS_DEFAULT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ConfigError(
            f"app.activity_alert_days = {raw!r} 非法，请填 1~{ACTIVITY_ALERT_DAYS_MAX} 之间的整数"
        )
    return max(1, min(value, ACTIVITY_ALERT_DAYS_MAX))


#: 结束推送三段阈值（小时）默认档：规划(72)→行动(24)→召回(6)。
#: 运行时每段 -1 = 该档不提醒（2026-11-11 用户裁定：三段独立、-1 关闭）。
ACTIVITY_DEADLINE_TIERS_DEFAULT = [72, 24, 6]
ACTIVITY_DEADLINE_TIER_MAX = 2160  # 90 天，覆盖最长活动周期


#: 手动打卡（2026-11-12）：无 API 游戏的日常手动记账项
CUSTOM_DAILY_MODES = ("merged", "standalone")
CUSTOM_DAILY_NAME_MAX = 20
CUSTOM_DAILY_TEMPLATE_MAX = 100


def _parse_custom_dailies(raw: Any) -> List["CustomDaily"]:
    """解析手动打卡清单：坏项跳过不致命，字段越界收敛。"""
    import uuid as _uuid

    out: List[CustomDaily] = []
    if not isinstance(raw, list):
        return out
    for node in raw:
        if not isinstance(node, dict):
            continue
        name = str(node.get("name") or "").strip()[:CUSTOM_DAILY_NAME_MAX]
        if not name:
            continue
        mode = str(node.get("mode") or "merged")
        if mode not in CUSTOM_DAILY_MODES:
            mode = "merged"
        time_s = str(node.get("time") or "").strip()
        if mode == "standalone" and not re.match(r"^\d{1,2}:\d{2}$", time_s):
            time_s = "20:00"
        if mode == "merged":
            time_s = ""
        reset = str(node.get("reset") or "04:00").strip()
        if not re.match(r"^\d{1,2}:\d{2}$", reset):
            reset = "04:00"
        template = str(node.get("template") or "").strip()[:CUSTOM_DAILY_TEMPLATE_MAX]
        days_raw = node.get("days")
        if (not isinstance(days_raw, list) or len(days_raw) != 7
                or not all(isinstance(x, bool) for x in days_raw)):
            days_raw = [True] * 7   # 缺失/坏格式 = 每天（向后兼容旧数据）
        out.append(CustomDaily(
            id=str(node.get("id") or f"cd-{_uuid.uuid4().hex[:8]}"),
            name=name, mode=mode, time=time_s, reset=reset, template=template,
            enabled=bool(node.get("enabled", True)), days=list(days_raw),
        ))
    return out


#: 后台刷新单款覆盖的间隔上下限（分钟）：5 分钟是风控安全底线，1440=一天一次
BG_REFRESH_INTERVAL_MIN = 5
BG_REFRESH_INTERVAL_MAX = 1440


def _parse_bg_refresh_overrides(raw: Any) -> Dict[str, Dict[str, Any]]:
    """解析每款游戏的刷新覆盖 {game_id: {enabled, interval}}。

    坏项/未知游戏/越界间隔整项丢弃（回落游戏族默认档），不致命不报错——
    这是锦上添花的覆盖层，不能因为它坏掉把整个配置搞挂。
    """
    if not isinstance(raw, dict):
        return {}
    known = set(KNOWN_GAMES)
    out: Dict[str, Dict[str, Any]] = {}
    for gid, node in raw.items():
        if not isinstance(gid, str) or gid not in known or not isinstance(node, dict):
            continue
        item: Dict[str, Any] = {}
        if "enabled" in node:
            item["enabled"] = bool(node.get("enabled"))
        if "interval" in node:
            try:
                iv = int(node.get("interval"))
            except (TypeError, ValueError):
                continue
            if BG_REFRESH_INTERVAL_MIN <= iv <= BG_REFRESH_INTERVAL_MAX:
                item["interval"] = iv
        if item:
            out[gid] = item
    return out


def _parse_deadline_tiers(raw: Any) -> List[int]:
    """解析结束推送三段（小时）：每段 -1（关闭）或 1~2160；缺省 = 默认档。"""
    if raw is None:
        return list(ACTIVITY_DEADLINE_TIERS_DEFAULT)
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise ConfigError(
            f"app.activity_deadline_tiers = {raw!r} 非法，应为 3 个整数（-1 或 1~2160）"
        )
    out: List[int] = []
    for item in raw:
        try:
            value = int(item)
        except (TypeError, ValueError):
            raise ConfigError(
                f"app.activity_deadline_tiers = {raw!r} 含非整数 {item!r}"
            ) from None
        if value == 0 or not (-1 <= value <= ACTIVITY_DEADLINE_TIER_MAX):
            raise ConfigError(
                f"app.activity_deadline_tiers = {raw!r} 非法，每段应为 -1 或 1~2160"
            )
        out.append(value)
    return out


def _parse_threshold(raw: Any, label: str, game: str) -> int:
    """解析体力溢出阈值：留空 -> 该游戏的默认值；0 或负数 -> 关闭；非法 -> 报错。"""
    default = RESOURCE_THRESHOLD_DEFAULT.get(game, 0)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ConfigError(f"app.{label} = {raw!r} 非法，请填 0（关闭）或正整数")
    if value < 0:
        return 0
    return min(value, RESOURCE_THRESHOLD_MAX)


def parse_config(data: Dict[str, Any], source: Optional[Path] = None) -> AppConfig:
    """把已解析的 dict 校验并转换成 AppConfig。缺省字段用默认值补齐。"""
    if not isinstance(data, dict):
        raise ConfigError("配置文件根节点必须是 JSON 对象")

    merged = copy.deepcopy(DEFAULT_CONFIG)
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value

    app_section = merged.get("app") or {}
    if not isinstance(app_section, dict):
        raise ConfigError("app 必须是 JSON 对象")

    test_mode = str(app_section.get("test_mode") or TEST_MODE_AUTO).strip().lower()
    if test_mode not in VALID_TEST_MODES:
        raise ConfigError(
            f"app.test_mode = \"{test_mode}\" 非法，可选值：{' / '.join(VALID_TEST_MODES)}"
        )

    accounts_raw = merged.get("accounts") or []
    if not isinstance(accounts_raw, list):
        raise ConfigError("accounts 必须是数组")
    accounts = [AccountConfig.from_dict(item, i) for i, item in enumerate(accounts_raw)]

    return AppConfig(
        test_mode=test_mode,
        check_times=_parse_check_times(app_section.get("check_times")),
        catch_up_minutes=_parse_catch_up_minutes(app_section.get("catch_up_minutes")),
        catch_up_enabled=_parse_bool(
            app_section.get("catch_up_enabled"), "catch_up_enabled", CATCH_UP_ENABLED_DEFAULT),
        autostart_desired=_parse_bool(
            app_section.get("autostart_desired"), "autostart_desired", False),
        resource_alert_enabled=_parse_bool(
            app_section.get("resource_alert_enabled"), "resource_alert_enabled",
            RESOURCE_ALERT_ENABLED_DEFAULT),
        bg_refresh_enabled=_parse_bool(
            app_section.get("bg_refresh_enabled"), "bg_refresh_enabled", True),
        bg_refresh_overrides=_parse_bg_refresh_overrides(
            app_section.get("bg_refresh_overrides")),
        activity_alert_enabled=_parse_bool(
            app_section.get("activity_alert_enabled"), "activity_alert_enabled",
            ACTIVITY_ALERT_ENABLED_DEFAULT),
        activity_alert_days=_parse_activity_days(app_section.get("activity_alert_days")),
        activity_deadline_tiers=_parse_deadline_tiers(
            app_section.get("activity_deadline_tiers")),
        custom_dailies=_parse_custom_dailies(app_section.get("custom_dailies")),
        genshin_resin_threshold=_parse_threshold(
            app_section.get("genshin_resin_threshold"), "genshin_resin_threshold", "genshin"),
        starrail_stamina_threshold=_parse_threshold(
            app_section.get("starrail_stamina_threshold"), "starrail_stamina_threshold", "starrail"),
        zzz_energy_threshold=_parse_threshold(
            app_section.get("zzz_energy_threshold"), "zzz_energy_threshold", "zzz"),
        arknights_ap_threshold=_parse_threshold(
            app_section.get("arknights_ap_threshold"), "arknights_ap_threshold", "arknights"),
        endfield_sanity_threshold=_parse_threshold(
            app_section.get("endfield_sanity_threshold"), "endfield_sanity_threshold", "endfield"),
        wuwa_energy_threshold=_parse_threshold(
            app_section.get("wuwa_energy_threshold"), "wuwa_energy_threshold", "wuwa"),
        pgr_serum_threshold=_parse_threshold(
            app_section.get("pgr_serum_threshold"), "pgr_serum_threshold", "pgr"),
        bh3_stamina_threshold=_parse_threshold(
            app_section.get("bh3_stamina_threshold"), "bh3_stamina_threshold", "bh3"),
        accounts=accounts,
        push=PushConfig.from_dict(merged.get("push")),
        query=QueryConfig.from_dict(merged.get("query")),
        logging=LoggingConfig.from_dict(merged.get("logging")),
        config_path=source,
    )


def load_config(path: str | Path, auto_create: bool = False) -> AppConfig:
    """读取本地 JSON 配置。

    Args:
        path: 配置文件路径。
        auto_create: 文件不存在时是否用默认配置生成一份（首次运行更顺手）。

    Raises:
        ConfigError: 文件不存在（且未 auto_create）、JSON 语法错误、字段非法。
    """
    config_path = Path(path).expanduser()
    if not config_path.exists():
        if auto_create:
            create_default_config(config_path)
        else:
            raise ConfigError(
                f"找不到配置文件：{config_path}\n"
                f"可先执行 `python main.py --init` 生成一份默认配置。"
            )

    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"读取配置文件失败：{exc}") from exc

    if not raw_text.strip():
        raise ConfigError(f"配置文件为空：{config_path}")

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"配置文件 JSON 语法错误（第 {exc.lineno} 行第 {exc.colno} 列）：{exc.msg}"
        ) from exc

    # 凭证字段落盘是 DPAPI 密文（core.secure_store，P3）：AppConfig 读端
    # 与 login_ui.load_raw_config 同样在此统一解密；旧明文原样通过
    try:
        from core.secure_store import decrypt_config_dict
    except ImportError:   # 防御：secure_store 缺席时退化为旧行为，不影响配置加载
        decrypt_config_dict = lambda d: d  # noqa: E731
    return parse_config(decrypt_config_dict(data), source=config_path)


def save_config(config: AppConfig, path: str | Path | None = None) -> Path:
    """把配置对象写回 JSON（UTF-8、缩进两格、保留中文），返回实际写入路径。

    与 login_ui.save_raw_config 同纪律：mkstemp 唯一临时名 + os.replace 原子替换，
    防并发交错与半截文件。"""
    target = Path(path or config.config_path or "config.json").expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=".config-", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    config.config_path = target
    return target


def create_default_config(path: str | Path) -> Path:
    """用内置 DEFAULT_CONFIG 生成一份可直接运行的配置，返回路径。"""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n"
    target.write_text(payload, encoding="utf-8")
    return target


# --------------------------------------------------------------------------- #
# 检查时间工具（本阶段只用于展示"下次检查"，不做常驻调度）
# --------------------------------------------------------------------------- #


def next_check_time(check_times: List[str], now: Optional[datetime] = None) -> Optional[datetime]:
    """返回下一个检查时间点；当天已全部过点则返回明天的第一个点。"""
    if not check_times:
        return None
    current = now or datetime.now()
    for text in sorted(check_times):
        hour, minute = (int(part) for part in text.split(":"))
        candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > current:
            return candidate
    hour, minute = (int(part) for part in sorted(check_times)[0].split(":"))
    tomorrow = current + timedelta(days=1)
    return tomorrow.replace(hour=hour, minute=minute, second=0, microsecond=0)

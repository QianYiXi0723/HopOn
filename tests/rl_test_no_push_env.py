# -*- coding: utf-8 -*-
"""HOPON_NO_PUSH=1 时禁止任何远程推送（E2E 沙箱隔离，2026-11-26）。

背景：virgin_e2e 沙箱曾因拿到真实 qmsg key，把假数据周报发到用户微信
（8 条垃圾，见 lesson 0mwwfevg）。门卫放在 build_notifiers 唯一收口点：
设了 HOPON_NO_PUSH 时不实例化任何远程通道，所有下游"无通道跳过"逻辑生效。
正常用户不设该变量，行为完全不变。

用法：python tests/rl_test_no_push_env.py   （0 = 通过）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  PASS " + name)
    else:
        FAIL += 1
        print("  FAIL " + name + (" | " + str(detail) if detail else ""))


from core.config import PushConfig  # noqa: E402
from core.notifier import build_notifiers  # noqa: E402

# 一个"配了全部远程通道"的推送配置
cfg = PushConfig(
    channels=["bark", "serverchan", "qmsg", "feishu", "dingtalk"],
)

# 不设变量：应含远程通道（基线，证明门卫没把正常功能弄坏）
os.environ.pop("HOPON_NO_PUSH", None)
normal = build_notifiers(cfg)
remote_names = {getattr(n, "name", "") for n in normal} - {"toast"}
check("未设 HOPON_NO_PUSH 时远程通道正常构造", len(remote_names) >= 1, remote_names)

# 设变量：不应有任何远程通道
os.environ["HOPON_NO_PUSH"] = "1"
blocked = build_notifiers(cfg)
blocked_remote = {getattr(n, "name", "") for n in blocked} - {"toast"}
check("HOPON_NO_PUSH=1 时无远程通道", not blocked_remote, blocked_remote)
os.environ.pop("HOPON_NO_PUSH", None)

print(f"\nPASS {PASS} / FAIL {FAIL}")
sys.exit(1 if FAIL else 0)

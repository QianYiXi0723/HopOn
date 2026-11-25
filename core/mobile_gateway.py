# -*- coding: utf-8 -*-
"""手机局域网配对：一次性码 + Bearer 令牌 + 按 IP 限流。

对标 agentre pairing / Syncthing：单 pending 码、短 TTL、用后即焚、
256-bit 令牌、constant-time 比较、失败限流。绝不处理凭证本身。
"""
from __future__ import annotations

import hmac
import secrets
import threading
import time
from typing import Dict, Optional

_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # 去掉易混淆 0O1I


class PairingManager:
    def __init__(self, ttl: float = 60.0, max_failures: int = 5,
                 cooldown: float = 300.0) -> None:
        self._ttl = ttl
        self._max_failures = max_failures
        self._cooldown = cooldown
        self._lock = threading.Lock()
        self._code: Optional[str] = None
        self._code_expire = 0.0
        self._tokens: set[str] = set()
        self._failures: Dict[str, tuple[int, float]] = {}

    def issue_code(self) -> str:
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
        with self._lock:
            self._code = code
            self._code_expire = time.monotonic() + self._ttl
        return code

    def redeem(self, code: str) -> Optional[str]:
        with self._lock:
            if (not self._code or not code or
                    time.monotonic() > self._code_expire or
                    not hmac.compare_digest(self._code, str(code))):
                return None
            token = secrets.token_urlsafe(32)  # 256-bit
            self._tokens.add(token)
            self._code = None                  # 用后即焚
            return token

    def verify_token(self, token: str) -> bool:
        if not token:
            return False
        with self._lock:
            return any(hmac.compare_digest(token, t) for t in self._tokens)

    def revoke(self, token: str) -> None:
        with self._lock:
            self._tokens.discard(token)

    def note_failure(self, ip: str) -> None:
        with self._lock:
            n, _ = self._failures.get(ip, (0, 0.0))
            self._failures[ip] = (n + 1, time.monotonic())

    def is_rate_limited(self, ip: str) -> bool:
        with self._lock:
            n, last = self._failures.get(ip, (0, 0.0))
            if n >= self._max_failures and time.monotonic() - last < self._cooldown:
                return True
            if n and time.monotonic() - last >= self._cooldown:
                self._failures.pop(ip, None)
            return False

# -*- coding: utf-8 -*-
"""手机局域网配对：一次性码 + Bearer 令牌 + 按 IP 限流。

对标 agentre pairing / Syncthing：单 pending 码、短 TTL、用后即焚、
256-bit 令牌、constant-time 比较、失败限流。绝不处理凭证本身。
"""
from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional
from urllib.parse import parse_qs, urlparse

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


class MobileGatewayServer:
    """独立端口的手机网关：只暴露白名单端点，绝不复用主控制台 handler。

    主控制台有 /api/push（读 Key）、/api/daemon/*（启停），一旦绑 0.0.0.0
    同网段任何人都能调——所以这里另起一个只做配对+只读+打卡的服务。
    """

    def __init__(self, pm: PairingManager, port: int = 8766, *,
                 snapshot_fn, dailies_fn, checkin_fn) -> None:
        self.pm = pm
        self.port = port
        self.snapshot_fn = snapshot_fn
        self.dailies_fn = dailies_fn
        self.checkin_fn = checkin_fn
        self._httpd = None
        self._thread = None
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, fmt, *a):  # 不把配对码/令牌写日志
                pass

            def _send(self, code, obj):
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authed(self):
                h = self.headers.get("Authorization", "")
                return h.startswith("Bearer ") and outer.pm.verify_token(h[7:])

            def do_GET(self):
                u = urlparse(self.path)
                ip = self.client_address[0]
                if outer.pm.is_rate_limited(ip):
                    return self._send(429, {"ok": False, "message": "尝试过多，请稍后再试"})
                if u.path == "/":
                    code = (parse_qs(u.query).get("code") or [""])[0]
                    page = (
                        "<!doctype html><meta charset='utf-8'>"
                        "<meta name='viewport' content='width=device-width'>"
                        "<h2>HopOn 配对</h2><p>正在连接…</p>"
                        f"<script>fetch('/pair?code={code}').then(r=>r.json())"
                        ".then(j=>document.querySelector('p').textContent="
                        "j.ok?'配对成功，可关闭本页':'配对码无效或已过期')</script>"
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(page)))
                    self.end_headers()
                    self.wfile.write(page)
                    return
                if u.path == "/pair":
                    code = (parse_qs(u.query).get("code") or [""])[0]
                    tok = outer.pm.redeem(code)
                    if not tok:
                        outer.pm.note_failure(ip)
                        return self._send(401, {"ok": False, "message": "配对码无效或已过期"})
                    return self._send(200, {"ok": True, "token": tok})
                if u.path in ("/api/mobile/snapshot", "/api/mobile/dailies"):
                    if not self._authed():
                        outer.pm.note_failure(ip)
                        return self._send(401, {"ok": False, "message": "未配对"})
                    fn = outer.snapshot_fn if u.path.endswith("snapshot") else outer.dailies_fn
                    return self._send(200, fn())
                return self._send(404, {"ok": False, "message": "not found"})

            def do_POST(self):
                u = urlparse(self.path)
                if u.path != "/api/mobile/checkin":
                    return self._send(404, {"ok": False, "message": "not found"})
                if not self._authed():
                    return self._send(401, {"ok": False, "message": "未配对"})
                try:
                    n = int(self.headers.get("Content-Length", "0"))
                    data = json.loads(self.rfile.read(n) or b"{}")
                    result = outer.checkin_fn(str(data.get("id", "")), str(data.get("date", "")))
                    return self._send(200, result)
                except Exception as exc:  # noqa: BLE001
                    return self._send(400, {"ok": False, "message": str(exc)})

        self._Handler = H

    def start(self) -> int:
        self._httpd = ThreadingHTTPServer(("0.0.0.0", self.port), self._Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self._httpd.server_address[1]

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()

    def pair_url(self, ip: str) -> str:
        return f"http://{ip}:{self.port}/?code={self.pm.issue_code()}"


import socket  # noqa: E402 - 数据适配函数放文件末尾，保持上方配对核心内聚


def lan_ip() -> str:
    """UDP connect 不发包，只用来选本机局域网地址（抄自旧 tools_server.py）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as p:
            p.settimeout(0.5)
            p.connect(("8.8.8.8", 80))
            return p.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _accounts_status():
    # 延迟导入避免在纯模块测试时触发 login_ui 的重量级初始化。
    from login_ui import accounts_status
    return accounts_status()


def build_snapshot():
    """只产白名单字段：账号 id/name/enabled/has_credential，绝不带 cookie/token。"""
    raw = _accounts_status()
    games = [{"id": str(i.get("id", "")), "name": str(i.get("name", "")),
              "enabled": bool(i.get("enabled")),
              "has_credential": bool(i.get("has_credential"))}
             for i in (raw.get("items") or []) if isinstance(i, dict)]
    return {"ok": True, "version": "m1", "games": games}


def build_dailies():
    from login_ui import custom_dailies_status
    raw = custom_dailies_status()
    items = [{"id": str(i.get("id", "")), "name": str(i.get("name", "")),
              "done": bool(i.get("done")), "times": list(i.get("times") or [])}
             for i in (raw.get("items") or []) if isinstance(i, dict)]
    return {"ok": True, "items": items}


def do_checkin(item_id: str, date: str):
    from login_ui import custom_dailies_update
    payload = {"action": "toggle_done", "id": item_id}
    if date:
        payload["date"] = date  # 04:00 日界由后端按此 date 裁定，手机不算日期
    return custom_dailies_update(payload)

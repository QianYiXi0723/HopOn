# -*- coding: utf-8 -*-
"""手机局域网配对：一次性码 + Bearer 令牌 + 按 IP 限流。

对标 agentre pairing / Syncthing：单 pending 码、短 TTL、用后即焚、
256-bit 令牌、constant-time 比较、失败限流。绝不处理凭证本身。
"""
from __future__ import annotations

import hmac
import json
import logging
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # 去掉易混淆 0O1I

#: 当前运行中的网关实例（由 login_ui.serve() 设置，供本机 UI 端点签发配对码）。
_ACTIVE_GATEWAY = None
#: 当前运行中的设备发现响应器（随网关启停）。
_ACTIVE_DISCOVERY = None


def set_active_gateway(gw) -> None:
    global _ACTIVE_GATEWAY
    _ACTIVE_GATEWAY = gw


def get_active_gateway():
    return _ACTIVE_GATEWAY


def start_gateway() -> dict:
    """运行时开启网关（UI 开关直接调用，立即生效，不必重启）。"""
    global _ACTIVE_DISCOVERY
    gw = get_active_gateway()
    if gw is None:
        gw = MobileGatewayServer(
            PairingManager(), port=8766,
            snapshot_fn=build_snapshot, dailies_fn=build_dailies, checkin_fn=do_checkin)
        gw.start()
        set_active_gateway(gw)
    if _ACTIVE_DISCOVERY is None:
        _ACTIVE_DISCOVERY = DiscoveryResponder()
        _ACTIVE_DISCOVERY.start()
    return {"ok": True, "enabled": True}


def stop_gateway() -> dict:
    """运行时关闭网关并撤销所有令牌。"""
    global _ACTIVE_DISCOVERY
    gw = get_active_gateway()
    if gw is not None:
        gw.stop()
        set_active_gateway(None)
    if _ACTIVE_DISCOVERY is not None:
        _ACTIVE_DISCOVERY.stop()
        _ACTIVE_DISCOVERY = None
    return {"ok": True, "enabled": False}


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

    def issue_pair_qr(self, ip: str) -> dict:
        """签发一次性配对码并生成二维码；地址藏进二维码，用户不用手填 IP。

        用自定义协议 hopon:// 而非 http:// — 否则手机系统相机/微信扫到 http
        链接会用浏览器打开（用户实测踩坑），自定义协议不会，且能唤起 App。
        """
        code = self.pm.issue_code()
        url = f"hopon://pair?ip={ip}&port={self.port}&code={code}"
        qr = ""
        try:
            import base64
            from io import BytesIO
            import qrcode
            q = qrcode.QRCode(border=2, box_size=8)
            q.add_data(url)
            q.make(fit=True)
            buf = BytesIO()
            q.make_image(fill_color="#111111", back_color="#ffffff").save(buf, format="PNG")
            qr = base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:  # noqa: BLE001
            logger.warning("配对二维码渲染失败", exc_info=True)
        return {"url": url, "code": code, "qr_base64": qr, "ip": ip}


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


DISCOVERY_MAGIC = b"HOPON_DISCOVER_V1"
DISCOVERY_PORT = 8767


class DiscoveryResponder:
    """局域网设备发现：收到约定帧回 IP+端口，不回任何凭证/数据。

    发现只负责让手机拿到 PC 的地址；信任完全靠 6 位配对码（见 PairingManager），
    所以即便校园网里有多台同名 PC、或有人伪造设备名，选错/猜错码也连不上。
    """
    def __init__(self, port: int = DISCOVERY_PORT, gateway_port: int = 8766) -> None:
        self.port = port
        self.gateway_port = gateway_port
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._sock is not None:
            return
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("0.0.0.0", self.port))
        outer = self

        def loop() -> None:
            while True:
                try:
                    data, addr = outer._sock.recvfrom(256)
                except OSError:
                    break
                if data != DISCOVERY_MAGIC:
                    continue
                ip = addr[0] if addr[0] not in ("127.0.0.1", "0.0.0.0") else lan_ip()
                body = json.dumps({"ip": ip, "port": outer.gateway_port}).encode("utf-8")
                try:
                    outer._sock.sendto(body, addr)
                except OSError:
                    break

        self._thread = threading.Thread(target=loop, daemon=True, name="hopon-discovery")
        self._thread.start()

    def stop(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._thread = None


def _accounts_status():
    # 延迟导入避免在纯模块测试时触发 login_ui 的重量级初始化。
    from login_ui import accounts_status
    return accounts_status()


def _live_snapshot():
    """只读 PC 已缓存的实时数据，绝不现场查询（风控安全）。"""
    from login_ui import _live_disk_read
    return _live_disk_read() or {"games": []}


def _dailies_snapshot():
    from login_ui import custom_dailies_status
    return custom_dailies_status()


def build_snapshot():
    """真实体力/日常进度 + 自定义提醒，全部来自 PC 缓存，白名单字段不含凭证。"""
    live = _live_snapshot()
    games = []
    for g in (live.get("games") or []):
        if not isinstance(g, dict):
            continue
        res = g.get("resource") or {}
        tasks = [t for t in (g.get("tasks") or []) if isinstance(t, dict)]
        done = sum(1 for t in tasks if t.get("done"))
        games.append({
            "id": str(g.get("game", "")), "name": str(g.get("game_name", "")),
            "stamina_name": str(res.get("name", "")),
            "current": int(res.get("current") or 0), "max": int(res.get("maximum") or 0),
            "recover": res.get("recover"),
            "daily_done": done, "daily_total": len(tasks),
            "daily_progress": str(tasks[0].get("progress", "")) if tasks else "",
            "finished": bool(g.get("finished")), "stale": bool(g.get("stale")),
        })
    raw = _dailies_snapshot()
    customs = [{"id": str(i.get("id", "")), "name": str(i.get("name", "")),
                "done": bool(i.get("done_today", i.get("done"))),
                "times": list(i.get("times") or [])}
               for i in (raw.get("items") or []) if isinstance(i, dict)]
    return {"ok": True, "version": "m1", "games": games, "custom_dailies": customs}


def build_dailies():
    from login_ui import custom_dailies_status
    raw = custom_dailies_status()
    items = [{"id": str(i.get("id", "")), "name": str(i.get("name", "")),
              "done": bool(i.get("done_today", i.get("done"))),
              "times": list(i.get("times") or [])}
             for i in (raw.get("items") or []) if isinstance(i, dict)]
    return {"ok": True, "items": items}


def do_checkin(item_id: str, date: str):
    from login_ui import custom_dailies_update
    payload = {"action": "toggle_done", "id": item_id}
    if date:
        payload["date"] = date  # 04:00 日界由后端按此 date 裁定，手机不算日期
    return custom_dailies_update(payload)

# -*- coding: utf-8 -*-
"""手机局域网配对网关测试（M1 Task 1-3）。unittest 风格，进 run_all_tests 自动发现。"""
import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from core.mobile_gateway import PairingManager, MobileGatewayServer


class TestPairingManager(unittest.TestCase):
    def test_code_single_pending_and_one_time(self):
        pm = PairingManager(ttl=60)
        c1 = pm.issue_code()
        c2 = pm.issue_code()
        self.assertNotEqual(c1, c2)
        self.assertIsNone(pm.redeem(c1), "旧码刷新后必须立即失效")
        tok = pm.redeem(c2)
        self.assertTrue(tok)
        self.assertIsNone(pm.redeem(c2), "一次性码用后即焚")

    def test_code_expiry(self):
        pm = PairingManager(ttl=0.01)
        code = pm.issue_code()
        time.sleep(0.03)
        self.assertIsNone(pm.redeem(code), "过期码必须拒绝")

    def test_token_verify_and_revoke(self):
        pm = PairingManager(ttl=60)
        tok = pm.redeem(pm.issue_code())
        self.assertTrue(pm.verify_token(tok))
        pm.revoke(tok)
        self.assertFalse(pm.verify_token(tok))

    def test_wrong_token_rejected(self):
        pm = PairingManager(ttl=60)
        pm.redeem(pm.issue_code())
        self.assertFalse(pm.verify_token("not-a-token"))

    def test_rate_limit(self):
        pm = PairingManager(ttl=60, max_failures=3, cooldown=60)
        ip = "10.0.0.5"
        for _ in range(3):
            pm.note_failure(ip)
        self.assertTrue(pm.is_rate_limited(ip))


class TestMobileGateway(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pm = PairingManager(ttl=60)
        cls.srv = MobileGatewayServer(
            cls.pm, port=0,
            snapshot_fn=lambda: {"ok": True, "version": "m1", "games": [],
                                 "cookie": "MUST-NOT-LEAK"},
            dailies_fn=lambda: {"ok": True, "items": []},
            checkin_fn=lambda item_id, date: {"ok": True, "message": "ok"})
        cls.port = cls.srv.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def _get(self, path, token=None):
        req = urllib.request.Request(self.base + path)
        if token:
            req.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, None

    def test_pair_then_access_snapshot(self):
        code = self.pm.issue_code()
        st, body = self._get("/pair?code=" + code)
        self.assertEqual(st, 200)
        tok = json.loads(body)["token"]
        st, snap = self._get("/api/mobile/snapshot", tok)
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(snap)["ok"])

    def test_no_token_rejected(self):
        st, _ = self._get("/api/mobile/snapshot")
        self.assertIn(st, (401, 403))

    def test_sensitive_endpoints_not_exposed(self):
        tok = self.pm.redeem(self.pm.issue_code())
        for p in ("/api/push", "/api/device", "/api/diag_export",
                  "/api/update/install", "/api/daemon/start"):
            st, _ = self._get(p, tok)
            self.assertEqual(st, 404, f"{p} 不该暴露到局域网")

    def test_snapshot_passthrough_from_whitelisted_fn(self):
        # 网关原样透传 snapshot_fn 的返回；脱敏责任在 build_snapshot()（Task 3 测）。
        # 这里只钉死"网关不加料、也不自己丢字段"，避免在传输层重复过滤。
        tok = self.pm.redeem(self.pm.issue_code())
        st, snap = self._get("/api/mobile/snapshot", tok)
        data = json.loads(snap)
        self.assertEqual(st, 200)
        self.assertEqual(data["version"], "m1")

    def test_checkin_post(self):
        tok = self.pm.redeem(self.pm.issue_code())
        body = json.dumps({"id": "d1", "date": "2026-11-25"}).encode()
        req = urllib.request.Request(self.base + "/api/mobile/checkin", data=body,
                                     headers={"Authorization": "Bearer " + tok,
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            self.assertEqual(r.status, 200)
            self.assertTrue(json.loads(r.read())["ok"])


if __name__ == "__main__":
    unittest.main()

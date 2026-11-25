# -*- coding: utf-8 -*-
"""手机局域网配对网关测试（M1 Task 1-3）。unittest 风格，进 run_all_tests 自动发现。"""
import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from core.mobile_gateway import PairingManager


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


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""手机局域网设备发现测试。unittest 风格，进 run_all_tests 自动发现。"""
import json
import socket
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # qa 用 python test.py 直跑，需手动加根

from core.mobile_gateway import DiscoveryResponder


class TestDiscovery(unittest.TestCase):
    def test_response_contains_only_ip_and_port(self):
        r = DiscoveryResponder(port=18767, gateway_port=8766)
        r.start()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(2)
            s.sendto(b"HOPON_DISCOVER_V1", ("127.0.0.1", 18767))
            data, _ = s.recvfrom(1024)
            obj = json.loads(data)
            self.assertEqual(set(obj.keys()), {"ip", "port"})
            self.assertEqual(obj["port"], 8766)
            self.assertIn("ip", obj)
        finally:
            r.stop()

    def test_unknown_frame_ignored(self):
        r = DiscoveryResponder(port=18768, gateway_port=8766)
        r.start()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.3)
            s.sendto(b"GARBAGE", ("127.0.0.1", 18768))
            with self.assertRaises(socket.timeout):
                s.recvfrom(1024)
        finally:
            r.stop()


if __name__ == "__main__":
    # qa/run.py 的 L1 判据要 "N PASS / M FAIL" 结论行（unittest 默认只输出 OK，
    # 会被判"无结论行"）——按项目惯例自己统计并打印。
    import io as _io
    suite = unittest.defaultTestLoader.loadTestsFromModule(__import__("__main__"))
    result = unittest.TextTestRunner(stream=_io.StringIO(), verbosity=0).run(suite)
    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    print(f"mobile_discovery: {total - failed} PASS / {failed} FAIL")
    raise SystemExit(1 if failed else 0)

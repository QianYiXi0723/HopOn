"""检查更新模块的回归测试（2026-11-05）。

只测纯逻辑（版本比较、缓存判定、降级行为），**不联网**——测试不该
依赖 GitHub 可达。
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0
FAILED = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS " + name)
    else:
        FAIL += 1
        FAILED.append(name + (" | " + str(detail) if detail else ""))
        print("  FAIL " + name + (" | " + str(detail) if detail else ""))


from core import update as U  # noqa: E402

print("=== A. 版本号解析 ===")

check("A1 2.4.10 -> (2,4,10)", U._parse("2.4.10") == (2, 4, 10), U._parse("2.4.10"))
check("A2 v2.5.0 去前缀", U._parse("v2.5.0") == (2, 5, 0), U._parse("v2.5.0"))
check("A3 2.4 -> (2,4)", U._parse("2.4") == (2, 4), U._parse("2.4"))
check("A4 空串不炸", U._parse("") == (0,), U._parse(""))
check("A5 非数字不炸", U._parse("abc") == (0,), U._parse("abc"))

print("=== B. 语义化比较（不能按字典序）===")

cases = [
    ("2.4.10", "2.4.9", True, "两位数进位"),
    ("2.4.9", "2.4.10", False, "反向"),
    ("2.5.0", "2.4.0", True, "主版本提升"),
    ("2.4.0", "2.4.0", False, "相同"),
    ("v2.5.0", "2.4.0", True, "带 v 前缀"),
    ("2.4", "2.4.0", False, "2.4 vs 2.4.0"),
    ("3.0.0", "2.99.99", True, "跨主版本"),
]
for remote, local, expect, desc in cases:
    got = U.is_newer(remote, local)
    check("B %s（%s vs %s）" % (desc, remote, local), got is expect, got)

# 专门钉住字典序陷阱
check("B8 字典序会误判：2.4.10 应大于 2.4.9（按字典序 2.4.10 < 2.4.9）",
      U.is_newer("2.4.10", "2.4.9") is True)

print("=== C. 缓存新鲜度 ===")

now = time.time()
check("C1 刚写入 -> 新鲜", U._cache_fresh({"checked_ts": now}) is True)
check("C2 5 小时前 -> 新鲜", U._cache_fresh({"checked_ts": now - 5 * 3600}) is True)
check("C3 7 小时前 -> 过期", U._cache_fresh({"checked_ts": now - 7 * 3600}) is False)
check("C4 无时间戳 -> 过期", U._cache_fresh({}) is False)
check("C5 时间戳非法 -> 过期", U._cache_fresh({"checked_ts": "abc"}) is False)

print("=== D. 缓存转换 ===")

cur = U.current_version()
info = U._info_from_cache(
    {
        "latest": "9.9.9",
        "url": "https://example.com",
        "notes": "notes",
        "assets": [{"name": "a.zip", "size": 1, "url": "u"}],
        "published_at": "2026-01-01",
        "checked_at": "2026-01-01 00:00:00",
        "message": "msg",
    },
    cur,
)
check("D1 ok=True", info.ok is True)
check("D2 current 用本地版本", info.current == cur, info.current)
check("D3 识别出有新版", info.has_update is True)
check("D4 url 透传", info.url == "https://example.com")
check("D5 assets 透传", len(info.assets) == 1)

print("=== E. 降级：网络失败不抛异常 ===")

import urllib.request  # noqa: E402

_orig = urllib.request.urlopen


def _boom(*a, **kw):
    raise OSError("network down")


urllib.request.urlopen = _boom
try:
    got = U.check(force=True)
    check("E1 网络失败不抛异常", isinstance(got, U.UpdateInfo))
    check("E2 ok=False", got.ok is False)
    check("E3 有给用户的提示", bool(got.message), got.message)
    check("E4 不把技术错误塞进 message", "network down" not in (got.message or ""),
          got.message)
    check("E5 current 仍填本地版本", got.current == cur, got.current)
finally:
    urllib.request.urlopen = _orig

print("=== F. 结果结构 ===")

d = U.UpdateInfo(current="2.4.0", latest="2.5.0", has_update=True).to_dict()
for key in ("ok", "current", "latest", "has_update", "url", "notes",
            "assets", "published_at", "checked_at", "message"):
    check("F 含字段 %s" % key, key in d, list(d.keys()))
check("F 不含 error（内部字段不进 API）", "error" not in d, list(d.keys()))

print()
print("=" * 50)
print("PASS %d / FAIL %d" % (PASS, FAIL))
if FAILED:
    print("失败项：")
    for f in FAILED:
        print("  - " + f)
sys.exit(1 if FAIL else 0)

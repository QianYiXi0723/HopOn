# -*- coding: utf-8 -*-
"""隐私审计：扫描已提交进 git 的内容，找出凭据 / 身份信息 / 设备标识 / 路径残留。

只扫 HEAD 树（= 真正上传到 GitHub 的东西），不扫工作区。
"""
import re
import subprocess
from collections import defaultdict

# ---------- 已知安全的白名单：公开协议常量、示例地址、本机回环 ----------
SAFE_IPS = {
    "127.0.0.1", "0.0.0.0", "255.255.255.255", "1.1.1.1", "8.8.8.8",
    "192.168.0.1", "10.0.0.1", "172.16.0.1",
}
# 公开的接口域名 / 版本号形式，避免误报
SAFE_HOST_SUFFIX = ("github.com", "example.com", "localhost")

PATTERNS = [
    ("邮箱", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("IPv4", re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")),
    ("Windows绝对路径", re.compile(r"[A-Za-z]:\\[^\s\"'<>|)]+")),
    ("Unix家目录", re.compile(r"/(?:home|Users)/[A-Za-z0-9._-]+")),
    ("16位以上hex", re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{16,}(?![0-9A-Za-z])")),
    ("疑似token参数", re.compile(r"(?:token|key|secret|password|cookie)\s*[=:]\s*[\"']?[A-Za-z0-9_\-]{12,}", re.I)),
]

# 需要人工确认的高危关键词（中文昵称 / 本机用户名等）
KEYWORDS = ["ASUS", "月渡寒川", "QianYiXi", "QIANYIXI", "17723063112"]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True).stdout.decode("utf-8", errors="ignore")


def main() -> None:
    files = [f for f in git("ls-tree", "-r", "HEAD", "--name-only").splitlines() if f]
    print(f"扫描对象：HEAD 树，共 {len(files)} 个文件\n")

    findings = defaultdict(list)
    keyword_hits = defaultdict(list)

    for path in files:
        text = git("show", f"HEAD:{path}")
        for lineno, line in enumerate(text.splitlines(), 1):
            for label, pat in PATTERNS:
                for m in pat.finditer(line):
                    value = m.group(0)
                    if label == "IPv4" and value in SAFE_IPS:
                        continue
                    if label == "邮箱" and value.endswith(SAFE_HOST_SUFFIX):
                        continue
                    # 过滤明显是版本号 / 尺寸的假 IP
                    if label == "IPv4":
                        parts = [int(x) for x in value.split(".")]
                        if any(p > 255 for p in parts):
                            continue
                    findings[label].append((path, lineno, value))
            for kw in KEYWORDS:
                if kw in line:
                    keyword_hits[kw].append((path, lineno, line.strip()[:110]))

    # ---------- 1) 模式命中 ----------
    print("=" * 66)
    print("一、模式扫描")
    print("=" * 66)
    if not findings:
        print("  (无命中)")
    for label in [p[0] for p in PATTERNS]:
        rows = findings.get(label) or []
        if not rows:
            continue
        print(f"\n[{label}]  共 {len(rows)} 处")
        seen = set()
        shown = 0
        for path, lineno, value in rows:
            key = (path, value)
            if key in seen:
                continue
            seen.add(key)
            if shown >= 8:
                print(f"    … 其余 {len(rows) - shown} 处略")
                break
            # 长串打码中间部分
            shown_val = value if len(value) <= 28 else value[:12] + "…" + value[-4:]
            print(f"    {path}:{lineno}  {shown_val}")
            shown += 1

    # ---------- 2) 关键词 ----------
    print()
    print("=" * 66)
    print("二、关键词命中（本机用户名 / 昵称 / 手机号）")
    print("=" * 66)
    if not keyword_hits:
        print("  (无命中)")
    for kw, rows in keyword_hits.items():
        print(f"\n[{kw}]  共 {len(rows)} 处")
        for path, lineno, snippet in rows[:6]:
            print(f"    {path}:{lineno}  {snippet}")
        if len(rows) > 6:
            print(f"    … 其余 {len(rows) - 6} 处略")

    # ---------- 3) 提交者身份 ----------
    print()
    print("=" * 66)
    print("三、提交者身份（GitHub 上会公开显示）")
    print("=" * 66)
    log = git("log", "--format=%an|%ae|%cn|%ce").strip().splitlines()
    for i, row in enumerate(log, 1):
        author, aemail, committer, cemail = (row.split("|") + ["", "", ""])[:4]
        print(f"  #{i}  作者={author}  <{aemail}>")
        if committer != author or cemail != aemail:
            print(f"       提交者={committer}  <{cemail}>")
        if re.search(r"(?<!\d)1[3-9]\d{9}(?!\d)", aemail) or re.search(r"(?<!\d)1[3-9]\d{9}(?!\d)", cemail):
            print("       ⚠ 邮箱中含手机号")


if __name__ == "__main__":
    main()

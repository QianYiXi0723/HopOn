# -*- coding: utf-8 -*-
"""创建 GitHub Release 并上传发行资产（版本化参数版，取代按版本硬编码的
gh_release_v2/v21——那些留着当历史记录，不再执行）。

用法：python tools/rl_release/gh_release.py <notes.md 路径>

- TAG / 版本号：core/__init__.py 单源派生
- 资产：dist/HopOn_v{V}_Windows_x64.zip + installer/Output/HopOn_v{V}_Setup.exe
  + dist/SHA256SUMS.txt（存在才传）
- **红线守卫**：目标 tag 已存在 → 立即中止（严禁重建 tag / 重传资产，
  见 exe-csharp-shell-plan 发版红线）
- notes：argv 传入的 markdown 文件全文作为 Release 正文
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
OWNER, REPO = "QianYiXi0723", "HopOn"
API = "https://api.github.com"
UA = "HopOn-release"
UPLOAD_RETRY = 3


def token() -> str:
    out = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("[FATAL] 取不到 token")


TOK = token()
HDR = {
    "Authorization": "token " + TOK,
    "Accept": "application/vnd.github+json",
    "User-Agent": UA,
    "X-GitHub-Api-Version": "2022-11-28",
}


def version() -> str:
    text = (ROOT / "core" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M).group(1)


def api(method: str, url: str, payload=None, raw=None, content_type=None):
    data = raw if raw is not None else (
        json.dumps(payload).encode("utf-8") if payload is not None else None)
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in HDR.items():
        req.add_header(k, v)
    if content_type:
        req.add_header("Content-Type", content_type)
    elif data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (json.loads(body) if body.strip() else {})
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("用法：python gh_release.py <notes.md>")
    notes_path = Path(sys.argv[1])
    if not notes_path.exists():
        raise SystemExit(f"[FATAL] notes 文件不存在：{notes_path}")
    notes = notes_path.read_text(encoding="utf-8")

    ver = version()
    tag = f"v{ver}"
    print(f"目标：{tag}（main HEAD）")

    # 红线守卫：tag 已存在 = 这个版本发过，绝不重建/重传
    status, existing = api("GET", f"{API}/repos/{OWNER}/{REPO}/releases/tags/{tag}")
    if status == 200:
        raise SystemExit(
            f"[FATAL] Release {tag} 已存在（资产 {[a['name'] for a in existing.get('assets') or []]}）。"
            "发版红线：严禁重建 tag / 重传资产——要发新内容请先升版本号。")

    assets = [
        ROOT / "dist" / f"HopOn_v{ver}_Windows_x64.zip",
        ROOT / "installer" / "Output" / f"HopOn_v{ver}_Setup.exe",
        ROOT / "dist" / "SHA256SUMS.txt",
    ]
    missing = [a for a in assets if not a.exists()]
    if missing:
        raise SystemExit(f"[FATAL] 资产缺失（先跑 make_release.py）：{missing}")

    status, rel = api("POST", f"{API}/repos/{OWNER}/{REPO}/releases", {
        "tag_name": tag,
        "target_commitish": "main",
        "name": f"HopOn v{ver}",
        "body": notes,
        "draft": False,
        "prerelease": False,
    })
    if status != 201:
        raise SystemExit(f"[FATAL] 创建 Release 失败（{status}）：{rel}")
    release_id = rel["id"]
    upload_url = rel["upload_url"].split("{")[0]
    print(f"Release {tag} 已创建（id={release_id}），开始传 {len(assets)} 个资产…")

    for asset in assets:
        size = asset.stat().st_size
        for attempt in range(1, UPLOAD_RETRY + 1):
            print(f"  上传 {asset.name}（{size/1048576:.1f} MB）第 {attempt} 次…")
            try:
                with open(asset, "rb") as fh:
                    st, body = api(
                        "POST",
                        f"{upload_url}?name={asset.name}",
                        raw=fh.read(),
                        content_type="application/octet-stream",
                    )
                if st == 201:
                    print(f"  ✓ {asset.name}")
                    break
                print(f"  ! HTTP {st}：{str(body)[:160]}")
            except Exception as exc:  # noqa: BLE001 - 上传网络抖动重试
                print(f"  ! 异常：{exc}")
            if attempt == UPLOAD_RETRY:
                raise SystemExit(f"[FATAL] {asset.name} 连续 {UPLOAD_RETRY} 次上传失败")
            time.sleep(3 * attempt)

    status, latest = api("GET", f"{API}/repos/{OWNER}/{REPO}/releases/tags/{tag}")
    names = [a["name"] for a in latest.get("assets") or []]
    print(f"完成：{latest.get('html_url')}")
    print(f"资产：{names}")
    if sorted(names) != sorted(a.name for a in assets):
        raise SystemExit("[FATAL] 资产清单与预期不一致，请人工核对")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gitee Release 补发工具（2026-11-13）。

背景：make_release 第 7 步「失败不阻断」静默吞错，v2.5.1/v2.6.0 的 Gitee
Release 空窗（tag 已补推）。本脚本按版本补建 Release+资产。

用法：
    python tools/rl_release/gitee_backfill.py v2.5.1 v2.6.0

约定：
* 资产三件套 = dist/HopOn_v{v}_Setup.exe + dist/HopOn_v{v}_Windows_x64.zip
  + dist/SHA256SUMS_v{v}.txt（后者若本地缺失则从 GitHub Release GET——
  对 GitHub 只许 GET 是发版红线，下载自家资产合法）。
* Release 已存在则跳过（幂等，可重复跑）。
* target_commitish 显式传该版本 tag 的 commit，防止 Gitee 把 Release
  锚到 main 头部（backfill 时 main 已经走到更新的版本）。
* notes 取 tools/rl_release/notes/v{v}.md，缺失回落一行版本号。
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GITEE_REPO = "qianyixi/HopOn"
GITHUB_REPO = "QianYiXi0723/HopOn"
TOKEN_FILE = Path(__file__).resolve().parent / ".gitee_token"


_UA = {"User-Agent": "HopOn-backfill/1.0"}


def _api(url: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload else None
    headers = {"Content-Type": "application/json", **_UA}
    req = urllib.request.Request(url, data=data, method="POST" if payload else "GET",
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def _upload_retry(gtok: str, rel_id: str, path: Path, tries: int = 3) -> None:
    """大文件 multipart 上传对断连敏感（实测 15MB 会 RemoteDisconnected），
    指数退避重试；每次重传都是完整文件，Gitee attach 幂等替换同名资产。"""
    import time as _t

    last: Exception | None = None
    for i in range(tries):
        try:
            _upload(gtok, rel_id, path)
            return
        except Exception as exc:  # noqa: BLE001 - 断连重试
            last = exc
            _t.sleep(2 * (i + 1))
    raise RuntimeError(f"上传 {path.name} 连续 {tries} 次失败：{last}")


def _tag_commit(version: str) -> str:
    """本地 tag 解出 commit（tag 必须已存在于本地，缺了先 git fetch origin --tags）。"""
    out = __import__("subprocess").run(
        ["git", "rev-parse", f"{version}^{{commit}}"],
        cwd=ROOT, capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _release_exists(gtok: str, tag: str) -> bool:
    rels = _api(f"https://gitee.com/api/v5/repos/{GITEE_REPO}/releases?access_token={gtok}")
    return any(str(r.get("tag_name")) == tag for r in rels if isinstance(r, dict))


def _upload(gtok: str, rel_id: str, path: Path) -> None:
    boundary = "----HopOnBackfillB7d1a2c"
    content = path.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="access_token"\r\n\r\n{gtok}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"https://gitee.com/api/v5/repos/{GITEE_REPO}/releases/{rel_id}/attach_files",
        data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", **_UA})
    with urllib.request.urlopen(req, timeout=300) as r:
        r.read()


def _sums_for(version: str) -> Path:
    """该版本的 SHA256SUMS：本地有就用，没有从 GitHub GET（红线内）。"""
    out = ROOT / "dist" / f"SHA256SUMS_{version}.txt"
    if out.exists():
        return out
    url = (f"https://github.com/{GITHUB_REPO}/releases/download/"
           f"{version}/SHA256SUMS.txt")
    with urllib.request.urlopen(url, timeout=60) as r:
        out.write_bytes(r.read())
    return out


def _verify(gtok: str, tag: str, expected: dict) -> None:
    """上传后验证（goreleaser Verify 语义）：线上资产名清单对 + 逐个下载
    sha256 对本地，不等即 FATAL——SUMS 缺失/错位类事故（v2.6.10.1/.2、
    v2.6.0/v2.6.1 两波实证）从此在发布器内被拦，不再靠人肉记得补。"""
    import hashlib
    import time as _t

    rels = _api(f"https://gitee.com/api/v5/repos/{GITEE_REPO}/releases?access_token={gtok}")
    hit = next(r for r in rels if str(r.get("tag_name")) == tag)
    have = {a.get("name") or "": a for a in (hit.get("assets") or [])}
    missing = [n for n in expected if n not in have]
    if missing:
        raise SystemExit(f"[FATAL] 线上缺资产：{missing}（上传断连或漏传，修完重跑——幂等可续）")
    for name, local in expected.items():
        url = (have[name].get("browser_download_url") or "")
        if not url:
            raise SystemExit(f"[FATAL] {name} 线上无下载地址")
        raw: bytes | None = None
        last: Exception | None = None
        for i in range(3):
            try:
                req = urllib.request.Request(
                    url + ("&" if "?" in url else "?") + f"access_token={gtok}",
                    headers=_UA)
                raw = urllib.request.urlopen(req, timeout=300).read()
                break
            except Exception as exc:  # noqa: BLE001 - 断连重试
                last = exc
                _t.sleep(2 * (i + 1))
        if raw is None:
            raise SystemExit(f"[FATAL] {name} 线上下载失败：{last}")
        if hashlib.sha256(raw).hexdigest() != hashlib.sha256(local.read_bytes()).hexdigest():
            raise SystemExit(f"[FATAL] {name} 线上内容与本地不一致（上传损坏/错位）")
        print(f"    ✓ 验证 {name} ({len(raw)} B sha256 一致)")


def backfill(version: str) -> bool:
    tag = version if version.startswith("v") else f"v{version}"
    v = tag.lstrip("v")
    gtok = TOKEN_FILE.read_text(encoding="utf-8").strip()
    assets = [ROOT / "dist" / f"HopOn_{tag}_Setup.exe",
              ROOT / "dist" / f"HopOn_{tag}_Windows_x64.zip",
              _sums_for(tag)]
    rel_id: str
    have: set[str] = set()
    if _release_exists(gtok, tag):
        # 修补模式（2026-11-13 实测：Release 建出来了但大文件上传断连，
        # 二跑必须只补缺资产而不是整体跳过）
        rels = _api(f"https://gitee.com/api/v5/repos/{GITEE_REPO}/releases?access_token={gtok}")
        hit = next(r for r in rels if str(r.get("tag_name")) == tag)
        rel_id = str(hit["id"])
        have = {a.get("name") or "" for a in (hit.get("assets") or [])}
        print(f"[REPAIR] {tag} Release 已存在（id={rel_id}），缺 {len(assets) - len(have & {a.name for a in assets})} 个资产")
    else:
        notes = Path(__file__).resolve().parent / "notes" / f"{tag}.md"
        rel = _api(
            f"https://gitee.com/api/v5/repos/{GITEE_REPO}/releases",
            {"access_token": gtok, "tag_name": tag, "name": f"HopOn {tag}",
             "body": notes.read_text(encoding="utf-8") if notes.exists() else f"HopOn {tag}",
             "prerelease": False, "target_commitish": _tag_commit(tag)})
        rel_id = str(rel["id"])
        print(f"    Release 已建（id={rel_id}，target={_tag_commit(tag)[:8]}）")
    for asset in assets:
        if not asset.exists():
            print(f"    [MISS] {asset.name} 不存在，跳过")
            continue
        if asset.name in have:
            print(f"    = {asset.name} 已在，跳过")
            continue
        _upload_retry(gtok, rel_id, asset)
        print(f"    ✓ {asset.name}")
    # 无版本化名第二份（2026-11-20）：更新器按固定名 SHA256SUMS.txt 拉取，
    # Gitee 历史上只传版本化名 → Gitee 渠道 SUMS 永远 404，一直靠 GitHub 兜底活着。
    import tempfile
    sums_plain = Path(tempfile.mkdtemp(prefix="rl_sums_")) / "SHA256SUMS.txt"
    sums_plain.write_bytes(_sums_for(tag).read_bytes())
    if "SHA256SUMS.txt" not in have:
        _upload_retry(gtok, rel_id, sums_plain)
        print("    ✓ SHA256SUMS.txt（无版本化名第二份）")
    # 上传后验证（goreleaser Verify 语义）：名清单 + 逐个下载 sha256 对本地，
    # 不等即 FATAL——SUMS 缺失/错位类事故从此在发布器内被拦。
    expected = {a.name: a for a in assets if a.exists()}
    expected["SHA256SUMS.txt"] = sums_plain
    _verify(gtok, tag, expected)
    return True


def main(argv: list[str]) -> int:
    versions = [a for a in argv if a.startswith("v")]
    if not versions:
        print(__doc__)
        return 1
    if not TOKEN_FILE.exists():
        print("[ERR] 无 .gitee_token —— 放私人令牌后再跑")
        return 1
    for v in versions:
        print(f"=== 补发 {v} ===")
        backfill(v)
    print("完成。核验：https://gitee.com/qianyixi/HopOn/releases")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

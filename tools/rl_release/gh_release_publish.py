# -*- coding: utf-8 -*-
"""创建/复用 GitHub Release 并上传发行资产（参数化版本，可反复使用）。

用法：
    python tools/rl_release/gh_release_publish.py 2.4.6
    python tools/rl_release/gh_release_publish.py 2.4.6 --notes-file path/to/notes.md
    python tools/rl_release/gh_release_publish.py 2.4.6 --dry-run

相比旧的 gh_release_v21.py（写死 TAG/ZIP/BODY 的一次性脚本）：
  - 版本号由命令行传入，资产按 dist/ 与 installer/Output/ 自动发现
  - zip + Setup.exe 一起传（旧脚本只传 zip）
  - 已存在的同名资产默认跳过（GitHub 不允许同名重传，需要 --replace 先删）
  - 发版红线：只新增 tag，绝不删 tag / 重建 release

token 走 `git credential fill` 动态取，不落盘（与既有脚本同口径）。
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OWNER, REPO = "QianYiXi0723", "HopOn"
API = "https://api.github.com"
UA = "HopOn-release"
ROOT = Path(__file__).resolve().parents[2]


def _die(msg):
    raise SystemExit("[FATAL] " + msg)


def get_token():
    """从 git credential helper 取 token，不写盘。"""
    try:
        out = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, timeout=30,
            cwd=str(ROOT),
        )
    except Exception as exc:
        _die("调用 git credential fill 失败：%s" % exc)
    for line in (out.stdout or "").splitlines():
        if line.startswith("password="):
            tok = line.split("=", 1)[1].strip()
            if tok:
                return tok
    _die("git credential fill 未返回 password（先确认已登录 GitHub）")


def humanize(version):
    parts = version.split(".")
    if len(parts) >= 3 and parts[2] != "0":
        return "v%s 热修" % version
    return "v%s" % version


def default_body(version):
    return (
        "## HopOn v%s\n\n"
        "### 本次更新\n\n"
        "（在此填写本版更新内容）\n\n"
        "### 下载\n\n"
        "- **安装版** `HopOn_v%s_Setup.exe`：双击安装，装到用户目录，卸载不删配置与数据。\n"
        "- **绿色版** `HopOn_v%s_Windows_x64.zip`：解压即用，不需要装 Python。\n"
        % (version, version, version)
    )


def make_api(hdr):
    def api(method, url, payload=None, raw=None, ctype=None, timeout=60, tries=5):
        last = None
        for attempt in range(1, tries + 1):
            body = None
            h = dict(hdr)
            if payload is not None:
                body = json.dumps(payload).encode("utf-8")
                h["Content-Type"] = "application/json"
            elif raw is not None:
                body = raw
                h["Content-Type"] = ctype or "application/octet-stream"
            req = urllib.request.Request(url, data=body, headers=h, method=method)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return r.status, json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as exc:
                text = exc.read().decode("utf-8", "replace")[:300]
                print("  HTTP %s: %s" % (exc.code, text))
                last = exc
                if exc.code in (401, 403, 404, 422):
                    raise
            except Exception as exc:
                print("  第 %d 次失败: %s" % (attempt, type(exc).__name__))
                last = exc
            if attempt < tries:
                time.sleep(8)
        _die("多次重试仍失败: %s (%s)" % (url, last))
    return api


def collect_assets(version):
    """按版本号收集 zip 与 Setup.exe；缺哪个报哪个。"""
    want = [
        (ROOT / "dist" / ("HopOn_v%s_Windows_x64.zip" % version), "application/zip"),
        (ROOT / "installer" / "Output" / ("HopOn_v%s_Setup.exe" % version),
         "application/vnd.microsoft.portable-executable"),
    ]
    found = []
    for path, ctype in want:
        if not path.is_file():
            _die("资产缺失：%s（先跑 make_release.py）" % path)
        found.append((path, ctype))
    return found


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if not args:
        _die("用法：python tools/rl_release/gh_release_publish.py <version> [--dry-run] [--replace]")

    version = args[0].lstrip("v")
    tag = "v" + version
    dry = "--dry-run" in flags
    replace = "--replace" in flags

    notes = None
    for i, a in enumerate(sys.argv[1:]):
        if a == "--notes-file" and i + 2 < len(sys.argv):
            notes = Path(sys.argv[i + 2])

    assets = collect_assets(version)
    body = notes.read_text(encoding="utf-8") if notes and notes.is_file() else default_body(version)
    if notes and not notes.is_file():
        print("[WARN] notes 文件不存在，用默认正文：%s" % notes)

    print("=== 目标 ===")
    print("  版本 : %s" % version)
    print("  tag  : %s" % tag)
    print("  仓库 : %s/%s" % (OWNER, REPO))
    for p, _ in assets:
        print("  资产 : %s (%.2f MB)" % (p.name, p.stat().st_size / 1024 / 1024))
    if dry:
        print("\n[dry-run] 不实际调用 API。")
        return

    api = make_api({
        "Authorization": "token " + get_token(),
        "Accept": "application/vnd.github+json",
        "User-Agent": UA,
        "X-GitHub-Api-Version": "2022-11-28",
    })

    print("\n=== 1. 查/建 release ===")
    try:
        status, rel = api("GET", "%s/repos/%s/%s/releases/tags/%s" % (API, OWNER, REPO, tag), tries=1)
        print("  已存在 release id=%s（复用，不重建）" % rel["id"])
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        print("  不存在，创建新的")
        status, rel = api("POST", "%s/repos/%s/%s/releases" % (API, OWNER, REPO), {
            "tag_name": tag,
            "name": "HopOn " + humanize(version),
            "body": body,
            "draft": False,
            "prerelease": False,
        })
        print("  创建成功 id=%s" % rel["id"])

    rel_id = rel["id"]
    print("  html_url:", rel.get("html_url"))

    existing = {a["name"]: a for a in (rel.get("assets") or [])}
    print("\n=== 2. 上传资产 ===")
    for path, ctype in assets:
        if path.name in existing:
            if not replace:
                print("  [跳过] %s 已存在（要覆盖请加 --replace）" % path.name)
                continue
            print("  [替换] 先删旧资产 %s" % path.name)
            api("DELETE", "%s/repos/%s/%s/releases/assets/%s" % (
                API, OWNER, REPO, existing[path.name]["id"]), tries=3)
        print("  上传 %s (%.2f MB)..." % (path.name, path.stat().st_size / 1024 / 1024))
        # 红线：资产上传必须走 uploads.github.com，用 api.github.com 会 404。
        # release 对象自带的 upload_url 是模板
        # （https://uploads.github.com/.../assets{?name,label}），优先用它。
        raw_tpl = (rel.get("upload_url") or "").strip()
        if raw_tpl:
            base = raw_tpl.split("{", 1)[0]
        else:
            base = "%s/repos/%s/%s/releases/%s/assets" % (API, OWNER, REPO, rel_id)
        url = "%s?name=%s" % (base, urllib.parse.quote(path.name))
        status, asset = api("POST", url, raw=path.read_bytes(), ctype=ctype, timeout=900, tries=6)
        print("  ✅ status=%s state=%s size=%s" % (status, asset.get("state"), asset.get("size")))
        print("     " + str(asset.get("browser_download_url")))

    print("\n=== 3. 复核 ===")
    status, rel2 = api("GET", "%s/repos/%s/%s/releases/%s" % (API, OWNER, REPO, rel_id), tries=3)
    got = rel2.get("assets") or []
    print("  draft=%s prerelease=%s tag=%s assets=%d" % (
        rel2["draft"], rel2["prerelease"], rel2["tag_name"], len(got)))
    for a in got:
        print("    - %s  %d bytes  %s" % (a["name"], a["size"], a["state"]))
    print("\n完成：%s" % rel2.get("html_url"))


if __name__ == "__main__":
    main()

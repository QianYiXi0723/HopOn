# -*- coding: utf-8 -*-
"""创建 GitHub Release v2.1 并上传发行包（api.github.com 可达，uploads 走重试）。"""
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

OWNER, REPO = "QianYiXi0723", "HopOn"
TAG = "v2.1"
ZIP = Path(r"E:\Apps\HopOn\dist\HopOn_v2.1_Windows_x64.zip")
API = "https://api.github.com"
UA = "HopOn-release"

BODY = """## HopOn v2.1

免安装绿色版：解压即用，不需要装 Python 或任何运行库。整个文件夹拷到任意 Windows 10/11 电脑都能直接跑。

### 本次更新

- **开机自启自动巡检**：过去开机自启后守护进程不会自动拉起，定时提醒会静默断线；现在桌面壳会在引擎就绪后自动拉起，失败时用托盘气泡提示。
- **凭证失效告警**：某个账号连续两次鉴权失败时单独推一条提醒（同一账号 24 小时内只提醒一次），不再混在日常「查询失败」里被忽略。
- **推送回压**：某个通道连续 5 次发送失败后自动静默 6 小时，避免手机被失败消息刷屏；恢复后自动解除。
- **托盘「立即巡检」**：托盘右键可直接触发一次检查，不必等定时点。
- **查询代理**：账号绑定页可为米游社查询单独指定出口代理（留空即直连），用于换网络环境排查风控。
- **明日方舟数据陈旧提示**：数据未更新时给出「上次在线时间」，不再误报为未完成。
- **控制台细节**：最近推送改为等高的可展开格子；活动日历游戏名与图标垂直居中；按 F5 刷新后整页闪一下作为反馈。

### 下载

`HopOn_v2.1_Windows_x64.zip`（约 24.5 MB），解压后双击 `HopOn.exe`。

### 说明

- 首次打开是演示模式（不显示任何编造数据），按页面引导扫码登录后才会展示真实信息。
- 只读查询：不签到、不领奖励、不修改任何游戏数据。
- 没有购买代码签名证书，部分杀毒软件会误报；添加信任即可，源码全部公开可自查。
"""


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


def api(method, url, payload=None, raw=None, ctype=None, timeout=60, tries=5):
    for attempt in range(1, tries + 1):
        body = None
        hdrs = dict(HDR)
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        elif raw is not None:
            body = raw
            hdrs["Content-Type"] = ctype or "application/octet-stream"
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")[:300]
            print("  HTTP %s: %s" % (exc.code, text))
            if exc.code in (401, 403, 404, 422):
                raise
        except Exception as exc:
            print("  第 %d 次失败: %s" % (attempt, type(exc).__name__))
        time.sleep(8)
    raise SystemExit("[FATAL] 多次重试仍失败: %s" % url)


print("=== 1. 查是否已有 release ===")
try:
    status, rel = api("GET", "%s/repos/%s/%s/releases/tags/%s" % (API, OWNER, REPO, TAG), tries=1)
    print("  已存在 release id=%s（复用）" % rel["id"])
except urllib.error.HTTPError as exc:
    if exc.code != 404:
        raise
    print("  不存在，创建新的")
    status, rel = api("POST", "%s/repos/%s/%s/releases" % (API, OWNER, REPO), {
        "tag_name": TAG,
        "name": "HopOn v2.1",
        "body": BODY,
        "draft": False,
        "prerelease": False,
    })
    print("  创建成功 id=%s draft=%s" % (rel["id"], rel["draft"]))

rel_id = rel["id"]
print("  html_url:", rel.get("html_url"))
print("  draft=%s prerelease=%s" % (rel.get("draft"), rel.get("prerelease")))

print("\n=== 2. 上传资产 ===")
print("  zip: %.2f MB" % (ZIP.stat().st_size / 1024 / 1024))
data = ZIP.read_bytes()
upload_url = "%s/repos/%s/%s/releases/%s/assets?name=%s" % (
    API, OWNER, REPO, rel_id, ZIP.name)
status, asset = api("POST", upload_url, raw=data,
                    ctype="application/zip", timeout=600, tries=6)
print("  上传成功 status=%s id=%s" % (status, asset.get("id")))
print("  size=%s state=%s" % (asset.get("size"), asset.get("state")))
print("  browser_download_url:", asset.get("browser_download_url"))

print("\n=== 3. 复核 ===")
status, rel2 = api("GET", "%s/repos/%s/%s/releases/%s" % (API, OWNER, REPO, rel_id), tries=3)
assets = rel2.get("assets") or []
print("  release: draft=%s tag=%s assets=%d" % (rel2["draft"], rel2["tag_name"], len(assets)))
for a in assets:
    print("    - %s  %d bytes  %s" % (a["name"], a["size"], a["state"]))
ok = (not rel2["draft"]) and rel2["tag_name"] == TAG and any(
    a["name"] == ZIP.name and a["size"] == ZIP.stat().st_size for a in assets)
print("\n=== 结论：" + ("通过" if ok else "不通过") + " ===")
raise SystemExit(0 if ok else 1)

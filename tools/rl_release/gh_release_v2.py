# -*- coding: utf-8 -*-
"""用 GitHub REST API 为 HopOn 创建 v2.0.0 tag + Release 并上传 zip。

token 从环境变量 GH_TOK 读取（不落盘、不打印）。
"""
import os
import sys
from pathlib import Path

import requests

OWNER, REPO = "QianYiXi0723", "HopOn"
API = f"https://api.github.com/repos/{OWNER}/{REPO}"
UPLOADS = f"https://uploads.github.com/repos/{OWNER}/{REPO}"
TAG = "v2.0.0"
ZIP = Path(r"E:\Apps\HopOn\dist\HopOn_v2.0.0_Windows_x64.zip")

TAG_MSG = """HopOn v2.0.0

首个 exe 发行版：C# WinForms + WebView2 外壳，内嵌便携 Python 引擎，解压即用。
- 五款游戏日常巡查（原神 / 星铁 / 绝区零 / 明日方舟 / 终末地）
- 多账号隔离：每款游戏独立凭证，不做任何同号预设
- 设备伪装：真机指纹档案，降低扫码风控
- 演示模式引导：未绑定账号时绝不展示假数据
- 免装 Python、无运行库依赖，托盘常驻 + 开机自启"""

BODY = """每天定时查一次游戏日常，没做完就推到手机。免安装绿色版：解压双击 `HopOn.exe` 即可，不用装 Python、不用装任何运行库。

**支持的游戏**：原神 · 崩坏：星穹铁道 · 绝区零 · 明日方舟 · 明日方舟：终末地

### 核心特性

- **多账号隔离** — 每款游戏各自独立凭证，任何平台的任何游戏都可以绑在不同账号上；扫码时勾哪款就更新哪款，不做任何「同号」预设。
- **防封伪装** — 内置真机设备指纹档案与官方签名算法（米游社 DS / 森空岛 HMAC-SHA256）。遇到 1034 / 5003 风控时，可在「账号绑定 → 设备伪装」贴上自己手机的指纹，通过率更高。
- **免装 Python** — 便携 Python 3.11 与全部依赖随包分发，零外部运行库（仅需系统自带的 WebView2）。
- **独立桌面端** — C# WinForms + WebView2 外壳，单文件 exe；托盘常驻、开机自启，点右上角 X 只是缩到托盘，巡查照常跑。
- **演示模式引导** — 首次启动不显示任何假数据，顶部蓝色横幅引导扫码，绑定后自动消失。
- **数据只存本机** — 登录凭证与巡检历史都在解压目录内，不上传任何服务器。

### 下载

`HopOn_v2.0.0_Windows_x64.zip`（24.4 MB）— 解压即用，详细步骤见包内 `小白使用说明.txt`。

> 本软件未购买代码签名证书，部分杀软会误报，选择「仍要运行 / 信任」即可。全部源码就在本仓库，可自行审阅。

### 首次使用四步

1. 解压，双击 `HopOn.exe`
2. 顶部横幅「去扫码登录」→ 在【账号绑定】页扫码
3. 【通知绑定】页填 Server酱（微信）/ Qmsg（QQ）的 key，点「发送测试推送」确认手机能收到
4. 完事 — 默认每天 08:30 / 20:30 自动检查并推送，时间可在【定时与状态】页修改

> 只读查询，不会代打游戏、不会自动签到。MIT 许可证，可自由修改分发。"""


def main() -> int:
    tok = os.environ.get("GH_TOK", "").strip()
    if not tok:
        print("FATAL: 缺少 GH_TOK")
        return 1
    s = requests.Session()
    s.trust_env = False  # 本机代理会拦，直连
    s.headers.update({
        "Authorization": f"token {tok}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })

    head = s.get(f"{API}/commits/main", timeout=30).json()
    sha = head["sha"]
    print(f"target commit: {sha[:9]}  {head['commit']['message'].splitlines()[0]}")

    # 1) 注释 tag 对象 + ref（已存在则跳过，避免留下孤儿 tag 对象）
    if s.get(f"{API}/git/ref/tags/{TAG}", timeout=30).status_code == 200:
        print(f"tag ref {TAG} 已存在，跳过创建")
    else:
        r = s.post(f"{API}/git/tags", json={
            "tag": TAG,
            "message": TAG_MSG,
            "object": sha,
            "type": "commit",
            "tagger": {
                "name": "QianYiXi0723",
                "email": "239953299+QianYiXi0723@users.noreply.github.com",
            },
        }, timeout=30)
        if r.status_code != 201:
            print(f"tag object resp {r.status_code}: {r.text[:200]}")
            return 1
        tag_sha = r.json()["sha"]
        print(f"tag object created: {tag_sha[:9]}")

        r = s.post(f"{API}/git/refs", json={"ref": f"refs/tags/{TAG}", "sha": tag_sha}, timeout=30)
        if r.status_code == 201:
            print("tag ref created")
        elif r.status_code == 422:
            print("tag ref 已存在，跳过")
        else:
            print(f"tag ref FAILED {r.status_code}: {r.text[:300]}")
            return 1

    # 3) Release：先最小 payload 建草稿，再 PATCH 补中文正文
    #    （一次性提交完整 payload 会被 GitHub 判 400 malformed，分两步稳妥）
    rel = s.get(f"{API}/releases/tags/{TAG}", timeout=30)
    if rel.status_code == 200:
        rel = rel.json()
        print("release 已存在，复用")
    else:
        r = s.post(f"{API}/releases", json={"tag_name": TAG, "draft": True}, timeout=30)
        if r.status_code != 201:
            print(f"release create FAILED {r.status_code}: {r.text[:300]}")
            return 1
        rid = r.json()["id"]
        r = s.patch(f"{API}/releases/{rid}", json={
            "tag_name": TAG,
            "target_commitish": sha,
            "name": "HopOn v2.0.0 · 首个桌面发行版",
            "body": BODY,
            "draft": False,
            "prerelease": False,
        }, timeout=30)
        if r.status_code != 200:
            print(f"release patch FAILED {r.status_code}: {r.text[:300]}")
            return 1
        rel = r.json()
    print(f"release: {rel['html_url']}")

    # 4) 上传资产
    size = ZIP.stat().st_size
    existing = {a["name"]: a for a in rel.get("assets", [])}
    if ZIP.name in existing:
        print(f"资产已存在，跳过上传（{existing[ZIP.name]['size']/1024/1024:.1f} MB）")
    else:
        with ZIP.open("rb") as fh:
            up = s.post(
                f"{UPLOADS}/releases/{rel['id']}/assets?name={ZIP.name}",
                data=fh,
                headers={"Content-Type": "application/zip"},
                timeout=600,
            )
        if up.status_code not in (201, 202):
            print(f"upload FAILED {up.status_code}: {up.text[:400]}")
            return 1
        asset = up.json()
        print(f"asset uploaded: {asset['name']} {asset['size']/1024/1024:.1f} MB")
        print(f"download: {asset['browser_download_url']}")

    print(f"local zip size: {size/1024/1024:.1f} MB")
    print(f"RELEASE_URL={rel['html_url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布器上传后验证门禁的结构断言（2026-11-20 ⑤，goreleaser Verify 语义）：

背景：SUMS 缺失/错位两波事故（v2.6.0/v2.6.1 错位、v2.6.10.1/.2 缺 SUMS
断链）都是「传完靠人肉记得补」漏的。本断言钉死两个发布器必须自带
「上传后验证」：资产名清单对 + 逐个下载 sha256 对本地，不等即 FATAL。

  * gh_release_publish.py（GH 渠道）：SUMS 纳入必传三资产 + 复核段验证；
  * gitee_backfill.py（Gitee 渠道）：无版本化名第二份（更新器按固定名
    SHA256SUMS.txt 拉取，Gitee 历史上只传版本化名=该渠道 SUMS 永远 404）
    + _verify 上传后验证。
"""
from __future__ import annotations

import py_compile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PASS = 0
FAIL = 0


def check(cond: bool, label: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}")


def main() -> int:
    gh = (ROOT / "tools" / "rl_release" / "gh_release_publish.py").read_text(encoding="utf-8")
    gt = (ROOT / "tools" / "rl_release" / "gitee_backfill.py").read_text(encoding="utf-8")

    # ---- 语法门禁（两脚本可编译）----
    for name, path in (("gh_release_publish", ROOT / "tools" / "rl_release" / "gh_release_publish.py"),
                       ("gitee_backfill", ROOT / "tools" / "rl_release" / "gitee_backfill.py")):
        try:
            py_compile.compile(str(path), doraise=True)
            check(True, f"语法可编译 {name}")
        except py_compile.PyCompileError as exc:
            check(False, f"语法可编译 {name}：{exc}")

    # ---- GH 渠道：SUMS 必传 + 上传后验证 ----
    collect = gh.split("def collect_assets", 1)[1].split("def main", 1)[0]
    check("SHA256SUMS.txt" in collect and "text/plain" in collect,
          "GH1 collect_assets 纳入 SHA256SUMS.txt 必传三资产")
    check("上传后验证" in gh and "goreleaser Verify" in gh,
          "GH2 复核段升级为上传后验证（goreleaser Verify 语义）")
    check("线上缺资产" in gh, "GH3 资产名清单断言（缺一即 FATAL）")
    check("application/octet-stream" in gh and "sha256 一致" in gh,
          "GH4 逐个下载实物哈希对本地")
    check("hdr = {" in gh and "headers={**hdr" in gh,
          "GH5 复核段下载头变量 hdr 已定义（2026-11-20 NameError 回归钉）")

    # ---- Gitee 渠道：双名 SUMS + 上传后验证 ----
    check("无版本化名第二份" in gt and '"SHA256SUMS.txt"' in gt,
          "GT1 无版本化名第二份（更新器固定名 SHA256SUMS.txt）")
    check("def _verify" in gt and "上传后验证" in gt,
          "GT2 _verify 上传后验证在位")
    check("线上缺资产" in gt and "sha256 一致" in gt,
          "GT3 名清单断言+实物哈希对本地")
    check("_sums_for(tag).read_bytes()" in gt,
          "GT4 第二份内容取自该版本 SUMS（不串版本）")

    print(f"PASS {PASS} / FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

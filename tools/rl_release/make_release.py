# -*- coding: utf-8 -*-
"""发版编排器：把现有七步按既有顺序串成一条命令（2026-11-06）。

**零新逻辑**——每一步都是调用既有脚本/命令，参数与人工操作完全一致；
任一步失败立即中止，绝不带病进下一步。上传 GitHub 不在编排内
（发版红线：tag 与资产上传必须人工触发，见 gh_release_v2.py）。

用法：python tools/rl_release/make_release.py [--skip-shell]
  --skip-shell  跳过第 1 步壳重编译（壳没改、只改 Python/前端时用）

步骤（即人工发版流程的固化）：
  1. shell/build.ps1            壳编译 + 全新目录冒烟门禁
  2. _build_release.py          组装 OUT + diet 瘦身 + 运行时冒烟
                                + 泄漏自检 + zip + SHA256SUMS
  3. rl_release/verify_release.py  结构/隐私/品牌门禁
  4. Inno Setup（可选）         有 ISCC 就编 Setup.exe，没有则跳过
  5. rl_release/check_installer.py 安装包 28 项审计（有 Setup 才跑）
  6. rl_release/check_release_privacy.py 安装内容隐私审计
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ISCC_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
)


def _find_iscc() -> Path | None:
    """探测 ISCC：环境变量 HOPON_ISCC > 常见安装路径 > 安装盘下的 Inno Setup*。

    机器上多套 Inno 并存时取版本号最新的（Inno 7 自带 ChineseSimplified.isl，
    旧 6.x 缺该语言文件会让 .iss 编译挂在 [Languages]）。
    """
    override = os.environ.get("HOPON_ISCC", "").strip()
    if override and Path(override).exists():
        return Path(override)
    pool = [c for c in ISCC_CANDIDATES if c.exists()]
    if ROOT.parent.is_dir():
        pool += sorted(ROOT.parent.glob("Inno Setup */ISCC.exe"), reverse=True)
    # 需求是「编中文安装包」：只挑 Languages 里真有 ChineseSimplified.isl 的那套
    # （旧 Inno 6.x 不自带，编译会挂在 [Languages]——两套并存时这是唯一选型判据）
    for cand in pool:
        if (cand.parent / "Languages" / "ChineseSimplified.isl").exists():
            return cand
    return None


def run(step: str, cmd: list[str]) -> None:
    print(f"\n=== {step} ===")
    print("$ " + " ".join(str(c) for c in cmd))
    result = subprocess.run([str(c) for c in cmd], cwd=str(ROOT))
    if result.returncode != 0:
        print(f"[FATAL] 第「{step}」步失败（exit {result.returncode}），编排中止")
        sys.exit(result.returncode or 1)


def main() -> None:
    skip_shell = "--skip-shell" in sys.argv
    if skip_shell:
        print("== 跳过壳重编译（--skip-shell）==")
    else:
        run(
            "1/6 壳编译 + 冒烟门禁",
            ["powershell", "-ExecutionPolicy", "Bypass", "-File",
             str(ROOT / "shell" / "build.ps1")],
        )

    run("2/6 组装 OUT + diet + 冒烟 + 泄漏自检 + zip",
        [sys.executable, str(ROOT / "_build_release.py")])

    run("3/6 结构/隐私/品牌门禁",
        [sys.executable, str(ROOT / "tools" / "rl_release" / "verify_release.py")])

    import re

    version = re.search(
        r'^__version__\s*=\s*"([^"]+)"',
        (ROOT / "core" / "__init__.py").read_text(encoding="utf-8"),
        re.M,
    ).group(1)
    iss = ROOT / "installer" / "HopOn.iss"
    iscc = _find_iscc()
    if iss.exists() and iscc is not None:
        run("4/6 Inno Setup 安装包",
            [str(iscc), f"/DAppVersion={version}", str(iss)])
    else:
        print("\n=== 4/6 Inno Setup 安装包 ===")
        print("[SKIP] 未找到 ISCC.exe 或 installer/HopOn.iss —— 跳过安装包编译")

    setup = ROOT / "installer" / "Output" / f"HopOn_v{version}_Setup.exe"
    if setup.exists():
        # Setup.exe 归位到 dist/：zip/校验和/安装器一个文件夹全齐，用户好找
        import shutil as _shutil
        _shutil.copy2(setup, ROOT / "dist" / setup.name)
        run("5/6 安装包审计（28 项）",
            [sys.executable, str(ROOT / "tools" / "check_installer.py")])
    else:
        print("\n=== 5/6 安装包审计 ===")
        print("[SKIP] 无 Setup.exe（上一步跳过）——审计随之跳过")

    run("6/6 安装内容隐私审计",
        [sys.executable, str(ROOT / "tools" / "check_release_privacy.py")])

    # 第 7 步：Gitee 国内镜像同步（2026-11-07 上线；失败不阻断发版，
    # Gitee 只是加速镜像，GitHub 才是主渠道）
    gitee_release_ok = False
    print("\n=== 7/7 Gitee 国内镜像同步 ===")
    try:
        token_file = ROOT / "tools" / "rl_release" / ".gitee_token"
        if not token_file.exists():
            print("[SKIP] 无 tools/rl_release/.gitee_token —— Gitee 不同步（该文件放私人令牌，已 gitignore）")
        else:
            gtok = token_file.read_text(encoding="utf-8").strip()
            import urllib.request as _uq

            def _gitee_api(url, payload=None):
                data = json.dumps(payload).encode() if payload else None
                req = _uq.Request(url, data=data, method="POST" if payload else "GET",
                    headers={"Content-Type": "application/json"})
                with _uq.urlopen(req, timeout=60) as r:
                    return json.loads(r.read().decode())

            # 7a. 推 main + tags（git 凭据里的 gitee 远端）
            run("7a Gitee 推代码", ["git", "push", "gitee", "main", "--tags"])
            # 7b. 建 Release（target_commitish 必填，漏了 400）
            notes_v = ROOT / "tools" / "rl_release" / "notes" / f"v{version}.md"
            rel = _gitee_api(
                "https://gitee.com/api/v5/repos/qianyixi/HopOn/releases",
                {"access_token": gtok, "tag_name": f"v{version}",
                 "name": f"HopOn v{version}",
                 "body": notes_v.read_text(encoding="utf-8") if notes_v.exists() else f"HopOn v{version}",
                 "prerelease": False, "target_commitish": "main"})
            print(f"    Release 已建（id={rel['id']}）")
            # 7c. 传资产
            boundary = "----HopOnBoundary7d1a2c"
            for asset in (ROOT / "dist" / f"HopOn_v{version}_Setup.exe",
                          ROOT / "dist" / f"HopOn_v{version}_Windows_x64.zip",
                          ROOT / "dist" / "SHA256SUMS.txt"):
                if not asset.exists():
                    continue
                content = asset.read_bytes()
                body = (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="access_token"\r\n\r\n{gtok}\r\n'
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="file"; filename="{asset.name}"\r\n'
                    f"Content-Type: application/octet-stream\r\n\r\n"
                ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
                req = _uq.Request(
                    f"https://gitee.com/api/v5/repos/qianyixi/HopOn/releases/{rel['id']}/attach_files",
                    data=body, method="POST",
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
                with _uq.urlopen(req, timeout=300) as r:
                    print(f"    ✓ {asset.name}")
            gitee_release_ok = True
    except SystemExit:
        raise   # run() 的失败要如实中断
    except Exception as exc:  # noqa: BLE001 - 镜像失败不阻断主发版
        print(f"[WARN] Gitee 同步失败（不影响 GitHub 主渠道）：{exc}")

    print("\n================ 发版编排完成 ================")
    for artifact in (
        ROOT / "release" / f"HopOn_v{version}",
        ROOT / "dist" / f"HopOn_v{version}_Windows_x64.zip",
        ROOT / "dist" / "SHA256SUMS.txt",
        setup,
    ):
        mark = "✓" if Path(artifact).exists() else "－"
        print(f"  [{mark}] {artifact}")
    print(f"  [{'✓' if gitee_release_ok else '－'}] Gitee 国内镜像（gitee.com/qianyixi/HopOn）")
    print("下一步（人工）：核对产物 → gh_release.py 上传 GitHub（发版红线：tag/资产人工触发）")


if __name__ == "__main__":
    main()

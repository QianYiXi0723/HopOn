# -*- coding: utf-8 -*-
"""安装包内容安全审计（2026-11-05）。

在**发出之前**确认 Setup.exe 里没有夹带用户隐私：
凭证、设备真机档案、历史数据、日志。

做法：用 Inno Setup 的 ISCC 反编译不可行，改用「解压安装包 + 扫描
字符串」的方式——Inno 的 LZMA 压缩无法直接解，所以换个角度：
扫描**构建输入目录**（release/HopOn_v*/）里到底有什么，安装器只是把它
打包进去。只要输入目录干净，包就干净。

再补一层：直接在 Setup.exe 的原始字节里搜敏感关键字（未压缩的文件名
和部分字符串仍以明文存在），防止漏网。
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

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


def find_release() -> Path:
    base = ROOT / "release"
    cands = sorted(
        [p for p in base.glob("HopOn_v*") if p.is_dir()],
        key=lambda p: p.name,
    )
    return cands[-1] if cands else base


REL = find_release()
SETUP = ROOT / "installer" / "Output"

print("=== A. 构建输入目录不含敏感文件 ===")
print("    目录：%s" % REL)

# A1：绝不能有 config.json（含 cookie/token）
cfg = list(REL.rglob("config.json"))
check("A1 无 config.json", not cfg, [str(p.relative_to(REL)) for p in cfg][:3])

# A2：不能有 data\（历史/推送/凭证健康）
data_dirs = [p for p in REL.rglob("data") if p.is_dir()]
check("A2 无 data 目录", not data_dirs, [str(p.relative_to(REL)) for p in data_dirs][:3])

# A3：不能有 logs\（设备身份/真机档案/森空岛凭证）
log_dirs = [p for p in REL.rglob("logs") if p.is_dir()]
check("A3 无 logs 目录", not log_dirs, [str(p.relative_to(REL)) for p in log_dirs][:3])

# A4：不能有设备敏感文件（散落在别处也要抓）
sensitive_names = (
    "device_identity.json", "device_fp.json", "device_profile.json",
    "mihoyo_binding.json", "skland_cred_arknights.json",
    "skland_cred_endfield.json", "history.json", "push_history.json",
    "cred_health.json", ".migrated.json",
)
leaked = []
for p in REL.rglob("*"):
    if p.is_file() and p.name in sensitive_names:
        leaked.append(str(p.relative_to(REL)))
check("A4 无任何敏感数据文件", not leaked, leaked[:5])

# A5：不能有 .bak 备份（可能含旧配置）
baks = [str(p.relative_to(REL)) for p in REL.rglob("*.bak*") if p.is_file()]
check("A5 无 *.bak 备份文件", not baks, baks[:5])

# A6：不能有用户证书/密钥（白名单：Python 自带的公共根证书）
#    certifi/cacert.pem 是 HTTPS 验证用的公共 CA 包，不是用户密钥——
#    它随 requests 一起装进便携 Python，属于必需依赖。
_CERT_WHITELIST = ("certifi", "cacert.pem")
secrets = []
for p in REL.rglob("*"):
    if not p.is_file() or p.suffix.lower() not in (".pem", ".key", ".p12", ".pfx"):
        continue
    rel = str(p.relative_to(REL))
    if any(w in rel.lower() for w in _CERT_WHITELIST):
        continue
    secrets.append(rel)
check("A6 无用户证书/密钥", not secrets, secrets[:3])


print("=== B. 安装包字节流不含敏感串 ===")

setups = sorted(SETUP.glob("*.exe")) if SETUP.is_dir() else []
check("B1 找到 Setup.exe", bool(setups), str(SETUP))
if setups:
    blob = setups[-1].read_bytes()
    print("    文件：%s（%.1f MB）" % (setups[-1].name, len(blob) / 1048576))

    # 真机型号是最关键的隐私标识（不该出现在发行物里）
    markers = {
        "真机型号 24122RKC7C": b"24122RKC7C",
        "真机设备名 miro": b"miro:16",
        "OAID 片段 a9cb56": b"a9cb56",
        "cookie_token": b"cookie_token",
        "ltoken": b"ltoken",
        "stoken": b"stoken",
    }
    for label, needle in markers.items():
        check("B2 不含 %s" % label, needle not in blob)


print("=== C. 安装包只应包含程序文件 ===")

if REL.is_dir():
    exe = list(REL.glob("*.exe"))
    check("C1 有 HopOn.exe", any(p.name == "HopOn.exe" for p in exe),
          [p.name for p in exe])
    check("C2 没有旧名 ResinLoop.exe",
          not any(p.name == "ResinLoop.exe" for p in exe),
          [p.name for p in exe])
    dlls = [p.name for p in REL.glob("*.dll")]
    for need in ("Microsoft.Web.WebView2.Core.dll", "WebView2Loader.dll"):
        check("C3 有 %s" % need, need in dlls, dlls)
    check("C4 有 engine 目录", (REL / "engine").is_dir())

    # engine 里必须有入口，没有入口的包是坏的
    for need in ("app.py", "daemon.py", "login_ui.py", "main.py"):
        check("C5 engine 含 %s" % need, (REL / "engine" / need).exists())


print("=== D. 数据外置后的首启行为 ===")

# 关键回归：发行版的 config.json 由 ensure_config 在用户数据目录生成，
# 不能靠安装包内置。所以包里**不该有** config.json —— A1 已验；
# 但要确认代码里确实会去 user_root 生成，而不是退回程序目录。
paths_py = ROOT / "core" / "paths.py"
check("D1 paths.py 存在", paths_py.exists())
if paths_py.exists():
    txt = paths_py.read_text(encoding="utf-8")
    check("D2 有 user_root()", "def user_root(" in txt)
    check("D3 用 LOCALAPPDATA", "LOCALAPPDATA" in txt)
    check("D4 有迁移许可开关", "allow_migrate" in txt)
    check("D5 有紧急闸", "HOPON_NO_MIGRATE" in txt)

# 安装脚本里 config.json 是从 engine 拷的模板，目标在数据目录
iss = ROOT / "installer" / "HopOn.iss"
if iss.exists():
    itxt = iss.read_text(encoding="utf-8")
    check("D6 安装器的 config 落数据目录", "{#UserDataDir}" in itxt)
    check("D7 安装器的 config 不覆盖", "onlyifdoesntexist" in itxt)


print()
print("=" * 52)
print("安装包审计：PASS %d / FAIL %d" % (PASS, FAIL))
if FAILED:
    print("失败项：")
    for f in FAILED:
        print("  - " + f)
sys.exit(1 if FAIL else 0)

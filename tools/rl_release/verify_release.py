# -*- coding: utf-8 -*-
"""发行包验收：结构、脱敏、版本、与工作树一致性。"""
import hashlib
import zipfile
from pathlib import Path

ROOT = Path(r"E:\Apps\HopOn")
OUT = ROOT / "release" / "HopOn_v2.1"
ZIP = ROOT / "dist" / "HopOn_v2.1_Windows_x64.zip"

ok = True


def chk(cond, label, detail=""):
    global ok
    if not cond:
        ok = False
    print(("PASS " if cond else "FAIL ") + label + (("  | " + str(detail)) if detail else ""))


def md5(p):
    return hashlib.md5(Path(p).read_bytes()).hexdigest()


print("=== 1. 目录结构 ===")
top = sorted(p.name for p in OUT.iterdir())
chk(top == ["HopOn.exe", "WebView2Loader.dll", "engine", "小白使用说明.txt", "LICENSE"],
    "顶层五件（2026-11-05 P5：LICENSE 随包）", top)
chk((OUT / "engine" / "python" / "python.exe").exists(), "便携解释器存在")
chk((OUT / "engine" / "app.py").exists(), "引擎入口存在")
chk((OUT / "engine" / "ui" / "index.html").exists(), "前端三件套存在")

print("\n=== 1b. 分发边界（P4/P5，2026-11-05） ===")
apk = OUT / "engine" / "tools" / "设备信息.apk"
chk(not apk.exists(), "设备伪装 APK 不随包（仅源码部署提供，P4）", str(apk))
notes_path = OUT / "小白使用说明.txt"
notes = notes_path.read_text(encoding="utf-8-sig") if notes_path.exists() else ""
chk("github.com/QianYiXi0723/HopOn" in notes, "说明含源码仓库地址（P5）")
chk("ResinLoop" not in notes, "说明无 ResinLoop 品牌残留（P5）")
chk("DPAPI" in notes, "说明含凭证加密说明（P3）")

print("\n=== 2. 脱敏（不得出现配置/日志/数据/备份/字节码） ===")
bad = []
for f in OUT.rglob("*"):
    if not f.is_file():
        continue
    rel = f.relative_to(OUT).as_posix()
    name = f.name.lower()
    if (name in ("config.json", "config.example.json")
            or rel.startswith("engine/logs/") or rel.startswith("engine/data/")
            or ".bak" in name or name.endswith(".pyc") or "__pycache__" in rel):
        bad.append(rel)
chk(not bad, "无敏感/临时文件", bad[:6] if bad else "0 个")

print("\n=== 3. 与工作树一致 ===")
for name in ("index.html", "style.css", "app.js"):
    a = md5(ROOT / "ui" / name)
    b = md5(OUT / "engine" / "ui" / name)
    chk(a == b, "ui/%s md5 一致" % name, a[:8])
chk(md5(ROOT / "HopOn.exe") == md5(OUT / "HopOn.exe"), "壳 exe md5 与根目录一致")
chk(md5(ROOT / "WebView2Loader.dll") == md5(OUT / "WebView2Loader.dll"), "WebView2Loader md5 一致")

print("\n=== 4. 版本与关键改动 ===")
idx = (OUT / "engine" / "ui" / "index.html").read_text(encoding="utf-8")
chk("v2.1 · 本地运行" in idx, "侧栏版本 v2.1")
daemon = (OUT / "engine" / "daemon.py").read_text(encoding="utf-8")
chk("use_tray=False" in daemon, "daemon 去托盘改写已生效")
chk("use_tray=not args.no_tray" not in daemon, "daemon 原托盘开关已消失")
app = (OUT / "engine" / "app.py").read_text(encoding="utf-8")
chk("create_window" not in app, "引擎入口不建窗口（纯 HTTP）")
tail_txt = (OUT / "小白使用说明.txt").read_text(encoding="utf-8-sig")
chk("v2.1" in tail_txt, "使用说明版本 v2.1")
chk("开机自启动" in tail_txt, "使用说明文案已更新")

print("\n=== 5. zip 完整性 ===")
with zipfile.ZipFile(ZIP) as z:
    names = z.namelist()
    roots = sorted({n.split("/")[0] for n in names})
    chk(roots == ["HopOn_v2.1"], "zip 顶层单一目录", roots)
    chk(len(names) >= 380, "条目数", len(names))
    bad_zip = [n for n in names if n.lower().endswith(".pyc") or "__pycache__" in n or "config.json" in n]
    chk(not bad_zip, "zip 内无字节码/配置", bad_zip[:5] if bad_zip else "0 个")
    inner = z.read("HopOn_v2.1/engine/ui/index.html").decode("utf-8")
    chk("v2.1 · 本地运行" in inner, "zip 内页面版本 v2.1")
    print("      zip 大小 %.1f MB / %d 条目" % (ZIP.stat().st_size / 1024 / 1024, len(names)))

print("\n=== 结论：" + ("全部通过" if ok else "有失败项") + " ===")
raise SystemExit(0 if ok else 1)

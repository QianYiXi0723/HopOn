"""数据外置与迁移的回归测试（2026-11-05）。

**绝对安全**：程序根用 ``HOPON_APP_DIR`` 指到临时目录，用户根用
``HOPON_USER_DIR`` 指到另一个临时目录，并在子进程里跑——不碰真实仓库。

⚠ 这条测试本身就是血泪教训的产物：第一版测试没有 HOPON_APP_DIR 覆盖，
`app_root()` 仍解析到真实仓库，于是 `ensure_layout()` 真的把开发机的
config.json / data\\ 搬进了临时目录、随 TemporaryDirectory 一起删掉了。
搬数据的代码必须能指向沙箱，这是硬要求，不是锦上添花。
"""

import json
import os
import subprocess
import sys
import tempfile
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


def make_legacy(root: Path):
    """在 root 下造一套旧布局数据。"""
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "config.json").write_text(
        json.dumps({"app": {"check_times": ["08:30"]}, "_marker": "LEGACY"}),
        encoding="utf-8",
    )
    (root / "data" / "history.json").write_text(
        '{"version":1,"days":{}}', encoding="utf-8"
    )
    (root / "logs" / "daemon.pid").write_text("12345", encoding="utf-8")


def run_in_sandbox(app_dir: Path, user_dir: Path, body: str):
    """在沙箱里跑一段用到 core.paths 的代码，返回解析后的 JSON。"""
    code = (
        "import os,sys,json;"
        "sys.path.insert(0, %r);"
        "import core.paths as P;"
        "%s"
    ) % (str(ROOT), body)
    env = {
        **os.environ,
        "HOPON_APP_DIR": str(app_dir),
        "HOPON_USER_DIR": str(user_dir),
        "PYTHONIOENCODING": "utf-8",
    }
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        env=env,
        cwd=str(app_dir if app_dir.exists() else ROOT),
    )
    txt = out.stdout.decode("utf-8", "replace").strip()
    lines = [l for l in txt.splitlines() if l.strip()]
    for line in reversed(lines):
        try:
            return json.loads(line)
        except Exception:  # noqa: BLE001
            continue
    return {"_raw": txt[-400:], "_err": out.stderr.decode("utf-8", "replace")[-300:]}


print("=== A. 沙箱隔离（防再次误伤真实数据）===")

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    app_dir, user_dir = td / "app", td / "user"
    app_dir.mkdir()
    rep = run_in_sandbox(
        app_dir, user_dir,
        "print(json.dumps({'app':str(P.app_root()),'user':str(P.user_root())}))",
    )
    check("A1 app_root 被 HOPON_APP_DIR 覆盖",
          Path(rep.get("app", "")).resolve() == app_dir.resolve(), rep)
    check("A2 user_root 被 HOPON_USER_DIR 覆盖",
          Path(rep.get("user", "")).resolve() == user_dir.resolve(), rep)
    check("A3 两者都在临时目录内（不在真实仓库）",
          str(ROOT.resolve()) not in str(rep.get("app", "")), rep)


print("=== B. 默认不迁移（allow_migrate=False）===")

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    app_dir, user_dir = td / "app", td / "user"
    app_dir.mkdir()
    make_legacy(app_dir)
    rep = run_in_sandbox(
        app_dir, user_dir,
        "print(json.dumps(P.ensure_layout(),ensure_ascii=False))",
    )
    check("B1 未授权时不迁移 action=pending", rep.get("action") == "pending", rep.get("action"))
    check("B2 原文件仍在", (app_dir / "config.json").exists())
    check("B3 目标未创建", not (user_dir / "config.json").exists())


print("=== C. 显式授权后迁移 ===")

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    app_dir, user_dir = td / "app", td / "user"
    app_dir.mkdir()
    make_legacy(app_dir)
    rep = run_in_sandbox(
        app_dir, user_dir,
        "print(json.dumps(P.ensure_layout(allow_migrate=True),ensure_ascii=False))",
    )
    check("C1 action=migrate", rep.get("action") == "migrate", rep.get("action"))
    check("C2 无错误", not rep.get("errors"), rep.get("errors"))
    check("C3 config 已搬", (user_dir / "config.json").exists())
    check("C4 data 已搬", (user_dir / "data" / "history.json").exists())
    check("C5 logs 已搬", (user_dir / "logs" / "daemon.pid").exists())
    check("C6 原位置已清空", not (app_dir / "config.json").exists())
    moved = json.loads((user_dir / "config.json").read_text(encoding="utf-8"))
    check("C7 内容完好", moved.get("_marker") == "LEGACY", moved)

    # D 幂等
    rep2 = run_in_sandbox(
        app_dir, user_dir,
        "print(json.dumps(P.ensure_layout(allow_migrate=True),ensure_ascii=False))",
    )
    check("D1 幂等 already", rep2.get("action") == "already", rep2.get("action"))


print("=== E. 冲突不覆盖 ===")

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    app_dir, user_dir = td / "app", td / "user"
    app_dir.mkdir()
    make_legacy(app_dir)
    user_dir.mkdir(parents=True)
    (user_dir / "config.json").write_text('{"_marker":"NEWER"}', encoding="utf-8")
    rep = run_in_sandbox(
        app_dir, user_dir,
        "print(json.dumps(P.ensure_layout(allow_migrate=True),ensure_ascii=False))",
    )
    kept = json.loads((user_dir / "config.json").read_text(encoding="utf-8"))
    check("E1 目标已存在时保留目标", kept.get("_marker") == "NEWER", kept)
    check("E2 该项标记 exists",
          rep.get("moved", {}).get("config.json") == "exists", rep.get("moved"))


print("=== F. 紧急闸 HOPON_NO_MIGRATE ===")

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    app_dir, user_dir = td / "app", td / "user"
    app_dir.mkdir()
    make_legacy(app_dir)
    code = (
        "import os,sys,json;sys.path.insert(0, %r);"
        "import core.paths as P;"
        "r=P.ensure_layout(allow_migrate=True);"
        "print(json.dumps(r,ensure_ascii=False))"
    ) % str(ROOT)
    env = {
        **os.environ,
        "HOPON_APP_DIR": str(app_dir),
        "HOPON_USER_DIR": str(user_dir),
        "HOPON_NO_MIGRATE": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         env=env, cwd=str(app_dir))
    rep = json.loads(out.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
    check("F1 紧急闸生效 action=pending", rep.get("action") == "pending", rep.get("action"))
    check("F2 紧急闸下原文件不动", (app_dir / "config.json").exists())


print("=== G. 静态扫描：程序目录不该再落数据 ===")

bad = []
scan_files = list((ROOT / "core").glob("*.py")) + [
    ROOT / n for n in ("app.py", "daemon.py", "main.py", "login_ui.py")
]
for f in scan_files:
    if not f.exists():
        continue
    text = f.read_text(encoding="utf-8", errors="replace")
    for line_no, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if s.startswith("#"):
            continue
        has_root = "app_root()" in s or "BASE_DIR" in s
        has_data = '"/logs"' in s or '"/data"' in s or '"logs"' in s or '"data"' in s
        if has_root and has_data and "paths.py" not in str(f):
            bad.append("%s:%d %s" % (f.name, line_no, s[:80]))

check("G1 无残留 BASE_DIR/app_root 数据路径", not bad, bad[:4])


print()
print("=" * 50)
print("PASS %d / FAIL %d" % (PASS, FAIL))
if FAILED:
    print("失败项：")
    for f in FAILED:
        print("  - " + f)
sys.exit(1 if FAIL else 0)

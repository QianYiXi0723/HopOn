# -*- coding: utf-8 -*-
"""首启体验验证：把发行包整包拷到临时目录，模拟小白第一次双击。

检查：demo_mode、是否出现假数据（明日方舟(博士) 全部完成）、页面/接口状态。
用 8909 端口，跑完杀进程并删临时目录。
"""
import json
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

SRC = Path(r"E:\Apps\HopOn\release\HopOn_v2.1")
PORT = 8909
BASE = "http://127.0.0.1:%d" % PORT

tmp_root = Path(tempfile.mkdtemp(prefix="rl-firstrun-"))
dst = tmp_root / "HopOn_v2.1"
print("临时目录:", dst)
shutil.copytree(SRC, dst)

PY = dst / "engine" / "python" / "pythonw.exe"
APP = dst / "engine" / "app.py"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(path, timeout=8):
    with opener.open(BASE + path, timeout=timeout) as r:
        return r.status, r.read()


proc = subprocess.Popen(
    [str(PY), str(APP), "--port", str(PORT), "--no-lan-tools"],
    cwd=str(dst / "engine"),
    creationflags=0x00000008,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
print("引擎 pid=%d port=%d" % (proc.pid, PORT))

try:
    status = None
    for _ in range(40):
        time.sleep(0.6)
        try:
            status, body = get("/")
            break
        except Exception:
            continue
    print("首页 status=%s bytes=%s" % (status, len(body) if body else 0))

    for path in ("/api/env", "/api/schedule", "/api/accounts", "/api/live"):
        try:
            s, b = get(path, timeout=25)
            text = b.decode("utf-8", "replace")
            if path == "/api/env":
                d = json.loads(text)
                print("  %-16s %s demo_mode=%s configured=%s" % (path, s, d.get("demo_mode"), d.get("configured")))
            elif path == "/api/accounts":
                d = json.loads(text)
                accs = d.get("accounts") if isinstance(d, dict) else d
                print("  %-16s %s accounts=%d" % (path, s, len(accs or [])))
                print("       账号列表:", [a.get("name") or a.get("game_id") for a in (accs or [])][:8])
            elif path == "/api/live":
                d = json.loads(text)
                st = d.get("statuses") or d.get("items") or []
                print("  %-16s %s stale=%s entries=%d" % (path, s, d.get("stale"), len(st)))
                for it in st[:6]:
                    print("       -", it.get("game_name") or it.get("name"), "|",
                          it.get("nickname") or it.get("uid") or "", "|",
                          "error=" + str(it.get("error"))[:40] if it.get("error") else "ok")
            else:
                print("  %-16s %s %s" % (path, s, text[:120]))
        except Exception as e:
            print("  %-16s ERR %s %s" % (path, type(e).__name__, e))

    print("\n--- 生成的文件 ---")
    for p in sorted((dst / "engine").rglob("*")):
        if p.is_file() and p.suffix in (".json", ".log") and "site-packages" not in str(p):
            print("  ", p.relative_to(dst).as_posix(), p.stat().st_size, "B")

    cfg = dst / "engine" / "config.json"
    if cfg.exists():
        d = json.loads(cfg.read_text(encoding="utf-8"))
        print("\nconfig.json provider=%r accounts=%d push_channels=%s"
              % (d.get("provider"), len(d.get("accounts") or []),
                 list((d.get("push") or {}).keys())[:4]))
finally:
    subprocess.run(["taskkill", "/F", "/PID", str(proc.pid)], capture_output=True)
    time.sleep(1.5)
    try:
        shutil.rmtree(tmp_root)
        print("\n已清理临时目录")
    except Exception as e:
        print("\n清理失败（可手动删）:", tmp_root, e)

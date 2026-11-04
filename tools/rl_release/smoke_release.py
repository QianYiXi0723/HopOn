# -*- coding: utf-8 -*-
"""发行包冒烟：用包里自带的 engine 在临时端口起一次服务，验证真的能跑。

用 8907（避开生产 8765 与测试 8899），起完立刻杀，不留残留。
"""
import json
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

REL = Path(r"E:\Apps\HopOn\release\HopOn_v2.1")
PY = REL / "engine" / "python" / "pythonw.exe"
APP = REL / "engine" / "app.py"
PORT = 8907
BASE = "http://127.0.0.1:%d" % PORT

if not PY.exists() or not APP.exists():
    raise SystemExit("[FATAL] 发行包缺少解释器或入口")


def get(path, timeout=6):
    req = urllib.request.Request(BASE + path)
    # 本机系统代理会拦 localhost
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as r:
        return r.status, r.read()


proc = subprocess.Popen(
    [str(PY), str(APP), "--port", str(PORT), "--no-lan-tools"],
    cwd=str(REL / "engine"),
    creationflags=0x00000008,  # DETACHED_PROCESS
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
print("引擎已启动 pid=%d port=%d" % (proc.pid, PORT))

ok = True
try:
    status = None
    for _ in range(30):
        time.sleep(0.6)
        try:
            status, body = get("/")
            break
        except Exception:
            continue

    if status != 200:
        ok = False
        print("FAIL 首页未就绪 status=%s" % status)
    else:
        html = body.decode("utf-8", "replace")
        print("PASS 首页 200，%d KB" % (len(body) // 1024))
        print("     " + ("PASS" if "v2.1 · 本地运行" in html else "FAIL") + " 页面内版本 v2.1")
        ok = ok and ("v2.1 · 本地运行" in html)

    s2, b2 = get("/api/env")
    env = json.loads(b2.decode("utf-8"))
    print("PASS /api/env 200  demo_mode=%s configured=%s" % (env.get("demo_mode"), env.get("configured")))
    ok = ok and s2 == 200

    s3, b3 = get("/api/schedule")
    sch = json.loads(b3.decode("utf-8"))
    print("PASS /api/schedule 200  times=%s" % sch.get("times"))
    ok = ok and s3 == 200

    s4, b4 = get("/api/push_history")
    print("PASS /api/push_history 200（%d 字节）" % len(b4))
    ok = ok and s4 == 200
finally:
    subprocess.run(["taskkill", "/F", "/PID", str(proc.pid)],
                   capture_output=True)
    time.sleep(1.2)

    # 端口必须释放
    released = False
    for _ in range(12):
        try:
            get("/", timeout=1.5)
            time.sleep(0.5)
        except Exception:
            released = True
            break
    print(("PASS" if released else "FAIL") + " 端口 %d 已释放" % PORT)
    ok = ok and released

    # 清掉冒烟产生的运行期目录（logs/data/config 是发行包不该带的）
    eng = REL / "engine"
    for sub in ("logs", "data"):
        p = eng / sub
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
            print("清理冒烟残留：engine/%s" % sub)
    cfg = eng / "config.json"
    if cfg.exists():
        cfg.unlink()
        print("清理冒烟残留：engine/config.json")

print("\n=== 冒烟结论：" + ("通过" if ok else "失败") + " ===")
raise SystemExit(0 if ok else 1)

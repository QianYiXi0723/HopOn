# -*- coding: utf-8 -*-
"""三 Tab 控制台接口集成测试。

注意：会短暂改动 config.json 与 logs/device_profile.json，
     并在测试结束后原样还原（含 logs/device_fp.json，避免影响真实设备身份）。
"""
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(r"E:\Work\WorkBuddyWorkspace\check in")
sys.path.insert(0, str(BASE))
PORT = 8797
API = f"http://127.0.0.1:{PORT}"

CFG = BASE / "config.json"
FP = BASE / "logs" / "device_fp.json"
PROFILE = BASE / "logs" / "device_profile.json"

ok_n = fail_n = 0


def check(label, cond, extra=""):
    global ok_n, fail_n
    if cond:
        ok_n += 1
        print(f"  [PASS] {label}")
    else:
        fail_n += 1
        print(f"  [FAIL] {label}  {extra}")


def get(path, timeout=20):
    with urllib.request.urlopen(API + path, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode())


def post(path, payload):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(API + path, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


# ---------------- 备份 ----------------
backup_cfg = CFG.read_bytes()
fp_backup = FP.read_bytes() if FP.exists() else None
profile_backup = PROFILE.read_bytes() if PROFILE.exists() else None
print(f"已备份：config.json={len(backup_cfg)}B，device_fp.json="
      f"{len(fp_backup) if fp_backup else '无'}，device_profile.json="
      f"{len(profile_backup) if profile_backup else '无'}")

proc = subprocess.Popen(
    [sys.executable, "login_ui.py", "--port", str(PORT), "--no-browser"],
    cwd=str(BASE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)

try:
    for _ in range(40):
        time.sleep(0.25)
        try:
            get("/api/env", timeout=2)
            break
        except Exception:
            continue
    else:
        print("服务启动失败：")
        print(proc.stdout.read()[:2000] if proc.stdout else "")
        raise SystemExit(1)

    print("=" * 64)
    print("1) 页面结构")
    with urllib.request.urlopen(API + "/", timeout=10) as r:
        html = r.read().decode()
    check("首页 200", r.status == 200)
    for tab in ("account", "notify", "schedule"):
        check(f"存在 tab-{tab}", f'id="tab-{tab}"' in html)
    check("三个导航按钮", html.count('data-tab="account"') +
          html.count('data-tab="notify"') + html.count('data-tab="schedule"') >= 3)
    check("设备伪装输入区", 'id="dev-text"' in html)
    check("时间点容器", 'id="d-times"' in html)
    check("守护进程按钮", 'id="btn-daemon-start"' in html and 'id="btn-daemon-stop"' in html)

    print("\n" + "=" * 64)
    print("2) GET /api/device —— 设备档案与 APK")
    st, d = get("/api/device")
    print("   型号:", d["profile"]["model"], "| OAID:", d["profile"]["oaid"],
          "| 自定义:", d["custom"])
    print("   APK :", d["apk"].get("name"), d["apk"].get("size_text"),
          d["apk"].get("url"))
    check("返回 ok", d["ok"] is True)
    check("当前是默认档案", d["custom"] is False)
    check("OAID 已打码", "****" in d["profile"]["oaid"])
    check("APK 可用且带二维码", d["apk"]["available"] and len(d["apk"]["qr"]) > 500)
    check("UA 与档案一致", d["profile"]["model"] in d["ua"])

    print("\n" + "=" * 64)
    print("3) POST /api/device —— 粘贴档案（容错：夹带说明文字）")
    sample = {
        "deviceModel": "PGT-AN00", "androidVersion": "15",
        "deviceFingerprint": "HONOR/PGT-AN00/HNPGT:15/HONORPGT-AN00/9.0.0.150:user/release-keys",
        "deviceName": "HNPGT", "deviceBoard": "HONORPGT",
        "oaid": "1122334455667788",
    }
    st, d = post("/api/device", {"text": "设备信息：\n" + json.dumps(sample, ensure_ascii=False)})
    print("   状态", st, "| message:", d.get("message"))
    print("   新档案:", d["profile"]["brand"], d["profile"]["model"],
          "Android", d["profile"]["os_version"], "| 缓存:", d["cache_exists"])
    check("返回 ok", d.get("ok") is True)
    check("型号已更新", d["profile"]["model"] == "PGT-AN00")
    check("品牌从指纹推导", d["profile"]["brand"] == "HONOR")
    check("SDK 按 Android 15 推导", d["profile"]["sdk_version"] == "35")
    check("UA 跟着换了", "PGT-AN00" in d["ua"] and "Android 15" in d["ua"])
    check("标记为自定义", d["custom"] is True)
    check("指纹缓存已清空", d["cache_exists"] is False)
    check("提示要重新扫码", "重新扫码" in (d.get("message") or ""))

    print("\n" + "=" * 64)
    print("4) GET /api/device —— 再次读取（应反映新档案）")
    st, d2 = get("/api/device")
    check("读取到自定义档案", d2["custom"] is True and d2["profile"]["model"] == "PGT-AN00")

    print("\n" + "=" * 64)
    print("5) POST /api/device —— 非法输入")
    st, d3 = post("/api/device", {"text": "这不是 JSON"})
    print("   非 JSON ->", st, d3.get("message"))
    check("返回 400", st == 400)
    st, d4 = post("/api/device", {"text": '{"foo":"bar"}'})
    print("   缺字段 ->", st, d4.get("message"))
    check("缺 deviceModel 被拒", st == 400 and "deviceModel" in (d4.get("message") or ""))

    print("\n" + "=" * 64)
    print("6) POST /api/device/reset —— 恢复默认")
    st, d5 = post("/api/device/reset", {})
    check("返回 ok", d5.get("ok") is True)
    check("回到默认型号", d5["profile"]["model"] == "24122RKC7C")
    check("恢复后仍需重新扫码", "重新扫码" in (d5.get("message") or ""))

    print("\n" + "=" * 64)
    print("7) GET /api/schedule")
    st, s = get("/api/schedule")
    print("   时间点:", s["times"], "| 下次:", s["next_run"],
          "| 守护进程:", s["daemon"]["running"])
    check("返回 ok", s["ok"] is True)
    check("读出原有时间点", isinstance(s["times"], list) and s["times"])
    check("给出下次提醒", bool(s["next_run"]))
    check("带守护进程状态", "running" in s["daemon"])

    print("\n" + "=" * 64)
    print("8) POST /api/schedule —— 增删时间点")
    st, s2 = post("/api/schedule", {"times": ["08:30", "20:30", "08:30"]})
    print("   状态", st, "| 时间点:", s2.get("times"), "| message:", s2.get("message"))
    check("返回 ok", s2.get("ok") is True)
    check("去重后两个", s2["times"] == ["08:30", "20:30"])
    saved = json.loads(CFG.read_text(encoding="utf-8"))
    check("已写入 config.json", saved["app"]["check_times"] == ["08:30", "20:30"])

    st, s3 = post("/api/schedule", {"times": ["25:99"]})
    print("   非法时间 ->", st, s3.get("message"))
    check("非法时间 400", st == 400)
    st, s4 = post("/api/schedule", {"times": []})
    print("   空数组   ->", st, s4.get("message"))
    check("空数组 400", st == 400)
    now_cfg = json.loads(CFG.read_text(encoding="utf-8"))
    check("非法输入未改动配置", now_cfg["app"]["check_times"] == ["08:30", "20:30"])

    print("\n" + "=" * 64)
    print("9) 守护进程 启动 / 状态 / 停止")
    st, d6 = post("/api/daemon/start", {})
    print("   启动 ->", st, d6.get("message"))
    check("启动成功", d6.get("ok") is True and d6["daemon"]["running"] is True)
    check("拿到 PID", bool(d6["daemon"]["pid"]))
    check("下次提醒非空", bool(d6["daemon"]["next_run"]))

    st, s5 = get("/api/schedule")
    check("状态接口显示运行中", s5["daemon"]["running"] is True)

    st, d7 = post("/api/daemon/start", {})
    check("重复启动不报错且提示已在运行", d7.get("ok") is True and "已在运行" in d7.get("message", ""))

    st, d8 = post("/api/daemon/stop", {})
    print("   停止 ->", st, d8.get("message"), "| forced:", d8.get("forced"))
    check("停止成功", d8.get("ok") is True)
    check("停止后不在运行", d8["daemon"]["running"] is False)
    check("优雅退出（非强杀）", d8.get("forced") is False)

    print("\n" + "=" * 64)
    print("10) APK 下载（局域网只读服务）")
    st, dev = get("/api/device")
    apk_url = dev["apk"].get("url", "")
    if apk_url:
        with urllib.request.urlopen(apk_url, timeout=60) as r:
            data = r.read()
        print("   ", apk_url, "->", len(data), "字节")
        check("下载 200", r.status == 200)
        check("大小与文件一致", len(data) == dev["apk"]["size"])
        check("是 APK（zip 头）", data[:2] == b"PK")
        # 越权路径
        bad = apk_url.rsplit("/", 1)[0] + "/../config.json"
        try:
            urllib.request.urlopen(bad, timeout=10)
            blocked = False
        except urllib.error.HTTPError as e:
            blocked = e.code in (403, 404)
        check("阻止 ../ 越权读取", blocked)
    else:
        check("APK 地址可用", False, apk_url)

finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

    # ---------------- 还原 ----------------
    CFG.write_bytes(backup_cfg)
    if fp_backup is not None:
        FP.write_bytes(fp_backup)
    elif FP.exists():
        FP.unlink()
    if profile_backup is not None:
        PROFILE.write_bytes(profile_backup)
    elif PROFILE.exists():
        PROFILE.unlink()
    for name in ("daemon.pid", "daemon.heartbeat", ".stop"):
        (BASE / "logs" / name).unlink(missing_ok=True)
    print("\n已还原 config.json / device_fp.json / device_profile.json")

print("=" * 64)
print(f"结果：通过 {ok_n} 项，失败 {fail_n} 项")
sys.exit(0 if fail_n == 0 else 1)

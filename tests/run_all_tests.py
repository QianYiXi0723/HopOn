# -*- coding: utf-8 -*-
"""测试运行器（2026-11-14 测试优化 #3）：一键全绿 + 失败清单汇总。

用法：
    python tests/run_all_tests.py            全量
    python tests/run_all_tests.py --fast     跳过真实进程烟测与网络型测试

约定：每个测试脚本退出码 0=全过，非 0=有失败；本运行器只汇总，
不吞失败、不重试、不解析 stdout 断言（失败以退出码为准）。
"""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

#: 需要真实网络/真实进程的慢测试（--fast 时跳过）
SLOW = {"rl_test_smoke_e2e.py", "rl_test_update.py", "rl_test_kuro_bind.py"}

#: 已知需要外部条件的脚本（缺 token/需真机）——列名仅作说明，默认仍尝试
NOTES = {
    "rl_test_mirror_sync.py": "双镜像一致性门禁（快）",
    "rl_test_smoke_e2e.py": "真实进程烟测（慢，起子进程服务）",
    "rl_test_kuro_bind.py": "库街区绑定链路（较慢）",
    "rl_test_update.py": "更新通道（走 GitHub/Gitee API）",
}


def main():
    fast = "--fast" in sys.argv
    scripts = sorted(p for p in HERE.glob("rl_test_*.py"))
    results = []
    print("=" * 60)
    print("HopOn 测试运行器  ·  %d 个脚本%s" % (len(scripts), "（--fast）" if fast else ""))
    print("=" * 60)
    for script in scripts:
        if fast and script.name in SLOW:
            results.append((script.name, None, "跳过（--fast）"))
            continue
        t0 = time.time()
        proc = subprocess.run(
            [sys.executable, str(script)], capture_output=True,
            text=True, encoding="utf-8", errors="replace", cwd=str(ROOT),
            timeout=300,
        )
        dt = time.time() - t0
        ok = proc.returncode == 0
        tail = ""
        if not ok:
            tail = (proc.stdout or "").strip().splitlines()[-2:] if (proc.stdout or "").strip() else []
            tail += (proc.stderr or "").strip().splitlines()[-2:]
        results.append((script.name, ok, "%.1fs" % dt, tail))
        mark = "✓" if ok else "✗"
        print("[%s] %-36s %6s  %s" % (mark, script.name, "%.1fs" % dt,
              NOTES.get(script.name, "")))

    print("=" * 60)
    fails = [r for r in results if r[1] is False]
    skips = [r for r in results if r[1] is None]
    print("结果：%d 通过 / %d 失败 / %d 跳过" % (
        len(results) - len(fails) - len(skips), len(fails), len(skips)))
    if fails:
        print("-- 失败清单 --")
        for name, ok, dt, tail in fails:
            print("  ✗ " + name)
            for line in tail or []:
                print("      " + str(line)[:150])
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

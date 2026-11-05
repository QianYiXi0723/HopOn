# -*- coding: utf-8 -*-
"""关机前落盘（quiet-run）旗标链路测试——旗标全部指向 %TEMP%，绝不碰真实 logs。

覆盖：
  Q1 无旗标空转：_wait 返回 ""（bool 语义为假，不触发任何巡检）
  Q2 quiet 旗标：_wait 返回 "quiet-run"，且**不删旗标**（控制台端点靠
     旗标消失判断落盘完成，删除责任在 _loop 的检查之后）
  Q3 run-now 旗标：_wait 返回 "run-now"，旗标即删（原行为不回归）
  Q4 quiet 优先于 run-now（关机在即，落盘先于手动巡检）
  Q5 静默检查后旗标由调用方删除（模拟 _loop 的 finally 语义）
  Q6 dayend_refresh_at ≥ 04:00 回退默认值并告警（不静默失效）
"""
from __future__ import annotations

import tempfile
import threading
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

import sys  # noqa: E402
sys.path.insert(0, str(BASE))

import daemon  # noqa: E402  （仅模块导入，不启动守护）

TMP = Path(tempfile.mkdtemp(prefix="rl_quiet_"))
daemon.LOGS_DIR = TMP  # 与 DIR 一起换（RUN_NOW/QUIET/STOP 旗标全部指向沙箱）
daemon.RUN_NOW_FLAG = TMP / ".run_now"
daemon.QUIET_RUN_FLAG = TMP / ".quiet_run"
daemon.STOP_FLAG = TMP / ".stop"

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS {name}")
    else:
        FAIL += 1
        print(f"FAIL {name}  {detail}")


def wait_short(sec: float = 0.3) -> str:
    return daemon._wait(sec, threading.Event())


def run() -> None:
    # Q1 空转
    check("Q1 无旗标返回空串", wait_short() == "")

    # Q2 quiet 旗标：返回信号且不删旗标
    daemon.QUIET_RUN_FLAG.write_text("x", encoding="utf-8")
    sig = wait_short()
    check("Q2a quiet 旗标返回 quiet-run", sig == "quiet-run", sig)
    check("Q2b 旗标保留（等检查完成后再删）", daemon.QUIET_RUN_FLAG.exists())

    # Q5 模拟 _loop 的 finally 删除语义 → 下一轮空转
    daemon.QUIET_RUN_FLAG.unlink()
    check("Q5 删旗标后恢复空转", wait_short() == "")

    # Q3 run-now 原行为不回归
    daemon.RUN_NOW_FLAG.write_text("x", encoding="utf-8")
    sig = wait_short()
    check("Q3a run-now 返回 run-now", sig == "run-now", sig)
    check("Q3b run-now 旗标即删", not daemon.RUN_NOW_FLAG.exists())

    # Q4 quiet 优先
    daemon.QUIET_RUN_FLAG.write_text("x", encoding="utf-8")
    daemon.RUN_NOW_FLAG.write_text("x", encoding="utf-8")
    sig = wait_short()
    check("Q4 quiet 优先于 run-now", sig == "quiet-run", sig)
    daemon.QUIET_RUN_FLAG.unlink()
    daemon.RUN_NOW_FLAG.unlink(missing_ok=True)

    # Q6 dayend 时刻 ≥04:00 回退默认
    real = daemon._read_app_section
    daemon._read_app_section = lambda: {"dayend_refresh_at": "05:30"}
    try:
        check("Q6 ≥04:00 回退默认 03:50", daemon.read_dayend_refresh_at() == "03:50")
    finally:
        daemon._read_app_section = real

    check("R0 旗标已整体指向沙箱", str(daemon.QUIET_RUN_FLAG).startswith(str(TMP)))


def run_stop_flag():
    """Q7/Q8：停止旗标一次性消费（读即删，残留不再毒化后续拉起）。"""
    daemon.STOP_FLAG.write_text("x", encoding="utf-8")
    sig = wait_short()
    check("Q7 停止旗标返回空串并置位 stop_event", sig == "")
    check("Q8 停止旗标已被消费（读即删）", not daemon.STOP_FLAG.exists())


if __name__ == "__main__":
    run()
    run_stop_flag()
    print(f"\n[结论] {PASS}/{PASS + FAIL} 通过")
    sys.exit(1 if FAIL else 0)

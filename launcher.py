# -*- coding: utf-8 -*-
"""素月无感冷启动 launcher（后台常驻监督进程）。

用法：
    python launcher.py                                # 默认拉起 webui/server.py（Web 界面）
    python launcher.py --entry main.py                # 指定入口（CLI 交互模式）
    python launcher.py --entry main.py --task "..."   # --entry 之后的参数透传给入口

退出码语义（2026-09-20 修订）：
- 77  = “代码已更新，请求重启” → 立即重新拉起（受窗口熔断约束）。
        见 webui/server.py 的 _reload_watcher：os._exit(77) 是请求重启的信号，不是结束。
- 0   = 正常结束（托盘退出等主动关闭）→ launcher 一并退出。
- 其他 = 异常结束（被外部 kill / 崩溃 / 未预期退出）→ 落一条 crash 记录，
        并在预算内自动重新拉起。
        【修订原因】原实现此处直接退出：2026-09-20 一天内素月被外部以退出码
        4294967295（即 0xFFFFFFFF，用户态 TerminateProcess 的特征码）掐掉 5 次
        （14:09 / 15:59 / 16:16 / 18:29 / 19:41），System 与 Application 事件日志
        全无记录、agent.log 无 traceback、cmd.log 无命令活动，即空闲期被静默杀死。
        launcher 不拉起 → 素月一直下线，直到人工发现（19:41 那次隔了 14 分钟）。

异常重启的护栏（防重启风暴 / 防紧循环）：
- 窗口熔断：RESTART_WINDOW 秒内重启超过 MAX_RESTARTS 次 → 停止拉起。
- 连续异常：连续 MAX_CRASH_STREAK 次异常重启 → 停止拉起；
  某次存活超过 STABLE_SECONDS 视为已恢复稳定，连续计数归零。
  （故“每隔几十分钟被外部杀一次”这类复发型 kill 能持续自愈，
    而“起来就挂”的紧循环会被立刻拦住。）

崩溃记录：data/crash_log.jsonl，一行一条 JSON（时间 / 退出码 / 存活秒数 / 尝试序号）。
pythonw 下无控制台，logs/launcher.log 与 data/crash_log.jsonl 是唯一观测口。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

RELOAD_CODE = 77
MAX_RESTARTS = 5            # 窗口内最大重启次数（熔断阈值，77 与异常退出共用）
RESTART_WINDOW = 600.0      # 熔断统计窗口（秒）
MAX_CRASH_STREAK = 4        # 连续异常退出上限（超过则停止拉起，留待人工介入）
STABLE_SECONDS = 180.0      # 单次存活超过此值 → 视为已稳定，连续异常计数归零
CRASH_RESTART_DELAY = 2.0   # 异常重启前的短暂等待（防抖）
DEFAULT_ENTRY = "webui/server.py"
ROOT = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(ROOT, "logs", "launcher.log")
CRASH_LOG = os.path.join(ROOT, "data", "crash_log.jsonl")
STOP_FLAG = os.path.join(ROOT, "data", "stop.flag")
MUTEX_NAME = "Global\\SuyueLauncherMutex"      # 单实例互斥（防重复双击/多拉起者抢端口）
ENTRY_STDERR_LOG = os.path.join(ROOT, "logs", "entry_stderr.log")


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except Exception:  # noqa: BLE001  pythonw 下 stdout 可能为 None
        pass
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def record_crash(rc: int, uptime: float, attempt: int) -> None:
    """异常退出落一条机器可读记录，供事后归因与启动时提醒。"""
    rec = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "exit_code": rc,
        "exit_code_hex": "0x%08X" % (rc & 0xFFFFFFFF),
        "uptime_sec": round(uptime, 1),
        "attempt": attempt,
        "stderr_log": "logs/entry_stderr.log",   # 崩溃原因可查（launcher 已捕获子进程 stderr）
    }
    try:
        os.makedirs(os.path.dirname(CRASH_LOG), exist_ok=True)
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def already_running() -> bool:
    """单实例互斥：Windows 命名互斥锁。已有 launcher 在跑 → 本进程直接退出。

    2026-09-23 修复：重复双击「启动.bat」/ watchdog 同时拉起 → 多个 launcher
    抢一个 8765 端口 → bind 失败循环崩溃。互斥锁保证全系统同一时刻只有一个 launcher。"""
    try:
        import ctypes
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        err = ctypes.windll.kernel32.GetLastError()
        if err == 183:  # ERROR_ALREADY_EXISTS
            log("检测到已有 launcher 实例在运行，本进程退出（单实例互斥）。")
            return True
    except Exception as e:  # noqa: BLE001  非 Windows / 无 ctypes → 退回端口检查
        log(f"互斥锁不可用（{e}），退回端口检查。")
        try:
            import socket
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sck:
                sck.settimeout(0.5)
                if sck.connect_ex(("127.0.0.1", 8765)) == 0:
                    log("检测到素月 WebUI 已在运行（8765 在听），本进程退出。")
                    return True
        except Exception:  # noqa: BLE001
            pass
    return False


def run_entry(entry: str, rest: list, err_log: str) -> int:
    """启动入口进程：stderr 实时落 logs/entry_stderr.log（崩溃原因可查）。

    2026-09-23 修复：此前 subprocess.call 不捕获子进程 stderr，crash_log 只有
    exit_code 没有原因——崩溃只能现场推理。现在 traceback 完整落日志文件。"""
    try:
        errf = open(err_log, "a", encoding="utf-8")
    except OSError:
        errf = None
    if errf is not None:
        errf.write("\n===== %s 启动 %s =====\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), os.path.basename(entry)))
        errf.flush()
    try:
        return subprocess.call([sys.executable, entry] + rest, cwd=ROOT,
                               stdout=subprocess.DEVNULL, stderr=errf)
    finally:
        if errf is not None:
            errf.close()


def parse_args(argv):
    """拆出 --entry，其余原样透传给入口。"""
    entry = DEFAULT_ENTRY
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] == "--entry" and i + 1 < len(argv):
            entry = argv[i + 1]
            i += 2
            continue
        rest.append(argv[i])
        i += 1
    if not os.path.isabs(entry):
        entry = os.path.join(ROOT, entry)
    return entry, rest


def main() -> int:
    entry, rest = parse_args(sys.argv[1:])
    if already_running():
        return 0
    if not os.path.exists(entry):
        log(f"入口不存在：{entry}")
        return 2
    rel = os.path.relpath(entry, ROOT)
    stamps = []
    attempt = 0
    crash_streak = 0

    def restart_budget_ok() -> bool:
        """窗口熔断：RESTART_WINDOW 秒内重启次数超限则拒绝继续拉起。"""
        now = time.time()
        stamps[:] = [t for t in stamps if now - t < RESTART_WINDOW] + [now]
        if len(stamps) > MAX_RESTARTS:
            log(f"熔断：{RESTART_WINDOW:.0f}s 内已重启 {len(stamps)} 次，疑似重启循环，停止拉起。")
            return False
        return True

    while True:
        attempt += 1
        log(f"启动素月（第 {attempt} 次拉起，入口 {rel}）")
        started = time.time()
        try:
            rc = run_entry(entry, rest, ENTRY_STDERR_LOG)
        except Exception as e:  # noqa: BLE001
            log(f"拉起失败：{type(e).__name__}: {e}")
            return 3
        uptime = time.time() - started

        if rc == 0:
            log("素月主动结束（退出码 0），launcher 结束。")
            return 0

        if rc == RELOAD_CODE:
            if not restart_budget_ok():
                return 1
            log("检测到代码更新，后台自动重启（无感冷启动）...")
            continue

        # 异常退出：外部 kill / 崩溃 / 未预期退出
        code = rc & 0xFFFFFFFF
        record_crash(rc, uptime, attempt)
        log(f"警告：素月异常退出（退出码 {rc} / 0x{code:08X}，存活 {uptime:.0f}s）"
            f"—— 已记入 data/crash_log.jsonl")
        if uptime >= STABLE_SECONDS:
            crash_streak = 0
        crash_streak += 1
        if crash_streak > MAX_CRASH_STREAK:
            log(f"连续异常退出 {crash_streak} 次，疑似起来即挂，停止拉起（留待人工介入）。")
            return 4
        if not restart_budget_ok():
            return 1
        log(f"异常退出自动拉起（连续异常 {crash_streak}/{MAX_CRASH_STREAK}）...")
        time.sleep(CRASH_RESTART_DELAY)


if __name__ == "__main__":
    sys.exit(main())

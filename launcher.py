# -*- coding: utf-8 -*-
"""素月无感冷启动 launcher（后台常驻监督进程）。

用法：
    python launcher.py                                # 默认拉起 webui/server.py（Web 界面）
    python launcher.py --entry main.py                # 指定入口（CLI 交互模式）
    python launcher.py --entry main.py --task "..."   # --entry 之后的参数透传给入口

机制：
- 拉起素月入口进程，工作目录固定为项目根。
- 入口退出码 77 = "代码已更新，请求重启" → 立即重新拉起（新代码生效，前端最多卡一下）。
- 其他退出码 = 正常结束（含托盘退出、崩溃）→ launcher 一并退出。
- 熔断：RESTART_WINDOW 秒内连续重启超过 MAX_RESTARTS 次 → 停止拉起并落日志（防无限重启循环）。
- 日志：logs/launcher.log（pythonw 下无控制台，日志文件是唯一的观测口）。

注意：本进程通常由 pythonw.exe 拉起（无控制台），因此所有输出都落日志文件，
print 失败时静默跳过，不影响主流程。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

RELOAD_CODE = 77
MAX_RESTARTS = 5          # 窗口内最大重启次数（熔断阈值）
RESTART_WINDOW = 60.0     # 熔断统计窗口（秒）
DEFAULT_ENTRY = "webui/server.py"
ROOT = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(ROOT, "logs", "launcher.log")


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
    if not os.path.exists(entry):
        log(f"入口不存在：{entry}")
        return 2
    rel = os.path.relpath(entry, ROOT)
    stamps = []
    attempt = 0
    while True:
        attempt += 1
        log(f"启动素月（第 {attempt} 次拉起，入口 {rel}）")
        try:
            rc = subprocess.call([sys.executable, entry] + rest, cwd=ROOT)
        except Exception as e:  # noqa: BLE001
            log(f"拉起失败：{type(e).__name__}: {e}")
            return 3
        if rc == RELOAD_CODE:
            now = time.time()
            stamps = [t for t in stamps if now - t < RESTART_WINDOW] + [now]
            if len(stamps) > MAX_RESTARTS:
                log(f"熔断：{RESTART_WINDOW:.0f}s 内已重启 {len(stamps)} 次，疑似重启循环，停止拉起。")
                return 1
            log("检测到代码更新，后台自动重启（无感冷启动）...")
            continue
        log(f"素月退出（退出码 {rc}），launcher 结束。")
        return rc


if __name__ == "__main__":
    sys.exit(main())

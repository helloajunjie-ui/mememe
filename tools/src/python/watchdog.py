# -*- coding: utf-8 -*-
"""watchdog: 每分钟检查素月是否在跑，没在跑且没有 stop.flag 就唤起守护者。

统一拉起逻辑（2026-10-13 修订）：
    旧版直接拉起 launcher.py，导致进程树归属混乱、与托盘启动器抢拉起。
    新版只负责"唤起"：若素月未运行 → 若托盘启动器不在 → 启动托盘启动器；
    托盘启动器常驻后会自行拉起 launcher（其内部自带单例清理与优雅接管）。
    这样所有素月进程都挂在唯一托盘启动器之下，保证单例与图标唯一。
"""
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PYW = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")
TRAY = os.path.join(ROOT, "素月启动器.pyw")
LAUNCHER = os.path.join(ROOT, "launcher.py")
STOP_FLAG = os.path.join(ROOT, "data", "stop.flag")
LOG = os.path.join(ROOT, "logs", "watchdog.log")
PORT = 8765  # WebUI 端口，在监听 = 素月活着


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def port_listening(port):
    """检查端口是否在监听"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False


def tray_running():
    """托盘启动器是否在跑（按命令行匹配，避免拉起第二个托盘）。"""
    ps = (
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'pythonw.exe' "
        "-and $_.CommandLine -like '*素月启动器.pyw*' } "
        "| Select-Object -ExpandProperty ProcessId"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, timeout=15,
            creationflags=0x08000000,
        )
        text = out.stdout.decode("utf-8", errors="ignore")
        return any(x.strip().isdigit() for x in text.splitlines())
    except Exception:
        return False


def main():
    # 有 stop.flag = 用户主动关闭，不拉起
    if os.path.exists(STOP_FLAG):
        log("stop.flag exists, user stopped, skip.")
        return 0

    # 端口在监听 = 素月在跑
    if port_listening(PORT):
        return 0

    # 端口不在监听：唤起托盘启动器（唯一守护者），由它负责拉起 launcher
    if tray_running():
        log(f"port {PORT} not listening but tray alive, skip (tray will handle).")
        return 0
    log(f"port {PORT} not listening and no tray, starting tray launcher.")
    subprocess.Popen([PYW, TRAY], cwd=ROOT,
                     creationflags=0x08000000)  # CREATE_NO_WINDOW
    return 0


if __name__ == "__main__":
    sys.exit(main())

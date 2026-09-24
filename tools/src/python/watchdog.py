# -*- coding: utf-8 -*-
"""watchdog: 每分钟检查 launcher 是否在跑，没在跑且没有 stop.flag 就拉起"""
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PYW = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")
LAUNCHER = os.path.join(ROOT, "launcher.py")
STOP_FLAG = os.path.join(ROOT, "data", "stop.flag")
LOG = os.path.join(ROOT, "logs", "watchdog.log")
PORT = 8765  # WebUI 端口，在监听 = launcher 活着


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


def main():
    # 有 stop.flag = 用户主动关闭，不拉起
    if os.path.exists(STOP_FLAG):
        log("stop.flag exists, user stopped, skip.")
        return 0

    # 端口在监听 = launcher 在跑
    if port_listening(PORT):
        return 0

    # 端口不在监听，拉起
    log(f"port {PORT} not listening, restarting launcher.")
    subprocess.Popen([PYW, LAUNCHER], cwd=ROOT,
                     creationflags=0x08000000)  # CREATE_NO_WINDOW
    return 0


if __name__ == "__main__":
    sys.exit(main())

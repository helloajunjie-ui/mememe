"""内置工具：cpu_usage —— 返回当前 CPU 占用率百分比。"""
from __future__ import annotations

import platform
import re
import subprocess

from tools.base import tool


@tool(
    "cpu_usage",
    "返回当前 CPU 占用率百分比（0~100）。Windows 走 wmic，Linux 走 /proc/stat。",
    {
        "type": "object",
        "properties": {},
        "required": [],
    },
)
def run() -> dict:
    system = platform.system()
    try:
        if system == "Windows":
            out = subprocess.run(
                ["wmic", "cpu", "get", "loadpercentage"],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
            m = re.search(r"(\d+)", out)
            if not m:
                return {"ok": False, "error": "无法解析 wmic 输出"}
            return {"ok": True, "cpu_percent": int(m.group(1)), "source": "wmic"}
        elif system == "Linux":
            usage = _linux_cpu_percent()
            return {"ok": True, "cpu_percent": usage, "source": "/proc/stat"}
        else:
            return {"ok": False, "error": f"暂不支持平台 {system}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _linux_cpu_percent() -> float:
    """基于两次采样 /proc/stat 的 CPU 占用率。"""
    def _read():
        with open("/proc/stat", "r") as f:
            parts = f.readline().split()
        vals = [int(x) for x in parts[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals)
        return idle, total

    idle0, total0 = _read()
    import time
    time.sleep(0.2)
    idle1, total1 = _read()
    dtotal = total1 - total0
    if dtotal <= 0:
        return 0.0
    didle = idle1 - idle0
    return round(100.0 * (1.0 - didle / dtotal), 1)

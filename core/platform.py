"""平台适配层：探测平台、按平台分发原生命令。

设计意图（见设计文档 4.4）：
- 白绫运行在真实电脑上，环境可能是 Windows / Linux / macOS。
- 屏蔽命令差异：执行始终走该平台的原生命令。
- 效率优先：原生命令 > 内置工具 > 安装依赖。
"""
from __future__ import annotations

import platform
import subprocess
from typing import Dict, List, Optional


def detect() -> Dict[str, str]:
    """探测并返回平台信息。"""
    system = platform.system().lower()  # windows / linux / darwin
    family_map = {
        "windows": "windows",
        "linux": "linux",
        "darwin": "darwin",
    }
    family = family_map.get(system, system)
    return {
        "family": family,
        "system": system,
        "version": platform.version(),
        "release": platform.release(),
        "arch": platform.machine(),
        "shell": "powershell" if family == "windows" else "bash",
        "hostname": platform.node(),
    }


def native_commands(family: str) -> Dict[str, str]:
    """返回该平台的原生命令模板表（效率优先的参考）。"""
    if family == "windows":
        return {
            "list_dir": "Get-ChildItem -Force {path} | Select-Object Mode,Length,Name | Format-Table -AutoSize",
            "read_file": "Get-Content -Path {path} -Encoding UTF8",
            "file_stat": "Get-Item -Path {path} | Select-Object FullName,Length,LastWriteTime | Format-List",
            "net_check": "Test-NetConnection -ComputerName {host} -Port {port} -InformationLevel Quiet",
            "processes": "Get-Process | Select-Object Id,ProcessName,CPU | Format-Table -AutoSize",
            "mem": "Get-CIMInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize,FreePhysicalMemory | Format-List",
            "disk": "Get-PSDrive -PSProvider FileSystem | Select-Object Name,Used,Free | Format-Table -AutoSize",
            "env_get": "Get-ChildItem Env:{name} -ErrorAction SilentlyContinue",
        }
    # Linux / macOS (bash)
    return {
        "list_dir": "ls -la {path}",
        "read_file": "cat {path}",
        "file_stat": "stat {path}",
        "net_check": "ping -c 1 -W 2 {host} >/dev/null 2>&1 && echo reachable || echo unreachable",
        "processes": "ps aux",
        "mem": "free -h",
        "disk": "df -h",
        "env_get": "echo ${" + "{name}" + ":-unset}",
    }


def run_shell(command: str, timeout: int = 30, cwd: Optional[str] = None) -> Dict:
    """在当前平台执行 shell 命令。

    返回 {"ok": bool, "stdout": str, "stderr": str, "exit_code": int}
    Windows 用 powershell，Linux/macOS 用 bash。
    """
    info = detect()
    if info["family"] == "windows":
        cmd_list = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    else:
        cmd_list = ["bash", "-c", command]
    try:
        proc = subprocess.run(
            cmd_list,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        return {
            "ok": proc.returncode == 0,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": f"命令超时（>{timeout}s）", "exit_code": -1}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "stdout": "", "stderr": f"命令执行异常: {e}", "exit_code": -1}


def env_get(name: str) -> Optional[str]:
    """跨平台读环境变量（优先系统原生命令）。"""
    import os

    val = os.environ.get(name)
    return val


if __name__ == "__main__":
    import json

    print(json.dumps(detect(), ensure_ascii=False, indent=2))

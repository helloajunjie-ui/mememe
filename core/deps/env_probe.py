"""环境画像探测（env_probe）：生成 env_profile.json。

设计意图（见设计文档 4.8.1）：
- 机器配置快照，是可行性判定的依据。
- 启动时探测，依赖安装前强制刷新（不用过期数据）。
- 无 GPU 如实写 null，不编造。
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from typing import Dict, Optional

from core import platform as plat


def probe_python() -> Dict:
    return {
        "version": platform.python_version(),
        "executable": sys.executable,
        "pip": shutil.which("pip") is not None,
    }


def probe_go() -> Dict:
    go = shutil.which("go")
    if not go:
        return {"installed": False, "version": None}
    try:
        out = subprocess.run(
            [go, "version"], capture_output=True, text=True, timeout=10
        ).stdout.strip()
        return {"installed": True, "version": out}
    except Exception:  # noqa: BLE001
        return {"installed": False, "version": None, "error": "go 版本读取失败"}


def probe_memory_windows() -> Optional[Dict]:
    """Windows 下用系统原生命令读内存（效率优先）。"""
    info = plat.detect()
    if info["family"] != "windows":
        return None
    res = plat.run_shell(
        "Get-CIMInstance Win32_OperatingSystem | "
        "Select-Object @{n='total';e={[math]::Round($_.TotalVisibleMemorySize/1MB,1)}},"
        "@{n='free';e={[math]::Round($_.FreePhysicalMemory/1MB,1)}} | ConvertTo-Json"
    )
    if not res["ok"]:
        return None
    try:
        data = json.loads(res["stdout"].strip())
        return {
            "total_gb": float(data.get("total") or 0),
            "free_gb": float(data.get("free") or 0),
        }
    except Exception:  # noqa: BLE001
        return None


def probe_memory() -> Dict:
    """内存探测：Windows 用原生命令，其他平台尽力而为。"""
    win = probe_memory_windows()
    if win:
        return win
    # Linux/macOS 尝试 free
    res = plat.run_shell("free -g 2>/dev/null | awk 'NR==2{print $2\" \"$4}'")
    if res["ok"] and res["stdout"].strip():
        try:
            total, free = res["stdout"].split()
            return {"total_gb": float(total), "free_gb": float(free)}
        except Exception:  # noqa: BLE001
            pass
    return {"total_gb": None, "free_gb": None}


def probe_network() -> Dict:
    """网络连通性自检：包仓库可达性（快速、超时短）。"""
    import httpx

    result = {"pypi": False, "detail": ""}
    try:
        r = httpx.get("https://pypi.org/simple/", timeout=5, follow_redirects=True)
        result["pypi"] = r.status_code < 500
        result["detail"] = f"pypi http {r.status_code}"
    except Exception as e:  # noqa: BLE001
        result["detail"] = f"pypi 不可达: {e}"
    return result


def full_probe() -> Dict:
    """完整环境探测。"""
    osinfo = plat.detect()
    cpu = {
        "cores": os.cpu_count() or 0,
        "processor": platform.processor() or "unknown",
    }
    try:
        disk_total, disk_free, _ = shutil.disk_usage(os.getcwd())
        disk = {"total_gb": round(disk_total / 2**30, 1), "free_gb": round(disk_free / 2**30, 1)}
    except Exception:  # noqa: BLE001
        disk = {"total_gb": None, "free_gb": None}

    profile = {
        "probed_at": __import__("datetime").datetime.now().isoformat(),
        "os": {
            "family": osinfo["family"],
            "system": osinfo["system"],
            "version": osinfo["version"],
            "release": osinfo["release"],
            "shell": osinfo["shell"],
        },
        "arch": osinfo["arch"],
        "cpu_cores": cpu["cores"],
        "cpu_processor": cpu["processor"],
        "memory_gb": probe_memory(),
        "disk_gb": disk,
        "python": probe_python(),
        "go": probe_go(),
        "gpu": None,  # 如实：当前不探测 GPU，写 null
        "network": probe_network(),
    }
    return profile


def save(profile: Dict, path: str = "data/env_profile.json") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)
    return path


def load(path: str = "data/env_profile.json") -> Optional[Dict]:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def refresh(path: str = "data/env_profile.json") -> Dict:
    """刷新环境画像（依赖安装前强制调用）。"""
    profile = full_probe()
    save(profile, path)
    return profile


if __name__ == "__main__":
    import json as _json

    print(_json.dumps(full_probe(), ensure_ascii=False, indent=2))

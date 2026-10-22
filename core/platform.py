"""平台适配层：探测平台、按平台分发原生命令。

设计意图（见设计文档 4.4）：
- 素月运行在真实电脑上，环境可能是 Windows / Linux / macOS。
- 屏蔽命令差异：执行始终走该平台的原生命令。
- 效率优先：原生命令 > 内置工具 > 安装依赖。

2026-09-20 修复：run_shell 旧版用 subprocess.run(capture_output=True) 走 pipe。
若命令启动 daemon 子进程（如 bsk.exe start / Start-Process 服务），daemon 继承
pipe write end，communicate() 永久 hang，timeout 形同虚设。
现改为：stdout/stderr 重定向临时文件（daemon 继承文件句柄不影响 wait），
wait(timeout) 到点 taskkill /F /T 杀整树。
"""
from __future__ import annotations

import os
import platform as _pyplat
import subprocess
import tempfile
from typing import Dict, Optional


def detect() -> Dict[str, str]:
    """探测并返回平台信息。"""
    system = _pyplat.system().lower()
    family_map = {
        "windows": "windows",
        "linux": "linux",
        "darwin": "darwin",
    }
    family = family_map.get(system, system)
    return {
        "family": family,
        "system": system,
        "version": _pyplat.version(),
        "release": _pyplat.release(),
        "arch": _pyplat.machine(),
        "shell": "powershell" if family == "windows" else "bash",
        "hostname": _pyplat.node(),
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
            "mem": "Get-CIMInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize,FreePhysicalMemory | Format-Table",
            "disk": "Get-PSDrive -PSProvider FileSystem | Select-Object Name,Used,Free | Format-Table -AutoSize",
            "env_get": "Get-ChildItem Env:{name} -ErrorAction SilentlyContinue",
        }
    return {
        "list_dir": "ls -la {path}",
        "read_file": "cat {path}",
        "file_stat": "stat {path}",
        "net_check": "ping -c 1 -W 2 {host} >/dev/null 2>&1 && echo reachable || echo unreachable",
        "processes": "ps aux",
        "mem": "free -h",
        "disk": "df -h",
        "env_get": "echo ${" + "{name}:-unset",
    }


def _decode_output(b: bytes) -> str:
    """子进程输出字节 → 文本：多编码回退（中文 Windows 常见 GBK/CP936，也兼容 UTF-8）。"""
    if not b:
        return ""
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


def run_shell(command: str, timeout: int = 30, cwd: Optional[str] = None,
              cancel_event=None) -> Dict:
    """在当前平台执行 shell 命令。

    返回 {"ok": bool, "stdout": str, "stderr": str, "exit_code": int}
    Windows 用 powershell，Linux/macOS 用 bash。

    2026-09-20：stdout/stderr 走临时文件重定向，避免 daemon 继承 pipe 导致 hang；
    超时后 taskkill /F /T 杀整个进程树。
    """
    info = detect()
    if info["family"] == "windows":
        cmd_list = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    else:
        cmd_list = ["bash", "-c", command]

    out_path = tempfile.mktemp(prefix="bail_sh_", suffix=".out")
    err_path = tempfile.mktemp(prefix="bail_sh_", suffix=".err")
    out_fp = err_fp = None
    try:
        out_fp = open(out_path, "wb")
        err_fp = open(err_path, "wb")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if info["family"] == "windows" else 0
        try:
            proc = subprocess.Popen(cmd_list, stdout=out_fp, stderr=err_fp,
                                    cwd=cwd, creationflags=flags)
        except OSError as e:
            return {"ok": False, "stdout": "", "stderr": f"命令执行异常: {e}", "exit_code": -1}
        def _kill_tree() -> None:
            if info["family"] == "windows":
                try:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                   capture_output=True, timeout=5)
                    return
                except Exception:  # noqa: BLE001
                    pass
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

        def _partial() -> bytes:
            try:
                with open(out_path, "rb") as f:
                    return f.read()
            except OSError:
                return b""

        # 2026-10-22：改成 0.3s 轮询——既保留超时杀树，又能在用户点"停止"时立刻结束，
        # 不用干等到 timeout（实测取消一条 20s 的命令会白等 20s）。
        import time as _time
        t0 = _time.time()
        while True:
            try:
                rc = proc.wait(timeout=0.3)
                break
            except subprocess.TimeoutExpired:
                if cancel_event is not None and cancel_event.is_set():
                    _kill_tree()
                    return {"ok": False, "stdout": _decode_output(_partial())[-4000:],
                            "stderr": "命令被用户终止（cancel）", "exit_code": -1,
                            "_cancelled": True}
                if _time.time() - t0 > timeout:
                    _kill_tree()
                    # 超时也把已经产生的输出带回去——"卡住但跑了一半"的排查全靠这半截；
                    # 旧版直接丢空串，然后 finally 连文件都删了。
                    try:
                        with open(err_path, "rb") as f:
                            partial_err = f.read()
                    except OSError:
                        partial_err = b""
                    tail_out = _decode_output(_partial())[-4000:]
                    tail_err = _decode_output(partial_err)[-2000:]
                    return {"ok": False,
                            "stdout": tail_out,
                            "stderr": (f"命令超时（>{timeout}s），已杀进程树"
                                       + ("；stderr: " + tail_err if tail_err.strip() else "")),
                            "exit_code": -1, "timed_out": True}
        try:
            with open(out_path, "rb") as f: out = f.read()
            with open(err_path, "rb") as f: err = f.read()
        except OSError:
            out, err = b"", b""
        return {
            "ok": rc == 0,
            "stdout": _decode_output(out),
            "stderr": _decode_output(err),
            "exit_code": rc,
        }
    finally:
        for fp in (out_fp, err_fp):
            try:
                if fp: fp.close()
            except Exception:
                pass
        for p in (out_path, err_path):
            try:
                if os.path.exists(p): os.remove(p)
            except OSError:
                pass


def env_get(name: str) -> Optional[str]:
    """跨平台读环境变量（优先系统原生命令）。"""
    return os.environ.get(name)


if __name__ == "__main__":
    import json
    print(json.dumps(detect(), ensure_ascii=False, indent=2))

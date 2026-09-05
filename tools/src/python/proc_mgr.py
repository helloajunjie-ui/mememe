"""内置工具：proc_mgr —— 进程管理（列表 / 结束卡死进程，跨平台）。

设计意图（补 cmd_run 黑名单缺口）：
- cmd_run 的安全策略拦截了 taskkill /f 等破坏性命令，导致白绫无法处理卡死/失控进程。
- 本工具提供受控的进程结束通道：proc_list 只读查询；proc_kill 需 confirm="KILL" 二次确认。
- 跨平台：Windows→tasklist/taskkill；Linux/macOS→ps/kill(pkill)。

安全边界（如实）：
- proc_kill 禁止结束自身进程（当前 python 解释器）与系统关键进程（pid 1 / System / Idle）。
- 结束进程是破坏性操作，默认温和结束（Windows 无 /F、POSIX 无 -9），force=True 才强杀。
- 进程名匹配为精确匹配（Windows 忽略大小写），避免误杀同名无关进程。
"""
from __future__ import annotations

import os
import platform as _platform
import subprocess

from tools.base import tool


def _family() -> str:
    system = _platform.system().lower()
    if system in ("windows", "linux", "darwin"):
        return system
    return "other"


def _run(cmd: list, timeout: float = 15) -> tuple:
    """执行命令，返回 (ok, stdout, stderr)。"""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return proc.returncode == 0, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return False, "", "执行超时"
    except Exception as e:  # noqa: BLE001
        return False, "", f"执行异常: {e}"


def _self_pid() -> int:
    try:
        return os.getpid()
    except Exception:  # noqa: BLE001
        return -1


# 系统关键进程名（禁止结束）
_CRITICAL_NAMES = {
    "system", "system idle process", "idle", "wininit", "winlogon",
    "services", "lsass", "csrss", "smss", "explorer", "taskmgr",
    "init", "systemd", "launchd", "kernel_task",
}


@tool(
    "proc_list",
    "列出当前运行的进程（可按名称关键词过滤，只读）",
    {
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "按进程名包含的关键词过滤（可选，空则列出全部）"},
            "max_results": {"type": "number", "description": "最多返回条数，默认 50"},
        },
        "required": [],
    },
)
def proc_list(keyword: str = "", max_results: int = 50) -> dict:
    fam = _family()
    limit = int(max_results or 50)
    kw = (keyword or "").strip().lower()
    try:
        if fam == "windows":
            ok, out, err = _run(["tasklist", "/FO", "CSV", "/NH"])
            if not ok:
                return {"ok": False, "error": f"tasklist 失败: {err}"}
            procs = []
            for line in out.splitlines():
                line = line.strip()
                if not line:
                    continue
                # CSV: "name","pid","session","session#","mem"
                parts = line.split('","')
                if len(parts) < 2:
                    continue
                name = parts[0].strip('"')
                try:
                    pid = int(parts[1].strip('"'))
                except ValueError:
                    continue
                procs.append({"pid": pid, "name": name})
        else:
            ok, out, err = _run(["ps", "-eo", "pid,comm"])
            if not ok:
                return {"ok": False, "error": f"ps 失败: {err}"}
            procs = []
            for line in out.splitlines()[1:]:  # 跳过表头
                line = line.strip()
                if not line:
                    continue
                parts = line.split(None, 1)
                if len(parts) < 2:
                    continue
                try:
                    pid = int(parts[0])
                except ValueError:
                    continue
                procs.append({"pid": pid, "name": parts[1].strip()})

        if kw:
            procs = [p for p in procs if kw in p["name"].lower()]
        procs.sort(key=lambda p: p["name"].lower())
        total = len(procs)
        procs = procs[:limit]
        return {"ok": True, "family": fam, "total": total, "count": len(procs), "processes": procs}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"列进程失败: {e}"}


@tool(
    "proc_kill",
    "结束进程（需 confirm=\"KILL\" 二次确认；禁止结束自身与系统关键进程）",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "进程 PID（数字）或进程名（如 notepad.exe / chrome）"},
            "confirm": {"type": "string", "description": "二次确认口令，必须为 KILL 才会执行"},
            "force": {"type": "boolean", "description": "是否强制结束（Windows /F、POSIX -9），默认 false 温和结束"},
        },
        "required": ["target", "confirm"],
    },
)
def proc_kill(target: str, confirm: str = "", force: bool = False) -> dict:
    if confirm != "KILL":
        return {"ok": False, "error": "未确认结束进程：请传入 confirm=\"KILL\" 以二次确认"}
    t = (target or "").strip()
    if not t:
        return {"ok": False, "error": "target 不能为空（PID 或进程名）"}
    fam = _family()

    # 解析 target：纯数字视为 PID，否则视为进程名
    is_pid = t.isdigit()
    pid = int(t) if is_pid else None

    # 防误杀：自身进程
    self_pid = _self_pid()
    if is_pid and pid == self_pid:
        return {"ok": False, "error": f"禁止结束自身进程（PID {pid}）"}

    # 防误杀：系统关键进程
    if not is_pid:
        name_low = t.lower()
        # 去掉常见扩展名后比对
        base = name_low.split(".")[0] if "." in name_low else name_low
        if name_low in _CRITICAL_NAMES or base in _CRITICAL_NAMES:
            return {"ok": False, "error": f"禁止结束系统关键进程: {t}"}

    try:
        if fam == "windows":
            if is_pid:
                cmd = ["taskkill", "/PID", str(pid)]
            else:
                cmd = ["taskkill", "/IM", t]
            if force:
                cmd.append("/F")
            ok, out, err = _run(cmd)
            if not ok:
                return {"ok": False, "error": f"结束进程失败: {err or out}"}
            return {"ok": True, "action": "kill", "target": t, "force": force, "detail": (out or err).strip()}
        else:
            # POSIX：PID 用 kill；进程名用 pkill
            if is_pid:
                if pid == 1:
                    return {"ok": False, "error": "禁止结束 PID 1（init/systemd）"}
                cmd = ["kill", "-9" if force else "-TERM", str(pid)]
            else:
                cmd = ["pkill", "-9" if force else "-TERM", "-x", t]
            ok, out, err = _run(cmd)
            if not ok:
                return {"ok": False, "error": f"结束进程失败: {err or out}"}
            return {"ok": True, "action": "kill", "target": t, "force": force, "detail": (out or err).strip()}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"结束进程异常: {e}"}

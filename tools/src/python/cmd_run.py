"""内置工具：cmd_run —— 执行系统命令（平台自适应 + 安全拦截）。

设计意图（见设计文档 5.3）：
- 白绫执行系统命令的唯一入口，底层走平台适配层 run_shell（Windows→PowerShell，Linux/macOS→bash）。
- 只读/查询命令放行；破坏性命令默认拦截，需用户确认。
- 全部命令写审计日志 logs/cmd.log。
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path

from tools.base import tool

# 破坏性命令黑名单（命中即拦截；保守优先，宁拦勿放）
_DESTRUCTIVE_PATTERNS = [
    # 跨平台/通用
    "rm -rf", "rm -fr", "rm -r -f", "mkfs", "dd if=", "shutdown", "reboot",
    "format ", "diskpart", "fdisk /", ":(){", "chmod -R 777 /",
    # Windows
    "del /f", "del /s", "remove-item", "rmdir /s", "rd /s",
    "taskkill /f", "reg delete", "reg.exe delete", "wmic process call terminate",
    "bcdedit", "vssadmin delete", "cipher /w",
    # Linux
    "rm /", "find / -delete", "chown -R", "> /dev/sda",
]

_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "logs", "cmd.log")


def _is_destructive(command: str) -> bool:
    low = command.lower().strip()
    for pat in _DESTRUCTIVE_PATTERNS:
        if pat in low:
            return True
    # 单命令危险词
    for word in ("format", "diskpart"):
        if low.split() and low.split()[0] == word:
            return True
    return False


def _audit(command: str, result: dict) -> None:
    """审计日志：命令 + 结果摘要。"""
    try:
        Path(_LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
        status = "ok" if result.get("ok") else "err"
        excerpt = (result.get("stdout") or result.get("stderr") or "")[:120].replace("\n", " ")
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now().isoformat()}] [{status}] {command} :: {excerpt}\n")
    except OSError:
        pass


@tool(
    "cmd_run",
    "执行系统命令（平台自适应：Windows 用 PowerShell，Linux/macOS 用 bash）。"
    "只读/查询命令放行；破坏性命令（删除/格式化/关机等）被安全策略拦截。全部命令记审计日志。",
    {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的命令"},
            "timeout": {"type": "number", "description": "超时秒数，默认 20"},
        },
        "required": ["command"],
    },
)
def run(command: str, timeout: float = 20) -> dict:
    if not command or not command.strip():
        return {"ok": False, "error": "命令不能为空"}
    if _is_destructive(command):
        _audit(command, {"ok": False, "stderr": "被安全策略拦截"})
        return {
            "ok": False,
            "error": "安全策略拦截：该命令属于破坏性操作（删除/格式化/关机等）。如需执行，请说明用途由用户确认后使用受控方式。",
        }
    from core import platform as plat

    result = plat.run_shell(command, timeout=int(timeout))
    out = {
        "ok": result["ok"],
        "stdout": (result["stdout"] or "")[:20000],
        "stderr": (result["stderr"] or "")[:5000],
        "exit_code": result["exit_code"],
    }
    _audit(command, out)
    return out

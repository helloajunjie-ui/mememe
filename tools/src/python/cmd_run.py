"""内置工具：cmd_run —— 执行系统命令（平台自适应 + 安全拦截 + 后台任务）。

设计意图（见设计文档 5.3）：
- 素月执行系统命令的唯一入口，底层走平台适配层 run_shell（Windows→PowerShell，Linux/macOS→bash）。
- 只读/查询命令放行；破坏性命令默认拦截，需用户确认。
- 全部命令写审计日志 logs/cmd.log。

2026-09-20 升级：
- background=false（默认）：同步等结果，timeout 到点杀进程树
- background=true：Popen 立即返回 job_id，素月拿回控制权，继续做别的；
  用 job_list/job_status/job_kill 管理后台任务
"""
from __future__ import annotations

import datetime
import os
import re
from pathlib import Path

from tools.base import tool

# 破坏性命令黑名单（命中即拦截；保守优先，宁拦勿放）
_DESTRUCTIVE_PATTERNS = [
    # 跨平台/通用
    "rm -rf", "rm -fr", "rm -r -f", "mkfs", "dd if=", "shutdown", "reboot",
    "diskpart", "fdisk /", ":(){", "chmod -R 777 /",
    # Windows
    "del /f", "del /s", "remove-item", "rmdir /s", "rd /s",
    "reg delete", "reg.exe delete", "wmic process call terminate",
    "bcdedit", "vssadmin delete", "cipher /w",
    # Linux
    "rm /", "find / -delete", "chown -R", "> /dev/sda",
]

_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "logs", "cmd.log")


# 2026-09-20 修复：原先把裸 "format " 当子串拦截，导致 PowerShell 的
# `Get-Date -Format 'HHmmss'` 等格式化用法被误判成"格式化磁盘"而拒绝执行。
# 改为只匹配"真格式化驱动器"的形态。
_DESTRUCTIVE_REGEX = (
    re.compile(r"\bformat\s+[a-z]:"),   # format C:      —— 格式化驱动器
    re.compile(r"\bformat-volume\b"),   # PowerShell Format-Volume
    re.compile(r"\bformat\s+/[a-z]"),   # format /q /fs:ntfs 等开关
)


def _is_destructive(command: str) -> bool:
    low = command.lower().strip()
    for pat in _DESTRUCTIVE_PATTERNS:
        if pat in low:
            return True
    for rx in _DESTRUCTIVE_REGEX:
        if rx.search(low):
            return True
    for word in ("format", "diskpart"):
        if low.split() and low.split()[0] == word:
            return True
    return False


def _audit(command: str, result: dict, background: bool = False) -> None:
    try:
        Path(_LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
        status = "ok" if result.get("ok") else "err"
        tag = "bg" if background else "sync"
        excerpt = (result.get("stdout") or result.get("stderr") or result.get("error") or "")[:120].replace("\n", " ")
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now().isoformat()}] [{status}/{tag}] {command} :: {excerpt}\n")
    except OSError:
        pass


@tool(
    "cmd_run",
    "执行系统命令（平台自适应：Windows 用 PowerShell，Linux/macOS 用 bash）。"
    "只读/查询命令放行；破坏性命令被安全策略拦截。"
    "background=true 时立即返回 job_id（不阻塞素月），用 job_list/job_status/job_kill 管理。",
    {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的命令"},
            "timeout": {"type": "number", "description": "同步模式超时秒数，默认 20（后台模式忽略）"},
            "background": {"type": "boolean", "description": "true=后台立即返回 job_id；false=同步等结果（默认）"},
        },
        "required": ["command"],
    },
    group="系统与执行",
)
def run(command: str, timeout: float = 20, background: bool = False) -> dict:
    if not command or not command.strip():
        return {"ok": False, "error": "命令不能为空"}
    if _is_destructive(command):
        _audit(command, {"ok": False, "stderr": "被安全策略拦截"}, background)
        return {
            "ok": False,
            "error": "安全策略拦截：该命令属于破坏性操作（删除/格式化/关机等）。如需执行，请说明用途由用户确认后使用受控方式。",
        }
    from core import platform as plat

    if background:
        from tools.src.python import job_mgr
        res = job_mgr.start_job(command)
        _audit(command, res, background=True)
        return res

    result = plat.run_shell(command, timeout=int(timeout))
    out = {
        "ok": result["ok"],
        "stdout": (result["stdout"] or "")[:20000],
        "stderr": (result["stderr"] or "")[:5000],
        "exit_code": result["exit_code"],
    }
    _audit(command, out, background=False)
    return out

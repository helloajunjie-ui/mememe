"""内置工具：self_restart —— 请求白绫无感冷启动（保存会话快照 → 后台自动重启）。

用途：核心代码（core/*.py、main.py、config.yaml）更新后，白绫主动请求重启以生效；
或用户明确说"重启白绫/更新后重启"。写重启标志文件 data/restart.flag，
agent 本轮结束时检测到即保存会话快照并以退出码 77 退出，launcher 自动拉起新进程。

与热更新的关系：工具代码（tools/src/python/*.py）更新走热更新（reload_if_changed），
**不需要**重启；只有核心层改动才需要本工具。
"""
from __future__ import annotations

import os

from tools.base import tool

_FLAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "data", "restart.flag")


@tool(
    "self_restart",
    "请求白绫无感冷启动：核心代码（core/main/config）更新后调用，保存会话快照并后台自动重启，"
    "前端最多卡一下。工具层更新（tools/*.py）不需要重启（热更新自动生效）。"
    "调用前先向用户说明：将自动重启以加载新代码，当前对话上下文会无缝续接。",
    {
        "type": "object",
        "properties": {
            "reason": {"type": "string",
                       "description": "重启原因（如：更新了 core/agent.py），记录到日志"},
        },
        "required": [],
    },
)
def run(reason: str = "") -> dict:
    try:
        os.makedirs(os.path.dirname(_FLAG), exist_ok=True)
        with open(_FLAG, "w", encoding="utf-8") as f:
            f.write(f"reason={reason or '手动请求'}")
        return {"ok": True, "result": "已请求无感冷启动：本轮完成后自动保存会话并后台重启（退出码 77，"
                                      "launcher 自动拉起）。工具层更新无需重启；核心层新代码重启后生效。"}
    except OSError as e:
        return {"ok": False, "error": f"写重启标志失败: {e}"}

"""内置工具：ws_write —— 把文本内容保存到 workspace/ 下的文件（自动建父目录）。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from tools.base import tool

WORKSPACE = Path(__file__).resolve().parents[3] / "workspace"
_MAX_BYTES = 2 * 1024 * 1024  # 单文件 2MB 上限


def _safe_resolve(rel_path: str) -> Path | None:
    if not rel_path or rel_path.startswith(("/", "\\")):
        return None
    target = (WORKSPACE / rel_path).resolve()
    if WORKSPACE.resolve() in target.parents or target == WORKSPACE.resolve():
        return target
    return None


@tool(
    "ws_write",
    "把文本内容保存到 workspace/ 工作区下的文件（相对路径，自动创建父目录）。用于保存任务中间产物、下载内容、报告草稿等。",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "workspace/ 下的相对文件路径，如 tasks/20260904_股票分析/结论.md"},
            "content": {"type": "string", "description": "要保存的文本内容"},
        },
        "required": ["path", "content"],
    },
)
def run(path: str, content: str) -> dict:
    target = _safe_resolve(path)
    if target is None:
        return {"ok": False, "error": "路径非法：必须为 workspace/ 内的相对路径"}
    if len(content.encode("utf-8")) > _MAX_BYTES:
        return {"ok": False, "error": f"内容超过单文件上限 {_MAX_BYTES // 1024 // 1024}MB"}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(target), "size": target.stat().st_size}
    except OSError as e:
        return {"ok": False, "error": f"写入失败: {e}"}

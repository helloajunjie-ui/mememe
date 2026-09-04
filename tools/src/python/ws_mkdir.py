"""内置工具：ws_mkdir —— 在 workspace/ 下开辟任务目录。"""
from __future__ import annotations

from pathlib import Path

from tools.base import tool

WORKSPACE = Path(__file__).resolve().parents[3] / "workspace"


def _safe_resolve(rel_path: str) -> Path | None:
    """把相对路径安全解析到 workspace 内（防路径穿越）。"""
    if not rel_path or rel_path.startswith(("/", "\\")):
        return None
    target = (WORKSPACE / rel_path).resolve()
    if target == WORKSPACE.resolve() or WORKSPACE.resolve() in target.parents:
        return target
    return None


@tool(
    "ws_mkdir",
    "在 workspace/ 工作区下开辟任务目录（相对路径，如 tasks/20260904_股票分析）。"
    "任务需要保存/下载内容时，先为任务建独立目录再使用。",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "workspace/ 下的相对目录路径"},
        },
        "required": ["path"],
    },
)
def run(path: str) -> dict:
    target = _safe_resolve(path)
    if target is None:
        return {"ok": False, "error": "路径非法：必须为 workspace/ 内的相对路径"}
    try:
        target.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "path": str(target), "abs": str(target), "created": target.exists()}
    except OSError as e:
        return {"ok": False, "error": f"创建失败: {e}"}

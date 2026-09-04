"""内置工具：ws_write —— 把文本内容保存到 workspace/ 下的文件（自动建父目录）。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from tools.base import tool

WORKSPACE = Path(__file__).resolve().parents[3] / "workspace"
_MAX_BYTES = 2 * 1024 * 1024  # 单文件 2MB 上限


def _safe_resolve(path: str) -> Path | None:
    if not path:
        return None
    p = Path(path)
    if p.is_absolute():
        target = p.resolve()
    else:
        # 容错：相对路径若带 workspace/ 前缀则剥掉（相对 workspace 根）
        s = path.replace("\\", "/").strip("/")
        if s.startswith("workspace/"):
            s = s[len("workspace/"):]
        target = (WORKSPACE / s).resolve()
    ws = WORKSPACE.resolve()
    if ws in target.parents or target == ws:
        return target
    return None


@tool(
    "ws_write",
    "把文本内容保存到 workspace/ 工作区内的文件（自动创建父目录）。三种路径都接受：①workspace 内绝对路径（如 F:\\...\\workspace\\tasks\\x.md）；②相对路径带 workspace/ 前缀（如 workspace/workflows/<id>/x.html）；③纯相对路径（如 workflows/<id>/x.html）。用于保存任务中间产物、工作流节点产物、下载内容、报告草稿等。",
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
    _trace({"path": path, "content_len": len(content or "")})
    target = _safe_resolve(path)
    if target is None:
        return _trace({"ok": False, "error": "路径非法：目标必须在 workspace/ 内。可传 workspace 内的相对路径"
                                     "（如 workflows/<id>/x.html 或 tasks/<主题>/x.md），"
                                     "也可传 workspace 内的绝对路径（如 F:\\...\\workspace\\workflows\\<id>\\x.html）。"})
    if len(content.encode("utf-8")) > _MAX_BYTES:
        return _trace({"ok": False, "error": f"内容超过单文件上限 {_MAX_BYTES // 1024 // 1024}MB"})
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return _trace({"ok": True, "path": str(target), "size": target.stat().st_size})
    except OSError as e:
        return _trace({"ok": False, "error": f"写入失败: {e}"})


def _trace(info: dict) -> dict:
    """诊断追踪：记录每次调用参数与结果到 data/ws_write_trace.jsonl。"""
    import json as _json
    try:
        p = Path(__file__).resolve().parents[3] / "data" / "ws_write_trace.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(_json.dumps(info, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return info

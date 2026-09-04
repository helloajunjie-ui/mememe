"""内置工具：self_clone —— 复制白绫自我到目标目录（复活/迁移到新环境）。

设计意图：白绫的"存在" = core/ + data/ + tools/ + config。复制它们到新位置即可在新环境复活，
保留记忆、方法论、工具库与人格。目标目录不能是自身项目根或其子目录（防自杀/递归复制）。
"""
from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path

from tools.base import get_meta, tool

_PROJECT_ROOT = Path(__file__).resolve().parents[3]

_BASE_ITEMS = ["core", "tools", "data", "config.yaml", "main.py", ".env"]
_EXCLUDES = [".venv", "__pycache__", "backups", "logs", "workspace"]


@tool(
    "self_clone",
    "复制自我到目标目录（复活/迁移到新环境）：核心代码+配置+数据+工具库整体复制，保留记忆/方法论/人格/工具。"
    "目标目录不能是自身项目根或其子目录。属中等风险（只新增不覆盖源）。",
    {
        "type": "object",
        "properties": {
            "target_dir": {"type": "string", "description": "目标目录绝对路径（需存在或可创建，不能是自身项目根或其内部）"},
            "include_workspace": {
                "type": "boolean",
                "description": "是否一并复制 workspace 任务档案（默认 false，体积较大）",
            },
            "include_venv": {
                "type": "boolean",
                "description": "是否复制 .venv（默认 false，体积巨大且可在新环境重建）",
            },
        },
        "required": ["target_dir"],
    },
)
def run(target_dir: str, include_workspace: bool = False, include_venv: bool = False) -> dict:
    src_root = _PROJECT_ROOT.resolve()
    try:
        dst_root = Path(target_dir).expanduser().resolve()
    except OSError as e:
        return {"ok": False, "error": f"目标路径无效: {e}"}

    # 防自杀/防递归：目标不能是自身项目根或其子目录
    if dst_root == src_root:
        return {"ok": False, "error": "目标目录不能是自身项目根。"}
    try:
        dst_root.relative_to(src_root)
        return {"ok": False, "error": f"目标目录 {dst_root} 是自身项目根的子目录，禁止复制到自身内部（会导致递归/自杀）。"}
    except ValueError:
        pass

    try:
        dst_root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": f"无法创建目标目录: {e}"}

    copied, skipped = [], []
    items = list(_BASE_ITEMS)
    if include_workspace:
        items.append("workspace")
    if include_venv:
        items.append(".venv")
    for item in items:
        src = src_root / item
        if not src.exists():
            skipped.append(item)
            continue
        dst = dst_root / item
        try:
            if src.is_dir():
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*_EXCLUDES))
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            copied.append(item)
        except OSError as e:
            return {"ok": False, "error": f"复制 {item} 失败: {e}"}

    # 写入克隆说明
    manifest = {
        "type": "self_clone",
        "source": str(src_root),
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "items": copied,
        "usage": "在此目录运行对应解释器执行 main.py 即可在新环境复活。",
    }
    (dst_root / "_clone_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(f.stat().st_size for f in dst_root.rglob("*") if f.is_file() and "site-packages" not in str(f))
    return {
        "ok": True,
        "clone_path": str(dst_root),
        "copied": copied,
        "skipped": skipped,
        "size_bytes": total,
        "note": f"自我已复制到 {dst_root}。恢复记忆/人格/工具库完整保留。进入该目录用 python main.py 即可复活（需按 README 装依赖）。",
    }

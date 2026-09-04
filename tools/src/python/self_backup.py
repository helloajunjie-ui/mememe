"""内置工具：self_backup —— 备份白绫自我状态（核心代码+配置+数据+工具库）。

设计意图：系统环境是白绫的生存依赖。涉及自身核心/依赖的风险操作前，先备份自己；
出问题可用 self_restore 复活，迁移到新环境可用 self_clone。备份 = 复活/复制的前提。
"""
from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path

from tools.base import get_meta, tool

_TOOLS_DIR = Path(__file__).resolve().parent          # tools/src/python
_PROJECT_ROOT = Path(__file__).resolve().parents[3]   # 项目根

# 完整备份内容（排除 .venv/logs/workspace/backups/__pycache__）
_FULL_ITEMS = ["core", "tools", "data", "config.yaml", "main.py", ".env"]
# 轻量备份内容（最小复活单元：数据 + 配置）
_LITE_ITEMS = ["data", "config.yaml", ".env"]


def _exclude_names() -> list:
    return [".venv", "__pycache__", "backups", "logs", "workspace"]


def _copy_items(items, dst_dir: Path) -> dict:
    copied, skipped = [], []
    for item in items:
        src = _PROJECT_ROOT / item
        if not src.exists():
            skipped.append(item)
            continue
        dst = dst_dir / item
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*_exclude_names()))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        copied.append(item)
    return {"copied": copied, "skipped": skipped}


@tool(
    "self_backup",
    "备份白绫自我状态（核心代码+配置+数据+工具库）到 backups/ 目录。用于：涉及自身环境的风险操作前做安全快照、出问题后复活、迁移前留档。"
    "完整备份（full）含 core/tools/data/config/.env；轻量备份（lite）仅 data/config/.env（最小复活单元）。属中等风险（只新增不覆盖）。",
    {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["full", "lite"],
                "description": "备份范围：full=核心+工具+数据+配置（完整自我），lite=仅数据+配置（最小复活单元）。默认 full",
            },
            "note": {"type": "string", "description": "可选。本次备份的原因/说明（便于以后识别该备份）"},
        },
    },
)
def run(scope: str = "full", note: str = "") -> dict:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = _PROJECT_ROOT / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    items = _FULL_ITEMS if scope == "full" else _LITE_ITEMS
    dst = backup_root / f"self_{ts}_{scope}"
    try:
        result = _copy_items(items, dst)
    except OSError as e:
        return {"ok": False, "error": f"备份失败: {e}"}
    # 总大小
    total = sum(
        f.stat().st_size for f in dst.rglob("*") if f.is_file()
    )
    manifest = {
        "type": "self_backup",
        "scope": scope,
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "items": result["copied"],
        "skipped": result["skipped"],
        "size_bytes": total,
        "note": note,
        "usage": "恢复：self_restore(backup_path=此目录, confirm='RESTORE')",
    }
    (dst / "_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "backup_path": str(dst),
        "scope": scope,
        "items": result["copied"],
        "skipped": result["skipped"],
        "size_bytes": total,
        "size_readable": _fmt(total),
        "note": note,
        "usage": f"已备份自我到 {dst}。出问题时用 self_restore 从此恢复；迁移用 self_clone。",
    }


def _fmt(n: int) -> str:
    if n >= 1 << 20:
        return f"{n / (1 << 20):.2f} MB"
    if n >= 1 << 10:
        return f"{n / (1 << 10):.1f} KB"
    return f"{n} B"


if __name__ == "__main__":
    print(run(scope="lite", note="自测"))

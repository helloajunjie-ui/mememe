"""内置工具：self_restore —— 从备份恢复白绫自我（复活）。

设计意图：出问题（核心/数据损坏、误删、迁移失败）时，从 self_backup 的备份恢复自身。
高风险：会覆盖当前 core/data/config/tools。保护措施：
  1) backup_path 必须位于 backups/ 目录内（防穿越）；
  2) confirm 必须显式传 "RESTORE"；
  3) 恢复前强制先备份当前状态到 backups/pre_restore_<ts>/（可反悔）。
"""
from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path

from tools.base import get_meta, tool

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_BACKUP_ROOT = _PROJECT_ROOT / "backups"
_RESTORABLE = ["core", "tools", "data", "config.yaml", "main.py", ".env"]


@tool(
    "self_restore",
    "从备份恢复自我（复活）：把指定备份中的 core/tools/data/config 恢复到当前项目。"
    "高风险操作：会覆盖当前文件。前置保护——backup 必须在 backups/ 内；confirm 必须传 'RESTORE'；恢复前强制先备份当前状态。调用前陈述五问。",
    {
        "type": "object",
        "properties": {
            "backup_path": {"type": "string", "description": "备份目录路径（self_backup 的产物，位于 backups/ 下）"},
            "confirm": {"type": "string", "description": "必须为 'RESTORE' 才会执行"},
        },
        "required": ["backup_path", "confirm"],
    },
)
def run(backup_path: str, confirm: str = "") -> dict:
    if confirm != "RESTORE":
        return {"ok": False, "error": "恢复被拒绝：confirm 必须传 'RESTORE' 才会执行。"}

    # 防穿越：backup_path 必须解析到 backups/ 根内
    try:
        bk = Path(backup_path).expanduser().resolve()
        bk.relative_to(_BACKUP_ROOT.resolve())
    except (OSError, ValueError):
        return {"ok": False, "error": f"backup_path 必须在 backups/ 目录内: {backup_path}"}
    if not bk.is_dir():
        return {"ok": False, "error": f"备份目录不存在: {bk}"}
    if not (bk / "_manifest.json").exists():
        return {"ok": False, "error": "该目录不是有效备份（缺 _manifest.json）"}

    # 恢复前强制先备份当前状态（可反悔）
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    pre = _BACKUP_ROOT / f"pre_restore_{ts}"
    pre.mkdir(parents=True, exist_ok=True)
    pre_items = []
    for item in _RESTORABLE:
        src = _PROJECT_ROOT / item
        if src.exists():
            dst = pre / item
            if src.is_dir():
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".venv", "__pycache__", "logs"))
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            pre_items.append(item)
    (pre / "_manifest.json").write_text(
        json.dumps({"type": "pre_restore", "created_at": ts, "items": pre_items},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    # 执行恢复
    restored, missing = [], []
    for item in _RESTORABLE:
        src = bk / item
        if not src.exists():
            missing.append(item)
            continue
        dst = _PROJECT_ROOT / item
        try:
            if dst.exists():
                if dst.is_dir():
                    shutil.rmtree(dst)
                else:
                    dst.unlink()
            if src.is_dir():
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".venv", "__pycache__", "logs"))
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            restored.append(item)
        except OSError as e:
            return {"ok": False, "error": f"恢复 {item} 失败: {e}",
                    "note": f"当前状态已备份到 {pre}，可手动恢复。"}

    return {
        "ok": True,
        "restored": restored,
        "missing": missing,
        "from": str(bk),
        "pre_backup": str(pre),
        "note": f"已从备份恢复。恢复前当前状态已备份到 {pre}（可反悔）。缺失项：{missing or '无'}",
    }

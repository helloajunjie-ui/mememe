# -*- coding: utf-8 -*-
"""私有状态备份工具：收集白绫实例的私有数据 → 打 zip 存本地 backups/。

设计意图（用户导师 2026-09-04）：
- 程序本体走公开仓库 mememe（任何人 clone 得独属实例）；
- 实例私有数据（记忆/方法论/自我状态/环境画像）绝不放公开仓库，
  走私有备份（本地 zip + 可扩展推私有 git）。
- 周期策略：每日定时 + 任务结束触发（git 增量成本低，多版本=复活点）。
"""
from __future__ import annotations

import datetime
import json
import zipfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_BACKUP_ROOT = _PROJECT_ROOT / "backups"

# 私有实例数据（绝不放公开仓库）
_PRIVATE_FILES = [
    "data/memory.db",
    "data/self.yaml",
    "data/methodology.json",
    "data/registry.json",
    "data/registry.json.bak_wordcount",
    "data/snapshots.json",
    "data/env_profile.json",
]
# 复活必需的非实例文件（配置/密钥——仅进私有备份，不进公开仓库）
_CORE_FILES = ["config.yaml", ".env"]


def run(note: str = "") -> dict:
    _BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = _BACKUP_ROOT / f"private_{ts}.zip"

    added, missing = [], []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in _PRIVATE_FILES + _CORE_FILES:
            src = _PROJECT_ROOT / rel
            if src.exists():
                zf.write(src, arcname=rel)
                added.append(rel)
            else:
                missing.append(rel)

    manifest = {
        "type": "private_backup",
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "files": added,
        "missing": missing,
        "note": note,
        "usage": "复活：解压本 zip 回项目根；程序本体从 mememe 仓库 clone 后覆盖 data/ 即可得到原实例",
    }
    meta = _BACKUP_ROOT / f"private_{ts}.json"
    meta.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    size = zip_path.stat().st_size
    return {
        "ok": True,
        "backup_zip": str(zip_path),
        "manifest": str(meta),
        "size_bytes": size,
        "size_readable": _fmt(size),
        "files": added,
        "missing": missing,
        "note": note,
    }


def _fmt(n: int) -> str:
    if n >= 1 << 20:
        return f"{n / (1 << 20):.2f} MB"
    if n >= 1 << 10:
        return f"{n / (1 << 10):.1f} KB"
    return f"{n} B"


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(run(note="手动/定时备份"), ensure_ascii=False, indent=2))

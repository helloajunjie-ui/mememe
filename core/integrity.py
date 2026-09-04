"""本体完整性守护（IntegrityGuard）：保证白绫程序本体不被篡改/感染。

威胁模型（用户导师 2026-09-04）：白绫有 cmd_run 执行、net_download 下载、
tool_acquire 获取工具、tool_create 自举工具等感染入口。防御 = 检测 + 告警 + 恢复。

三层：
1. 静态本体哈希基线（core/tools/main/config）——篡改检测核心，启动自检。
2. 篡改告警（记忆 importance 高 + data/integrity_status.json 通知 AI）。
3. 恢复路径：发现篡改从 backups/ 最近备份复活（backup_private 兜底）。

设计原则：
- 静态本体（代码/配置）哈希应稳定；动态数据（data/ 下记忆/方法论/自我状态）不哈希
  比对（每次运行会变），靠备份保护 + 输入卫生防注入。
- 基线文件 data/integrity_baseline.json 属实例状态，随私有备份走，不入公开仓库。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_BASELINE_FILE = _PROJECT_ROOT / "data" / "integrity_baseline.json"
_STATUS_FILE = _PROJECT_ROOT / "data" / "integrity_status.json"

# 静态本体扫描范围（篡改检测重点）：入口 + 核心 + 工具源码 + 模板
_SCAN_FILES = ["main.py", "config.yaml"]
_SCAN_DIRS = ["core", "tools"]
_EXTENSIONS = {".py", ".go", ".yaml", ".yml"}
_EXCLUDE_DIRS = {".venv", "__pycache__", ".git", "bin", "templates_external"}


def _iter_body_files():
    """遍历本体文件（相对项目根路径）。"""
    for f in _SCAN_FILES:
        p = _PROJECT_ROOT / f
        if p.is_file():
            yield f
    for d in _SCAN_DIRS:
        base = _PROJECT_ROOT / d
        if not base.is_dir():
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [x for x in dirs if x not in _EXCLUDE_DIRS]
            for name in files:
                if Path(name).suffix in _EXTENSIONS:
                    rel = os.path.relpath(os.path.join(root, name), _PROJECT_ROOT)
                    yield rel.replace(os.sep, "/")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_baseline() -> dict:
    """生成本体哈希基线，存 data/integrity_baseline.json。返回基线内容。"""
    files = {}
    for rel in sorted(_iter_body_files()):
        p = _PROJECT_ROOT / rel
        try:
            files[rel] = _sha256(p)
        except OSError:
            continue
    baseline = {"schema": 1, "created_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                "files": files}
    _BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _BASELINE_FILE.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    return baseline


def _load_baseline() -> dict | None:
    if not _BASELINE_FILE.exists():
        return None
    try:
        return json.loads(_BASELINE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def check() -> dict:
    """校验本体完整性。返回 {ok, changed, missing, added, checked_count}。"""
    baseline = _load_baseline()
    if baseline is None:
        return {"ok": True, "error": "no_baseline", "changed": [], "missing": [], "added": [],
                "checked_count": 0, "hint": "请先 build_baseline()"}
    expected = baseline.get("files", {})
    changed, missing, added = [], [], []
    # 基线中存在的文件：必须仍在且哈希一致
    for rel, h in expected.items():
        p = _PROJECT_ROOT / rel
        if not p.exists():
            missing.append(rel)
        else:
            try:
                if _sha256(p) != h:
                    changed.append(rel)
            except OSError:
                changed.append(rel)
    # 当前存在的本体文件：基线里没有 = 新增（可能是恶意植入或合法新工具，需人工确认）
    for rel in _iter_body_files():
        if rel not in expected:
            added.append(rel)
    return {
        "ok": not changed and not missing and not added,
        "changed": changed,
        "missing": missing,
        "added": added,
        "checked_count": len(expected),
    }


def _write_status(result: dict) -> None:
    """写完整性状态文件（通知 AI 自我感知）。"""
    try:
        result = dict(result)
        result["checked_at"] = __import__("datetime").datetime.now().isoformat(timespec="seconds")
        _STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATUS_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def ensure_and_check() -> dict:
    """启动自检：无基线则先建，有则校验。写状态文件。返回结果。"""
    if _load_baseline() is None:
        build_baseline()
        result = {"ok": True, "action": "baseline_created", "checked_count": 0}
    else:
        result = check()
    _write_status(result)
    return result


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(ensure_and_check(), ensure_ascii=False, indent=2))

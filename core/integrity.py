"""本体完整性守护（IntegrityGuard）：保证素月程序本体不被篡改/感染。

威胁模型（用户导师 2026-09-04）：素月有 cmd_run 执行、net_download 下载、
tool_acquire 获取工具、tool_create 自举工具等感染入口。防御 = 检测 + 告警 + 恢复。

三层：
1. 静态本体哈希基线（core/tools/main/config）——篡改检测核心，启动自检。
2. 篡改告警（记忆 importance 高 + data/integrity_status.json 通知 AI）。
3. 恢复路径：发现篡改从 backups/ 最近备份复活（backup_private 兜底）。

设计原则：
- 静态本体（代码/配置）哈希应稳定；动态数据（data/ 下记忆/方法论/自我状态）不哈希
  比对（每次运行会变），靠备份保护 + 输入卫生防注入。
- 基线文件 data/integrity_baseline.json 属实例状态，随私有备份走，不入公开仓库。

告警纪律（2026-09-13 修复重复告警）：
- 异常指纹（changed/missing/added 的稳定摘要）存 data/integrity_status.json，
  只有指纹变化才 alert=True → 同一异常重启多次只告警一次，不再刷屏写记忆。
- 已提交的迭代自动认账：变更文件若都不在工作区脏文件里（即都已 commit），
  视为合法迭代，自动重建基线放行；未提交的改动照旧告警（可能正在改，也可能被注入）。
- 手动确认路径：python -m core.integrity --rebuild（重建基线）/ --status（看状态）。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_BASELINE_FILE = _PROJECT_ROOT / "data" / "integrity_baseline.json"
_STATUS_FILE = _PROJECT_ROOT / "data" / "integrity_status.json"

# 静态本体扫描范围（篡改检测重点）：入口 + 核心 + 工具源码 + 模板
_SCAN_FILES = ["main.py", "config.yaml", ".gitignore", "data/persona.yaml"]  # 含防泄露守门文件 + 人格基座（灵魂文件）
# 注：data/self.yaml 未纳入——boot_count 每次启动自增属正常变动，纳入会制造常态告警噪音。
_SCAN_DIRS = ["core", "tools", "webui"]  # 含最暴露的 HTTP 接口面
_EXTENSIONS = {".py", ".go", ".yaml", ".yml", ".html"}
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


def _fingerprint(result: dict) -> str:
    """异常指纹：变更集合的稳定摘要，用于告警去重。"""
    parts = [f"{key}:" + ",".join(sorted(result.get(key) or []))
             for key in ("changed", "missing", "added")]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _load_status() -> dict:
    if not _STATUS_FILE.exists():
        return {}
    try:
        return json.loads(_STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _git_dirty_files() -> set | None:
    """git 工作区未提交文件集合；git 不可用/非仓库返回 None（保守）。"""
    try:
        p = subprocess.run(["git", "status", "--porcelain"], cwd=_PROJECT_ROOT,
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    dirty = set()
    for line in p.stdout.splitlines():
        if len(line) > 3:
            path = line[3:].strip().strip('"')
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            dirty.add(path.replace("\\", "/"))
    return dirty


def _changes_committed(result: dict) -> bool:
    """变更文件是否都已提交（工作区干净）。

    信任锚 = git 历史：只有本机用户/素月本人能 commit。未提交的改动不自动认账，
    照旧告警（可能正在改，也可能是被注入）。git 不可用返回 False（保守）。
    """
    dirty = _git_dirty_files()
    if dirty is None:
        return False
    touched = list(result.get("changed") or []) + list(result.get("added") or []) + list(result.get("missing") or [])
    if not touched:
        return False
    return not (set(touched) & dirty)


def rebuild_baseline() -> dict:
    """人工确认合法迭代后，按当前本体重建基线。"""
    baseline = build_baseline()
    result = {"ok": True, "action": "baseline_rebuilt", "rebuilt_files": [],
              "changed": [], "missing": [], "added": [],
              "checked_count": len(baseline.get("files", {}))}
    result["signature"] = _fingerprint(result)
    result["alert"] = False
    _write_status(result)
    return result


def _write_status(result: dict) -> None:
    """写完整性状态文件（通知 AI 自我感知）。"""
    try:
        result = dict(result)
        result.setdefault("signature", _fingerprint(result))
        result["checked_at"] = __import__("datetime").datetime.now().isoformat(timespec="seconds")
        _STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATUS_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def ensure_and_check(auto_rebuild: bool = True) -> dict:
    """启动自检：无基线则先建；异常但变更已提交则自动重建基线；否则按指纹告警。

    返回 {ok, action?, changed, missing, added, checked_count, signature, alert}。
    alert=True 仅当异常指纹与上次不同（同一异常重启不重复告警）。
    """
    if _load_baseline() is None:
        build_baseline()
        result = {"ok": True, "action": "baseline_created", "changed": [], "missing": [],
                  "added": [], "checked_count": 0}
        result["signature"] = _fingerprint(result)
        result["alert"] = False
        _write_status(result)
        return result

    result = check()
    if not result["ok"] and auto_rebuild and _changes_committed(result):
        rebuilt = sorted(list(result.get("changed") or []) + list(result.get("added") or [])
                         + list(result.get("missing") or []))
        baseline = build_baseline()
        result = {"ok": True, "action": "baseline_rebuilt", "rebuilt_files": rebuilt,
                  "changed": [], "missing": [], "added": [],
                  "checked_count": len(baseline.get("files", {}))}
    result["signature"] = _fingerprint(result)
    prev = _load_status()
    result["alert"] = bool(not result.get("ok") and prev.get("signature") != result["signature"])
    _write_status(result)
    return result


if __name__ == "__main__":
    import sys as _sys
    _sys.stdout.reconfigure(encoding="utf-8")
    _args = _sys.argv[1:]
    if "--rebuild" in _args:
        _out = rebuild_baseline()
    elif "--status" in _args:
        _out = _load_status()
    else:
        _out = ensure_and_check()
    print(json.dumps(_out, ensure_ascii=False, indent=2))

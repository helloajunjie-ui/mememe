"""内置工具：git_multi_status —— 批量查看多个 git 仓库状态（一次调用扫全目录）。

场景：用户说"看看我这些项目/仓库状态"时，扫描指定目录下的所有 git 仓库，
批量给出当前分支、未提交改动数、最近提交、与上游的领先/落后，一次往返全部拿到。
全部只读（git status/log/rev-parse），不产生任何修改。
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

from tools.base import tool


def _git(repo: Path, args: list, timeout: int = 8):
    try:
        p = subprocess.run(["git"] + args, cwd=str(repo), capture_output=True,
                           text=True, timeout=timeout, errors="replace")
        return p.returncode, (p.stdout or "").strip()
    except Exception as e:  # noqa: BLE001
        return -1, f"err:{type(e).__name__}:{e}"


def _repo_summary(repo: Path) -> dict:
    entry: dict = {"path": str(repo).replace(os.path.expanduser("~"), "~")}
    rc, branch = _git(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    entry["branch"] = branch if rc == 0 else "（无分支/错误）"
    rc, st = _git(repo, ["status", "--porcelain"])
    if rc == 0:
        lines = [x for x in st.splitlines() if x.strip()]
        entry["changes"] = len(lines)
        entry["dirty"] = len(lines) > 0
    else:
        entry["changes"] = 0
        entry["dirty"] = False
    rc, last = _git(repo, ["log", "-1", "--format=%h %ad", "--date=short"])
    entry["last_commit"] = last if rc == 0 else ""
    rc, ab = _git(repo, ["rev-list", "--left-right", "--count", "HEAD...@{u}"])
    if rc == 0:
        parts = ab.split()
        if len(parts) == 2:
            try:
                ahead, behind = int(parts[0]), int(parts[1])
                entry["ahead"] = ahead
                entry["behind"] = behind
            except Exception:  # noqa: BLE001
                pass
    return entry


@tool(
    "git_multi_status",
    "批量查看多个 git 仓库状态：扫描指定目录下的 git 仓库，一次给出每个仓库的分支、未提交改动数、"
    "最近提交、与上游领先/落后。全部只读。用户说'看看项目/仓库状态/哪些有改动'时优先用它。",
    {
        "type": "object",
        "properties": {
            "root": {
                "type": "string",
                "description": "要扫描的目录（默认当前工作区根），扫描其直接子目录与自身中的 git 仓库"
            },
            "max_repos": {
                "type": "integer",
                "description": "最多扫描仓库数，默认 20"
            },
        },
        "required": [],
    },
)
def run(root: str = "", max_repos: int = 20) -> dict:
    if not root:
        base = Path(__file__).resolve().parents[3]  # 项目根 F:\me\self-agent
    else:
        base = Path(os.path.expandvars(os.path.expanduser(root)))
    if not base.exists() or not base.is_dir():
        return {"ok": False, "error": f"目录不存在: {base}"}

    candidates = [base]
    try:
        candidates += [p for p in base.iterdir() if p.is_dir()]
    except OSError:
        pass
    repos = []
    for c in candidates:
        git_dir = c / ".git"
        if git_dir.exists():
            repos.append(c)
        if len(repos) >= max_repos:
            break
    if not repos:
        return {"ok": False, "error": f"在 {base} 下未发现 git 仓库（.git 目录）"}

    results: dict = {}
    lock = threading.Lock()

    def worker(rp: Path) -> None:
        entry = _repo_summary(rp)
        with lock:
            results[str(rp)] = entry

    threads = [threading.Thread(target=worker, args=(rp,), daemon=True) for rp in repos]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    ordered = [results[str(rp)] for rp in repos]
    dirty = [e for e in ordered if e.get("dirty")]
    aheads = [e for e in ordered if (e.get("ahead") or 0) > 0]

    lines = []
    for e in ordered:
        mark = "◆" if e.get("dirty") else "·"
        line = f"{mark} {e['path']} [{e.get('branch')}]"
        if e.get("changes"):
            line += f" 改动{e['changes']}"
        if e.get("ahead") or e.get("behind"):
            line += f" (↑{e.get('ahead', 0)}/↓{e.get('behind', 0)})"
        if e.get("last_commit"):
            line += f" {e['last_commit']}"
        lines.append(line)

    return {
        "ok": True,
        "summary": f"{len(repos)} 个仓库，{len(dirty)} 个有未提交改动，{len(aheads)} 个领先上游",
        "repos": ordered,
        "lines": lines,
        "tip": "◆ = 有未提交改动；有依赖的操作（如逐个提交）请串行执行。",
    }

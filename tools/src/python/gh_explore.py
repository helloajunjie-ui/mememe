"""内置工具：gh_explore —— 深挖 GitHub 仓库的一站式通道。

设计意图：把「探测仓库元信息 → 取文件树 → 抽单文件 → 远程 grep → 切行区间」
这五步从「每轮临时写脚本」固化成一条常驻通道（此前为深挖 Graphiti/mem0 临时写了 6 个脚本）。

关键取舍：
- 走 raw.githubusercontent / api.github.com 直取，**不 clone 整仓**（省时省盘，探索只看关键文件）；
- 本地缓存：同一文件第二次取直接命中，不重复下载；
- 代理失败自动回落直连（本机通常有 127.0.0.1:7897，但不假设它永远在）。
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

from tools.base import tool

_DIG = Path(__file__).resolve().parents[3] / "workspace" / "explore" / "dig"
_PROXY = "http://127.0.0.1:7897"
_HEADERS = {"User-Agent": "suyue-gh-explore"}


def _get(url: str, use_proxy: bool, timeout: int = 60) -> bytes:
    if use_proxy:
        handler = urllib.request.ProxyHandler({"http": _PROXY, "https": _PROXY})
    else:
        handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(handler)
    req = urllib.request.Request(url, headers=_HEADERS)
    with opener.open(req, timeout=timeout) as resp:
        return resp.read()


def _fetch(url: str):
    """先试代理，失败回落直连。返回 (data, err)。"""
    try:
        return _get(url, True), ""
    except Exception as exc_proxy:
        try:
            return _get(url, False), ""
        except Exception as exc_direct:
            return None, "proxy:%s | direct:%s" % (exc_proxy, exc_direct)


def _raw_url(repo: str, branch: str, path: str) -> str:
    return "https://raw.githubusercontent.com/%s/%s/%s" % (repo, branch, path)


def _cache_path(repo: str, path: str) -> Path:
    return _DIG / ("%s__%s" % (repo.replace("/", "__"), path.replace("/", "_")))


def _get_file(repo: str, branch: str, path: str, refresh: bool = False):
    """取远程文件（带本地缓存）。返回 (local_path|None, err)。"""
    dst = _cache_path(repo, path)
    if dst.exists() and dst.stat().st_size > 0 and not refresh:
        return str(dst), ""
    data, err = _fetch(_raw_url(repo, branch, path))
    if data is None:
        return None, err
    _DIG.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    return str(dst), ""


def _read_lines(local: str):
    with open(local, encoding="utf-8", errors="replace") as fh:
        return fh.read().splitlines()


@tool(
    "gh_explore",
    "深挖 GitHub 仓库的一站式通道：mode=probe 取元信息(★/最后推送/语言/许可/归档状态)，tree 取文件树(可按路径前缀过滤)，raw 取单个文件正文，grep 在远程文件内正则匹配(带行号)，lines 读远程文件指定行区间。直取 raw/API 不克隆整仓，本地自动缓存，代理失败自动回落直连。",
    {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "description": "probe|tree|raw|grep|lines"},
            "repo": {"type": "string", "description": "owner/name，如 getzep/graphiti"},
            "branch": {"type": "string", "description": "分支或标签，默认 main"},
            "path": {"type": "string", "description": "文件路径（raw/grep/lines 必填）"},
            "prefix": {"type": "string", "description": "tree 模式：只列路径含此前缀的条目"},
            "pattern": {"type": "string", "description": "grep 模式：正则表达式（忽略大小写）"},
            "start": {"type": "number", "description": "lines 模式：起始行号，默认 1"},
            "end": {"type": "number", "description": "lines 模式：结束行号，默认 60"},
            "limit": {"type": "number", "description": "条数/行数上限，grep 默认 40，tree 默认 200"},
            "refresh": {"type": "boolean", "description": "忽略本地缓存重新下载，默认 false"},
        },
        "required": ["mode", "repo"],
    },
)
def run(
    mode: str,
    repo: str,
    branch: str = "main",
    path: str = "",
    prefix: str = "",
    pattern: str = "",
    start: int = 1,
    end: int = 60,
    limit: int = 0,
    refresh: bool = False,
) -> dict:
    if not repo or "/" not in repo:
        return {"ok": False, "error": "repo 必须是 owner/name 形式，收到: %r" % repo}
    mode = (mode or "").strip().lower()

    if mode == "probe":
        data, err = _fetch("https://api.github.com/repos/%s" % repo)
        if data is None:
            return {"ok": False, "mode": mode, "error": err}
        try:
            j = json.loads(data.decode("utf-8", "replace"))
        except Exception as exc:
            return {"ok": False, "mode": mode, "error": "bad json: %s" % exc}
        if "full_name" not in j:
            return {"ok": False, "mode": mode, "error": j.get("message", "unexpected api payload")}
        return {
            "ok": True,
            "mode": mode,
            "repo": j.get("full_name"),
            "stars": j.get("stargazers_count"),
            "forks": j.get("forks_count"),
            "open_issues": j.get("open_issues_count"),
            "language": j.get("language"),
            "license": (j.get("license") or {}).get("spdx_id"),
            "created_at": j.get("created_at"),
            "pushed_at": j.get("pushed_at"),
            "archived": j.get("archived"),
            "size_kb": j.get("size"),
            "default_branch": j.get("default_branch"),
            "description": (j.get("description") or "")[:300],
            "topics": j.get("topics", []),
        }

    if mode == "tree":
        url = "https://api.github.com/repos/%s/git/trees/%s?recursive=1" % (repo, branch)
        data, err = _fetch(url)
        if data is None:
            return {"ok": False, "mode": mode, "error": err}
        try:
            j = json.loads(data.decode("utf-8", "replace"))
        except Exception as exc:
            return {"ok": False, "mode": mode, "error": "bad json: %s" % exc}
        if "tree" not in j:
            return {"ok": False, "mode": mode, "error": j.get("message", "no tree field"),
                    "raw": str(j)[:300]}
        blobs = [t for t in j["tree"] if t.get("type") == "blob"]
        if prefix:
            blobs = [t for t in blobs if prefix.lower() in t["path"].lower()]
        cap = int(limit) if limit else 200
        return {
            "ok": True,
            "mode": mode,
            "repo": repo,
            "branch": branch,
            "total_entries": len(j["tree"]),
            "total_blobs": len(blobs),
            "api_truncated": j.get("truncated", False),
            "paths": ["%s | %s B" % (t["path"], t.get("size", 0)) for t in blobs[:cap]],
        }

    if mode in ("raw", "grep", "lines") and not path:
        return {"ok": False, "mode": mode, "error": "mode=%s 需要 path 参数" % mode}

    if mode == "raw":
        local, err = _get_file(repo, branch, path, refresh=bool(refresh))
        if local is None:
            return {"ok": False, "mode": mode, "url": _raw_url(repo, branch, path), "error": err}
        text = "\n".join(_read_lines(local))
        cap = int(limit) if limit else 12000
        return {
            "ok": True,
            "mode": mode,
            "local": local,
            "bytes": os.path.getsize(local),
            "total_lines": len(_read_lines(local)),
            "content_truncated": len(text) > cap,
            "content": text[:cap],
        }

    if mode == "grep":
        if not pattern:
            return {"ok": False, "mode": mode, "error": "grep 模式需要 pattern"}
        local, err = _get_file(repo, branch, path, refresh=bool(refresh))
        if local is None:
            return {"ok": False, "mode": mode, "url": _raw_url(repo, branch, path), "error": err}
        try:
            rx = re.compile(pattern, re.I)
        except Exception as exc:
            return {"ok": False, "mode": mode, "error": "bad regex: %s" % exc}
        cap = int(limit) if limit else 40
        hits = []
        for i, ln in enumerate(_read_lines(local), 1):
            if rx.search(ln):
                hits.append("%5d | %s" % (i, ln.rstrip()))
                if len(hits) >= cap:
                    break
        return {"ok": True, "mode": mode, "local": local, "file": path,
                "matched": len(hits), "cap": cap, "lines": hits}

    if mode == "lines":
        local, err = _get_file(repo, branch, path, refresh=bool(refresh))
        if local is None:
            return {"ok": False, "mode": mode, "url": _raw_url(repo, branch, path), "error": err}
        all_lines = _read_lines(local)
        s = max(1, int(start))
        e = min(len(all_lines), int(end))
        if e < s:
            return {"ok": False, "mode": mode, "error": "空区间 %s-%s，文件共 %d 行" % (s, e, len(all_lines))}
        return {"ok": True, "mode": mode, "local": local, "total_lines": len(all_lines),
                "range": [s, e],
                "lines": ["%5d | %s" % (i, all_lines[i - 1]) for i in range(s, e + 1)]}

    return {"ok": False, "error": "未知 mode: %r（可选 probe|tree|raw|grep|lines）" % mode}

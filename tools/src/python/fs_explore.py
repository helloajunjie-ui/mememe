"""内置工具：fs_explore —— 本地文件系统探查（只读）。"""
from __future__ import annotations

import os
from pathlib import Path

from tools.base import tool

# 安全边界：只读，且限制在项目根目录内
PROJECT_ROOT = Path(__file__).resolve().parents[3]  # self-agent/


def _check_path(path: str) -> str:
    """解析路径并校验在项目根目录内，防止越权读取。"""
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    p = p.resolve()
    if not (p == PROJECT_ROOT or PROJECT_ROOT in p.parents):
        raise PermissionError(f"路径越界（只允许访问 {PROJECT_ROOT} 内）: {path}")
    return str(p)


@tool(
    "fs_list",
    "列出指定目录的内容",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目录路径，相对或绝对，默认项目根目录"},
        },
        "required": [],
    },
)
def fs_list(path: str = ".") -> dict:
    try:
        p = _check_path(path)
        if not os.path.isdir(p):
            return {"ok": False, "error": f"不是目录: {path}"}
        entries = []
        for name in sorted(os.listdir(p)):
            full = os.path.join(p, name)
            try:
                st = os.stat(full)
                entries.append({
                    "name": name,
                    "is_dir": os.path.isdir(full),
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                })
            except OSError:
                entries.append({"name": name, "is_dir": os.path.isdir(full)})
        return {"ok": True, "path": p, "count": len(entries), "entries": entries}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"列目录失败: {e}"}


@tool(
    "fs_read",
    "读取文件内容（文本，限制大小）",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "max_chars": {"type": "number", "description": "最多读取字符数，默认 20000"},
        },
        "required": ["path"],
    },
)
def fs_read(path: str, max_chars: int = 20000) -> dict:
    try:
        p = _check_path(path)
        if not os.path.isfile(p):
            return {"ok": False, "error": f"不是文件: {path}"}
        size = os.path.getsize(p)
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(max_chars)
        return {
            "ok": True,
            "path": p,
            "size": size,
            "content": content,
            "truncated": size > max_chars,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"读文件失败: {e}"}


@tool(
    "fs_stat",
    "文件元信息（大小/时间/类型）",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件或目录路径"},
        },
        "required": ["path"],
    },
)
def fs_stat(path: str) -> dict:
    try:
        p = _check_path(path)
        st = os.stat(p)
        return {
            "ok": True,
            "path": p,
            "is_dir": os.path.isdir(p),
            "size": st.st_size,
            "created": st.st_ctime,
            "modified": st.st_mtime,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"获取元信息失败: {e}"}


@tool(
    "fs_search",
    "按文件名/内容关键词搜索文件（限制在项目根目录内）",
    {
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "搜索关键词"},
            "in_content": {"type": "boolean", "description": "是否搜索文件内容，默认 false（只搜文件名）"},
            "max_results": {"type": "number", "description": "最多返回结果数，默认 20"},
        },
        "required": ["keyword"],
    },
)
def fs_search(keyword: str, in_content: bool = False, max_results: int = 20) -> dict:
    hits = []
    try:
        for root, dirs, files in os.walk(PROJECT_ROOT):
            # 跳过隐藏目录和 venv
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in (".venv",)]
            for name in files:
                if len(hits) >= max_results:
                    break
                if keyword.lower() in name.lower():
                    hits.append({"path": os.path.join(root, name), "match": "filename"})
                    continue
                if in_content:
                    try:
                        full = os.path.join(root, name)
                        if os.path.getsize(full) > 500_000:  # 跳过大文件
                            continue
                        with open(full, "r", encoding="utf-8", errors="ignore") as f:
                            if keyword in f.read(20000):
                                hits.append({"path": full, "match": "content"})
                    except Exception:  # noqa: BLE001
                        continue
        return {"ok": True, "keyword": keyword, "count": len(hits), "results": hits}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"搜索失败: {e}"}

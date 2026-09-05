"""内置工具：fs_explore —— 本地文件系统探查与文件操作。

安全边界（读/写分离）：
- 写操作（fs_delete / fs_move / fs_copy）经 _check_path() 校验，仅允许项目根目录内。
- 读操作（fs_list / fs_read / fs_stat / fs_search）经 _check_read_path() 校验，
  允许项目根目录内 + 只读白名单目录（FS_READ_WHITELIST 环境变量，分号分隔绝对路径；
  默认含项目根父目录，即 workspace 上级的兄弟目录）。
技术债：白名单放开的是"只读"访问，写操作仍严格限项目根内，防越权修改宿主文件。
"""
from __future__ import annotations

import os
from pathlib import Path

from tools.base import tool

# 项目根：self-agent/
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# 只读白名单：环境变量 FS_READ_WHITELIST（分号分隔绝对路径）；默认含项目根父目录（f:/me）
def _load_read_whitelist() -> list:
    dirs = []
    raw = os.environ.get("FS_READ_WHITELIST", "").strip()
    if raw:
        for d in raw.split(";"):
            d = d.strip()
            if d:
                dirs.append(Path(d).resolve())
    # 默认：项目根父目录（workspace 上级的兄弟目录所在层）
    parent = PROJECT_ROOT.parent.resolve()
    if parent not in dirs:
        dirs.append(parent)
    return dirs


_READ_WHITELIST = _load_read_whitelist()


def _check_path(path: str) -> str:
    """写操作路径校验：仅允许项目根目录内。"""
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    p = p.resolve()
    if not (p == PROJECT_ROOT or PROJECT_ROOT in p.parents):
        raise PermissionError(f"路径越界（写操作只允许访问 {PROJECT_ROOT} 内）: {path}")
    return str(p)


def _check_read_path(path: str) -> str:
    """读操作路径校验：允许项目根目录内 + 只读白名单目录内。"""
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    p = p.resolve()
    # 项目根内
    if p == PROJECT_ROOT or PROJECT_ROOT in p.parents:
        return str(p)
    # 白名单目录内
    for wl in _READ_WHITELIST:
        if p == wl or wl in p.parents:
            return str(p)
    allowed = [str(PROJECT_ROOT)] + [str(w) for w in _READ_WHITELIST]
    raise PermissionError(f"路径越界（只读允许 {allowed} 内）: {path}")


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
        p = _check_read_path(path)
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
        p = _check_read_path(path)
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
        p = _check_read_path(path)
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
    "按文件名/内容关键词搜索文件（path 指定搜索起点，默认项目根目录，均限项目根内）",
    {
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "搜索关键词"},
            "path": {"type": "string", "description": "搜索起点目录（相对或绝对，默认项目根目录；限项目根内）"},
            "in_content": {"type": "boolean", "description": "是否搜索文件内容，默认 false（只搜文件名）"},
            "max_results": {"type": "number", "description": "最多返回结果数，默认 20"},
        },
        "required": ["keyword"],
    },
)
def fs_search(keyword: str, path: str = ".", in_content: bool = False, max_results: int = 20) -> dict:
    hits = []
    try:
        base = _check_read_path(path)
        if not os.path.isdir(base):
            return {"ok": False, "error": f"搜索起点不是目录: {path}"}
        for root, dirs, files in os.walk(base):
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
        return {"ok": True, "keyword": keyword, "path": base, "count": len(hits), "results": hits}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"搜索失败: {e}"}


@tool(
    "fs_delete",
    "删除文件或空目录（需 confirm=\"DELETE\" 二次确认，仅限项目根目录内）",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要删除的文件或空目录路径"},
            "confirm": {"type": "string", "description": "二次确认口令，必须为 DELETE 才会执行"},
        },
        "required": ["path", "confirm"],
    },
)
def fs_delete(path: str, confirm: str = "") -> dict:
    """删除文件或空目录。为防误删，仅允许删除文件或空目录；非空目录需先清空内容。"""
    try:
        if confirm != "DELETE":
            return {"ok": False, "error": "未确认删除：请传入 confirm=\"DELETE\" 以二次确认"}
        p = _check_path(path)
        if p == str(PROJECT_ROOT):
            return {"ok": False, "error": "禁止删除项目根目录"}
        if os.path.isdir(p):
            # 仅允许删除空目录，防止递归删除失控
            if os.listdir(p):
                return {"ok": False, "error": f"目录非空，禁止递归删除: {path}（请先删除其内容）"}
            os.rmdir(p)
            return {"ok": True, "action": "delete", "type": "dir", "path": p}
        if os.path.isfile(p):
            os.remove(p)
            return {"ok": True, "action": "delete", "type": "file", "path": p}
        return {"ok": False, "error": f"路径不存在: {path}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"删除失败: {e}"}


@tool(
    "fs_move",
    "移动/重命名文件或目录（源与目标均须在项目根目录内）",
    {
        "type": "object",
        "properties": {
            "src": {"type": "string", "description": "源路径"},
            "dst": {"type": "string", "description": "目标路径（可为新文件名或目标目录）"},
        },
        "required": ["src", "dst"],
    },
)
def fs_move(src: str, dst: str) -> dict:
    try:
        s = _check_path(src)
        d = _check_path(dst)
        if not os.path.exists(s):
            return {"ok": False, "error": f"源路径不存在: {src}"}
        # 目标若为已存在目录，则移动到该目录下（保留原名）
        if os.path.isdir(d):
            d = os.path.join(d, os.path.basename(s.rstrip("/\\")))
        if os.path.exists(d):
            return {"ok": False, "error": f"目标已存在，拒绝覆盖: {dst}"}
        os.rename(s, d)
        return {"ok": True, "action": "move", "from": s, "to": d}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"移动失败: {e}"}


@tool(
    "fs_copy",
    "复制文件或目录到目标位置（源与目标均须在项目根目录内）",
    {
        "type": "object",
        "properties": {
            "src": {"type": "string", "description": "源路径"},
            "dst": {"type": "string", "description": "目标路径（可为新文件名或目标目录）"},
        },
        "required": ["src", "dst"],
    },
)
def fs_copy(src: str, dst: str) -> dict:
    try:
        import shutil
        s = _check_path(src)
        d = _check_path(dst)
        if not os.path.exists(s):
            return {"ok": False, "error": f"源路径不存在: {src}"}
        if os.path.isdir(d):
            d = os.path.join(d, os.path.basename(s.rstrip("/\\")))
        if os.path.exists(d):
            return {"ok": False, "error": f"目标已存在，拒绝覆盖: {dst}"}
        if os.path.isdir(s):
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
        return {"ok": True, "action": "copy", "from": s, "to": d}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"复制失败: {e}"}

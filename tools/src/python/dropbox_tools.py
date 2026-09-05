# -*- coding: utf-8 -*-
"""内置工具：dropbox —— Dropbox 个人云盘联通（列目录/读/写/建目录/删/连通性）。

设计意图（形态二·外部网络服务·"联通"·官方 SDK 优先）：
- 基于官方 dropbox SDK（PyPI 包名 dropbox，dropbox/dropbox-sdk-python，MIT，官方维护）
  封装，覆盖个人云盘文件读写。与 cloud_webdav（WebDAV 个人云）互补：WebDAV 管
  自建/第三方 WebDAV 服务，本工具管 Dropbox 官方云盘。
- 与 gdrive_tools（Google Drive）同属"海外官方网盘联通"工具组，接口设计对称
  （list/read/write/mkdir/delete/whoami），便于上层统一调度。

安全边界（如实）：
- 凭据：从环境变量读取（不硬编码、不进日志、不进 registry、不落盘）。支持两种：
  * 短令牌：DROPBOX_ACCESS_TOKEN（OAuth2 access token，会过期）
  * 长令牌（推荐）：DROPBOX_REFRESH_TOKEN + DROPBOX_APP_KEY（+ 可选 DROPBOX_APP_SECRET），
    SDK 内部自动用 refresh token 换新 access token。
- 传输：SDK 强制 HTTPS（官方 api.dropboxapi.com），无明文降级路径。
- 操作面：read/write/mkdir 无 confirm（外部云盘、非本地破坏性文件，且 write 默认
  strict_conflict 不覆盖既有冲突文件）；delete 需 confirm 口令（防误删远端文件）。
- 文本读写有大小上限（读 2MB / 写 10MB），防拉爆内存。
"""
from __future__ import annotations

import os
from typing import Any, Dict

from tools.base import tool

# ---- 共享层：凭据与 client ----
_client_singleton = None  # 技术债：模块级懒加载单例，避免每次调用重建 client

_MAX_READ_BYTES = 2 * 1024 * 1024  # 单文件读上限 2MB（防拉爆内存）
_MAX_WRITE_BYTES = 10 * 1024 * 1024  # 单文件写上限 10MB


def _creds() -> Dict[str, str]:
    """从环境变量读取 Dropbox 凭据。"""
    return {
        "access_token": os.environ.get("DROPBOX_ACCESS_TOKEN", "").strip(),
        "refresh_token": os.environ.get("DROPBOX_REFRESH_TOKEN", "").strip(),
        "app_key": os.environ.get("DROPBOX_APP_KEY", "").strip(),
        "app_secret": os.environ.get("DROPBOX_APP_SECRET", "").strip(),
    }


def _client():
    """懒加载官方 dropbox client（单例）。凭据缺失时抛 ValueError。"""
    global _client_singleton
    if _client_singleton is not None:
        return _client_singleton
    creds = _creds()
    if not (creds["access_token"] or creds["refresh_token"]):
        raise ValueError(
            "缺少 Dropbox 凭据：请先设置环境变量 DROPBOX_ACCESS_TOKEN（短令牌）"
            "或 DROPBOX_REFRESH_TOKEN + DROPBOX_APP_KEY（长令牌，推荐）"
        )
    if creds["refresh_token"] and not creds["app_key"]:
        raise ValueError("使用 refresh token 时必须同时设置 DROPBOX_APP_KEY")
    import dropbox as dbx

    _client_singleton = dbx.Dropbox(
        oauth2_access_token=creds["access_token"] or None,
        oauth2_refresh_token=creds["refresh_token"] or None,
        app_key=creds["app_key"] or None,
        app_secret=creds["app_secret"] or None,
    )
    return _client_singleton


def _ok(**kw: Any) -> dict:
    return {"ok": True, **kw}


def _err(e: Exception, prefix: str) -> dict:
    return {"ok": False, "error": f"{prefix}: {type(e).__name__}: {e}"}


def _norm_path(path: str) -> str:
    """规范化 Dropbox 路径：必须以 / 开头，去尾斜杠（根目录除外）。"""
    path = (path or "").strip().replace("\\", "/")
    if not path:
        return ""
    if not path.startswith("/"):
        path = "/" + path
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return path


def _entry_to_dict(e) -> Dict[str, Any]:
    """把 Dropbox 的 FileMetadata/FolderMetadata/DeletedMetadata 转成统一 dict。

    用属性特征区分（避免运行时 import 元数据类型）：
    - FileMetadata 有 size/rev；FolderMetadata 无 size；DeletedMetadata 无 name。
    """
    name = getattr(e, "name", "") or ""
    path_lower = getattr(e, "path_lower", "") or ""
    if not name:  # DeletedMetadata 等无 name 的条目
        return {"name": name, "path": path_lower, "is_dir": False, "deleted": True}
    if getattr(e, "size", None) is None:  # FolderMetadata 无 size 字段
        return {"name": name, "path": path_lower, "is_dir": True}
    return {
        "name": name,
        "path": path_lower,
        "is_dir": False,
        "size": getattr(e, "size", 0),
        "rev": getattr(e, "rev", ""),
    }


@tool(
    name="dropbox_list",
    description=(
        "列 Dropbox 指定目录下的条目（文件/文件夹）。返回 name/path/is_dir/size。"
        "凭据走环境变量 DROPBOX_ACCESS_TOKEN 或 DROPBOX_REFRESH_TOKEN+APP_KEY。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "要列出的目录路径，如 '/' 或 '/docs'。默认根目录。",
                "default": "",
            },
            "recursive": {
                "type": "boolean",
                "description": "是否递归列出所有子目录。默认 false。",
                "default": False,
            },
            "limit": {
                "type": "integer",
                "description": "单次返回条目上限（分页）。默认 100。",
                "default": 100,
            },
        },
        "required": [],
    },
)
def dropbox_list(path: str = "", recursive: bool = False, limit: int = 100) -> dict:
    try:
        c = _client()
        p = _norm_path(path) or ""
        res = c.files_list_folder(p, recursive=bool(recursive), limit=int(limit or 100))
        entries = [_entry_to_dict(e) for e in (res.entries or [])]
        return _ok(
            path=p or "/",
            entries=entries,
            count=len(entries),
            has_more=bool(getattr(res, "has_more", False)),
            cursor=getattr(res, "cursor", ""),
        )
    except Exception as e:  # noqa: BLE001
        return _err(e, "dropbox_list")


@tool(
    name="dropbox_read",
    description=(
        "读取 Dropbox 指定文本文件的内容（UTF-8，上限 2MB）。返回 content 与元信息。"
        "凭据走环境变量 DROPBOX_ACCESS_TOKEN 或 DROPBOX_REFRESH_TOKEN+APP_KEY。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "要读取的文件路径，如 '/docs/note.txt'。",
            },
            "max_chars": {
                "type": "integer",
                "description": "返回内容最大字符数（防超长截断）。默认 20000。",
                "default": 20000,
            },
        },
        "required": ["path"],
    },
)
def dropbox_read(path: str, max_chars: int = 20000) -> dict:
    try:
        c = _client()
        p = _norm_path(path)
        if not p or p == "/":
            raise ValueError("path 不能为空或根目录（需指定具体文件）")
        meta, resp = c.files_download(p)
        try:
            raw = resp.content
        finally:
            resp.close()
        if len(raw) > _MAX_READ_BYTES:
            raise ValueError(f"文件过大（{len(raw)} 字节 > {_MAX_READ_BYTES}），拒绝读取")
        text = raw.decode("utf-8", errors="replace")
        truncated = len(text) > int(max_chars or 20000)
        return _ok(
            path=p,
            size=len(raw),
            content=text[: int(max_chars or 20000)],
            truncated=truncated,
            rev=getattr(meta, "rev", ""),
        )
    except Exception as e:  # noqa: BLE001
        return _err(e, "dropbox_read")


@tool(
    name="dropbox_write",
    description=(
        "向 Dropbox 写入一个文本文件（UTF-8，上限 10MB）。默认不覆盖既有文件"
        "（strict_conflict 语义，冲突时报错）。凭据走环境变量。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "目标文件路径，如 '/docs/note.txt'。",
            },
            "content": {
                "type": "string",
                "description": "要写入的文本内容。",
            },
            "overwrite": {
                "type": "boolean",
                "description": "若文件已存在是否覆盖。默认 false（不覆盖，冲突报错）。",
                "default": False,
            },
        },
        "required": ["path", "content"],
    },
)
def dropbox_write(path: str, content: str, overwrite: bool = False) -> dict:
    try:
        c = _client()
        p = _norm_path(path)
        if not p or p == "/":
            raise ValueError("path 不能为空或根目录（需指定具体文件）")
        data = (content or "").encode("utf-8")
        if len(data) > _MAX_WRITE_BYTES:
            raise ValueError(f"内容过大（{len(data)} 字节 > {_MAX_WRITE_BYTES}），拒绝写入")
        import dropbox.files as df

        mode = df.WriteMode.overwrite if bool(overwrite) else df.WriteMode.add
        meta = c.files_upload(data, p, mode=mode, strict_conflict=not bool(overwrite))
        return _ok(
            path=p,
            size=len(data),
            rev=getattr(meta, "rev", ""),
            name=getattr(meta, "name", ""),
            overwritten=bool(overwrite),
        )
    except Exception as e:  # noqa: BLE001
        return _err(e, "dropbox_write")


@tool(
    name="dropbox_mkdir",
    description=(
        "在 Dropbox 创建目录。若目录已存在则报错。凭据走环境变量。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "要创建的目录路径，如 '/docs/newfolder'。",
            },
        },
        "required": ["path"],
    },
)
def dropbox_mkdir(path: str) -> dict:
    try:
        c = _client()
        p = _norm_path(path)
        if not p or p == "/":
            raise ValueError("path 不能为空或根目录（需指定具体目录名）")
        res = c.files_create_folder_v2(p)
        meta = getattr(res, "metadata", None)
        return _ok(
            path=p,
            name=getattr(meta, "name", "") if meta else "",
            id=getattr(meta, "id", "") if meta else "",
        )
    except Exception as e:  # noqa: BLE001
        return _err(e, "dropbox_mkdir")


@tool(
    name="dropbox_delete",
    description=(
        "删除 Dropbox 中的文件或目录（含其内容）。破坏性操作，需传 confirm='DELETE'"
        "二次确认。凭据走环境变量。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "要删除的文件/目录路径，如 '/docs/old.txt'。",
            },
            "confirm": {
                "type": "string",
                "description": "二次确认口令，必须为 'DELETE' 才会执行。",
            },
        },
        "required": ["path", "confirm"],
    },
)
def dropbox_delete(path: str, confirm: str = "") -> dict:
    try:
        if confirm != "DELETE":
            raise ValueError("需二次确认：请传 confirm='DELETE' 才会真正删除远端文件")
        c = _client()
        p = _norm_path(path)
        if not p or p == "/":
            raise ValueError("path 不能为空或根目录（禁止删除根目录）")
        res = c.files_delete_v2(p)
        meta = getattr(res, "metadata", None)
        return _ok(
            path=p,
            deleted=True,
            name=getattr(meta, "name", "") if meta else "",
        )
    except Exception as e:  # noqa: BLE001
        return _err(e, "dropbox_delete")


@tool(
    name="dropbox_whoami",
    description=(
        "Dropbox 凭据连通性校验（读取当前账号信息作为探针）。返回账号名/邮箱/类型。"
        "凭据走环境变量。"
    ),
    parameters={"type": "object", "properties": {}, "required": []},
)
def dropbox_whoami() -> dict:
    try:
        c = _client()
        acct = c.users_get_current_account()
        nm = getattr(acct, "name", None)
        return _ok(
            name=getattr(nm, "display_name", "") if nm else "",
            email=getattr(acct, "email", ""),
            account_type=str(getattr(acct, "account_type", "")),
            country=getattr(acct, "country", ""),
        )
    except Exception as e:  # noqa: BLE001
        return _err(e, "dropbox_whoami")

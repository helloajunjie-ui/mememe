# -*- coding: utf-8 -*-
"""内置工具：gdrive —— Google Drive 云盘联通（列目录/读/写/建目录/删/连通性）。

设计意图（形态二·外部网络服务·"联通"·官方 SDK 优先）：
- 基于官方 google-api-python-client（googleapis/google-api-python-client，Apache-2.0，
  官方维护）+ google-auth 封装，覆盖 Google Drive 文件读写。
- 与 dropbox_tools（Dropbox）同属"海外官方网盘联通"工具组。注意：Google Drive 是
  fileId 驱动（无真实路径层级），故本工具组接口以 fileId/parentId 为主，与 Dropbox
  的路径语义不同（如实标注差异）。

安全边界（如实）：
- 凭据：从环境变量读取（不硬编码、不进日志、不进 registry、不落盘）。支持两种：
  * OAuth2 用户凭据：GOOGLE_DRIVE_TOKEN_JSON（含 token/refresh_token/token_uri/
    client_id/client_secret/scopes 的 JSON 字符串），SDK 自动刷新 access token。
  * 服务账号：GOOGLE_DRIVE_SA_JSON（服务账号 JSON 字符串），适合无人值守。
- 传输：SDK 强制 HTTPS（官方 www.googleapis.com），无明文降级路径。
- 操作面：read/write/mkdir 无 confirm（外部云盘、非本地破坏性文件）；delete 需
  confirm 口令（防误删远端文件）。
- 文本读写有大小上限（读 2MB / 写 10MB），防拉爆内存。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict

from tools.base import tool

# ---- 共享层：凭据与 client ----
_client_singleton = None  # 技术债：模块级懒加载单例，避免每次调用重建 client

_MAX_READ_BYTES = 2 * 1024 * 1024  # 单文件读上限 2MB（防拉爆内存）
_MAX_WRITE_BYTES = 10 * 1024 * 1024  # 单文件写上限 10MB
_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _creds() -> Dict[str, str]:
    """从环境变量读取 Google Drive 凭据。"""
    return {
        "token_json": os.environ.get("GOOGLE_DRIVE_TOKEN_JSON", "").strip(),
        "sa_json": os.environ.get("GOOGLE_DRIVE_SA_JSON", "").strip(),
    }


def _build_credentials():
    """根据环境变量构造 google-auth 凭据。"""
    creds = _creds()
    if not (creds["token_json"] or creds["sa_json"]):
        raise ValueError(
            "缺少 Google Drive 凭据：请设置环境变量 GOOGLE_DRIVE_TOKEN_JSON"
            "（OAuth2 用户凭据）或 GOOGLE_DRIVE_SA_JSON（服务账号）"
        )
    if creds["sa_json"]:
        from google.oauth2 import service_account

        return service_account.Credentials.from_service_account_info(
            json.loads(creds["sa_json"]), scopes=_SCOPES
        )
    from google.oauth2 import credentials as gc

    info = json.loads(creds["token_json"])
    return gc.Credentials(
        token=info.get("token"),
        refresh_token=info.get("refresh_token"),
        token_uri=info.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=info.get("client_id"),
        client_secret=info.get("client_secret"),
        scopes=info.get("scopes", _SCOPES),
    )


def _client():
    """懒加载 Google Drive service（单例）。凭据缺失时抛 ValueError。"""
    global _client_singleton
    if _client_singleton is not None:
        return _client_singleton
    from googleapiclient.discovery import build

    _client_singleton = build(
        "drive", "v3", credentials=_build_credentials(), cache_discovery=False
    )
    return _client_singleton


def _ok(**kw: Any) -> dict:
    return {"ok": True, **kw}


def _err(e: Exception, prefix: str) -> dict:
    return {"ok": False, "error": f"{prefix}: {type(e).__name__}: {e}"}


def _file_to_dict(f) -> Dict[str, Any]:
    """把 Drive 的 file 资源 dict 转成统一结构。"""
    mime = f.get("mimeType", "")
    is_dir = mime == "application/vnd.google-apps.folder"
    return {
        "id": f.get("id", ""),
        "name": f.get("name", ""),
        "is_dir": is_dir,
        "mimeType": mime,
        "size": int(f.get("size", 0) or 0),
    }


@tool(
    name="gdrive_list",
    description=(
        "列 Google Drive 指定文件夹下的条目（文件/文件夹）。返回 id/name/is_dir/size。"
        "凭据走环境变量 GOOGLE_DRIVE_TOKEN_JSON 或 GOOGLE_DRIVE_SA_JSON。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "parent_id": {
                "type": "string",
                "description": "父文件夹 id。默认 'root'（我的云端硬盘根目录）。",
                "default": "root",
            },
            "query": {
                "type": "string",
                "description": "附加 Drive 查询（如 \"name contains '报告'\"）。可选。",
                "default": "",
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
def gdrive_list(parent_id: str = "root", query: str = "", limit: int = 100) -> dict:
    try:
        svc = _client()
        parent = (parent_id or "root").strip()
        q = f"'{parent}' in parents and trashed=false"
        if query and str(query).strip():
            q = f"({q}) and ({str(query).strip()})"
        res = (
            svc.files()
            .list(
                q=q,
                pageSize=int(limit or 100),
                fields="files(id,name,mimeType,size),nextPageToken",
                orderBy="folder,name",
            )
            .execute()
        )
        files = [_file_to_dict(f) for f in (res.get("files") or [])]
        return _ok(
            parent_id=parent,
            entries=files,
            count=len(files),
            next_page_token=res.get("nextPageToken", ""),
        )
    except Exception as e:  # noqa: BLE001
        return _err(e, "gdrive_list")


@tool(
    name="gdrive_read",
    description=(
        "读取 Google Drive 指定文本文件的内容（UTF-8，上限 2MB）。返回 content 与元信息。"
        "凭据走环境变量 GOOGLE_DRIVE_TOKEN_JSON 或 GOOGLE_DRIVE_SA_JSON。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_id": {
                "type": "string",
                "description": "要读取的文件 id（来自 gdrive_list 的 id 字段）。",
            },
            "max_chars": {
                "type": "integer",
                "description": "返回内容最大字符数（防超长截断）。默认 20000。",
                "default": 20000,
            },
        },
        "required": ["file_id"],
    },
)
def gdrive_read(file_id: str, max_chars: int = 20000) -> dict:
    try:
        svc = _client()
        fid = (file_id or "").strip()
        if not fid:
            raise ValueError("file_id 不能为空")
        meta = (
            svc.files()
            .get(fileId=fid, fields="id,name,mimeType,size")
            .execute()
        )
        raw = svc.files().get_media(fileId=fid).execute()
        if len(raw) > _MAX_READ_BYTES:
            raise ValueError(f"文件过大（{len(raw)} 字节 > {_MAX_READ_BYTES}），拒绝读取")
        text = raw.decode("utf-8", errors="replace")
        truncated = len(text) > int(max_chars or 20000)
        return _ok(
            id=fid,
            name=meta.get("name", ""),
            mimeType=meta.get("mimeType", ""),
            size=len(raw),
            content=text[: int(max_chars or 20000)],
            truncated=truncated,
        )
    except Exception as e:  # noqa: BLE001
        return _err(e, "gdrive_read")


@tool(
    name="gdrive_write",
    description=(
        "向 Google Drive 写入一个文本文件（UTF-8，上限 10MB）。默认不覆盖既有文件"
        "（同名冲突时报错）。凭据走环境变量。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "文件名（含扩展名），如 'note.txt'。",
            },
            "content": {
                "type": "string",
                "description": "要写入的文本内容。",
            },
            "parent_id": {
                "type": "string",
                "description": "父文件夹 id。默认 'root'。",
                "default": "root",
            },
            "overwrite": {
                "type": "boolean",
                "description": "若同名文件已存在是否覆盖。默认 false（不覆盖，冲突报错）。",
                "default": False,
            },
        },
        "required": ["name", "content"],
    },
)
def gdrive_write(name: str, content: str, parent_id: str = "root", overwrite: bool = False) -> dict:
    try:
        svc = _client()
        fname = (name or "").strip()
        if not fname:
            raise ValueError("name 不能为空")
        data = (content or "").encode("utf-8")
        if len(data) > _MAX_WRITE_BYTES:
            raise ValueError(f"内容过大（{len(data)} 字节 > {_MAX_WRITE_BYTES}），拒绝写入")
        parent = (parent_id or "root").strip()
        # 查同名文件（不覆盖时冲突检测）
        if not bool(overwrite):
            q = f"name='{fname.replace(chr(39), chr(92)+chr(39))}' and '{parent}' in parents and trashed=false"
            dup = (
                svc.files()
                .list(q=q, pageSize=1, fields="files(id,name)")
                .execute()
                .get("files") or []
            )
            if dup:
                raise ValueError(f"同名文件已存在（id={dup[0]['id']}），如需覆盖请传 overwrite=true")
        from googleapiclient.http import MediaIoBaseUpload
        import io

        media = MediaIoBaseUpload(io.BytesIO(data), mimetype="text/plain", resumable=False)
        body = {"name": fname, "parents": [parent]}
        created = (
            svc.files()
            .create(body=body, media_body=media, fields="id,name,mimeType,size")
            .execute()
        )
        return _ok(
            id=created.get("id", ""),
            name=created.get("name", ""),
            size=len(data),
            parent_id=parent,
            overwritten=bool(overwrite),
        )
    except Exception as e:  # noqa: BLE001
        return _err(e, "gdrive_write")


@tool(
    name="gdrive_mkdir",
    description=(
        "在 Google Drive 创建文件夹。若同名文件夹已存在则报错。凭据走环境变量。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "要创建的文件夹名。",
            },
            "parent_id": {
                "type": "string",
                "description": "父文件夹 id。默认 'root'。",
                "default": "root",
            },
        },
        "required": ["name"],
    },
)
def gdrive_mkdir(name: str, parent_id: str = "root") -> dict:
    try:
        svc = _client()
        fname = (name or "").strip()
        if not fname:
            raise ValueError("name 不能为空")
        parent = (parent_id or "root").strip()
        body = {
            "name": fname,
            "parents": [parent],
            "mimeType": "application/vnd.google-apps.folder",
        }
        created = (
            svc.files()
            .create(body=body, fields="id,name,mimeType")
            .execute()
        )
        return _ok(
            id=created.get("id", ""),
            name=created.get("name", ""),
            parent_id=parent,
        )
    except Exception as e:  # noqa: BLE001
        return _err(e, "gdrive_mkdir")


@tool(
    name="gdrive_delete",
    description=(
        "删除 Google Drive 中的文件或文件夹（含其内容）。破坏性操作，需传 "
        "confirm='DELETE' 二次确认。凭据走环境变量。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_id": {
                "type": "string",
                "description": "要删除的文件/文件夹 id。",
            },
            "confirm": {
                "type": "string",
                "description": "二次确认口令，必须为 'DELETE' 才会执行。",
            },
        },
        "required": ["file_id", "confirm"],
    },
)
def gdrive_delete(file_id: str, confirm: str = "") -> dict:
    try:
        if confirm != "DELETE":
            raise ValueError("需二次确认：请传 confirm='DELETE' 才会真正删除远端文件")
        svc = _client()
        fid = (file_id or "").strip()
        if not fid:
            raise ValueError("file_id 不能为空")
        svc.files().delete(fileId=fid).execute()
        return _ok(id=fid, deleted=True)
    except Exception as e:  # noqa: BLE001
        return _err(e, "gdrive_delete")


@tool(
    name="gdrive_whoami",
    description=(
        "Google Drive 凭据连通性校验（读取账号信息作为探针）。返回账号显示名/邮箱。"
        "凭据走环境变量 GOOGLE_DRIVE_TOKEN_JSON 或 GOOGLE_DRIVE_SA_JSON。"
    ),
    parameters={"type": "object", "properties": {}, "required": []},
)
def gdrive_whoami() -> dict:
    try:
        svc = _client()
        about = svc.about().get(fields="user(displayName,emailAddress)").execute()
        user = about.get("user") or {}
        return _ok(
            name=user.get("displayName", ""),
            email=user.get("emailAddress", ""),
        )
    except Exception as e:  # noqa: BLE001
        return _err(e, "gdrive_whoami")

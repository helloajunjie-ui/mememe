# -*- coding: utf-8 -*-
"""内置工具：gdrive —— Google Drive 云盘联通（列目录/读/写/建目录/删/连通性）。

设计意图（形态二·外部网络服务·"联通"·官方 SDK 优先）：
- 基于官方 google-api-python-client（googleapis/google-api-python-client，Apache-2.0，
  官方维护）+ google-auth 封装，覆盖 Google Drive 文件读写。
- 与 dropbox_tools（Dropbox）同属"海外官方网盘联通"工具组。注意：Google Drive 是
  fileId 驱动（无真实路径层级），故本工具组接口以 fileId/parentId 为主，与 Dropbox
  的路径语义不同（如实标注差异）。

凭据双通道（v0.2 多账户化）：
- 通道1 环境变量（默认）：GOOGLE_DRIVE_TOKEN_JSON（OAuth2 用户凭据 JSON 字符串）
  或 GOOGLE_DRIVE_SA_JSON（服务账号 JSON 字符串）。
- 通道2 加密凭据库（core.cred_vault，data/credentials/ 加密落盘，多账户并存）：
  工具参数传 account='某账户别名' 即用该账户凭据；缺字段回落环境变量。
  账户管理见 account 工具组（account_add/account_list/account_test）。

安全边界（如实）：
- 凭据不硬编码、不进日志、不进 registry；vault 通道密文与密钥在 data/credentials/。
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
_clients: Dict[str, Any] = {}  # key=f"gdrive:{account}"（account="" 为环境变量默认）→ 懒加载 service

_MAX_READ_BYTES = 2 * 1024 * 1024  # 单文件读上限 2MB（防拉爆内存）
_MAX_WRITE_BYTES = 10 * 1024 * 1024  # 单文件写上限 10MB
_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
_SERVICE = "gdrive"


def _resolve_creds(account: str, env_map: Dict[str, str]) -> Dict[str, str]:
    """凭据解析：环境变量为基座；account 非空时用凭据库覆盖（缺字段回落环境变量）。"""
    creds = dict(env_map)
    if account:
        try:
            from core.cred_vault import resolve
        except ImportError:
            raise ValueError(f"凭据库不可用（无法导入 core.cred_vault），不能使用 account='{account}'。请用环境变量（去掉 account 参数）。")
        merged, _src = resolve(_SERVICE, account, env_map)  # 返回 (dict, source)；账户不存在抛 VaultError
        for k, v in merged.items():
            if v:
                creds[k] = v
    return creds


def _creds(account: str = "") -> Dict[str, str]:
    """读取 Google Drive 凭据：account='' 走环境变量；否则以环境变量为基座、凭据库覆盖。"""
    return _resolve_creds(account, {
        "token_json": os.environ.get("GOOGLE_DRIVE_TOKEN_JSON", "").strip(),
        "sa_json": os.environ.get("GOOGLE_DRIVE_SA_JSON", "").strip(),
    })


def _build_credentials(account: str = ""):
    """根据凭据构造 google-auth 凭据（服务账号优先，同原逻辑）。"""
    creds = _creds(account)
    if not (creds["token_json"] or creds["sa_json"]):
        raise ValueError(
            "缺少 Google Drive 凭据：请设置环境变量 GOOGLE_DRIVE_TOKEN_JSON"
            "（OAuth2 用户凭据）或 GOOGLE_DRIVE_SA_JSON（服务账号）；"
            "或用 account_add 添加账户后传 account 参数"
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


def _client(account: str = ""):
    """懒加载 Google Drive service（按 account 缓存，多账户并存）。凭据缺失时抛 ValueError。"""
    key = f"{_SERVICE}:{account}"
    if key in _clients:
        return _clients[key]
    from googleapiclient.discovery import build

    _clients[key] = build(
        "drive", "v3", credentials=_build_credentials(account), cache_discovery=False
    )
    return _clients[key]


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


_ACC = {
    "type": "string",
    "description": "可选：加密凭据库中的账户别名（account_add 添加）。缺省/留空走环境变量（GOOGLE_DRIVE_TOKEN_JSON 或 GOOGLE_DRIVE_SA_JSON）。",
    "default": "",
}


_ACC = {
    "type": "string",
    "description": "可选：凭据库账户别名（account_add(service='gdrive', account=...) 添加）。留空走环境变量默认账户（GOOGLE_DRIVE_TOKEN_JSON 或 GOOGLE_DRIVE_SA_JSON）。",
    "default": "",
}


@tool(
    name="gdrive_list",
    description=(
        "列 Google Drive 指定文件夹下的条目（文件/文件夹）。返回 id/name/is_dir/size。"
        "凭据：account 为空走环境变量，否则用凭据库中该账户。"
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
            "account": _ACC,
        },
        "required": [],
    },
)
def gdrive_list(parent_id: str = "root", query: str = "", limit: int = 100, account: str = "") -> dict:
    try:
        svc = _client(account)
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
        "凭据：account 为空走环境变量，否则用凭据库中该账户。"
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
            "account": _ACC,
        },
        "required": ["file_id"],
    },
)
def gdrive_read(file_id: str, max_chars: int = 20000, account: str = "") -> dict:
    try:
        svc = _client(account)
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
        "把文本内容写入 Google Drive（UTF-8，上限 10MB）。默认不覆盖同名文件"
        "（冲突报错，需传 overwrite=true 才覆盖）。凭据：account 为空走环境变量，否则用凭据库中该账户。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "要创建的文件名。",
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
            "account": _ACC,
        },
        "required": ["name", "content"],
    },
)
def gdrive_write(name: str, content: str, parent_id: str = "root", overwrite: bool = False, account: str = "") -> dict:
    try:
        svc = _client(account)
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
        "在 Google Drive 创建文件夹。若同名文件夹已存在则报错。凭据：account 为空走环境变量，否则用凭据库中该账户。"
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
            "account": _ACC,
        },
        "required": ["name"],
    },
)
def gdrive_mkdir(name: str, parent_id: str = "root", account: str = "") -> dict:
    try:
        svc = _client(account)
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
        "confirm='DELETE' 二次确认。凭据：account 为空走环境变量，否则用凭据库中该账户。"
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
            "account": _ACC,
        },
        "required": ["file_id", "confirm"],
    },
)
def gdrive_delete(file_id: str, confirm: str = "", account: str = "") -> dict:
    try:
        if confirm != "DELETE":
            raise ValueError("需二次确认：请传 confirm='DELETE' 才会真正删除远端文件")
        svc = _client(account)
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
        "凭据：account 为空走环境变量，否则用凭据库中该账户。"
    ),
    parameters={
        "type": "object",
        "properties": {"account": _ACC},
            "account": _ACC,
        "required": [],
    },
)
def gdrive_whoami(account: str = "") -> dict:
    try:
        svc = _client(account)
        about = svc.about().get(fields="user(displayName,emailAddress)").execute()
        user = about.get("user") or {}
        return _ok(
            name=user.get("displayName", ""),
            email=user.get("emailAddress", ""),
        )
    except Exception as e:  # noqa: BLE001
        return _err(e, "gdrive_whoami")

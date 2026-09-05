# -*- coding: utf-8 -*-
"""内置工具：feishu —— 飞书/Lark 开放平台联通（发消息/多维表格/云文档）。

设计意图（形态二·外部网络服务·"联通"）：
- 基于官方 lark-oapi SDK（larksuite/oapi-sdk-python，PyPI 包名 lark-oapi）封装，
  覆盖三大能力域：IM 发消息 / 多维表格(bitable)读写 / 云文档(docx)读写。
- 与 cloud_webdav（个人云文件联通）互补：WebDAV 管文件，本工具管飞书协作数据。

安全边界（如实）：
- 凭据：app_id + app_secret 从环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET 读取
  （不硬编码、不进日志、不进 registry）。这是技术债：无持久化凭据存储，需先设环境变量。
- 所有请求由官方 SDK 内部走 HTTPS 并自动管理 tenant_access_token。
- 写操作（发消息/建记录/建文档）不设 confirm（飞书是外部协作服务、非本地破坏性文件），
  但返回完整结果便于追溯。
- 文档/表格 ID（app_token/table_id/document_id）由调用方显式传入，工具不自行枚举越权资源。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from tools.base import tool

# ---- 共享层：凭据与 client ----
_clients: Dict[str, Any] = {}  # key=f"feishu:{account}" → 懒加载 client（多账户并存）
_SERVICE = "feishu"
_ACC = {
    "type": "string",
    "description": "可选：加密凭据库中的账户别名（account_add 添加）。缺省/留空走环境变量（FEISHU_APP_ID/FEISHU_APP_SECRET）。",
    "default": "",
}


def _creds(account: str = "") -> Dict[str, str]:
    """读取飞书应用凭据：account='' 走环境变量；否则以环境变量为基座、凭据库覆盖。"""
    env_map = {
        "app_id": os.environ.get("FEISHU_APP_ID", "").strip(),
        "app_secret": os.environ.get("FEISHU_APP_SECRET", "").strip(),
    }
    if not account:
        return env_map
    try:
        from core.cred_vault import resolve
    except ImportError:
        raise ValueError(
            f"凭据库不可用（无法导入 core.cred_vault），不能使用 account='{account}'。"
            "请去掉 account 参数改用环境变量。"
        )
    merged, _src = resolve(_SERVICE, account, env_map)  # 返回 (dict, source)；账户不存在抛 VaultError
    return {k: (merged.get(k) or "").strip() for k in ("app_id", "app_secret")}


def _client(account: str = ""):
    """懒加载官方 lark-oapi client（按 account 缓存，多账户并存）。凭据缺失时抛 ValueError。"""
    key = f"{_SERVICE}:{account}"
    if key in _clients:
        return _clients[key]
    creds = _creds(account)
    if not creds["app_id"] or not creds["app_secret"]:
        raise ValueError(
            "缺少飞书凭据：请先设置环境变量 FEISHU_APP_ID 与 FEISHU_APP_SECRET"
            "（在飞书开放平台创建企业自建应用后获取），或用 account_add 添加账户后传 account 参数"
        )
    import lark_oapi as lark

    _clients[key] = (
        lark.Client.builder()
        .app_id(creds["app_id"])
        .app_secret(creds["app_secret"])
        .log_level(lark.LogLevel.ERROR)
        .build()
    )
    return _clients[key]


def _ok(**kw: Any) -> dict:
    return {"ok": True, **kw}


def _err(e: Exception, prefix: str) -> dict:
    return {"ok": False, "error": f"{prefix}: {type(e).__name__}: {e}"}


def _resp_err(resp, prefix: str) -> dict:
    """把 SDK 响应失败转成可读错误（含 code/msg/log_id）。"""
    return {
        "ok": False,
        "error": f"{prefix}: code={resp.code}, msg={resp.msg}, log_id={resp.get_log_id()}",
    }


def _text_content(text: str) -> str:
    """text 消息 content 需为 JSON 字符串 {"text": "..."}。"""
    return json.dumps({"text": text}, ensure_ascii=False)


def _post_content(title: str, text: str) -> str:
    """post 富文本消息 content：单段纯文本，带标题。"""
    payload = {
        "zh": {
            "title": title,
            "content": [[{"tag": "text", "text": text}]],
        }
    }
    return json.dumps(payload, ensure_ascii=False)


def _obj_fields(obj, keys: List[str]) -> Dict[str, Any]:
    """从 SDK model 实例提取指定字段（缺失/None 跳过）。"""
    out: Dict[str, Any] = {}
    for k in keys:
        v = getattr(obj, k, None)
        if v is not None:
            out[k] = v
    return out


# ---- 工具 1：发文本消息 ----
_ACC = {
    "type": "string",
    "description": "可选：凭据库账户别名（account_add(service='feishu', account=...) 添加，存 app_id/app_secret）。留空走环境变量默认账户（FEISHU_APP_ID/FEISHU_APP_SECRET）。",
    "default": "",
}


@tool(
    "feishu_send_text",
    "向飞书用户/群聊发送纯文本消息。receive_id 传接收方 ID（open_id/user_id/chat_id，"
    "由 receive_id_type 指定，默认 open_id），文本内容走 text 消息。"
    "凭据：account 为空走环境变量（FEISHU_APP_ID/FEISHU_APP_SECRET），否则用凭据库中该账户。",
    {
        "type": "object",
        "properties": {
            "receive_id": {"type": "string", "description": "接收方 ID（open_id/user_id/chat_id）"},
            "text": {"type": "string", "description": "要发送的文本内容"},
            "receive_id_type": {"type": "string", "description": "receive_id 类型：open_id/user_id/chat_id/email，默认 open_id"},
            "account": _ACC,
        },
        "required": ["receive_id", "text"],
    },
)
def feishu_send_text(receive_id: str, text: str, receive_id_type: str = "open_id", account: str = "") -> dict:
    try:
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        client = _client(account)
        req = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("text")
                .content(_text_content(text))
                .build()
            )
            .build()
        )
        resp = client.im.v1.message.create(req)
        if not resp.success():
            return _resp_err(resp, "发文本消息失败")
        d = resp.data
        return _ok(
            message_id=getattr(d, "message_id", None),
            chat_id=getattr(d, "chat_id", None),
            msg_type=getattr(d, "msg_type", None),
            create_time=getattr(d, "create_time", None),
        )
    except Exception as e:  # noqa: BLE001
        return _err(e, "发文本消息失败")


# ---- 工具 2：发富文本/标题消息 ----
@tool(
    "feishu_send_post",
    "向飞书用户/群聊发送带标题的富文本消息（post 消息）。receive_id 传接收方 ID，"
    "title 为消息标题，text 为正文。凭据：account 为空走环境变量，否则用凭据库中该账户。",
    {
        "type": "object",
        "properties": {
            "receive_id": {"type": "string", "description": "接收方 ID（open_id/user_id/chat_id）"},
            "text": {"type": "string", "description": "消息正文内容"},
            "title": {"type": "string", "description": "消息标题，默认'白绫通知'"},
            "receive_id_type": {"type": "string", "description": "receive_id 类型，默认 open_id"},
            "account": _ACC,
        },
        "required": ["receive_id", "text"],
    },
)
def feishu_send_post(receive_id: str, text: str, title: str = "白绫通知",
                     receive_id_type: str = "open_id", account: str = "") -> dict:
    try:
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        client = _client(account)
        req = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("post")
                .content(_post_content(title, text))
                .build()
            )
            .build()
        )
        resp = client.im.v1.message.create(req)
        if not resp.success():
            return _resp_err(resp, "发富文本消息失败")
        d = resp.data
        return _ok(
            message_id=getattr(d, "message_id", None),
            chat_id=getattr(d, "chat_id", None),
            create_time=getattr(d, "create_time", None),
        )
    except Exception as e:  # noqa: BLE001
        return _err(e, "发富文本消息失败")


# ---- 工具 3：列出群聊 ----
@tool(
    "feishu_list_chat",
    "列出当前应用可访问的群聊（分页）。返回 chat_id/name/description/type 等。"
    "凭据：account 为空走环境变量，否则用凭据库中该账户。",
    {
        "type": "object",
        "properties": {
            "page_size": {"type": "integer", "description": "每页数量，默认 20，最大 100"},
            "page_token": {"type": "string", "description": "分页游标，首次调用留空"},
            "account": _ACC,
        },
        "required": [],
    },
)
def feishu_list_chat(page_size: int = 20, page_token: str = "", account: str = "") -> dict:
    try:
        from lark_oapi.api.im.v1 import ListChatRequest

        client = _client(account)
        builder = ListChatRequest.builder().page_size(int(page_size or 20))
        if page_token:
            builder.page_token(page_token)
        resp = client.im.v1.chat.list(builder.build())
        if not resp.success():
            return _resp_err(resp, "列群聊失败")
        d = resp.data
        items = []
        for c in (d.items or []):
            items.append(_obj_fields(c, ["chat_id", "name", "description", "type",
                                         "owner_user_id", "avatar"]))
        return _ok(items=items, has_more=bool(getattr(d, "has_more", False)),
                   page_token=getattr(d, "page_token", None), count=len(items))
    except Exception as e:  # noqa: BLE001
        return _err(e, "列群聊失败")


# ---- 工具 4：列多维表格记录 ----
@tool(
    "feishu_bitable_list",
    "列出飞书多维表格(bitable)某数据表的记录。app_token 传多维表格 token，"
    "table_id 传数据表 ID。返回每条记录的 record_id 与 fields。凭据：account 为空走环境变量，否则用凭据库中该账户。",
    {
        "type": "object",
        "properties": {
            "app_token": {"type": "string", "description": "多维表格 app_token（形如 bascn...）"},
            "table_id": {"type": "string", "description": "数据表 table_id（形如 tbl...）"},
            "page_size": {"type": "integer", "description": "每页数量，默认 20，最大 100"},
            "page_token": {"type": "string", "description": "分页游标，首次调用留空"},
            "view_id": {"type": "string", "description": "视图 ID（可选，按视图过滤）"},
            "account": _ACC,
        },
        "required": ["app_token", "table_id"],
    },
)
def feishu_bitable_list(app_token: str, table_id: str, page_size: int = 20,
                        page_token: str = "", view_id: str = "", account: str = "") -> dict:
    try:
        from lark_oapi.api.bitable.v1 import ListAppTableRecordRequest

        client = _client(account)
        builder = (
            ListAppTableRecordRequest.builder()
            .app_token(app_token)
            .table_id(table_id)
            .page_size(int(page_size or 20))
        )
        if page_token:
            builder.page_token(page_token)
        if view_id:
            builder.view_id(view_id)
        resp = client.bitable.v1.app_table_record.list(builder.build())
        if not resp.success():
            return _resp_err(resp, "列多维表格记录失败")
        d = resp.data
        items = []
        for r in (d.items or []):
            items.append(_obj_fields(r, ["record_id", "fields"]))
        return _ok(items=items, total=getattr(d, "total", None),
                   has_more=bool(getattr(d, "has_more", False)),
                   page_token=getattr(d, "page_token", None), count=len(items))
    except Exception as e:  # noqa: BLE001
        return _err(e, "列多维表格记录失败")


# ---- 工具 5：新增多维表格记录 ----
@tool(
    "feishu_bitable_create",
    "向飞书多维表格(bitable)某数据表新增一条记录。app_token/table_id 定位数据表，"
    "fields 传字段名->值的字典（如 {\"姓名\":\"张三\",\"年龄\":30}）。"
    "凭据：account 为空走环境变量，否则用凭据库中该账户。",
    {
        "type": "object",
        "properties": {
            "app_token": {"type": "string", "description": "多维表格 app_token"},
            "table_id": {"type": "string", "description": "数据表 table_id"},
            "fields": {"type": "object", "description": "字段名->值的字典，如 {\"姓名\":\"张三\"}"},
            "account": _ACC,
        },
        "required": ["app_token", "table_id", "fields"],
    },
)
def feishu_bitable_create(app_token: str, table_id: str, fields: dict, account: str = "") -> dict:
    try:
        from lark_oapi.api.bitable.v1 import (
            AppTableRecord,
            CreateAppTableRecordRequest,
        )

        client = _client(account)
        record = AppTableRecord.builder().fields(dict(fields or {})).build()
        req = (
            CreateAppTableRecordRequest.builder()
            .app_token(app_token)
            .table_id(table_id)
            .request_body(record)
            .build()
        )
        resp = client.bitable.v1.app_table_record.create(req)
        if not resp.success():
            return _resp_err(resp, "新增多维表格记录失败")
        rec = resp.data.record
        return _ok(record_id=getattr(rec, "record_id", None),
                   fields=getattr(rec, "fields", None))
    except Exception as e:  # noqa: BLE001
        return _err(e, "新增多维表格记录失败")


# ---- 工具 6：读云文档纯文本 ----
@tool(
    "feishu_docx_read",
    "读取飞书云文档(docx)的纯文本内容。document_id 传文档 ID（形如 dox...）。"
    "返回文档纯文本正文。凭据：account 为空走环境变量，否则用凭据库中该账户。",
    {
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "云文档 document_id（形如 dox...）"},
            "account": _ACC,
        },
        "required": ["document_id"],
    },
)
def feishu_docx_read(document_id: str, account: str = "") -> dict:
    try:
        from lark_oapi.api.docx.v1 import RawContentDocumentRequest

        client = _client(account)
        req = RawContentDocumentRequest.builder().document_id(document_id).build()
        resp = client.docx.v1.document.raw_content(req)
        if not resp.success():
            return _resp_err(resp, "读云文档失败")
        content = getattr(resp.data, "content", "") or ""
        return _ok(content=content, length=len(content))
    except Exception as e:  # noqa: BLE001
        return _err(e, "读云文档失败")


# ---- 工具 7：新建云文档 ----
@tool(
    "feishu_docx_create",
    "新建飞书云文档(docx)。title 传文档标题，folder_token 可选（父文件夹 token，"
    "留空则建在根目录）。返回新建文档的 document_id 与标题。凭据：account 为空走环境变量，否则用凭据库中该账户。",
    {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "文档标题"},
            "folder_token": {"type": "string", "description": "父文件夹 token（可选，留空建在根目录）"},
            "account": _ACC,
        },
        "required": ["title"],
    },
)
def feishu_docx_create(title: str, folder_token: str = "", account: str = "") -> dict:
    try:
        from lark_oapi.api.docx.v1 import CreateDocumentRequest, CreateDocumentRequestBody

        client = _client(account)
        body_builder = CreateDocumentRequestBody.builder().title(title)
        if folder_token:
            body_builder.folder_token(folder_token)
        req = CreateDocumentRequest.builder().request_body(body_builder.build()).build()
        resp = client.docx.v1.document.create(req)
        if not resp.success():
            return _resp_err(resp, "新建云文档失败")
        doc = resp.data.document
        return _ok(document_id=getattr(doc, "document_id", None),
                   title=getattr(doc, "title", None),
                   revision_id=getattr(doc, "revision_id", None))
    except Exception as e:  # noqa: BLE001
        return _err(e, "新建云文档失败")


# ---- 工具 8：校验凭据/连通性 ----
@tool(
    "feishu_whoami",
    "校验飞书凭据是否有效并测试连通性。通过调用一个轻量 API（列群聊 page_size=1）"
    "验证 app_id/app_secret 能否换取访问令牌。返回凭据配置状态与连通性结论。",
    {
        "type": "object",
        "properties": {"account": _ACC},
            "account": _ACC,
        "required": [],
    },
)
def feishu_whoami(account: str = "") -> dict:
    try:
        creds = _creds(account)
        if not creds["app_id"] or not creds["app_secret"]:
            return {
                "ok": False,
                "error": "未配置飞书凭据：请设置环境变量 FEISHU_APP_ID 与 FEISHU_APP_SECRET",
                "configured": False,
            }
        from lark_oapi.api.im.v1 import ListChatRequest

        client = _client(account)
        req = ListChatRequest.builder().page_size(1).build()
        resp = client.im.v1.chat.list(req)
        if not resp.success():
            return {
                "ok": False,
                "configured": True,
                "error": f"凭据已配置但调用失败: code={resp.code}, msg={resp.msg}, "
                         f"log_id={resp.get_log_id()}（可能无 im:chat 读取权限）",
            }
        return _ok(configured=True, app_id=creds["app_id"],
                   message="凭据有效，飞书 API 连通正常")
    except Exception as e:  # noqa: BLE001
        return _err(e, "校验飞书凭据失败")

# -*- coding: utf-8 -*-
"""内置工具：account —— 凭据库多账户管理（add/list/remove/test）。

设计意图（凭据库 cred_vault 的对外管理面）：
- 外部服务工具（dropbox/gdrive/feishu/webdav）原先只认环境变量 → 一套凭据/进程。
  core/cred_vault.py 提供加密多账户存储；本工具组让 LLM/用户能直接管理账户：
  * account_add    新增/更新一个账户的凭据（加密落盘，fields 传 JSON）
  * account_list   列出已存账户（只回账户名，绝不回明文）
  * account_remove 删除账户（confirm="REMOVE" 二次确认）
  * account_test   检查账户凭据填充完整性，并提示如何做真连通验证

安全边界（如实）：
- 全程不打印明文凭据；list 只给账户名；删除需 confirm。
- 密文与密钥均在 data/credentials/（.gitignore 隔离，不进公开仓库与私有备份清单）。
- 支持的服务与字段（与 core.cred_vault.SERVICES 一致）：
  * dropbox: access_token / refresh_token / app_key / app_secret
  * gdrive : token_json（OAuth 用户凭据 JSON 串）/ sa_json（服务账号 JSON 串）
  * feishu : app_id / app_secret
  * webdav : base_url / username / password
  至少填一组可用字段（dropbox 的 access_token 或 refresh_token+app_key 等）。
"""
from __future__ import annotations

import json
from typing import Any, Dict

from tools.base import tool

try:
    from core.cred_vault import SERVICES, VaultError, get, list_accounts, put, remove
except ImportError:  # 独立运行兜底：项目根入 path
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from core.cred_vault import SERVICES, VaultError, get, list_accounts, put, remove

_KNOWN = ", ".join(sorted(SERVICES))

# 各服务"可用账户"的最小字段要求（用于 account_test 提示）
_MIN_REQ = {
    "dropbox": [("access_token",), ("refresh_token", "app_key")],
    "gdrive": [("token_json",), ("sa_json",)],
    "feishu": [("app_id", "app_secret")],
    "webdav": [("base_url", "username")],
}


def _ok(**kw: Any) -> dict:
    return {"ok": True, **kw}


def _err(msg: str) -> dict:
    return {"ok": False, "error": msg}


@tool(
    name="account_add",
    description=(
        "新增/更新一个外部服务账户的凭据到加密凭据库（多账户并存，互不干扰）。"
        "service 支持: dropbox/gdrive/feishu/webdav。fields 为 JSON 对象字符串，"
        "字段与 service 对应：dropbox→access_token/refresh_token/app_key/app_secret；"
        "gdrive→token_json/sa_json；feishu→app_id/app_secret；webdav→base_url/username/password。"
        "至少提供一个非空字段；同 account 重复添加即覆盖更新。添加后可用 account_test 校验、"
        "并在对应服务工具调用时传 account 参数选用该账户。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "service": {"type": "string", "description": f"服务名，可选: {_KNOWN}"},
            "account": {"type": "string", "description": "账户别名（小写字母/数字/_-.，如 work、alice、家里nas）。调用外部工具时以 account 参数选用"},
            "fields": {"type": "string", "description": '凭据字段 JSON 字符串，如 {"access_token":"sl.AAA...","app_key":"x"}'},
        },
        "required": ["service", "account", "fields"],
    },
)
def account_add(service: str, account: str, fields: str) -> dict:
    try:
        parsed = json.loads(fields or "{}")
        if not isinstance(parsed, dict):
            return _err("fields 必须是 JSON 对象字符串，如 {\"access_token\":\"...\"}")
    except json.JSONDecodeError as e:
        return _err("fields 不是合法 JSON: " + str(e) + '。示例: {"access_token": "..."}')
    try:
        r = put(service, account, parsed)
        return _ok(
            service=r["service"], account=r["account"],
            filled=r["fields"],
            hint="已加密存储。测试: account_test；调用时在对应工具传 account 参数。",
        )
    except VaultError as e:
        return _err(str(e))


@tool(
    name="account_list",
    description=(
        "列出加密凭据库中已存的账户名（只返回服务+账户名，绝不返回明文凭据）。"
        "service 留空列全部服务；不传任何参数即全览。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "service": {"type": "string", "description": f"可选，只看某服务（{_KNOWN}）的账户"},
        },
        "required": [],
    },
)
def account_list(service: str = "") -> dict:
    try:
        out = list_accounts(service) if service else list_accounts()
    except VaultError as e:
        return _err(str(e))
    total = sum(len(v) for v in out.values())
    if total == 0:
        return _ok(accounts={}, total=0,
                   hint="凭据库为空。用 account_add 添加账户，或继续走环境变量默认账户。")
    return _ok(accounts=out, total=total,
               hint="用 account_test 校验某账户，或直接传 account 使用。")


@tool(
    name="account_remove",
    description=(
        "从加密凭据库删除某服务的某账户。破坏性操作：需传 confirm=\"REMOVE\" 二次确认。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "service": {"type": "string", "description": f"服务名，可选: {_KNOWN}"},
            "account": {"type": "string", "description": "要删除的账户别名"},
            "confirm": {"type": "string", "description": "二次确认口令，必须为 REMOVE 才会执行"},
        },
        "required": ["service", "account", "confirm"],
    },
)
def account_remove(service: str, account: str, confirm: str = "") -> dict:
    if confirm != "REMOVE":
        return _err("删除需二次确认：请传 confirm=\"REMOVE\"")
    try:
        r = remove(service, account, confirm)
        return _ok(removed=r.get("removed")) if r.get("ok") else _err(r.get("error", "删除失败"))
    except VaultError as e:
        return _err(str(e))


@tool(
    name="account_test",
    description=(
        "校验凭据库中某账户的凭据填充完整性（只查本地，不访问外部服务）。"
        "返回已填/缺失字段与是否达到可用最小要求，并提示做真连通验证的对应工具"
        "（dropbox→dropbox_list/whoami、gdrive→gdrive_list、feishu→feishu_whoami、"
        "webdav→cloud_webdav_list，均需传 account 参数）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "service": {"type": "string", "description": f"服务名，可选: {_KNOWN}"},
            "account": {"type": "string", "description": "要校验的账户别名"},
        },
        "required": ["service", "account"],
    },
)
def account_test(service: str, account: str) -> dict:
    try:
        entry = get(service, account)
    except VaultError as e:
        return _err(str(e))
    if entry is None:
        return _err(f"{service} 下无账户 '{account}'。先用 account_add 添加，或 account_list 查看已有账户。")
    filled = {k: v for k, v in entry.items() if v}
    missing = {k: v for k, v in entry.items() if not v}
    usable = False
    why = ""
    for combo in _MIN_REQ.get(service, []):
        if all(entry.get(k) for k in combo):
            usable = True
            why = f"已满足最小要求（{', '.join(combo)}）"
            break
    if not usable:
        why = "未满足最小要求：需要 " + " 或 ".join(
            "+".join(c) for c in _MIN_REQ.get(service, [])
        )
    probe = {"dropbox": "dropbox_list(account=...)", "gdrive": "gdrive_list(account=...)",
             "feishu": "feishu_whoami(account=...)", "webdav": "cloud_webdav_list(account=...)"}.get(service)
    return _ok(
        service=service, account=account,
        filled_fields=sorted(filled), missing_fields=sorted(missing),
        usable=usable, detail=why,
        next_step=f"真连通验证：调用 {probe}（记得传 account 参数）" if usable else
                  "先补齐缺失字段：account_add 同 account 覆盖更新",
    )

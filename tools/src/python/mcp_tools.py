"""内置工具：MCP 软件接口管理（mcp_list / mcp_connect / mcp_disconnect / mcp_scan）。

架构（v2.7 起）：MCP 独立为服务（mcp-service/server.py，端口 8767），统一管理所有
软件接口（Blender/Godot/filesystem/playwright 等）的连接、激活与调用。素月侧只做
HTTP 调用，MCP 工具**不注册进核心工具库**，按需激活注入：

  流程：mcp_list（看目录）→ mcp_connect（激活，工具 schema 自动注入）
        → 直接调用 mcp_<server>_<tool> → 用完 mcp_disconnect（释放）

mcp_scan 用于软件接口升级/工具变化后强制刷新工具清单。
"""
from __future__ import annotations

import re

from tools.base import tool
from core.mcp import get_mcp_manager

_NAME_RE = re.compile(r"^[a-z0-9_-]+$")


@tool(
    "mcp_list",
    "查看 MCP 软件接口目录：每个已配置软件的名称、用途说明、激活状态与工具数。"
    "指定 server 时返回该软件的工具明细（名称+描述）。"
    "【流程】先看目录确定要用哪个软件 → mcp_connect 激活 → 工具自动注入可用 → 用完 mcp_disconnect 释放。",
    {
        "type": "object",
        "properties": {
            "server": {"type": "string",
                       "description": "可选。指定软件接口名则返回该软件工具详情，如 filesystem"},
        },
    },
)
def run_list(server: str = "") -> dict:
    mcp = get_mcp_manager()
    if not mcp.ensure_running():
        return {"ok": False, "error": "MCP 服务无法启动，请检查 mcp-service 目录"}
    if server:
        r = mcp.fetch_tools(server)
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error")}
        return {"ok": True, "server": server, "tools": r.get("tools", []),
                "count": r.get("count", 0)}
    r = mcp.servers_info()
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error")}
    return {"ok": True, "servers": r.get("servers", []), "count": len(r.get("servers", []))}


@tool(
    "mcp_connect",
    "激活一个 MCP 软件接口（如 filesystem/blender/godot/playwright）：拉取其工具清单并注入对话，"
    "激活后即可直接调用 mcp_<软件>_<工具>。激活失败会返回原因（软件未运行/未安装等）。"
    "【流程】mcp_list 看目录确定软件 → 本工具激活 → 使用 → 用完 mcp_disconnect 释放。",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string",
                     "description": "要激活的软件接口名（目录里的 name），如 filesystem / blender"},
        },
        "required": ["name"],
    },
)
def run_connect(name: str) -> dict:
    if not _NAME_RE.fullmatch(name or ""):
        return {"ok": False, "error": "软件接口名只能含小写字母/数字/下划线/连字符"}
    mcp = get_mcp_manager()
    if not mcp.ensure_running():
        return {"ok": False, "error": "MCP 服务无法启动，请检查 mcp-service 目录"}
    r = mcp.activate(name)
    if not r.get("ok"):
        # 激活失败：附带依赖评估（缺什么/多大/多久/环境影响），供素月通知用户
        deps = mcp.assess_deps(name)
        plan = deps.get("plan") or {}
        if plan.get("type") == "manual":
            # 大型桌面软件：直接说明需用户自行安装，不尝试代装
            return {
                "ok": False,
                "error": f"激活 {name} 失败: {r.get('error')}",
                "need_user_install": True,
                "note": f"{name} 是大型软件（无法自动安装）。请告知用户：需要自行下载安装 "
                        f"（安装包通常较大，按官方流程安装），安装完成后告诉素月再重新激活。",
            }
        return {
            "ok": False,
            "error": f"激活 {name} 失败: {r.get('error')}",
            "deps": deps.get("issues", []),
            "plan": plan,
            "installable": deps.get("installable", False),
            "note": "先调 mcp_deps 看明细，把缺失/大小/时间/环境影响告知用户；"
                    "用户同意后 mcp_install 安装，再重试激活。",
        }
    return {
        "ok": True, "server": name, "active": True,
        "count": r.get("count", 0),
        "note": f"已激活 {name}（{r.get('count', 0)} 个工具），工具已注入对话可直接调用。"
                f"用完调 mcp_disconnect 释放。",
    }


@tool(
    "mcp_disconnect",
    "释放一个已激活的 MCP 软件接口：其工具从对话上下文移除（不再注入）。"
    "释放后需要再次使用须重新 mcp_connect。",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "要释放的软件接口名，如 filesystem"},
        },
        "required": ["name"],
    },
)
def run_disconnect(name: str) -> dict:
    mcp = get_mcp_manager()
    if not mcp.ensure_running():
        return {"ok": False, "error": "MCP 服务无法启动"}
    r = mcp.deactivate(name)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error")}
    return {"ok": True, "server": name, "active": False,
            "note": "已释放，该软件工具已从对话移除。"}


@tool(
    "mcp_key_set",
    "保存/更新一个软件接口的凭据（如 GITHUB_TOKEN、FIGMA_API_KEY、COOKIE 等）。"
    "凭据按软件接口隔离存储（config/secrets.json），值永不回传，只在服务端使用。"
    "【安全】不要在对话里复述凭据值；保存后确认键名即可。",
    {
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "软件接口名，如 github / figma"},
            "key": {"type": "string", "description": "凭据键名，如 GITHUB_TOKEN / FIGMA_API_KEY / COOKIE"},
            "value": {"type": "string", "description": "凭据值（token / key / cookie 字符串）"},
        },
        "required": ["server", "key", "value"],
    },
)
def run_key_set(server: str, key: str, value: str) -> dict:
    mcp = get_mcp_manager()
    if not mcp.ensure_running():
        return {"ok": False, "error": "MCP 服务无法启动"}
    r = mcp.set_secret(server, key, value)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error")}
    return {"ok": True, "server": server, "key": key,
            "note": f"已保存 {server} 的 {key}（隔离存储，值不回传）。"}


@tool(
    "mcp_key_list",
    "查看某软件接口已保存的凭据键名（只返回键名，永不回传值）。"
    "用于确认哪个接口已配好凭据、缺哪个。",
    {
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "软件接口名；留空列出全部接口的键名"},
        },
    },
)
def run_key_list(server: str = "") -> dict:
    mcp = get_mcp_manager()
    if not mcp.ensure_running():
        return {"ok": False, "error": "MCP 服务无法启动"}
    r = mcp.secret_keys(server)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error")}
    return {"ok": True, "server": server or "(全部)", "keys": r.get("keys", {})}


@tool(
    "mcp_key_remove",
    "删除某软件接口的一个凭据（如 token 失效/换新时先删旧再存新）。",
    {
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "软件接口名"},
            "key": {"type": "string", "description": "要删除的凭据键名"},
        },
        "required": ["server", "key"],
    },
)
def run_key_remove(server: str, key: str) -> dict:
    mcp = get_mcp_manager()
    if not mcp.ensure_running():
        return {"ok": False, "error": "MCP 服务无法启动"}
    r = mcp.remove_secret(server, key)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error")}
    return {"ok": True, "server": server, "key": key, "note": "已删除。"}


@tool(
    "mcp_deps",
    "评估某软件接口的依赖/凭据状态：缺什么（npm/pip/二进制/凭据）、容量大小、"
    "预计时间、对当前环境的影响。激活失败时用它找出原因，然后向用户说明并征询是否安装。",
    {
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "软件接口名，如 git / github / figma"},
        },
        "required": ["server"],
    },
)
def run_deps(server: str) -> dict:
    mcp = get_mcp_manager()
    if not mcp.ensure_running():
        return {"ok": False, "error": "MCP 服务无法启动"}
    r = mcp.assess_deps(server)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error")}
    return {"ok": True, "server": server,
            "issues": r.get("issues", []), "plan": r.get("plan"),
            "installable": r.get("installable", False),
            "note": "如需要安装，先向用户说明大小/时间/环境影响并征得同意，再调 mcp_install。"}


@tool(
    "mcp_install",
    "执行软件接口的依赖安装或升级（npm 预拉 / pip 安装-升级 / 二进制下载）。"
    "【必须】调用前先向用户说明：装什么、多大、预计多久、对环境影响（mcp_deps 的 plan 字段），"
    "征得用户明确同意后才能调用。upgrade=true 时表示升级到最新版（如 mcp_deps 提示'可升级'后）。",
    {
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "软件接口名"},
            "upgrade": {"type": "boolean",
                        "description": "是否升级到最新版（默认 false 安装；true 升级 pip 包/刷新 npx）"},
        },
        "required": ["server"],
    },
)
def run_install(server: str, upgrade: bool = False) -> dict:
    mcp = get_mcp_manager()
    if not mcp.ensure_running():
        return {"ok": False, "error": "MCP 服务无法启动"}
    r = mcp.install_deps(server, upgrade=upgrade)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error")}
    return {"ok": True, "server": server, "note": r.get("note", "安装完成，可重新 mcp_connect 激活。")}


@tool(
    "mcp_scan",
    "强制刷新一个（或全部）软件接口的工具清单：软件升级/接口变化后重新拉取，"
    "并保持激活状态。一般不常用。",
    {
        "type": "object",
        "properties": {
            "server": {"type": "string",
                       "description": "可选。只刷新指定软件；留空刷新全部已配置软件"},
        },
    },
)
def run_scan(server: str = "") -> dict:
    mcp = get_mcp_manager()
    if not mcp.ensure_running():
        return {"ok": False, "error": "MCP 服务无法启动"}
    if server:
        r = mcp.activate(server, force=True)
        if not r.get("ok"):
            return {"ok": False, "error": f"刷新 {server} 失败: {r.get('error')}"}
        return {"ok": True, "server": server, "count": r.get("count", 0),
                "note": f"已刷新 {server}（{r.get('count', 0)} 个工具）。"}
    r = mcp.servers_info()
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error")}
    out = []
    for s in r.get("servers", []):
        rr = mcp.activate(s["name"], force=True)
        out.append({"server": s["name"], "ok": rr.get("ok"),
                    "count": rr.get("count", 0),
                    "error": rr.get("error") if not rr.get("ok") else ""})
    return {"ok": True, "servers": out, "note": "已全部刷新。"}

"""内置工具：MCP 万能接口（mcp_connect / mcp_scan / mcp_list / mcp_disconnect）。

设计意图（见设计文档 5.37）：
- MCP（Model Context Protocol）是开放协议，MCP server 暴露标准 tools 供客户端调用。
- 白绫作为 MCP **客户端**：连接本机/远程 server（如 Blender 的 `uvx blender-mcp`），
  把其工具注册进工具名单（命名 mcp_<server>_<tool>），执行时转发调用——
  实现对支持 API 的外部软件（Blender/文件系统/浏览器等）的标准化操控。
- 工具先测可用再进名单：mcp_connect 连接成功（能枚举工具）才保留配置；
  mcp_scan 枚举成功的 server 工具才注册，失败记录原因。
"""
from __future__ import annotations

import re

from tools.base import tool
from core.mcp import get_mcp_manager

_NAME_RE = re.compile(r"^[a-z0-9_-]+$")


@tool(
    "mcp_connect",
    "连接一个 MCP server（万能接口）：配置并测试连接。支持两种形态："
    "① stdio 子进程（command/args/env，如 Blender 用 command=uvx args=[\"blender-mcp\"]）；"
    "② 远程端点（url + transport=http/sse）。配置保存到 config/mcp.json。"
    "连接成功（能枚举到工具）才算可用；失败自动回滚配置。配置后需调 mcp_scan 同步进工具名单。",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "MCP server 名称（英文小写下划线/连字符），如 blender"},
            "command": {"type": "string", "description": "启动命令（默认 uvx），如 uvx / npx / python"},
            "args": {"type": "array", "items": {"type": "string"},
                     "description": "命令参数，如 [\"blender-mcp\"]"},
            "env": {"type": "object",
                    "description": "环境变量，如 {\"DISABLE_TELEMETRY\": \"true\"}"},
            "url": {"type": "string",
                    "description": "远程端点 URL（HTTP streamable 或 SSE，配 transport）"},
            "transport": {"type": "string", "enum": ["http", "sse"],
                          "description": "url 形态：http（默认）/ sse"},
        },
        "required": ["name"],
    },
)
def run_connect(name: str, command: str = "uvx", args: list = None, env: dict = None,
                url: str = "", transport: str = "http") -> dict:
    if not _NAME_RE.fullmatch(name or ""):
        return {"ok": False, "error": "server 名称只能含小写字母/数字/下划线/连字符"}
    mcp = get_mcp_manager()
    cfg = mcp.add_server(name, command=command, args=args, env=env,
                         url=url or None, transport=transport)
    # 连接测试：能枚举到工具才算可用（工具先测可用再进名单）
    r = mcp.fetch_tools(name)
    if not r.get("ok"):
        mcp.remove_server(name)
        return {"ok": False, "error": f"连接 MCP server 失败，已回滚配置: {r.get('error')}"}
    return {
        "ok": True, "server": name, "config": cfg,
        "tools": [t["name"] for t in r.get("tools", [])],
        "count": r.get("count", 0),
        "note": f"连接成功，枚举到 {r.get('count', 0)} 个工具。调 mcp_scan 同步进工具名单后即可调用。",
    }


@tool(
    "mcp_scan",
    "同步已配置 MCP server 的工具进工具名单（命名 mcp_<server>_<tool>）。"
    "枚举成功的 server 工具才注册，失败的记录原因。同步后即可像普通工具一样被 LLM 调用。",
    {
        "type": "object",
        "properties": {
            "server": {"type": "string",
                       "description": "可选。只同步指定 server；留空同步全部已配置 server"},
        },
    },
)
def run_scan(server: str = "") -> dict:
    from core.registry import ToolRegistry
    mcp = get_mcp_manager()
    reg = ToolRegistry()
    reg.load()
    if server:
        if server not in mcp.servers:
            return {"ok": False, "error": f"MCP server 未配置: {server}"}
        reg.remove_mcp_server(server)
        r = mcp.fetch_tools(server)
        if not r.get("ok"):
            return {"ok": False, "error": f"枚举 {server} 失败: {r.get('error')}"}
        added = []
        for t in r.get("tools", []):
            tn = f"mcp_{server}_{t['name']}"
            reg.register_mcp(tn, server=server, tool=t["name"],
                             description=t.get("description", ""), schema=t.get("inputSchema"))
            added.append(tn)
        reg.save()
        return {"ok": True, "server": server, "added": added, "count": len(added)}
    res = mcp.sync_to_registry(reg)
    return {"ok": True, **res}


@tool(
    "mcp_list",
    "查看已配置的 MCP server 及各自可用的工具（或指定 server 的详细工具列表）。",
    {
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "可选。指定 server 则返回该 server 工具详情"},
        },
    },
)
def run_list(server: str = "") -> dict:
    mcp = get_mcp_manager()
    if server:
        r = mcp.fetch_tools(server)
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error")}
        return {"ok": True, "server": server, "tools": r.get("tools", []),
                "count": r.get("count", 0)}
    out = []
    for name, cfg in mcp.servers.items():
        out.append({
            "server": name,
            "type": "stdio" if "command" in cfg else cfg.get("transport", "http"),
            "command": cfg.get("command"), "url": cfg.get("url"),
        })
    return {"ok": True, "servers": out, "count": len(out)}


@tool(
    "mcp_disconnect",
    "移除一个 MCP server 的配置，并清理其同步进工具名单的工具（只清该 server 的，不影响其他 server）。",
    {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "要移除的 MCP server 名称"}},
        "required": ["name"],
    },
)
def run_disconnect(name: str) -> dict:
    from core.registry import ToolRegistry
    mcp = get_mcp_manager()
    removed = mcp.remove_server(name)
    reg = ToolRegistry()
    reg.load()
    n = reg.remove_mcp_server(name)
    reg.save()
    return {
        "ok": True, "server": name, "server_existed": removed, "cleaned": n,
        "note": "已移除配置并清理该 server 的工具名单条目。",
    }

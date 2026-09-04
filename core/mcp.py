"""MCP 万能接口（Model Context Protocol 客户端管理器）。

设计意图（见设计文档 5.37）：
- MCP 是开放协议，MCP server 通过标准 tools/resources 暴露能力（如 blender-mcp 用
  `uvx blender-mcp` 启动 stdio server，暴露 blender_* 工具）。
- 白绫作为 MCP **客户端**：连接本机/远程 MCP server，把其工具注册进工具注册表
  （命名 `mcp_<server>_<tool>`），执行时转发调用——实现对 Blender、文件系统、浏览器等
  支持 API 的外部软件的标准化操控。
- 配置：`config/mcp.json` → {"servers": {"<name>": {...}}}。
  三种连接形态：
  - stdio 子进程：{"command": "uvx", "args": [...], "env": {...}}
  - HTTP streamable：{"url": "https://...", "transport": "http"}
  - SSE：{"url": "https://.../sse", "transport": "sse"}
- 工具先测可用再入名单：sync_to_registry 枚举成功才注册；连接失败标记 error 并报告。
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List, Optional, Tuple


def _mcp_libs():
    """延迟导入 mcp SDK（未安装时抛出，由调用方给出安装提示）。"""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client
    from mcp.client.sse import sse_client
    return (ClientSession, StdioServerParameters, stdio_client,
            streamable_http_client, sse_client)


class McpManager:
    """MCP server 配置、连接、工具枚举与调用管理（同步接口，内部 asyncio 封装）。"""

    def __init__(self, config_path: str = "config/mcp.json"):
        self.config_path = config_path
        self.servers: Dict[str, Dict] = {}
        self.load()

    # ---------- 配置持久化 ----------
    def load(self) -> Dict[str, Dict]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self.servers = (json.load(f) or {}).get("servers", {}) or {}
            except (OSError, json.JSONDecodeError):
                self.servers = {}
        else:
            self.servers = {}
        return self.servers

    def save(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.config_path)), exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({"servers": self.servers}, f, ensure_ascii=False, indent=2)

    def add_server(self, name: str, command: Optional[str] = None, args: Optional[List[str]] = None,
                   env: Optional[Dict[str, str]] = None, url: Optional[str] = None,
                   transport: str = "http") -> Dict:
        """添加/更新 MCP server 配置。url 优先；否则用 command（默认 uvx）。"""
        cfg: Dict[str, Any] = {}
        if url:
            cfg = {"url": url.strip(), "transport": transport}
            if env:
                cfg["env"] = env
        else:
            cfg["command"] = (command or "uvx").strip()
            if args:
                cfg["args"] = list(args)
            if env:
                cfg["env"] = env
        self.servers[name] = cfg
        self.save()
        return cfg

    def remove_server(self, name: str) -> bool:
        existed = name in self.servers
        self.servers.pop(name, None)
        self.save()
        return existed

    # ---------- 连接与调用（async 内核） ----------
    async def _connect(self, name: str, cfg: Dict) -> Tuple[Any, Any]:
        """建立连接，返回 (session, transport_ctx)。调用方负责 __aexit__ 清理。"""
        ClientSession, StdioServerParameters, stdio_client, streamable_http_client, sse_client = _mcp_libs()
        headers = cfg.get("headers")
        if cfg.get("url"):
            if cfg.get("transport") == "sse":
                ctx = sse_client(cfg["url"], headers=headers)
            else:
                ctx = streamable_http_client(cfg["url"], headers=headers)
            read, write = await ctx.__aenter__()
            session = ClientSession(read, write)
            await session.__aenter__()
            await session.initialize()
            return session, ctx
        # stdio 子进程（uvx/npx/python 等）
        params = StdioServerParameters(
            command=cfg.get("command", "uvx"),
            args=cfg.get("args") or [],
            env=cfg.get("env"),
        )
        ctx = stdio_client(params)
        read, write = await ctx.__aenter__()
        session = ClientSession(read, write)
        await session.__aenter__()
        await session.initialize()
        return session, ctx

    @staticmethod
    def _run(coro):
        """同步封装 async 协程（白绫主循环为同步，无运行中 event loop）。"""
        return asyncio.run(coro)

    async def _fetch_tools_async(self, name: str, cfg: Dict) -> List[Dict]:
        session, ctx = await self._connect(name, cfg)
        try:
            res = await session.list_tools()
            tools = []
            for t in res.tools:
                schema = getattr(t, "inputSchema", None) or {"type": "object", "properties": {}}
                tools.append({
                    "name": t.name,
                    "description": t.description or "",
                    "inputSchema": schema,
                })
            return tools
        finally:
            await session.__aexit__(None, None, None)
            await ctx.__aexit__(None, None, None)

    async def _call_async(self, name: str, cfg: Dict, tool: str, args: Dict) -> Dict:
        session, ctx = await self._connect(name, cfg)
        try:
            res = await session.call_tool(tool, args or {})
            texts = []
            for c in (res.content or []):
                txt = getattr(c, "text", None)
                texts.append(str(txt) if txt is not None else str(c))
            return {
                "ok": not bool(getattr(res, "isError", False)),
                "result": "\n".join(texts),
            }
        finally:
            await session.__aexit__(None, None, None)
            await ctx.__aexit__(None, None, None)

    # ---------- 同步对外接口 ----------
    def fetch_tools(self, server: str) -> Dict:
        """枚举指定 server 的工具列表。"""
        cfg = self.servers.get(server)
        if not cfg:
            return {"ok": False, "error": f"MCP server 未配置: {server}"}
        try:
            tools = self._run(self._fetch_tools_async(server, cfg))
            return {"ok": True, "server": server, "tools": tools, "count": len(tools)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "server": server, "error": f"{type(e).__name__}: {e}"}

    def call(self, server: str, tool: str, args: Dict) -> Dict:
        """调用 MCP 工具。返回 {"ok", "result"} 或 {"ok": False, "error"}。"""
        cfg = self.servers.get(server)
        if not cfg:
            return {"ok": False, "error": f"MCP server 未配置: {server}"}
        try:
            return self._run(self._call_async(server, cfg, tool, args))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def sync_to_registry(self, registry) -> Dict:
        """枚举所有已配置 server 的工具，注册进 registry（命名 mcp_<server>_<tool>）。

        工具先测可用再入名单：枚举成功的 server 工具才注册；失败的记录 error 不注册。
        重同步前清理旧的 MCP 来源工具（server 可能已删除/改名）。
        """
        added: List[str] = []
        failed: List[Dict] = []
        registry.remove_mcp_all()  # 先清旧 MCP 工具，避免残留
        for name, cfg in self.servers.items():
            r = self.fetch_tools(name)
            if not r.get("ok"):
                failed.append({"server": name, "error": r.get("error", "未知错误")})
                continue
            for t in r.get("tools", []):
                tool_name = f"mcp_{name}_{t['name']}"
                registry.register_mcp(
                    tool_name,
                    server=name,
                    tool=t["name"],
                    description=t.get("description", ""),
                    schema=t.get("inputSchema"),
                )
                added.append(tool_name)
        registry.save()
        return {"ok": True, "added": added, "failed": failed, "count": len(added)}


# ---------- 全局单例（供内置工具/registry 访问） ----------
_mcp_manager: Optional[McpManager] = None


def get_mcp_manager(config_path: str = "config/mcp.json") -> McpManager:
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = McpManager(config_path)
    return _mcp_manager

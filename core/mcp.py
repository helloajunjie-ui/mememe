"""MCP 客户端（HTTP 网关版）。

架构（与 Go LLM 网关对称）：
- MCP 独立服务（mcp-service/server.py，端口 8767）统一管理所有软件接口的连接、
  激活状态与工具调用；素月侧本模块只是轻量 HTTP 客户端。
- 按需激活：素月先看目录（mcp_list）→ 激活（mcp_connect）→ 工具 schema 临时注入
  对话 → 调用（mcp_<server>_<tool>）→ 释放（mcp_disconnect）。MCP 工具不常驻核心工具。
- 服务未运行时自动拉起（后台无窗口进程）。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from typing import Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

DEFAULT_URL = os.environ.get("MCP_SERVICE_URL", "http://127.0.0.1:8767")


class McpManager:
    """MCP 服务 HTTP 客户端（保持旧接口名：servers/fetch_tools/call/add_server/remove_server）。"""

    def __init__(self, config_path: str = "config/mcp.json", service_url: str = DEFAULT_URL):
        self.config_path = config_path
        self._url = service_url.rstrip("/")
        # 本目录 = core/ 的上一级（self-agent/），服务目录 = self-agent/mcp-service
        self._service_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp-service")
        self.servers: Dict[str, Dict] = {}
        self.load()

    # ---------- 配置（本地只读镜像，真源在 mcp-service/config/mcp.json） ----------
    def load(self) -> Dict[str, Dict]:
        """读取服务端配置镜像（供前缀解析/列表展示；实际状态以服务 API 为准）。"""
        cfg_path = os.path.join(self._service_dir, "config", "mcp.json")
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                self.servers = (json.load(f) or {}).get("servers", {}) or {}
        except (OSError, json.JSONDecodeError):
            self.servers = {}
        return self.servers

    def save(self) -> None:
        """本地镜像保存：直接写服务端配置文件（服务进程每次请求前会重读吗？
        不会——服务端 McpManager 常驻内存。本地镜像仅供展示，真正新增走 /mcp/add。"""
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.config_path)), exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump({"servers": self.servers}, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    # ---------- 服务生命周期 ----------
    def _alive(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", 8767), timeout=1):
                return True
        except OSError:
            return False

    def ensure_running(self, wait: float = 20.0) -> bool:
        """MCP 服务未运行则自动拉起（后台无窗口），返回是否就绪。"""
        if self._alive():
            return True
        script = os.path.join(self._service_dir, "server.py")
        if not os.path.exists(script):
            return False
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(
                [sys.executable, "-X", "utf8", script],
                cwd=self._service_dir,
                creationflags=flags,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError:
            return False
        t0 = time.time()
        while time.time() - t0 < wait:
            if self._alive():
                return True
            time.sleep(0.3)
        return False

    def _req(self, method: str, path: str, timeout: float = 120.0, **kw) -> Dict:
        if requests is None:
            return {"ok": False, "error": "requests 库不可用"}
        try:
            r = requests.request(method, self._url + path, timeout=timeout, **kw)
            return r.json()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"MCP 服务不可达({self._url}): {type(e).__name__}: {e}"}

    # ---------- 目录与激活流程 ----------
    def servers_info(self) -> Dict:
        """软件接口目录（含 desc/激活状态/工具数）——素月决策激活哪个的依据。"""
        return self._req("GET", "/mcp/servers")

    def activate(self, server: str, force: bool = False) -> Dict:
        """激活：拉取并缓存工具清单。激活后素月装配其 schema 即可调用。"""
        return self._req("POST", "/mcp/activate", json={"server": server, "force": force})

    def deactivate(self, server: str) -> Dict:
        """释放：工具清单从素月上下文移除。"""
        return self._req("POST", "/mcp/deactivate", json={"server": server})

    def schemas_for_active(self) -> List[Dict]:
        """拉取所有已激活 server 的 OpenAI function schema（agent 装配 tools 用）。"""
        r = self._req("GET", "/mcp/active")
        if not r.get("ok"):
            return []
        out: List[Dict] = []
        for srv in r.get("active", {}):
            s = self._req("GET", f"/mcp/schemas?server={srv}")
            if s.get("ok"):
                out.extend(s.get("schemas", []))
        return out

    def active_servers(self) -> List[str]:
        r = self._req("GET", "/mcp/active")
        return list((r.get("active") or {}).keys()) if r.get("ok") else []

    # ---------- 工具枚举与调用（HTTP 代理） ----------
    def fetch_tools(self, server: str) -> Dict:
        """枚举指定 server 的工具列表（已激活则返回缓存，未激活则实时拉取）。"""
        return self._req("GET", f"/mcp/tools?server={server}")

    def call(self, server: str, tool: str, args: Dict, timeout: float = 600.0,
             cancel_event=None) -> Dict:
        """调用 MCP 工具（转发给独立服务执行）。

        timeout 默认 600s：写操作（发布/编辑/删除/回复）走浏览器自动化，
        实测单次发布约 150s+，远超只读工具的秒级耗时；沿用 120s 会把
        已成功的发布误判为失败，且客户端断开会连带中断服务端在跑的子进程。

        2026-09-20：支持 cancel_event——用户点停止时立即返回"被取消"，
        不再干等 HTTP timeout。底层用 Thread 包装 requests，cancel_event
        置位就返回，thread 后台跑完丢弃结果。
        """
        if cancel_event is None:
            return self._req("POST", "/mcp/call",
                             json={"server": server, "tool": tool, "args": args or {}},
                             timeout=timeout)
        # 可中断版本：主线程等 cancel_event 或请求完成
        import threading
        result_box = {}
        def _do():
            try:
                result_box["r"] = self._req("POST", "/mcp/call",
                                            json={"server": server, "tool": tool, "args": args or {}},
                                            timeout=timeout)
            except Exception as e:  # noqa: BLE001
                result_box["r"] = {"ok": False, "error": f"MCP 调用异常: {e}"}
        t = threading.Thread(target=_do, daemon=True)
        t.start()
        # 每 0.5 秒查一次 cancel_event 或 thread 是否完成
        while t.is_alive():
            if cancel_event.is_set():
                return {"ok": False, "error": "工具被用户终止（cancel）",
                        "_cancelled": True}
            t.join(timeout=0.5)
        return result_box.get("r", {"ok": False, "error": "MCP 调用无返回"})

    # ---------- 配置管理（HTTP 代理到服务端） ----------
    def add_server(self, name: str, command: Optional[str] = None,
                   args: Optional[List[str]] = None, env: Optional[Dict[str, str]] = None,
                   url: Optional[str] = None, transport: str = "http",
                   desc: str = "") -> Dict:
        """添加/更新软件接口配置（保存到服务端，立即生效）。"""
        return self._req("POST", "/mcp/add",
                         json={"name": name, "desc": desc, "command": command,
                               "args": args, "env": env, "url": url,
                               "transport": transport})

    def remove_server(self, name: str) -> bool:
        r = self._req("POST", "/mcp/remove", json={"name": name})
        return bool(r.get("removed"))

    # ---------- 凭据库（按软件接口隔离） ----------
    def set_secret(self, server: str, key: str, value: str) -> Dict:
        return self._req("POST", "/mcp/secrets",
                         json={"server": server, "key": key, "value": value})

    def remove_secret(self, server: str, key: str) -> Dict:
        return self._req("POST", "/mcp/secrets/remove",
                         json={"server": server, "key": key})

    def secret_keys(self, server: str = "") -> Dict:
        """查询凭据键名。server 留空返回全部接口的键名（只含键名，不回传值）。"""
        path = "/mcp/secrets" + (f"?server={server}" if server else "")
        r = self._req("GET", path)
        return r

    # ---------- 依赖评估与安装 ----------
    def assess_deps(self, server: str) -> Dict:
        return self._req("POST", "/mcp/deps/assess", json={"server": server})

    def install_deps(self, server: str, upgrade: bool = False) -> Dict:
        return self._req("POST", "/mcp/deps/install",
                         json={"server": server, "upgrade": upgrade}, timeout=600)

    def sync_to_registry(self, registry) -> Dict:
        """兼容旧调用：新架构不注册进 registry，返回目录信息。"""
        return {"ok": True, "added": [], "failed": [], "count": 0,
                "note": "MCP 已独立为服务（mcp-service）：工具按需激活注入，不再注册进核心工具库。"
                        "用 mcp_list 看目录，mcp_connect 激活。"}

    # ---------- 工具名解析 ----------
    def match_server(self, tool_name: str) -> Optional[str]:
        """从 mcp_<server>_<tool> 解析出 server 名（最长前缀匹配，兼容 server 含下划线）。

        本地镜像可能陈旧（运行中经 /mcp/add 新增的软件不会自动进镜像），
        故未命中时重载一次镜像再试——否则新软件调用一律报"工具不存在"。
        """
        if not tool_name.startswith("mcp_"):
            return None
        for srv in sorted(self.servers, key=len, reverse=True):
            if tool_name.startswith(f"mcp_{srv}_"):
                return srv
        if self.load():  # 自愈：镜像陈旧则重载后重试
            for srv in sorted(self.servers, key=len, reverse=True):
                if tool_name.startswith(f"mcp_{srv}_"):
                    return srv
        return None


# ---------- 全局单例（供内置工具/registry 访问） ----------
_mcp_manager: Optional[McpManager] = None


def get_mcp_manager(config_path: str = "config/mcp.json") -> McpManager:
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = McpManager(config_path)
    return _mcp_manager

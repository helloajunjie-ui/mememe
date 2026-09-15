"""MCP 独立服务核心（自包含，从素月 core/mcp.py 提炼，去掉 registry 依赖）。

设计：
- MCP 是开放协议，MCP server 通过标准 tools 暴露能力（blender-mcp / gobot-mcp /
  filesystem / playwright 等），本模块作为 MCP **客户端**管理这些连接。
- 新增"激活"概念：activate(server) 拉取并缓存工具清单；deactivate 释放。
  素月侧只在激活状态下装配对应工具 schema（按需激活，不常驻核心工具）。
- 独立进程运行，供素月通过 HTTP API 调用，与 LLM 网关（Go）架构对称。
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import re
import shutil
import subprocess
import urllib.request
import sys
import threading
import time
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

    def __init__(self, config_path: str):
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

    def add_server(self, name: str, command: Optional[str] = None,
                   args: Optional[List[str]] = None, env: Optional[Dict[str, str]] = None,
                   url: Optional[str] = None, transport: str = "http") -> Dict:
        """添加/更新 MCP server 配置。url 优先；否则用 command。"""
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
        # stdio 子进程（npx/python/exe 等）
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
        """同步封装 async 协程（服务为同步 HTTP 层，无运行中 event loop）。"""
        return asyncio.run(coro)

    async def _fetch_tools_async(self, name: str, cfg: Dict) -> List[Dict]:
        # 连接超时保护：挂起/无响应 server 快速失败，不阻塞整个请求
        return await asyncio.wait_for(self._fetch_tools_inner(name, cfg), timeout=45)

    async def _fetch_tools_inner(self, name: str, cfg: Dict) -> List[Dict]:
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
        return await asyncio.wait_for(self._call_inner(name, cfg, tool, args), timeout=120)

    async def _call_inner(self, name: str, cfg: Dict, tool: str, args: Dict) -> Dict:
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
                "is_error": bool(getattr(res, "isError", False)),
            }
        finally:
            await session.__aexit__(None, None, None)
            await ctx.__aexit__(None, None, None)

    # ---------- 同步对外接口 ----------
    def fetch_tools(self, server: str, cfg: Optional[Dict] = None) -> Dict:
        """枚举指定 server 的工具列表。"""
        base = cfg or self.servers.get(server)
        if not base:
            return {"ok": False, "error": f"MCP server 未配置: {server}"}
        try:
            tools = self._run(self._fetch_tools_async(server, base))
            return {"ok": True, "server": server, "tools": tools, "count": len(tools)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "server": server, "error": f"{type(e).__name__}: {e}"}

    def call(self, server: str, tool: str, args: Dict, cfg: Optional[Dict] = None) -> Dict:
        """调用 MCP 工具。返回 {"ok", "result"} 或 {"ok": False, "error"}。"""
        base = cfg or self.servers.get(server)
        if not base:
            return {"ok": False, "error": f"MCP server 未配置: {server}"}
        try:
            return self._run(self._call_async(server, base, tool, args))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}


class McpGateway:
    """MCP 服务门面：配置 + 激活状态 + 工具缓存。HTTP 层只依赖本类。"""

    def __init__(self, config_path: str, data_path: str,
                 idle_timeout_min: Optional[float] = None):
        self.mgr = McpManager(config_path)
        self.data_path = data_path
        self._lock = threading.Lock()
        self.active: Dict[str, List[Dict]] = {}   # server -> tools 缓存
        # 空闲自动释放：激活的软件接口超过 idle_timeout_min 无调用 → 自动释放
        self.idle_timeout_min = idle_timeout_min
        if self.idle_timeout_min is None:
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    _cfg = json.load(f) or {}
            except (OSError, json.JSONDecodeError):
                _cfg = {}
            self.idle_timeout_min = float(
                _cfg.get("idle_timeout_min") or os.environ.get("MCP_IDLE_TIMEOUT_MIN", "30"))
            # 国内镜像（可开关：置空字符串禁用）
            self.npm_registry = str(_cfg.get("npm_registry") or "").strip()
            self.pip_index = str(_cfg.get("pip_index") or "").strip()
            self.playwright_host = str(_cfg.get("playwright_host") or "").strip()
        self.last_used: Dict[str, float] = {}     # server -> 最后使用时间戳
        # 凭据库（按 server 隔离）：config/secrets.json，{server: {KEY: 值}}
        self.secrets_path = os.path.join(os.path.dirname(os.path.abspath(config_path)),
                                         "secrets.json")
        self.secrets: Dict[str, Dict[str, str]] = {}
        self.load_secrets()
        self.load_active()
        # 重启后：已激活的 server 重新计时（视为刚激活）
        for srv in self.active:
            self.last_used[srv] = time.time()

    # ---------- 凭据库（按 server 隔离） ----------
    def load_secrets(self) -> None:
        if os.path.exists(self.secrets_path):
            try:
                with open(self.secrets_path, "r", encoding="utf-8") as f:
                    self.secrets = (json.load(f) or {}) or {}
            except (OSError, json.JSONDecodeError):
                self.secrets = {}
        else:
            self.secrets = {}

    def save_secrets(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.secrets_path)), exist_ok=True)
        with open(self.secrets_path, "w", encoding="utf-8") as f:
            json.dump(self.secrets, f, ensure_ascii=False, indent=2)

    def secret_keys(self, server: str) -> List[str]:
        """只返回该软件接口已存的凭据键名（永不回传值）。"""
        return sorted((self.secrets.get(server) or {}).keys())

    def set_secret(self, server: str, key: str, value: str) -> Dict:
        if not server or not key:
            return {"ok": False, "error": "server 与 key 必填"}
        with self._lock:
            self.secrets.setdefault(server, {})[key] = value
            self.save_secrets()
            return {"ok": True, "server": server, "key": key,
                    "note": "已保存（按软件接口隔离存储，值不回传）"}

    def remove_secret(self, server: str, key: str) -> Dict:
        with self._lock:
            bucket = self.secrets.get(server)
            if not bucket or key not in bucket:
                return {"ok": False, "error": f"{server} 下无凭据 {key}"}
            del bucket[key]
            if not bucket:
                self.secrets.pop(server, None)
            self.save_secrets()
            return {"ok": True, "server": server, "key": key, "removed": True}

    def _eff_cfg(self, server: str, cfg: Dict) -> Dict:
        """凭据注入 + 国内镜像注入：stdio 型 → 合并进启动环境变量；HTTP 型 → 合并进请求头
        （COOKIE 键映射为 Cookie 请求头，其余键名作为 header 名）。
        npm 型接口注入 npm 镜像 registry；playwright 注入浏览器下载镜像。"""
        sec = self.secrets.get(server) or {}
        if "url" in cfg:
            headers = dict(cfg.get("headers") or {})
            for k, v in sec.items():
                headers["Cookie" if k.upper() == "COOKIE" else k] = v
            return {**cfg, "headers": headers}
        env = dict(cfg.get("env") or {})
        env.update(sec)
        low = str(cfg.get("command", "")).lower()
        if self.npm_registry and "npx" in low:
            env.setdefault("npm_config_registry", self.npm_registry)
        if self.playwright_host and server == "playwright":
            env.setdefault("PLAYWRIGHT_DOWNLOAD_HOST", self.playwright_host)
        return {**cfg, "env": env}



    # ---------- 依赖评估与安装 ----------
    _VER_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")

    def _locate_app(self, exe: str) -> str:
        """定位本体软件可执行文件：PATH → 常见固定目录 → 版本目录 glob。找不到返回空串。"""
        hit = shutil.which(exe)
        if hit:
            return hit
        for base in (r"C:\Program Files", r"C:\Program Files (x86)",
                     r"C:\Program Files\nodejs", r"C:\Python310"):
            cand = os.path.join(base, exe)
            if os.path.exists(cand):
                return cand
        # 版本目录：Blender Foundation\Blender*\blender.exe、Kingsoft\WPS Office\**\wps.exe
        for pattern in (rf"C:\Program Files\**\{exe}", rf"C:\Program Files (x86)\**\{exe}"):
            try:
                hits = glob.glob(pattern, recursive=True)
                if hits:
                    return hits[0]
            except (OSError, ValueError):
                continue
        return ""

    def _read_version(self, path: str) -> str:
        """读可执行文件版本号（--version / -v / -V / version 按需）。读不到返回空串。"""
        for flag in ("--version", "-v", "-V", "version"):
            try:
                r = subprocess.run([path, flag], capture_output=True, text=True,
                                   timeout=8, errors="replace",
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                text = (r.stdout or "") + " " + (r.stderr or "")
                m = self._VER_RE.search(text)
                if m:
                    return m.group(1)
            except Exception:  # noqa: BLE001
                continue
        return ""

    def _ver_ge(self, ver: str, minimum: str) -> bool:
        """点分数字版本比较：ver >= minimum。解析失败时保守返回 False（提示用户核验）。"""
        try:
            a = [int(x) for x in re.findall(r"\d+", ver)[:3]]
            b = [int(x) for x in re.findall(r"\d+", minimum)[:3]]
            while len(a) < 3:
                a.append(0)
            while len(b) < 3:
                b.append(0)
            return a >= b
        except (TypeError, ValueError):
            return False

    def _pip_index_latest(self, pkg: str) -> str:
            """HTTP 请求镜像 simple 页解析最新版（不依赖 pip index 子进程，规避镜像限流）。
            失败返回空串。"""
            base = (self.pip_index or "https://pypi.org/simple").rstrip("/")
            url = f"{base}/{pkg}/"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "mcp-service"})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    html = resp.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                return ""
            name = pkg.replace("-", "_").lower()
            versions = set()
            for m in re.finditer(rf"{re.escape(name)}[-_]([0-9][0-9A-Za-z._]*)\.(?:tar\.gz|whl)", html):
                v = m.group(1).split(".post")[0].split(".dev")[0].split("+")[0]
                if re.match(r"^\d+\.\d+", v):
                    versions.add(v)
            if not versions:
                return ""
            return max(versions, key=lambda v: [int(x) for x in re.findall(r"\d+", v)[:3]] or [0, 0, 0])

    def _pip_latest(self, pkg: str) -> Optional[Dict]:
        """查 pip 包已装版本 vs 最新版（清华镜像）。返回 None 表示查询失败。"""
        try:
            r = subprocess.run([sys.executable, "-m", "pip", "show", pkg],
                               capture_output=True, text=True, timeout=20)
            installed = ""
            for line in r.stdout.splitlines():
                if line.lower().startswith("version:"):
                    installed = line.split(":", 1)[1].strip()
                    break
            if not installed:
                return {"installed": "", "latest": "", "has_update": False}
            latest = self._pip_index_latest(pkg)
            if not latest:
                return {"installed": installed, "latest": "", "has_update": False}
            return {"installed": installed, "latest": latest,
                    "has_update": self._ver_ge(latest, installed) and latest != installed}
        except Exception:  # noqa: BLE001
            return None

    def _npm_latest(self, pkg: str) -> Optional[Dict]:
        """查 npm 包最新版（npmmirror）。返回 None 表示查询失败。"""
        npm = shutil.which("npm") or r"C:\Program Files\nodejs\npm.cmd"
        try:
            env = dict(os.environ)
            if self.npm_registry:
                env["npm_config_registry"] = self.npm_registry
            out = subprocess.run([npm, "view", pkg, "version", "--json"],
                                 capture_output=True, text=True, timeout=25, env=env)
            if out.returncode != 0:
                return None
            latest = json.loads(out.stdout)
            if isinstance(latest, list):
                latest = latest[-1] if latest else ""
            return {"installed": "(npx 自动拉取)", "latest": str(latest or ""),
                    "has_update": False}
        except Exception:  # noqa: BLE001
            return None

    def _npm_view(self, pkg: str) -> Optional[Dict]:
        """查 npm registry：包是否存在 + 解压体积（MB）。不存在返回 None。"""
        npm = shutil.which("npm") or r"C:\Program Files\nodejs\npm.cmd"
        try:
            out = subprocess.run([npm, "view", pkg, "dist", "--json"],
                                 capture_output=True, text=True, timeout=25)
            if out.returncode != 0:
                return None
            d = json.loads(out.stdout)
            size = d.get("unpackedSize") or d.get("tarballSize") or 0
            return {"size_mb": round(size / 1048576, 1) if size else 0}
        except Exception:  # noqa: BLE001
            return None

    def analyze_deps(self, server: str) -> Dict:
        """评估某软件接口的依赖/凭据状态，产出安装计划（供素月通知用户确认）。"""
        cfg = self.mgr.servers.get(server)
        if not cfg:
            return {"ok": False, "error": f"MCP server 未配置: {server}"}
        cmd = str(cfg.get("command", ""))
        args = [str(a) for a in (cfg.get("args") or [])]
        issues: List[str] = []
        plan: Optional[Dict] = None
        # 1) 凭据检查：配置里 env 为空的占位 key（如 GITHUB_TOKEN: ""）
        # 1.5) 本体软件版本校验：app_exe + min_version（如 blender.exe >= 3.0）
        app_exe = cfg.get("app_exe")
        min_ver = str(cfg.get("min_version") or "").strip()
        if app_exe and min_ver:
            path = self._locate_app(app_exe)
            ver = self._read_version(path) if path else ""
            if not path:
                issues.append(f"本体软件未找到: {app_exe}（装好后本接口才能用；"
                              f"装的位置素月可用 app_probe 探查）")
            elif not ver:
                issues.append(f"本体 {app_exe} 已找到（{path}），但版本读取失败，"
                              f"无法校验是否 >= {min_ver}")
            elif not self._ver_ge(ver, min_ver):
                issues.append(f"本体版本过低: {app_exe} 当前 {ver}，需要 >= {min_ver}"
                              f"（{cfg.get('version_note') or ''}）")
        # 1.6) 升级检查：check_update 且为 pip/npm 型时查最新版
        if cfg.get("check_update"):
            pip_pkg_c = cfg.get("pip")
            if pip_pkg_c:
                latest = self._pip_latest(pip_pkg_c)
                if latest and latest.get("has_update"):
                    issues.append(f"可升级: {pip_pkg_c} 已装 {latest['installed']}，"
                                  f"最新 {latest['latest']}（确认后可 mcp_install 升级）")
            low2 = cmd.lower()
            if "npx" in low2:
                pkg2 = next((a for a in args if a and not a.startswith("-")), None)
                if pkg2:
                    latest = self._npm_latest(pkg2)
                    if latest and latest.get("has_update"):
                        issues.append(f"可升级: {pkg2} 最新 {latest['latest']}"
                                      f"（npx 激活时自动拉取最新，通常无需手动）")
        for k, v in (cfg.get("env") or {}).items():
            if isinstance(v, str) and not v.strip():
                issues.append(f"缺少凭据 {k}（用 mcp_key_set 保存 {server} 的 {k}）")
        # 2) pip 依赖（配置里显式声明 "pip": "包名"）
        pip_pkg = cfg.get("pip")
        if pip_pkg:
            try:
                r = subprocess.run([sys.executable, "-m", "pip", "show", pip_pkg],
                                   capture_output=True, text=True, timeout=20)
                if r.returncode != 0:
                    issues.append(f"Python 包未安装: {pip_pkg}")
                    plan = {"type": "pip", "action": "install", "pkg": pip_pkg,
                            "size_mb": None, "est_sec": 60,
                            "env_impact": f"pip 安装到当前 Python 环境（{sys.executable}，"
                                          f"素月 venv，隔离不影响系统 Python）"}
            except Exception:  # noqa: BLE001
                issues.append(f"无法检查 Python 包: {pip_pkg}")
        # 3) 命令类型判定
        low = cmd.lower()
        if "npx" in low:
            pkg = next((a for a in args if a and not a.startswith("-")), None)
            if pkg:
                info = self._npm_view(pkg)
                if info is None:
                    issues.append(f"npm 包不存在: {pkg}（包名可能有误或已下架）")
                    plan = {"type": "npm", "action": "check", "pkg": pkg,
                            "size_mb": None, "est_sec": None,
                            "env_impact": "无需安装；请核实包名后更新配置（mcp/add 或 "
                                          "mcp-service/config/mcp.json）"}
                else:
                    est = max(10, round(info["size_mb"] / 4))
                    issues.append(f"npm 包 {pkg} 首次激活时自动下载"
                                  f"（约 {info['size_mb']} MB，预计 {est} 秒）")
                    plan = {"type": "npm", "action": "auto", "pkg": pkg,
                            "size_mb": info["size_mb"], "est_sec": est,
                            "env_impact": "下载到 npm 缓存（%LOCALAPPDATA%\\npm-cache），"
                                          "不修改项目文件，不注册系统"}
        elif pip_pkg:
            pass  # 已在上方处理
        elif low.endswith(".py") or "python" in low:
            py_target = next((a for a in args
                              if not a.startswith("-") and (".py" in a or "/" in a or "\\" in a)),
                             None)
            if py_target and ".py" in py_target and not os.path.exists(py_target):
                issues.append(f"脚本不存在: {py_target}")
                plan = {"type": "file", "action": "check", "path": py_target,
                        "env_impact": "文件缺失：请确认对应软件已安装，或检查路径"}
        else:
            if not os.path.exists(cmd):
                issues.append(f"可执行文件不存在: {cmd}")
                if cfg.get("install_mode") == "manual":
                    # 大型桌面软件（3DMAX/Photoshop/Blender 本体等）：用户自行安装，
                    # 素月只说明，绝不自动下载（几 GB、需授权/序列号、环境复杂）
                    issues.append("这是大型软件，无法自动安装，需用户自行安装（下载安装包、"
                                  "按官方流程安装）；安装完成后重新 mcp_connect 即可。")
                    plan = {"type": "manual", "action": "user_install",
                            "pkg": cmd,
                            "size_mb": None, "est_sec": None,
                            "env_impact": "需用户手动下载并安装（大型桌面软件，"
                                          "涉及安装目录/授权/系统注册），素月不代装"}
                elif "ffmpeg" in low:
                    plan = {"type": "binary", "action": "install", "pkg": "ffmpeg",
                            "size_mb": 90, "est_sec": 180,
                            "env_impact": "winget 安装 ffmpeg（用户级，加入用户 PATH，"
                                          "可用 winget uninstall 卸载）"}
                else:
                    plan = {"type": "file", "action": "check", "path": cmd,
                            "env_impact": "可执行文件缺失，请确认软件已安装"}
        return {"ok": True, "server": server, "issues": issues, "plan": plan,
                "installable": bool(plan and plan.get("action") in ("auto", "install"))}

    def install_deps(self, server: str, upgrade: bool = False) -> Dict:
        """执行依赖安装/升级（素月已获得用户确认后调用）。

        upgrade=True 时对 pip 型执行 pip install -U（走镜像）；npm 型说明 npx 机制；
        大型软件（manual）仍拒绝代装。"""
        cfg = self.mgr.servers.get(server)
        if not cfg:
            return {"ok": False, "error": f"MCP server 未配置: {server}"}
        if upgrade:
            pip_pkg = cfg.get("pip")
            if pip_pkg:
                try:
                    cmd = [sys.executable, "-m", "pip", "install", "-U", "-q", pip_pkg]
                    if self.pip_index:
                        cmd += ["-i", self.pip_index]
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                    if r.returncode == 0:
                        return {"ok": True, "server": server, "pkg": pip_pkg,
                                "note": f"已升级 {pip_pkg} 到最新版（清华镜像），可重新 mcp_connect 激活。"}
                    return {"ok": False, "error": f"pip 升级失败: {r.stderr[-300:]}"}
                except Exception as e:  # noqa: BLE001
                    return {"ok": False, "error": f"pip 升级异常: {type(e).__name__}: {e}"}
            if "npx" in str(cfg.get("command", "")).lower():
                pkg = next((a for a in (cfg.get("args") or [])
                            if a and not a.startswith("-")), "")
                return {"ok": True, "server": server, "pkg": pkg,
                        "note": "npx 型接口激活时自动拉取最新版，无需手动升级；"
                                "如需强制重拉可清 %LOCALAPPDATA%\\npm-cache\\_npx 缓存后重新激活。"}
            return {"ok": False, "error": "该接口无自动升级方式（本体软件请用户自行升级："
                                          "装新版后 mcp_connect 重连即可）"}
        plan = self.analyze_deps(server).get("plan")
        if not plan:
            return {"ok": False, "error": "无需安装（依赖已就绪或不可自动安装）"}
        kind, action = plan["type"], plan["action"]
        if kind == "manual" and action == "user_install":
            return {"ok": False, "error": f"大型软件需用户自行安装（{plan['pkg']}），"
                                          f"素月不自动代装；安装完成后重新 mcp_connect 即可。"}
        if kind == "npm" and action == "auto":
            return {"ok": True, "server": server, "note": "npm 包无需预装：直接激活时自动下载。"}
        if kind == "npm" and action == "check":
            return {"ok": False, "error": f"npm 包不存在: {plan['pkg']}，请核实包名"}
        if kind == "pip" and action == "install":
            try:
                cmd = [sys.executable, "-m", "pip", "install", "-q", plan["pkg"]]
                if self.pip_index:
                    cmd += ["-i", self.pip_index]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if r.returncode == 0:
                    return {"ok": True, "server": server, "pkg": plan["pkg"],
                            "note": f"已安装 {plan['pkg']}，可重新 mcp_connect 激活。"}
                return {"ok": False, "error": f"pip 安装失败: {r.stderr[-300:]}"}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"pip 安装异常: {type(e).__name__}: {e}"}
        if kind == "binary" and action == "install":
            try:
                winget = shutil.which("winget")
                if not winget:
                    return {"ok": False, "error": "未找到 winget，请手动安装 ffmpeg 或提供下载地址"}
                r = subprocess.run(
                    [winget, "install", "--id", "Gyan.FFmpeg", "-e",
                     "--accept-source-agreements", "--accept-package-agreements",
                     "--disable-interactivity"],
                    capture_output=True, text=True, timeout=600)
                if r.returncode == 0 or "already installed" in (r.stdout + r.stderr):
                    return {"ok": True, "server": server, "note": "ffmpeg 已安装，可重新 mcp_connect 激活。"}
                return {"ok": False, "error": f"winget 安装失败: {(r.stdout + r.stderr)[-300:]}"}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"安装异常: {type(e).__name__}: {e}"}
        return {"ok": False, "error": f"不支持的安装计划: {kind}/{action}"}

    # ---------- 激活状态持久化 ----------
    def load_active(self) -> None:
        if os.path.exists(self.data_path):
            try:
                with open(self.data_path, "r", encoding="utf-8") as f:
                    self.active = (json.load(f) or {}).get("active", {}) or {}
            except (OSError, json.JSONDecodeError):
                self.active = {}

    def save_active(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.data_path)), exist_ok=True)
        with open(self.data_path, "w", encoding="utf-8") as f:
            json.dump({"active": self.active}, f, ensure_ascii=False, indent=2)

    # ---------- 对外操作 ----------
    def servers_info(self) -> List[Dict]:
        """配置的软件接口目录 + 状态（描述/激活/工具数）。素月据此决定激活哪个。"""
        out = []
        for name, cfg in self.mgr.servers.items():
            tools = self.active.get(name)
            out.append({
                "name": name,
                "desc": cfg.get("desc", ""),
                "type": "stdio" if "command" in cfg else cfg.get("transport", "http"),
                "configured": True,
                "active": tools is not None,
                "tool_count": len(tools) if tools else 0,
                "tool_names": [t["name"] for t in tools] if tools else [],
            })
        return out

    def activate(self, server: str, force: bool = False) -> Dict:
        """激活 server：拉取并缓存工具清单。force=True 强制重拉。"""
        with self._lock:
            if not force and server in self.active:
                self.last_used[server] = time.time()
                return {"ok": True, "server": server, "active": True,
                        "count": len(self.active[server]), "cached": True}
            r = self.mgr.fetch_tools(server, self._eff_cfg(server, self.mgr.servers.get(server) or {}))
            if not r.get("ok"):
                return r
            self.active[server] = r["tools"]
            self.last_used[server] = time.time()
            self.save_active()
            return {"ok": True, "server": server, "active": True,
                    "count": len(r["tools"]), "cached": False}

    def deactivate(self, server: str) -> Dict:
        with self._lock:
            existed = self.active.pop(server, None) is not None
            self.last_used.pop(server, None)
            self.save_active()
            return {"ok": True, "server": server, "active": False, "changed": existed}

    def idle_sweep(self) -> List[str]:
        """空闲清扫：超过 idle_timeout_min 无调用的激活 server 自动释放。返回被释放的列表。"""
        if self.idle_timeout_min <= 0:
            return []
        now = time.time()
        released = []
        with self._lock:
            for srv in list(self.active):
                last = self.last_used.get(srv, now)
                if now - last > self.idle_timeout_min * 60:
                    self.active.pop(srv, None)
                    self.last_used.pop(srv, None)
                    released.append(srv)
            if released:
                self.save_active()
        return released

    def tools(self, server: Optional[str] = None) -> Dict:
        if server:
            tools = self.active.get(server)
            return {"ok": tools is not None, "server": server,
                    "active": tools is not None, "tools": tools or []}
        return {"ok": True,
                "active": {k: [t["name"] for t in v] for k, v in self.active.items()}}

    def schemas(self, server: str) -> Dict:
        """已激活 server 的 OpenAI function-calling schema（素月装配 tools 用）。"""
        tools = self.active.get(server)
        if tools is None:
            return {"ok": False, "error": f"server 未激活: {server}"}
        schemas = []
        for t in tools:
            schemas.append({
                "type": "function",
                "function": {
                    "name": f"mcp_{server}_{t['name']}",
                    "description": t.get("description", ""),
                    "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
                },
            })
        return {"ok": True, "server": server, "schemas": schemas, "count": len(schemas)}

    def call(self, server: str, tool: str, args: Dict) -> Dict:
        """工具调用代理。未激活也可调用（按名直连），激活只影响 schema 注入。
        调用会刷新该 server 的最后使用时间（空闲自动释放的依据）。"""
        with self._lock:
            if server in self.active:
                self.last_used[server] = time.time()
        return self.mgr.call(server, tool, args or {},
                             self._eff_cfg(server, self.mgr.servers.get(server) or {}))

    def add(self, name: str, desc: str = "", command: Optional[str] = None,
            args: Optional[List[str]] = None, env: Optional[Dict[str, str]] = None,
            url: Optional[str] = None, transport: str = "http",
            install_mode: str = "", pip: str = "") -> Dict:
        """添加/更新软件接口配置（素月侧通过 API 新增来源）。"""
        with self._lock:
            cfg = self.mgr.add_server(name, command=command, args=args, env=env,
                                      url=url, transport=transport)
            if desc:
                cfg["desc"] = desc
            if install_mode:
                cfg["install_mode"] = install_mode
            if pip:
                cfg["pip"] = pip
            self.mgr.save()
            return {"ok": True, "server": name, "config": cfg}

    def remove(self, name: str) -> Dict:
        """移除软件接口配置，并释放其激活状态。"""
        with self._lock:
            self.active.pop(name, None)
            self.save_active()
            removed = self.mgr.remove_server(name)
            return {"ok": True, "server": name, "removed": removed}

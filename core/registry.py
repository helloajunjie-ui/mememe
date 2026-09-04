"""工具注册表（ToolRegistry）与工具分发执行。

设计意图（见设计文档 4.3 / 4.4）：
- 工具 = Agent 可调用的函数，对外以 OpenAI function schema 暴露，对内以 Python 函数或 Go 二进制实现。
- 统一调用协议：LLM 只看工具名和参数，不知道底层语言。
- 分发：python_function → importlib 动态加载；go_binary → 子进程 + stdin/stdout JSON。
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from tools.base import get_meta


def normalize_schema(schema: Any) -> Dict:
    """规范化工具参数 schema 为标准 JSON Schema（type=object）。

    支持两种输入，统一输出标准格式：
    1) 标准格式：{"type":"object","properties":{...},"required":[...]}
    2) 扁平参数表：{"name":{"type":"...","description":"...","required":true}, ...}
       —— 自动包装为 properties，并把字段级 required:true 收集进 required 数组。
    """
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    if schema.get("type") == "object" and isinstance(schema.get("properties"), dict):
        props = schema["properties"]
        required = list(schema.get("required", []))
        for k, v in props.items():
            if isinstance(v, dict) and "required" in v:
                if v.get("required") is True and k not in required:
                    required.append(k)
                v.pop("required", None)
        if required:
            schema["required"] = required
        return schema
    # 扁平参数表 → 标准格式
    properties, required = {}, []
    for k, v in schema.items():
        if isinstance(v, dict):
            if v.get("required") is True:
                required.append(k)
            v.pop("required", None)
            properties[k] = v
        else:
            properties[k] = {"type": "string", "description": str(v)}
    out = {"type": "object", "properties": properties}
    if required:
        out["required"] = required
    return out


class ToolRegistry:
    # 核心工具：每轮常驻（schema 全量给 LLM）；其余为长尾工具（世界书：目录可见，按需加载 schema）
    CORE_TOOLS = {
        "net_search", "net_fetch", "fs_list", "fs_read", "fs_search", "fs_stat",
        "memory_write", "memory_search", "cmd_run", "ws_mkdir", "ws_write", "sys_info",
        "ctx_search",
    }

    def __init__(self, path: str = "data/registry.json", tools_dir: str = "tools"):
        self.path = path
        self.tools_dir = tools_dir
        self.tools: Dict[str, Dict] = {}      # name -> registry entry
        self._fns: Dict[str, Callable] = {}   # name -> python 函数（动态加载）
        self.mcp: Any = None                  # MCP 管理器（McpManager，agent 启动时注入）

    # ---------- 持久化 ----------
    def load(self) -> bool:
        if not os.path.exists(self.path):
            return False
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.tools = data.get("tools", {})
        return True

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"tools": self.tools}, f, ensure_ascii=False, indent=2)

    # ---------- 内置工具发现 ----------
    def discover_builtin(self) -> int:
        """扫描 tools/src/python/*.py，动态加载带 @tool 装饰器的函数。"""
        src_dir = Path(self.tools_dir) / "src" / "python"
        if not src_dir.exists():
            return 0
        count = 0
        sys.path.insert(0, str(Path(self.tools_dir).parent))  # 让 "tools.base" 可导入
        for py in sorted(src_dir.glob("*.py")):
            if py.name.startswith("_"):
                continue
            mod_name = f"tools.src.python.{py.stem}"
            try:
                mod = importlib.import_module(mod_name)
                for _, fn in inspect.getmembers(mod, inspect.isfunction):
                    meta = get_meta(fn)
                    if meta:
                        self._register_python(meta, fn)
                        count += 1
            except Exception as e:  # noqa: BLE001
                print(f"[registry] 加载 {py.name} 失败: {e}")
        self.save()
        return count

    def _register_python(self, meta: Dict, fn: Callable) -> None:
        name = meta["name"]
        source_path = f"{self.tools_dir}/src/python/{fn.__module__.split('.')[-1]}.py"
        desc = meta["description"]
        self.tools[name] = {
            "name": name,
            "name_zh": self._name_zh(name),
            "description": desc,
            "keywords": self._TOOL_KEYWORDS.get(name) or self._extract_keywords(f"{self._name_zh(name)} {desc}"),
            "parameters": normalize_schema(meta["parameters"]),
            "impl": {
                "language": meta.get("language", "python"),
                "type": "python_function",
                "source": source_path,
                "entry": fn.__name__,
                "deps": meta.get("deps", []),
                "hash": self._hash_file(source_path),
            },
            "version": 1,
            "runtime": {
                "status": "active",
                "last_tested": None,
                "test_results": {"passed": 0, "failed": 0},
            },
        }
        self._fns[name] = fn

    # ---------- MCP 工具注册（见设计文档 5.37） ----------
    def register_mcp(self, tool_name: str, server: str, tool: str,
                     description: str = "", schema: Optional[Dict] = None) -> None:
        """注册一个 MCP server 暴露的工具为白绫工具（impl.type="mcp"，执行转发调用）。"""
        import re
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{server}_{tool}")
        self.tools[tool_name] = {
            "name": tool_name,
            "name_zh": f"MCP·{server}·{tool}",
            "description": description or f"调用 MCP server {server} 的 {tool} 工具",
            "keywords": [server, tool, "mcp"],
            "parameters": normalize_schema(schema or {"type": "object", "properties": {}}),
            "impl": {"type": "mcp", "server": server, "tool": tool},
            "version": 1,
            "runtime": {"status": "active", "last_tested": None,
                        "test_results": {"passed": 0, "failed": 0}},
            "source": "mcp",
        }
        _ = safe  # 命名已在 sync 层完成，此处保留工具原名映射即可
        self._fns.pop(tool_name, None)

    def remove_mcp_all(self) -> int:
        """清空所有 MCP 来源工具（server 删除/重同步前调用）。返回移除数量。"""
        removed = [n for n, t in self.tools.items() if t.get("source") == "mcp"]
        for n in removed:
            self.tools.pop(n, None)
            self._fns.pop(n, None)
        return len(removed)

    def remove_mcp_server(self, server: str) -> int:
        """只移除指定 MCP server 注册的工具（保留其他 server 的）。返回移除数量。"""
        removed = [n for n, t in self.tools.items()
                   if t.get("source") == "mcp" and t.get("impl", {}).get("server") == server]
        for n in removed:
            self.tools.pop(n, None)
            self._fns.pop(n, None)
        return len(removed)

    _TOOL_ZH = {
        "net_search": "网络搜索", "net_fetch": "网页抓取", "net_download": "文件下载",
        "cmd_run": "系统命令", "fs_list": "列目录", "fs_read": "读文件", "fs_search": "文件搜索",
        "fs_stat": "文件信息", "memory_write": "写记忆", "memory_search": "查记忆", "method_learn": "沉淀方法论",
        "ctx_search": "上下文检索",
        "sys_info": "环境信息", "sys_tools": "系统工具发现", "tool_create": "自建工具",
        "tool_import": "导入工具", "tool_acquire": "获取工具", "tool_scan": "扫描工具",
        "ws_mkdir": "建工作目录", "ws_write": "写工作文件", "word_count": "文本统计",
        "cpu_usage": "CPU占用", "self_backup": "自我备份", "self_clone": "自我复制",
        "self_restore": "自我恢复",
    }

    # 工具分组（多级目录：功能域 → 工具条目）
    _TOOL_GROUPS = {
        "网络": ["net_search", "net_fetch", "net_download"],
        "文件系统": ["fs_list", "fs_read", "fs_search", "fs_stat"],
        "系统与执行": ["cmd_run", "sys_info", "cpu_usage", "sys_tools"],
        "记忆与经验": ["memory_write", "memory_search", "method_learn"],
        "工作区": ["ws_mkdir", "ws_write"],
        "自我维护": ["self_backup", "self_clone", "self_restore"],
        "工具工程": ["tool_create", "tool_import", "tool_acquire", "tool_scan"],
        "文本处理": ["word_count"],
    }

    # 世界书触发关键词（手动配置，覆盖用户自然语言说法；自动提取仅作兜底）
    _TOOL_KEYWORDS = {
        "net_search": ["搜索", "查一下", "查找", "查询", "检索", "搜", "资讯", "了解下"],
        "net_fetch": ["抓取", "打开网页", "读取网页", "网址", "网页内容", "链接内容"],
        "net_download": ["下载", "下载视频", "保存文件", "拉文件", "下个"],
        "cmd_run": ["执行命令", "运行命令", "命令行", "powershell", "cmd", "终端"],
        "fs_list": ["列目录", "看看目录", "有哪些文件", "目录下"],
        "fs_read": ["读取", "打开文件", "看文件", "文件内容", "读一下"],
        "fs_search": ["搜索文件", "找文件", "文件名", "定位文件"],
        "fs_stat": ["文件信息", "文件大小", "文件详情", "元信息"],
        "memory_write": ["记住", "记忆", "记录", "学习", "长期记住", "存档"],
        "memory_search": ["查记忆", "搜记忆", "记忆里", "之前查过", "以前", "我记得", "存档", "查过"],
        "method_learn": ["方法论", "沉淀", "经验教训", "总结方法", "教训"],
        "sys_info": ["环境", "系统信息", "配置", "cpu", "内存", "磁盘", "机器"],
        "sys_tools": ["已安装", "系统工具", "软件", "发现了什么", "有哪些软件"],
        "tool_create": ["创建工具", "新工具", "写个工具", "自建", "编写工具"],
        "tool_import": ["导入工具", "继承", "前辈", "收集工具", "旧工具"],
        "tool_acquire": ["获取工具", "拉取", "克隆", "github", "git仓库"],
        "tool_scan": ["扫描工具", "发现工具", "tool_scan"],
        "ws_mkdir": ["建目录", "任务目录", "创建文件夹", "工作区"],
        "ws_write": ["保存", "写入文件", "写入", "生成文件", "写到"],
        "word_count": ["统计", "字数", "词频", "word_count"],
        "cpu_usage": ["cpu占用", "cpu使用率", "cpu高"],
        "self_backup": ["备份", "保存状态", "快照", "备份自己", "保险"],
        "self_clone": ["复制", "迁移", "换个地方", "克隆自己", "复制到"],
        "self_restore": ["恢复", "还原", "复活", "从备份"],
    }

    # 工具功能域（多级目录一级分类）
    _TOOL_CATEGORY = {
        "网络": ["net_search", "net_fetch", "net_download", "tool_acquire"],
        "文件": ["fs_list", "fs_read", "fs_search", "fs_stat", "ws_mkdir", "ws_write"],
        "系统": ["cmd_run", "sys_info", "sys_tools", "cpu_usage"],
        "自我": ["memory_write", "memory_search", "method_learn", "self_backup", "self_clone", "self_restore"],
        "工具自举": ["tool_create", "tool_import", "tool_scan"],
        "文本": ["word_count"],
    }

    def _name_zh(self, name: str) -> str:
        return self._TOOL_ZH.get(name, name)

    @staticmethod
    def _extract_keywords(text: str, maxn: int = 8) -> List[str]:
        """从工具名+描述提取触发关键词（2~4 字中文片段去重）。"""
        import re
        stops = {"一个", "这个", "那个", "什么", "怎么", "进行", "可以", "需要", "文件", "工具",
                 "内容", "返回", "指定", "支持", "如果", "用于", "以及", "自动", "当前", "系统",
                 "获取", "网络", "搜索"}
        kws: List[str] = []
        for w in re.findall(r"[\u4e00-\u9fff]{2,4}", text or ""):
            if w not in stops and w not in kws:
                kws.append(w)
        return kws[:maxn]

    @staticmethod
    def _hash_file(path: str) -> str:
        try:
            with open(path, "rb") as f:
                return "sha256:" + hashlib.sha256(f.read()).hexdigest()[:16]
        except Exception:  # noqa: BLE001
            return "unknown"

    # ---------- 查询 ----------
    def get(self, name: str) -> Optional[Dict]:
        return self.tools.get(name)

    def list_active(self) -> List[Dict]:
        return [t for t in self.tools.values() if t.get("runtime", {}).get("status") == "active"]

    def to_openai_schemas(self, names: Optional[List[str]] = None) -> List[Dict]:
        """转成 OpenAI function calling 格式。names=None 默认返回核心工具（世界书：长尾按需加载）。"""
        active = self.list_active()
        if names is not None:
            name_set = set(names)
            active = [t for t in active if t["name"] in name_set]
        else:
            active = [t for t in active if t["name"] in self.CORE_TOOLS or t["name"] == "method_learn"]
        schemas = []
        for t in active:
            schemas.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": normalize_schema(t["parameters"]),
                },
            })
        return schemas

    def get_schema(self, name: str) -> Optional[Dict]:
        """单个工具 schema（世界书按需加载用）。"""
        t = self.tools.get(name)
        if not t or t.get("runtime", {}).get("status") != "active":
            return None
        return {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": normalize_schema(t["parameters"]),
            },
        }

    def is_core(self, name: str) -> bool:
        return name in self.CORE_TOOLS

    def to_index_json(self) -> List[Dict]:
        """工具世界书多级目录（JSON 结构，适配底层逻辑）：
        [{"group", "core":[{name,name_zh}], "ext":[{name,name_zh,keywords}]}]"""
        active = {t["name"]: t for t in self.list_active()}
        grouped = set()
        for names in self._TOOL_GROUPS.values():
            grouped.update(names)
        groups: Dict[str, List[str]] = {g: list(ns) for g, ns in self._TOOL_GROUPS.items()}
        for n in active:
            if n not in grouped:
                groups.setdefault("其他", []).append(n)
        out = []
        for gname, names in groups.items():
            core = [{"name": n, "name_zh": active[n].get("name_zh", "")}
                    for n in names if n in active and self.is_core(n)]
            ext = [{"name": n, "name_zh": active[n].get("name_zh", ""),
                    "keywords": active[n].get("keywords", [])[:4]}
                   for n in names if n in active and not self.is_core(n)]
            if core or ext:
                out.append({"group": gname, "core": core, "ext": ext})
        return out

    def to_index(self) -> str:
        """工具世界书多级目录（Markdown 渲染视图，给 LLM 展示用）：从 JSON 结构渲染。"""
        lines = ["（多级目录：功能域 → 工具条目；核心=常驻可用，扩展=说工具名自动加载）"]
        for g in self.to_index_json():
            parts = []
            if g["core"]:
                parts.append("核心:" + ", ".join(f"{c['name']}({c['name_zh']})" for c in g["core"]))
            if g["ext"]:
                parts.append("扩展:" + "; ".join(f"{e['name']}({e['name_zh']})触发{ '/'.join(e['keywords']) }" for e in g["ext"]))
            if parts:
                lines.append(f"▶ {g['group']}  " + "  ".join(parts))
        return "\n".join(lines)

    def suggest_ext(self, text: str, loaded: set) -> List[str]:
        """世界书触发：输入文本命中长尾工具关键词 → 建议加载（返回未加载的工具名）。"""
        low = text.lower()
        names = []
        for t in self.list_active():
            n = t["name"]
            if n in self.CORE_TOOLS or n in loaded:
                continue
            if any(k and k.lower() in low for k in t.get("keywords", [])):
                names.append(n)
        return names

    # ---------- 执行分发 ----------
    def execute(self, name: str, args: Dict) -> Dict:
        """分发执行工具。返回 {"ok": bool, "result": ...} 或 {"ok": False, "error": ...}"""
        entry = self.tools.get(name)
        if not entry:
            return {"ok": False, "error": f"工具不存在: {name}"}
        impl = entry["impl"]
        if impl["type"] == "python_function":
            result = self._execute_python(name, args)
            # 工具创建/导入成功后：先 load 磁盘（含 Go 工具等非 Python 条目），
            # 再扫描注册 Python 工具，保存时保留全部（含刚创建的 Go 二进制工具）。
            if name in ("tool_create", "tool_import") and result.get("ok"):
                self.load()
                self.discover_builtin()
            # MCP 工具同步/移除写入磁盘后，重载生效（MCP 工具命名 mcp_<server>_<tool>）
            if name in ("mcp_scan", "mcp_disconnect") and result.get("ok"):
                self.load()
            return result
        if impl["type"] == "go_binary":
            return self._execute_go(name, args, impl)
        if impl["type"] == "mcp":
            if self.mcp is None:
                return {"ok": False, "error": f"MCP 管理器未初始化（server={impl.get('server')}）"}
            return self.mcp.call(impl.get("server", ""), impl.get("tool", ""), args)
        return {"ok": False, "error": f"未知工具类型: {impl['type']}"}

    def _execute_python(self, name: str, args: Dict) -> Dict:
        fn = self._fns.get(name)
        if fn is None:
            return {"ok": False, "error": f"工具未加载: {name}"}
        try:
            result = fn(**args)
            return {"ok": True, "result": result}
        except TypeError as e:
            return {"ok": False, "error": f"参数错误: {e}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"执行异常: {type(e).__name__}: {e}"}

    def _execute_go(self, name: str, args: Dict, impl: Dict) -> Dict:
        """Go 工具：子进程 + stdin/stdout JSON 协议。"""
        binary = impl.get("binary")
        if not binary or not os.path.exists(binary):
            return {"ok": False, "error": f"Go 二进制缺失: {binary}，需要先编译"}
        try:
            payload = json.dumps({"args": args}, ensure_ascii=False)
            proc = subprocess.run(
                [binary],
                input=payload,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                return {"ok": False, "error": f"Go 工具退出码 {proc.returncode}: {proc.stderr[:500]}"}
            out = json.loads(proc.stdout.strip())
            return out
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Go 工具执行超时（>30s）"}
        except json.JSONDecodeError as e:
            return {"ok": False, "error": f"Go 工具输出非 JSON: {e}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"Go 工具执行异常: {e}"}


if __name__ == "__main__":
    reg = ToolRegistry()
    n = reg.discover_builtin()
    print(f"发现 {n} 个内置工具")
    print(json.dumps(reg.to_openai_schemas(), ensure_ascii=False, indent=2))
